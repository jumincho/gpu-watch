from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 600_000
MAX_PIN_HASH_ITERATIONS = 2_000_000
PIN_LENGTH = 4
MIN_ANNOUNCEMENT_PASSPHRASE_LENGTH = 4
MAX_ANNOUNCEMENT_PASSPHRASE_LENGTH = 64
MAX_PASSPHRASE_HASH_INPUT_LENGTH = 512


def is_valid_pin(value: object) -> bool:
    text = str(value or "")
    return len(text) == PIN_LENGTH and text.isascii() and text.isdigit()


def is_valid_announcement_passphrase(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return (
        MIN_ANNOUNCEMENT_PASSPHRASE_LENGTH <= len(value) <= MAX_ANNOUNCEMENT_PASSPHRASE_LENGTH
        and value.isprintable()
        and not value.isspace()
    )


def hash_passphrase(passphrase: str, *, iterations: int = PIN_HASH_ITERATIONS) -> str:
    if len(str(passphrase)) > MAX_PASSPHRASE_HASH_INPUT_LENGTH:
        raise ValueError("passphrase is too long")
    iterations = int(iterations)
    if not 1 <= iterations <= MAX_PIN_HASH_ITERATIONS:
        raise ValueError("passphrase hash iteration count is outside the supported range")
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(passphrase).encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PIN_HASH_ALGORITHM}${iterations}${salt}${encoded}"


def verify_passphrase(passphrase: str | None, encoded: str | None) -> bool:
    if not passphrase or not encoded:
        return False
    try:
        if len(str(passphrase)) > MAX_PASSPHRASE_HASH_INPUT_LENGTH:
            return False
        algorithm, iterations, salt, digest_text = encoded.split("$", 3)
        if algorithm != PIN_HASH_ALGORITHM:
            return False
        iteration_count = int(iterations)
        if not 1 <= iteration_count <= MAX_PIN_HASH_ITERATIONS:
            return False
        if not 8 <= len(salt) <= 128:
            return False
        expected = base64.b64decode(digest_text.encode("ascii"), validate=True)
        if len(expected) != hashlib.sha256().digest_size:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            str(passphrase).encode("utf-8"),
            salt.encode("utf-8"),
            iteration_count,
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def hash_needs_upgrade(encoded: str | None) -> bool:
    try:
        algorithm, iterations, salt, digest_text = str(encoded or "").split("$", 3)
        iteration_count = int(iterations)
        digest = base64.b64decode(digest_text.encode("ascii"), validate=True)
        return (
            algorithm != PIN_HASH_ALGORITHM
            or iteration_count < PIN_HASH_ITERATIONS
            or iteration_count > MAX_PIN_HASH_ITERATIONS
            or not 8 <= len(salt) <= 128
            or len(digest) != hashlib.sha256().digest_size
        )
    except (TypeError, ValueError):
        return True
