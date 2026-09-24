#!/usr/bin/env python3
"""Keep the GPU Watch runtime SSH security preamble singular and idempotent."""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path


MAX_CONFIG_BYTES = 1024 * 1024
MANAGED_PREAMBLE = (
    "Host *\n"
    "  StrictHostKeyChecking yes\n"
    "  UpdateHostKeys no\n"
    "  UserKnownHostsFile /home/gpuwatch/.ssh/known_hosts\n\n"
)


def normalize_config(path: Path) -> tuple[bool, int]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("runtime SSH config must not be a symlink")
    path = path.resolve(strict=True)
    file_stat = path.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("runtime SSH config must be a regular file")
    if file_stat.st_size > MAX_CONFIG_BYTES:
        raise ValueError("runtime SSH config is too large")

    source = path.read_bytes()
    if b"\x00" in source:
        raise ValueError("runtime SSH config contains NUL")
    text = source.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    removed = 0
    while text.startswith(MANAGED_PREAMBLE):
        text = text[len(MANAGED_PREAMBLE):]
        removed += 1
    normalized = MANAGED_PREAMBLE + text.lstrip("\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    payload = normalized.encode("utf-8")
    changed = payload != source
    if not changed:
        os.chmod(path, 0o600)
        return False, removed

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.normalize-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True, removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    changed, removed = normalize_config(args.config)
    print(f"runtime SSH config normalized: changed={str(changed).lower()} removed_preambles={removed}")


if __name__ == "__main__":
    main()
