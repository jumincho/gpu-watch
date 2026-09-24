from __future__ import annotations

import os
import math
import re
from typing import Any

from gpu_watch.security import redact_sensitive_text


_SAFE_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")
_SAFE_START_IDENTITY = re.compile(r"^[0-9]{1,32}$")
_SAFE_COMMAND_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SAFE_MODULE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_SCRIPT_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.(?:py|pyw|sh|bash|zsh|js|mjs|cjs|r|pl|rb|lua)$",
    re.IGNORECASE,
)
_GPU_NUMERIC_LIMIT = 2_147_483_647
_DISK_NUMERIC_LIMIT = 9_223_372_036_854_775_807


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _redacted_text(value: Any, limit: int) -> str:
    return redact_sensitive_text(value, limit)


def _safe_label(value: Any, limit: int, fallback: str, *, basename: bool = False) -> str:
    """Bound a display label and discard it completely if it embeds a secret."""
    label = _text(value, limit * 2)
    if basename:
        label = os.path.basename(label)
    label = label[:limit]
    # Leave room for the marker even when an assignment begins at the label's
    # final bytes; otherwise truncation could hide that redaction was needed.
    if not label or "[redacted]" in redact_sensitive_text(label, limit + len("[redacted]")):
        return fallback
    return label


def _safe_command_summary(value: Any, process_name: str) -> str:
    """Accept only an executable plus one script basename or a Python module."""
    summary = _text(value, 260)
    parts = summary.split(" ") if summary else []
    if not parts or parts[0] != process_name or not _SAFE_COMMAND_TOKEN.fullmatch(parts[0]):
        return process_name
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2 and _SAFE_SCRIPT_NAME.fullmatch(parts[1]):
        return summary
    if len(parts) == 3 and parts[1] == "-m" and _SAFE_MODULE_NAME.fullmatch(parts[2]):
        return summary
    return process_name


def sanitize_process(process: Any) -> dict[str, Any] | None:
    """Keep only fields needed by the compact dashboard; never persist argv."""
    if not isinstance(process, dict):
        return None
    raw_pid = process.get("pid")
    if raw_pid is None:
        return None
    try:
        pid = int(raw_pid)
    except (OverflowError, TypeError, ValueError):
        return None
    if pid <= 0 or pid > _GPU_NUMERIC_LIMIT:
        return None
    raw_memory = process.get("used_memory")
    try:
        numeric_memory = float(raw_memory) if raw_memory is not None else math.nan
        used_memory = max(0, min(_GPU_NUMERIC_LIMIT, int(numeric_memory))) if math.isfinite(numeric_memory) else None
    except (OverflowError, TypeError, ValueError):
        used_memory = None

    process_name = _safe_label(process.get("process_name"), 128, "process", basename=True)
    # Keep unresolved or redacted ownership empty.  A presentation fallback is
    # safer than storing a value that can later be mistaken for a real user.
    user = _safe_label(process.get("user"), 64, "")
    if user == "?":
        user = ""
    container_name = _safe_label(process.get("container_name"), 128, "container")
    container_id = _text(process.get("container_id"), 64)
    if not _SAFE_CONTAINER_ID.fullmatch(container_id):
        container_id = ""

    result: dict[str, Any] = {
        "pid": pid,
        "user": user,
        "process_name": process_name,
        "command_summary": _safe_command_summary(process.get("command_summary"), process_name),
        "used_memory": used_memory,
        "started": _text(process.get("started"), 64),
        "container_name": container_name if process.get("container_name") else "",
    }
    if container_id:
        result["container_id"] = container_id[:12]
    start_identity = _text(process.get("start_identity"), 32)
    if _SAFE_START_IDENTITY.fullmatch(start_identity):
        # Linux /proc start ticks distinguish PID generations. This field is
        # persisted for attribution continuity but removed from public output.
        result["start_identity"] = start_identity
    return result


def sanitize_processes(processes: Any, *, limit: int = 128) -> list[dict[str, Any]]:
    if not isinstance(processes, list):
        return []
    result = []
    for item in processes[: max(0, int(limit))]:
        sanitized = sanitize_process(item)
        if sanitized is not None:
            result.append(sanitized)
    return result


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        result = int(numeric)
    except (OverflowError, TypeError, ValueError):
        return None
    return result if minimum <= result <= maximum else None


def sanitize_gpu_snapshot(gpu: Any) -> dict[str, Any] | None:
    """Normalize the exact GPU fields accepted from a remote probe."""
    if not isinstance(gpu, dict):
        return None
    index = _bounded_int(gpu.get("index"), minimum=0, maximum=1024)
    if index is None:
        return None
    return {
        "index": index,
        "name": _text(gpu.get("name"), 256) or "GPU",
        "uuid": _text(gpu.get("uuid"), 128),
        "utilization": _bounded_int(gpu.get("utilization"), minimum=0, maximum=100),
        "memory_used": _bounded_int(gpu.get("memory_used"), minimum=0, maximum=_GPU_NUMERIC_LIMIT),
        "memory_total": _bounded_int(gpu.get("memory_total"), minimum=0, maximum=_GPU_NUMERIC_LIMIT),
        "temperature": _bounded_int(gpu.get("temperature"), minimum=-100, maximum=300),
        "processes": sanitize_processes(gpu.get("processes")),
    }


def sanitize_disk_snapshot(disk: Any) -> dict[str, Any]:
    """Keep a bounded, exact allowlist for remote disk-usage snapshots."""
    result: dict[str, Any] = {"filesystems": [], "users": [], "errors": []}
    if not isinstance(disk, dict):
        return result

    filesystems = disk.get("filesystems")
    if isinstance(filesystems, list):
        for item in filesystems[:128]:
            if not isinstance(item, dict):
                continue
            total = _bounded_int(item.get("total_bytes"), minimum=0, maximum=_DISK_NUMERIC_LIMIT)
            used = _bounded_int(item.get("used_bytes"), minimum=0, maximum=_DISK_NUMERIC_LIMIT)
            available = _bounded_int(item.get("available_bytes"), minimum=0, maximum=_DISK_NUMERIC_LIMIT)
            use_percent = _bounded_int(item.get("use_percent"), minimum=0, maximum=100)
            result["filesystems"].append({
                "filesystem": _text(item.get("filesystem"), 256),
                "type": _text(item.get("type"), 64),
                "mount": _text(item.get("mount"), 512),
                "total_bytes": total,
                "used_bytes": used,
                "available_bytes": available,
                "use_percent": use_percent,
            })

    legacy_incomplete = any(
        any(word in str(error).lower() for word in ("partial", "permission", "timeout", "unavailable"))
        for error in (disk.get("errors") or [])[:32]
    ) if isinstance(disk.get("errors"), list) else False
    users = disk.get("users")
    if isinstance(users, list):
        for item in users[:64]:
            if not isinstance(item, dict):
                continue
            user = _text(item.get("user"), 128)
            size = _bounded_int(item.get("bytes"), minimum=0, maximum=_DISK_NUMERIC_LIMIT)
            if not user or size is None:
                continue
            locations: list[dict[str, str | int]] = []
            raw_locations = item.get("locations")
            if isinstance(raw_locations, list):
                for location in raw_locations[:32]:
                    if not isinstance(location, dict):
                        continue
                    path = _text(location.get("path"), 512)
                    location_size = _bounded_int(
                        location.get("bytes"), minimum=0, maximum=_DISK_NUMERIC_LIMIT
                    )
                    if path and location_size is not None:
                        locations.append({"path": path, "bytes": location_size, "complete": location.get("complete", not legacy_incomplete) is True})
            if len(locations) == 1:
                path = str(locations[0]["path"])
            elif locations:
                path = f"{len(locations)} locations"
            else:
                path = _text(item.get("path"), 512)
            result["users"].append({
                "user": user,
                "bytes": size,
                "complete": item.get("complete", not legacy_incomplete) is True and all(loc["complete"] for loc in locations),
                "locations": locations,
                "path": path,
            })

    errors = disk.get("errors")
    if isinstance(errors, list):
        result["errors"] = [_redacted_text(item, 300) for item in errors[:32] if _text(item, 1)]
    return result
