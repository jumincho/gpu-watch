#!/usr/bin/env python3
"""Read-only consistency audit; JSON output contains counts, never credentials."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

KST = timezone(timedelta(hours=9))


def audit_database(database: Path, config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    labs = {h["name"]: h.get("lab", "default") for h in config["hosts"]}
    failures = []
    result = {"ok": False, "checks": {}, "daily_recalculations": []}
    # One consistent in-memory snapshot; never migrate or write the supplied DB.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(":memory:")) as conn:
        source.backup(conn)
        conn.row_factory = sqlite3.Row
        integrity = [r[0] for r in conn.execute("pragma quick_check")]
        foreign_keys = list(conn.execute("pragma foreign_key_check"))
        result["checks"]["integrity"] = integrity
        result["checks"]["foreign_key_violations"] = len(foreign_keys)
        if integrity != ["ok"] or foreign_keys:
            failures.append("database_integrity")
        for table, query in (
            ("gpu_usage_interval", "select * from gpu_usage_interval order by host,gpu_index,start_ts,end_ts"),
            ("gpu_capacity_interval", "select * from gpu_capacity_interval order by host,gpu_index,start_ts,end_ts"),
        ):
            rows = conn.execute(query).fetchall()
            previous = {}
            malformed = overlaps = 0
            for row in rows:
                start, end = row["start_ts"], row["end_ts"]
                malformed += int(end < start or abs(row["seconds"] - (end-start)) > 0.001)
                key = (row["host"], row["gpu_index"])
                overlaps += int(start < previous.get(key, start) - 0.001)
                previous[key] = max(previous.get(key, end), end)
            result["checks"][table] = {"rows": len(rows), "invalid_durations": malformed, "overlaps": overlaps}
            if malformed or overlaps:
                failures.append(table)
        attribution_errors = 0
        for row in conn.execute("select observed_seconds,busy_seconds,user_busy_json from gpu_runtime"):
            shares = json.loads(row["user_busy_json"] or "{}")
            attribution_errors += int(
                row["observed_seconds"] + 0.001 < row["busy_seconds"]
                or any(float(v) < 0 for v in shares.values())
                or sum(map(float, shares.values())) > row["busy_seconds"] + 0.001
                or "?" in shares
            )
        result["checks"]["attribution_invariant_errors"] = attribution_errors
        if attribution_errors:
            failures.append("attribution")
        question_users = conn.execute("select count(*) from events where users='?' or users like '%, ?%' or users like '?, %'").fetchone()[0]
        result["checks"]["literal_question_mark_users"] = question_users
        if question_users:
            failures.append("question_mark_user")
        raw = conn.execute("""
            select c.* from gpu_capacity_interval c join gpu_runtime g
              on g.host=c.host and g.gpu_index=c.gpu_index
            where g.active=1 and c.memory_used_mib is not null and c.memory_total_mib is not null
            order by c.start_ts
        """).fetchall()
        # A long coalesced interval may begin before retention while other
        # GPUs have already pruned that day: verify only complete retained days.
        latest = max((r["end_ts"] for r in raw), default=0)
        earliest = latest - max(8, int(config.get("raw_interval_retention_days", 8))) * 86400
        for daily in conn.execute("select * from lab_daily_index order by day,lab_id"):
            # A running day is revised as later samples extend earlier intervals.
            # Only finalized days can be reconstructed from a later snapshot exactly.
            if not daily["complete"]:
                continue
            start = datetime.fromisoformat(daily["day"]).replace(tzinfo=KST).timestamp()
            if start < earliest:  # Older rollups outlive their eight-day raw data.
                continue
            end = min(start + 86400, daily["updated_ts"])
            if end <= start:
                continue
            weights, durations, sweep = defaultdict(float), defaultdict(float), defaultdict(int)
            for row in raw:
                if labs.get(row["host"]) != daily["lab_id"]:
                    continue
                a, b = max(start, row["start_ts"]), min(end, row["end_ts"])
                if a >= b:
                    continue
                key = (row["host"], row["gpu_index"])
                memory = max(0, min(row["memory_used_mib"], row["memory_total_mib"])) / 1024
                weights[key] += memory * (b-a)
                durations[key] += b-a
                sweep[a] += 1
                sweep[b] -= 1
            if not durations:
                continue
            expected = sum(weights[k] / duration for k, duration in durations.items())
            active, last, wall = 0, start, 0.0
            for stamp in sorted(sweep):
                if active:
                    wall += stamp-last
                active += sweep[stamp]
                last = stamp
            delta = abs(expected-daily["average_used_gb"])
            wall_delta = abs(wall-daily["observed_seconds"])
            result["daily_recalculations"].append({
                "lab": daily["lab_id"], "day": daily["day"],
                "index": round(expected, 4), "index_error": round(delta, 8),
                "coverage_error_seconds": round(wall_delta, 6),
            })
            if delta > 0.000051 or wall_delta > 0.001:
                failures.append("daily:" + daily["lab_id"] + ":" + daily["day"])
    result["failures"] = failures
    result["ok"] = not failures
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "hosts.json")
    args = parser.parse_args()
    result = audit_database(args.database, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
