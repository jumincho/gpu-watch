#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gpu_watch.processes import sanitize_processes  # noqa: E402 -- repository root must precede this script import.
from gpu_watch.security import redact_sensitive_text  # noqa: E402 -- same repository import boundary.


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "select 1 from sqlite_master where type='table' and name=?", (name,)
    ).fetchone() is not None


def sanitize_database(path: Path) -> dict[str, int | str]:
    path = path.resolve()
    original_stat = path.stat()
    temporary = path.with_name(f".{path.name}.sanitize.tmp")
    def cleanup_temporary() -> None:
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)

    cleanup_temporary()
    result: dict[str, int | str] = {"path": str(path), "events": 0, "gpus": 0, "host_errors": 0}
    try:
        with ExitStack() as stack:
            source = stack.enter_context(closing(sqlite3.connect(path, timeout=30)))
            destination = stack.enter_context(closing(sqlite3.connect(temporary, timeout=30)))
            checkpoint = source.execute("pragma wal_checkpoint(truncate)").fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise RuntimeError(f"source WAL checkpoint was busy: {checkpoint}")
            source.backup(destination)
            destination.commit()
    except Exception:
        cleanup_temporary()
        raise

    connection = sqlite3.connect(temporary, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        if table_exists(connection, "events"):
            result["events"] = connection.execute(
                "update events set processes_json=null where processes_json is not null"
            ).rowcount
        if table_exists(connection, "gpu_runtime"):
            rows = connection.execute(
                "select host, gpu_index, last_snapshot_json, last_processes_json from gpu_runtime"
            ).fetchall()
            for row in rows:
                try:
                    snapshot = json.loads(row["last_snapshot_json"] or "{}")
                except Exception:
                    snapshot = {}
                if not isinstance(snapshot, dict):
                    snapshot = {}
                try:
                    internal_processes = json.loads(row["last_processes_json"] or "[]")
                except Exception:
                    internal_processes = []
                if not isinstance(internal_processes, list):
                    internal_processes = snapshot.get("processes")
                processes = sanitize_processes(internal_processes)
                snapshot["processes"] = [
                    {key: value for key, value in process.items() if key != "start_identity"}
                    for process in processes
                ]
                connection.execute(
                    "update gpu_runtime set last_processes_json=?, last_snapshot_json=? where host=? and gpu_index=?",
                    (
                        json.dumps(processes, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                        row["host"],
                        row["gpu_index"],
                    ),
                )
                result["gpus"] = int(result["gpus"]) + 1
        if table_exists(connection, "host_runtime"):
            host_columns = {
                str(row[1]) for row in connection.execute("pragma table_info(host_runtime)")
            }
            if "last_error" in host_columns:
                rows = connection.execute(
                    "select host, last_error from host_runtime where last_error is not null"
                ).fetchall()
                for row in rows:
                    sanitized = redact_sensitive_text(row["last_error"])
                    if sanitized == row["last_error"]:
                        continue
                    connection.execute(
                        "update host_runtime set last_error=? where host=?",
                        (sanitized, row["host"]),
                    )
                    result["host_errors"] = int(result["host_errors"]) + 1
        connection.commit()
        connection.execute("vacuum")
        connection.execute("pragma optimize")
        integrity = str(connection.execute("pragma quick_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"integrity check failed: {integrity}")
    except Exception:
        connection.close()
        cleanup_temporary()
        raise
    finally:
        connection.close()
    try:
        temporary.chmod(original_stat.st_mode & 0o777)
        os.utime(
            temporary,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        os.replace(temporary, path)
    finally:
        cleanup_temporary()
    return result


def expanded_paths(values: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        if value.is_dir():
            paths.extend(sorted(value.glob("*.sqlite3")))
        elif value.is_file():
            paths.append(value)
    return list(dict.fromkeys(path.resolve() for path in paths))


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove unsafe historic process arguments from GPU Watch databases")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in expanded_paths(args.paths):
        print(json.dumps(sanitize_database(path), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
