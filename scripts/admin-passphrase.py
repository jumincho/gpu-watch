#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gpu_watch.auth import (  # noqa: E402 -- repository root must precede this script import.
    hash_needs_upgrade,
    hash_passphrase,
    is_valid_pin,
)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure(hash_path: Path, initial_path: Path) -> int:
    encoded = hash_path.read_text(encoding="utf-8").strip() if hash_path.exists() else ""
    if encoded and not hash_needs_upgrade(encoded):
        if initial_path.exists():
            print(
                f"warning: plaintext initial passphrase still exists at {initial_path}; "
                "remove it after secure storage",
                file=sys.stderr,
            )
        print("admin passphrase hash is current")
        return 0
    passphrase = f"{secrets.randbelow(10_000):04d}"
    new_hash = hash_passphrase(passphrase)
    previous_initial = initial_path.read_text(encoding="utf-8") if initial_path.exists() else None
    atomic_write(initial_path, passphrase)
    try:
        atomic_write(hash_path, new_hash)
    except Exception:
        try:
            if previous_initial is None:
                initial_path.unlink(missing_ok=True)
            else:
                atomic_write(initial_path, previous_initial)
        except Exception as rollback_error:
            raise RuntimeError(
                f"admin hash update failed and the previous initial passphrase could not be restored: "
                f"{rollback_error}"
            ) from rollback_error
        raise
    print(f"admin passphrase rotated; retrieve it from {initial_path} and remove that file after secure storage")
    return 0


def rotate(hash_path: Path) -> int:
    first = getpass.getpass("New admin passphrase: ")
    second = getpass.getpass("Confirm admin passphrase: ")
    if first != second:
        raise SystemExit("passphrases do not match")
    if not is_valid_pin(first):
        raise SystemExit("admin PIN must be exactly 4 digits")
    atomic_write(hash_path, hash_passphrase(first))
    print("admin passphrase hash updated")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision the GPU Watch admin passphrase hash")
    parser.add_argument("mode", choices=("ensure", "rotate"))
    parser.add_argument("--hash-path", type=Path, default=ROOT / "data" / "admin_pin.hash")
    parser.add_argument(
        "--initial-path",
        type=Path,
        default=ROOT / "operator-secrets" / "admin-passphrase.initial",
    )
    args = parser.parse_args()
    return ensure(args.hash_path, args.initial_path) if args.mode == "ensure" else rotate(args.hash_path)


if __name__ == "__main__":
    raise SystemExit(main())
