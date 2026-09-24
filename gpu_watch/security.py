from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any, Protocol
from urllib.parse import urlparse


DEFAULT_ALLOWED_NETWORKS = "127.0.0.0/8,::1/128"
DEFAULT_ALLOWED_HOSTS = "127.0.0.1:8787,localhost:8787,[::1]:8787"

_ASSIGNMENT_LABEL = re.compile(
    r"(?<![A-Za-z0-9_.-])[\"']?(?P<key>[A-Za-z0-9_.-]{1,128})[\"']?\s*[:=]\s*",
    re.IGNORECASE,
)
_CLI_OPTION_LABEL = re.compile(
    r"(?<!\S)--(?P<key>[A-Za-z0-9_.-]{1,128})(?:=|\s+)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_PARTS = frozenset({
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "csrf",
    "key",
    "passcode",
    "passphrase",
    "passwd",
    "password",
    "pin",
    "secret",
    "session",
    "sessionid",
    "token",
    "xsrf",
})
_SENSITIVE_KEY_SUFFIXES = (
    "accesskey",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "passcode",
    "passphrase",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "sessionid",
    "token",
)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class HeaderCollection(Protocol):
    def get(self, name: str, default: Any = None) -> Any: ...


def redact_sensitive_text(value: Any, limit: int = 300) -> str:
    """Return a bounded one-line diagnostic without credential values.

    Once a credential assignment/header begins, the complete remaining value is
    discarded. Keeping less diagnostic text is preferable to leaking the tail of
    a quoted, bearer, or whitespace-containing secret.
    """
    maximum = max(1, int(limit))
    raw = str(value or "")[: maximum * 8]
    text = re.sub(r"[\r\n\t]+", " ", raw).strip()
    sensitive_ends: list[int] = []
    for pattern in (_ASSIGNMENT_LABEL, _CLI_OPTION_LABEL):
        for match in pattern.finditer(text):
            normalized = re.sub(r"[^a-z0-9]+", "_", match.group("key").lower()).strip("_")
            parts = {part for part in normalized.split("_") if part}
            joined = normalized.replace("_", "")
            if parts.intersection(_SENSITIVE_KEY_PARTS) or joined.endswith(_SENSITIVE_KEY_SUFFIXES):
                sensitive_ends.append(match.end())
    if sensitive_ends:
        text = f"{text[:min(sensitive_ends)]}[redacted]"
    return text[:maximum]


def parse_allowed_networks(value: str | Iterable[str] | None) -> tuple[Any, ...]:
    if value is None:
        value = DEFAULT_ALLOWED_NETWORKS
    parts = value.split(",") if isinstance(value, str) else list(value)
    networks = []
    for part in parts:
        text = str(part).strip()
        if text:
            networks.append(ipaddress.ip_network(text, strict=False))
    if not networks:
        raise ValueError("at least one allowed network is required")
    return tuple(networks)


def is_ip_allowed(address: str, networks: Iterable[Any]) -> bool:
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    return any(ip.version == network.version and ip in network for network in networks)


def parse_allowed_hosts(value: str | Iterable[str] | None) -> frozenset[str]:
    if value is None:
        value = DEFAULT_ALLOWED_HOSTS
    parts = value.split(",") if isinstance(value, str) else list(value)
    hosts: set[str] = set()
    for part in parts:
        host = str(part).strip().lower()
        if (
            not host
            or len(host) > 255
            or any(character.isspace() for character in host)
            or any(character in host for character in "/\\@,#?")
        ):
            raise ValueError("allowed hosts contain an invalid authority")
        hosts.add(host)
    if not hosts:
        raise ValueError("at least one allowed host is required")
    return frozenset(hosts)


def is_host_allowed(host_header: Any, allowed_hosts: Iterable[str]) -> bool:
    host = str(host_header or "").strip().lower()
    return bool(host) and host in allowed_hosts


def is_same_origin(headers: HeaderCollection) -> bool:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all) and len(get_all("Origin") or []) > 1:
        return False
    origin = str(headers.get("Origin") or "").strip()
    if not origin:
        return True
    if any(character.isspace() or ord(character) < 32 for character in origin):
        return False
    host = str(headers.get("Host") or "").strip().lower()
    try:
        parsed = urlparse(origin)
        # Invalid bracketed hosts and ports are untrusted input, not server
        # errors. An Origin is a serialized authority, never a full page URL.
        _ = parsed.port  # Access validates the port and can raise ValueError.
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower() == host
        and not (parsed.path or parsed.params or parsed.query or parsed.fragment)
        and parsed.username is None
        and parsed.password is None
    )


def client_ip_from_proxy(
    peer_address: str,
    headers: HeaderCollection,
    trusted_proxy_networks: Iterable[Any],
) -> str:
    try:
        peer_ip = str(ipaddress.ip_address(str(peer_address).split("%", 1)[0]))
    except ValueError:
        # The socket peer is normally supplied by the OS, so an invalid value is
        # not expected. Keep it as a stable fallback while refusing to trust any
        # forwarded address associated with it.
        return str(peer_address)
    if not is_ip_allowed(peer_ip, trusted_proxy_networks):
        return peer_ip
    forwarded = str(headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    if not forwarded:
        return peer_ip
    try:
        # Canonicalise equivalent IPv6 spellings and discard an optional scope
        # identifier. Otherwise one client can manufacture multiple rate-limit
        # keys for the same address. ipaddress deliberately rejects ambiguous
        # IPv4 spellings such as octets with leading zeroes.
        return str(ipaddress.ip_address(forwarded.split("%", 1)[0]))
    except ValueError:
        return peer_ip


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else float(now)
        cutoff = ts - float(window_seconds)
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= int(limit):
                return False
            hits.append(ts)
            if len(self._hits) > 1024:
                stale_keys = [name for name, values in self._hits.items() if not values or values[-1] <= cutoff]
                for name in stale_keys:
                    self._hits.pop(name, None)
            return True

    def check(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> bool:
        """Return whether a hit could be accepted without consuming the slot."""
        ts = time.monotonic() if now is None else float(now)
        cutoff = ts - float(window_seconds)
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            return len(hits) < int(limit)

    def record(self, key: str, window_seconds: float, now: float | None = None) -> None:
        """Record a hit after an operation has been confirmed as a failure."""
        ts = time.monotonic() if now is None else float(now)
        cutoff = ts - float(window_seconds)
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            hits.append(ts)
            if len(self._hits) > 1024:
                stale_keys = [name for name, values in self._hits.items() if not values or values[-1] <= cutoff]
                for name in stale_keys:
                    self._hits.pop(name, None)
