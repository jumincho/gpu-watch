#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import sys


def get_json_response(path: str, timeout: float) -> tuple[int, dict]:
    if not path.startswith("/api/") or "://" in path:
        raise ValueError("health-check path must be a local API path")
    connection = http.client.HTTPConnection("127.0.0.1", 8787, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, json.load(response)
    finally:
        connection.close()


def get_json(path: str, timeout: float) -> dict:
    status, payload = get_json_response(path, timeout)
    if status != 200:
        raise RuntimeError(f"health-check endpoint returned HTTP {status}")
    return payload


def build_version_matches(health: dict, expected_build_version: str | None) -> bool:
    return not expected_build_version or health.get("build_version") == expected_build_version


def low_disk_is_the_only_health_failure(health: dict, expected_build_version: str | None) -> bool:
    database = health.get("database") or {}
    collector = health.get("collector") or {}
    maintenance = health.get("maintenance") or {}
    return (
        health.get("ok") is False
        and build_version_matches(health, expected_build_version)
        and database.get("ok") is False
        and database.get("disk_warning") is True
        and isinstance(database.get("disk_free_bytes"), int)
        and database["disk_free_bytes"] > 0
        and isinstance(database.get("disk_free_percent"), (int, float))
        and 0 < database["disk_free_percent"] < 5
        and collector.get("alive") is True
        and health.get("collector_fresh") is True
        and not collector.get("last_cycle_error")
        and maintenance.get("alive") is True
        and not maintenance.get("last_error")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the GPU Watch process from inside its container")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--health-only", action="store_true")
    mode.add_argument("--allow-low-disk-only", action="store_true")
    parser.add_argument("--expected-build-version")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()

    if args.allow_low_disk_only:
        status, health = get_json_response("/api/health", args.timeout)
        if status != 503 or not low_disk_is_the_only_health_failure(health, args.expected_build_version):
            print(json.dumps(health, ensure_ascii=False))
            return 1
        print(json.dumps({
            "ok": True,
            "accepted_warning": "low_disk_only",
            "build_version": health.get("build_version"),
            "disk_free_bytes": health["database"]["disk_free_bytes"],
            "disk_free_percent": health["database"]["disk_free_percent"],
        }, ensure_ascii=False))
        return 0

    health = get_json("/api/health", args.timeout)
    if not build_version_matches(health, args.expected_build_version):
        print(json.dumps({
            "ok": False,
            "error": "build_version_mismatch",
            "expected_build_version": args.expected_build_version,
            "actual_build_version": health.get("build_version"),
        }, ensure_ascii=False))
        return 1
    if not health.get("ok"):
        print(json.dumps(health, ensure_ascii=False))
        return 1
    if args.health_only:
        print("ok")
        return 0

    snapshot = get_json("/api/snapshot", args.timeout)
    hosts = snapshot.get("hosts") or []
    result = {
        "ok": True,
        "build_version": health.get("build_version"),
        "hosts": len(hosts),
        "online": sum(1 for host in hosts if host.get("online")),
        "gpus": sum(len(host.get("gpus") or []) for host in hosts),
        "collector": health.get("collector"),
        "maintenance": health.get("maintenance"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
