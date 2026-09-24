import tempfile
import time
import unittest
import gc
import json
import os
import runpy
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock

import server
from gpu_watch.auth import hash_passphrase, verify_passphrase


class StoreMaintenanceTests(unittest.TestCase):
    @staticmethod
    def collector_config():
        return {
            "disk_probe_workers": 1,
            "busy_memory_threshold_mib": 500,
            "busy_utilization_threshold_percent": 10,
            "usage_gap_seconds": 60,
            "offline_after_failures": 3,
            "poll_interval_seconds": 10,
        }

    def test_user_facing_time_helpers_are_explicitly_kst(self):
        expected = datetime(2026, 8, 5, 12, 34, tzinfo=server.KST).timestamp()
        self.assertEqual(server.parse_local_datetime("2026-08-05T12:34"), expected)
        self.assertEqual(server.parse_iso_datetime("2026-08-05T12:34"), expected)
        start, end = server.local_date_bounds("2026-08-05")
        self.assertEqual(start, datetime(2026, 8, 5, tzinfo=server.KST).timestamp())
        self.assertEqual(end - start, 86400)
        self.assertEqual(server.iso(0), "1970-01-01T09:00:00+09:00")

    def test_store_rejects_future_database_schema_without_downgrading_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "gpu_watch.sqlite3"
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute(
                    "create table schema_meta (key text primary key, value text not null, updated_ts real not null)"
                )
                conn.execute(
                    "insert into schema_meta(key, value, updated_ts) values('schema_version', '99', 1)"
                )
            with self.assertRaisesRegex(RuntimeError, "unsupported GPU Watch database schema version"):
                server.Store(database)
            with closing(sqlite3.connect(database)) as conn:
                value = conn.execute(
                    "select value from schema_meta where key='schema_version'"
                ).fetchone()[0]
            self.assertEqual(value, "99")

    def test_obsolete_metadata_is_removed_only_after_replacement_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(database)
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values (?, '{}', 1)",
                    (server.LEGACY_CONFERENCE_DEADLINE_KEY,),
                )
            server.Store(database)
            with closing(store.connect()) as conn:
                self.assertIsNotNone(conn.execute(
                    "select 1 from dashboard_settings where key=?",
                    (server.LEGACY_CONFERENCE_DEADLINE_KEY,),
                ).fetchone())

            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values (?, '[]', 2)",
                    (server.CONFERENCE_DEADLINES_KEY,),
                )
                conn.execute(
                    "insert into maintenance_meta(key, value) values ('event_usage_v1_0_1', 'complete')"
                )
            server.Store(database)
            with closing(store.connect()) as conn:
                self.assertIsNone(conn.execute(
                    "select 1 from dashboard_settings where key=?",
                    (server.LEGACY_CONFERENCE_DEADLINE_KEY,),
                ).fetchone())
                self.assertIsNone(conn.execute(
                    "select 1 from maintenance_meta where key='event_usage_v1_0_1'"
                ).fetchone())
                self.assertIsNotNone(conn.execute(
                    "select 1 from maintenance_meta where key=?",
                    (server.EVENT_USAGE_MIGRATION_KEY,),
                ).fetchone())

    def test_every_store_connection_uses_wal_normal_synchronous(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            for _ in range(2):
                with closing(store.connect()) as conn:
                    self.assertEqual(conn.execute("pragma journal_mode").fetchone()[0], "wal")
                    self.assertEqual(conn.execute("pragma synchronous").fetchone()[0], 1)

    def test_successful_eight_gpu_payload_uses_one_atomic_transaction(self):
        class TraceStore(server.Store):
            def __init__(self, path):
                self.statements = []
                super().__init__(path)

            def connect(self):
                conn = super().connect()
                conn.set_trace_callback(self.statements.append)
                return conn

        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir) / "gpu_watch.sqlite3")
            collector = server.Collector(store, self.collector_config())
            gpus = [
                {
                    "index": index,
                    "uuid": f"GPU-{index}",
                    "name": "GPU",
                    "utilization": 0,
                    "memory_used": 0,
                    "memory_total": 8192,
                    "temperature": 40,
                    "processes": [],
                }
                for index in range(8)
            ]
            store.statements.clear()
            try:
                collector.handle_payload(
                    {"name": "host", "label": "Host", "expected_gpu_count": 8},
                    {"ok": True, "gpus": gpus, "disk": {"filesystems": []}},
                )
            finally:
                collector.stop()
            transaction_statements = [
                statement.strip().upper() for statement in store.statements
                if statement.strip().upper() in {"BEGIN", "COMMIT", "ROLLBACK"}
            ]
            self.assertEqual(transaction_statements, ["BEGIN", "COMMIT"])
            with closing(store.connect()) as conn:
                self.assertEqual(conn.execute("select count(*) from gpu_runtime").fetchone()[0], 8)
                self.assertEqual(conn.execute("select count(*) from host_runtime").fetchone()[0], 1)
                self.assertEqual(conn.execute("select count(*) from host_disk").fetchone()[0], 1)

    def test_host_payload_rolls_back_everything_on_mid_gpu_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    create trigger reject_gpu_four before insert on gpu_runtime
                    when new.gpu_index=4
                    begin
                        select raise(abort, 'injected failure');
                    end
                    """
                )
            collector = server.Collector(store, self.collector_config())
            gpus = [
                {
                    "index": index,
                    "uuid": f"GPU-{index}",
                    "name": "GPU",
                    "utilization": 0,
                    "memory_used": 0,
                    "memory_total": 8192,
                    "temperature": 40,
                    "processes": [],
                }
                for index in range(8)
            ]
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    collector.handle_payload(
                        {"name": "host", "label": "Host", "expected_gpu_count": 8},
                        {"ok": True, "gpus": gpus, "disk": {"filesystems": []}},
                    )
            finally:
                collector.stop()
            with closing(store.connect()) as conn:
                for table in ("gpu_runtime", "events", "host_runtime", "host_disk"):
                    self.assertEqual(
                        conn.execute(f"select count(*) from {table}").fetchone()[0],
                        0,
                        table,
                    )

    def test_database_sanitizer_replaces_clean_verified_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into events(ts, host, gpu_index, event, processes_json) values (1, 'host', 0, 'legacy', ?)",
                    (json.dumps([{"cmd": "python --token secret"}]),),
                )
                conn.execute(
                    "insert into host_runtime(host, online, last_error) values ('host', 0, ?)",
                    ("Authorization: Bearer historic-secret trailing-value",),
                )
                notice_hash = hash_passphrase("2468")
                conn.execute(
                    "insert into announcements(created_ts, expires_ts, author, message, pin_hash) values (?, ?, ?, ?, ?)",
                    (time.time(), time.time() + 3600, "researcher", "keep owner PIN", notice_hash),
                )
            store.update_gpu("host", {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
                "processes": [{
                    "pid": 10,
                    "user": "researcher",
                    "started": "same-second",
                    "start_identity": "123456",
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": 1024,
                }],
            }, True, 60)
            namespace = runpy.run_path(
                str(Path(__file__).resolve().parents[1] / "scripts" / "sanitize-databases.py"),
                run_name="gpu_watch_sanitize_test",
            )
            result = namespace["sanitize_database"](path)
            with closing(sqlite3.connect(path)) as conn:
                remaining = conn.execute(
                    "select count(*) from events where processes_json is not null"
                ).fetchone()[0]
                integrity = conn.execute("pragma quick_check").fetchone()[0]
                preserved_hash = conn.execute(
                    "select pin_hash from announcements where author='researcher'"
                ).fetchone()[0]
                host_error = conn.execute(
                    "select last_error from host_runtime where host='host'"
                ).fetchone()[0]
                runtime = conn.execute(
                    "select last_processes_json, last_snapshot_json from gpu_runtime "
                    "where host='host' and gpu_index=0"
                ).fetchone()
            self.assertEqual(result["events"], 1)
            self.assertEqual(result["host_errors"], 1)
            self.assertEqual(remaining, 0)
            self.assertNotIn("historic-secret", host_error)
            self.assertNotIn("Bearer", host_error)
            self.assertEqual(integrity, "ok")
            self.assertTrue(verify_passphrase("2468", preserved_hash))
            internal_processes = json.loads(runtime[0])
            public_snapshot = json.loads(runtime[1])
            self.assertEqual(internal_processes[0]["start_identity"], "123456")
            self.assertNotIn("start_identity", public_snapshot["processes"][0])
            self.assertFalse(path.with_name(f".{path.name}.sanitize.tmp").exists())

    def test_process_payloads_are_sanitized_and_historic_argv_is_purged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            gpu = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
                "processes": [{
                    "pid": 10,
                    "user": "user",
                    "process_name": "/usr/bin/python",
                    "command_summary": "python train.py",
                    "cmd": "python train.py --password secret",
                    "used_memory": 1024,
                }],
            }
            store.update_gpu("host", gpu, True, 60)
            with closing(store.connect()) as conn, conn:
                snapshot = json.loads(conn.execute(
                    "select last_snapshot_json from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0])
                snapshot["processes"][0]["cmd"] = "python train.py --token historic-secret"
                conn.execute(
                    "update gpu_runtime set last_snapshot_json=?, last_processes_json=? where host='host' and gpu_index=0",
                    (json.dumps(snapshot), json.dumps(snapshot["processes"])),
                )
                conn.execute(
                    "insert into events(ts, host, gpu_index, event, processes_json) values (?, 'host', 0, 'legacy', ?)",
                    (time.time(), json.dumps([{"cmd": "historic-secret"}])),
                )
            reopened = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(reopened.connect()) as conn:
                snapshot = json.loads(conn.execute(
                    "select last_snapshot_json from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0])
                event_payloads = conn.execute(
                    "select count(*) from events where processes_json is not null"
                ).fetchone()[0]
            self.assertEqual(snapshot["processes"][0]["process_name"], "python")
            self.assertEqual(snapshot["processes"][0]["command_summary"], "python train.py")
            self.assertNotIn("cmd", snapshot["processes"][0])
            self.assertNotIn("historic-secret", str(snapshot))
            self.assertEqual(event_payloads, 0)
            del reopened
            del store
            gc.collect()

    def test_daily_rollup_excludes_inactive_gpu_inventory(self):
        today_start = datetime(2026, 8, 15, tzinfo=server.KST).timestamp()
        ts = today_start + 120
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_name, active, busy,
                        first_seen_ts, last_seen_ts, last_snapshot_json
                    ) values ('nll-a', ?, 'GPU', ?, 1, ?, ?, ?)
                    """,
                    [
                        (0, 1, today_start, ts, '{"memory_total":1024,"memory_used":1024}'),
                        (1, 0, today_start, ts, '{"memory_total":1024,"memory_used":1024}'),
                    ],
                )
                for gpu_index in (0, 1):
                    store._record_capacity_interval(
                        conn, "nll-a", gpu_index, today_start, ts, True, 1024, 1024
                    )
            config = {
                "hosts": [{"name": "nll-a", "lab": "nll"}],
                "labs": [{"id": "nll", "label": "NLL LAB"}],
                "insights_cache_seconds": 0,
            }
            lab = store.insights(config, ts)["labs"][0]
            self.assertEqual(lab["max_index"], 1.0)
            self.assertEqual(lab["daily_index"], 1.0)
            self.assertLessEqual(lab["daily_index"], lab["max_index"])
            del store
            gc.collect()

    def test_gpu_uuid_change_resets_recent_intervals_and_missing_gpu_is_inactive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {"index": 0, "name": "GPU", "memory_used": 0, "memory_total": 1024, "processes": []}
            store.update_gpu("host", {**base, "uuid": "GPU-old"}, False, 60)
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds) values ('host', 0, 1, 2, 'user', 1)"
                )
                conn.execute(
                    "insert into gpu_capacity_interval(host, gpu_index, start_ts, end_ts, busy, seconds) values ('host', 0, 1, 2, 0, 1)"
                )
            store.update_gpu("host", {**base, "uuid": "GPU-new"}, False, 60)
            store.reconcile_host_gpus("host", set())
            with closing(store.connect()) as conn:
                row = conn.execute(
                    "select gpu_uuid, active from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()
                changes = conn.execute(
                    "select count(*) from events where event='hardware_change'"
                ).fetchone()[0]
                usage_intervals = conn.execute(
                    "select count(*) from gpu_usage_interval where host='host' and gpu_index=0"
                ).fetchone()[0]
                capacity_intervals = conn.execute(
                    "select count(*) from gpu_capacity_interval where host='host' and gpu_index=0"
                ).fetchone()[0]
            self.assertEqual(tuple(row), ("GPU-new", 0))
            self.assertEqual(changes, 1)
            self.assertEqual(usage_intervals, 0)
            self.assertEqual(capacity_intervals, 0)
            del store
            gc.collect()

    def test_partial_gpu_payload_preserves_unavailable_runtime_without_observing_idle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            gpus = [
                {
                    "index": index,
                    "uuid": f"GPU-{index}",
                    "name": "GPU",
                    "utilization": 0,
                    "memory_used": 0,
                    "memory_total": 8192,
                    "temperature": 40,
                    "processes": [],
                }
                for index in range(2)
            ]
            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 115.0, 120.0]):
                store.apply_host_payload(
                    "host",
                    "Host",
                    [(gpus[0], False), (gpus[1], False)],
                    expected_gpu_indices={0, 1},
                )
                store.apply_host_payload(
                    "host",
                    "Host",
                    [(gpus[0], False)],
                    expected_gpu_indices={0, 1},
                    gpu_errors={1: "token=historic-secret Unknown Error"},
                    max_observation_gap_seconds=60,
                )
                store.mark_host(
                    "host",
                    "Host",
                    False,
                    error="temporary SSH miss",
                )
                config = server.load_config()
                config["hosts"] = [{
                    "name": "host",
                    "label": "Host",
                    "lab": "test",
                    "expected_gpu_count": 2,
                }]
                config["labs"] = [{"id": "test", "label": "Test"}]
                snapshot = store.snapshot(config)

            with closing(store.connect()) as conn:
                rows = conn.execute(
                    "select gpu_index, active, last_seen_ts, observed_seconds from gpu_runtime "
                    "where host='host' order by gpu_index"
                ).fetchall()
                runtime = conn.execute(
                    "select online, gpu_errors_json from host_runtime where host='host'"
                ).fetchone()
                event_count = conn.execute(
                    "select count(*) from events where host='host' and event in ('busy_start', 'free_start')"
                ).fetchone()[0]
            self.assertEqual([tuple(row) for row in rows], [(0, 1, 100.0, 0.0), (1, 1, 100.0, 0.0)])
            self.assertEqual(runtime["online"], 0)
            self.assertNotIn("historic-secret", runtime["gpu_errors_json"])
            self.assertEqual(event_count, 2)

            host = snapshot["hosts"][0]
            self.assertFalse(host["online"])
            self.assertNotIn("reachable",host)
            self.assertNotIn("degraded",host)
            self.assertEqual([gpu["index"] for gpu in host["gpus"]], [0, 1])
            self.assertFalse(host["gpus"][0]["available"])
            self.assertFalse(host["gpus"][1]["available"])
            self.assertIsNone(host["gpus"][1]["busy"])
            self.assertEqual(host["gpus"][1]["processes"], [])
            self.assertEqual(host["gpus"][1]["last_error"], "temporary SSH miss")
            del store
            gc.collect()

    def test_observation_gap_resets_same_state_duration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            gpu = {"index": 0, "uuid": "GPU-test", "name": "GPU", "memory_used": 0, "memory_total": 1024, "processes": []}
            with mock.patch("server.now_ts", side_effect=[100.0, 200.0]):
                store.update_gpu("host", gpu, False, 30)
                store.update_gpu("host", gpu, False, 30)
            with closing(store.connect()) as conn:
                row = conn.execute(
                    "select free_since_ts, observed_seconds from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()
                gap = conn.execute(
                    "select event, note from events where event='observation_gap'"
                ).fetchone()
            self.assertEqual(row["free_since_ts"], 200.0)
            self.assertEqual(row["observed_seconds"], 0.0)
            self.assertEqual(gap["event"], "observation_gap")
            self.assertIn("observation gap", gap["note"])
            self.assertEqual(
                [event["event"] for event in store.events()["events"]],
                ["free_start"],
            )

    def test_unknown_owner_on_same_process_cohort_retains_confirmed_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
            }

            def process(user):
                return {
                    "pid": 10,
                    "user": user,
                    "started": "Mon Aug 24 10:00:00 2026",
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": 1024,
                }

            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0, 130.0]):
                store.update_gpu("host", {**base, "processes": [process("alice")]}, True, 60)
                store.update_gpu("host", {**base, "processes": [process("?")]}, True, 60)
                store.update_gpu("host", {**base, "processes": [process("alice")]}, True, 60)
                store.update_gpu("host", {**base, "processes": [process("bob")]}, True, 60)

            with closing(store.connect()) as conn:
                rows = conn.execute(
                    "select event, users from events order by ts, id"
                ).fetchall()
                last_user = conn.execute(
                    "select last_user from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0]
            self.assertEqual([tuple(row) for row in rows], [("busy_start", "alice"), ("user_change", "bob")])
            self.assertEqual(last_user, "bob")

    def test_single_empty_drain_sample_preserves_last_owner_without_charging_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
            }
            alice = {
                "pid": 10,
                "user": "alice",
                "started": "start-a",
                "process_name": "python",
                "command_summary": "python train.py",
                "used_memory": 1024,
            }

            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0]):
                store.update_gpu("host", {**base, "processes": [alice]}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, False, 60)

            with closing(store.connect()) as conn:
                runtime = conn.execute(
                    "select last_user, last_used_ts, user_busy_json from gpu_runtime "
                    "where host='host' and gpu_index=0"
                ).fetchone()
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts"
                )]
            self.assertEqual(runtime["last_user"], "alice")
            self.assertEqual(runtime["last_used_ts"], 120.0)
            self.assertEqual(json.loads(runtime["user_busy_json"]), {"alice": 10.0})
            self.assertEqual(
                intervals,
                [(100.0, 110.0, "alice"), (110.0, 120.0, None)],
            )

    def test_second_empty_busy_sample_expires_drain_owner_grace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
            }
            alice = {
                "pid": 10,
                "user": "alice",
                "started": "start-a",
                "process_name": "python",
                "command_summary": "python train.py",
                "used_memory": 1024,
            }

            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0]):
                store.update_gpu("host", {**base, "processes": [alice]}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, True, 60)

            with closing(store.connect()) as conn:
                runtime = conn.execute(
                    "select last_user, user_busy_json from gpu_runtime "
                    "where host='host' and gpu_index=0"
                ).fetchone()
            self.assertIsNone(runtime["last_user"])
            self.assertEqual(json.loads(runtime["user_busy_json"]), {"alice": 10.0})

    def test_unproven_process_continuity_does_not_misattribute_previous_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
            }

            def process(pid, user, started):
                return {
                    "pid": pid,
                    "user": user,
                    "started": started,
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": 1024,
                }

            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0, 130.0]):
                store.update_gpu("host", {**base, "processes": [process(10, "alice", "start-a")]}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, True, 60)
                store.update_gpu("host", {**base, "processes": [process(20, "bob", "start-b")]}, True, 60)
                store.update_gpu("host", {**base, "processes": [process(20, "bob", "start-b")]}, True, 60)

            with closing(store.connect()) as conn:
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts"
                )]
                user_seconds = json.loads(conn.execute(
                    "select user_busy_json from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0])
                events = [tuple(row) for row in conn.execute(
                    "select event, users from events order by ts, id"
                )]

            self.assertEqual(
                intervals,
                [(100.0, 110.0, "alice"), (110.0, 120.0, None), (120.0, 130.0, "bob")],
            )
            self.assertEqual(user_seconds, {"alice": 10.0, "bob": 10.0})
            self.assertEqual(events, [("busy_start", "alice"), ("user_change", "bob")])

    def test_pid_reuse_with_a_different_start_time_breaks_owner_continuity(self):
        previous = [{"pid": 10, "started": "Mon Aug 24 10:00:00 2026"}]
        current = [{"pid": 10, "started": "Mon Aug 24 11:00:00 2026"}]
        self.assertFalse(server.same_process_cohort(previous, current))
        self.assertTrue(server.same_process_cohort(previous, [{"pid": 10, "started": previous[0]["started"]}]))
        self.assertFalse(server.same_process_cohort([{"pid": 10, "started": ""}], [{"pid": 10, "started": ""}]))

    def test_pid_reuse_in_same_second_breaks_on_proc_start_identity(self):
        previous = [{"pid": 10, "started": "same-second", "start_identity": "10001"}]
        current = [{"pid": 10, "started": "same-second", "start_identity": "10002"}]
        self.assertFalse(server.same_process_cohort(previous, current))
        self.assertTrue(server.same_process_cohort(
            previous,
            [{"pid": 10, "started": "same-second", "start_identity": "10001"}],
        ))
        self.assertFalse(server.same_process_cohort(
            previous,
            [{"pid": 10, "started": "same-second"}],
        ))

    def test_proc_start_identity_is_persisted_only_in_internal_process_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            store.update_gpu("host", {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
                "processes": [{
                    "pid": 10,
                    "user": "alice",
                    "started": "same-second",
                    "start_identity": "123456",
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": 1024,
                }],
            }, True, 60)
            with closing(store.connect()) as conn:
                row = conn.execute(
                    "select last_processes_json, last_snapshot_json from gpu_runtime "
                    "where host='host' and gpu_index=0"
                ).fetchone()
            internal = json.loads(row["last_processes_json"])
            public = json.loads(row["last_snapshot_json"])
            self.assertEqual(internal[0]["start_identity"], "123456")
            self.assertNotIn("start_identity", public["processes"][0])

    def test_verified_owner_carry_recovers_one_missing_user_in_multi_user_cohort(self):
        previous = [
            {"pid": 10, "started": "start-a", "user": "alice"},
            {"pid": 20, "started": "start-b", "user": "bob"},
        ]
        current = [
            {"pid": 10, "started": "start-a", "user": "?"},
            {"pid": 20, "started": "start-b", "user": "bob"},
        ]
        self.assertTrue(server.carry_verified_process_owners(previous, current))
        self.assertEqual(server.confirmed_process_users(current), ["alice", "bob"])

    def test_multiple_processes_and_users_are_preserved_and_split_by_distinct_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 4096,
                "memory_total": 8192,
            }

            def process(pid, user, memory):
                return {
                    "pid": pid,
                    "user": user,
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": memory,
                }

            shared_processes = [
                process(11, "alice", 2048),
                process(12, "alice", 1024),
                process(21, "bob", 768),
                process(22, "bob", 256),
            ]
            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0]):
                store.update_gpu("host", {**base, "processes": shared_processes}, True, 60)
                with closing(store.connect()) as conn:
                    stored = json.loads(conn.execute(
                        "select last_processes_json from gpu_runtime where host='host' and gpu_index=0"
                    ).fetchone()[0])
                    owners = conn.execute(
                        "select last_user from gpu_runtime where host='host' and gpu_index=0"
                    ).fetchone()[0]
                store.update_gpu("host", {**base, "processes": shared_processes}, True, 60)
                store.update_gpu("host", {**base, "processes": [process(21, "bob", 768)]}, True, 60)

            with closing(store.connect()) as conn:
                user_seconds = json.loads(conn.execute(
                    "select user_busy_json from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0])
                events = [tuple(row) for row in conn.execute(
                    "select event, users from events order by ts, id"
                )]

            self.assertEqual(len(stored), 4)
            self.assertEqual({item["pid"] for item in stored}, {11, 12, 21, 22})
            self.assertEqual(owners, "alice, bob")
            self.assertEqual(user_seconds, {"alice": 10.0, "bob": 10.0})
            self.assertEqual(events, [("busy_start", "alice, bob"), ("user_change", "bob")])
            del store
            gc.collect()

    def test_new_unknown_busy_session_does_not_reuse_previous_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            base = {
                "index": 0,
                "uuid": "GPU-test",
                "name": "GPU",
                "memory_used": 1024,
                "memory_total": 2048,
            }

            def process(user):
                return {
                    "pid": 10,
                    "user": user,
                    "process_name": "python",
                    "command_summary": "python train.py",
                    "used_memory": 1024,
                }

            with mock.patch("server.now_ts", side_effect=[100.0, 110.0, 120.0, 130.0]):
                store.update_gpu("host", {**base, "processes": [process("alice")]}, True, 60)
                store.update_gpu("host", {**base, "processes": []}, False, 60)
                store.update_gpu("host", {**base, "processes": [process("?")]}, True, 60)
                store.update_gpu("host", {**base, "processes": [process("?")]}, True, 60)

            with closing(store.connect()) as conn:
                events = [tuple(row) for row in conn.execute(
                    "select event, users from events order by ts, id"
                )]
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts"
                )]
                last_user = conn.execute(
                    "select last_user from gpu_runtime where host='host' and gpu_index=0"
                ).fetchone()[0]
            self.assertEqual(
                events,
                [("busy_start", "alice"), ("free_start", None), ("busy_start", None)],
            )
            self.assertEqual(intervals, [(100.0, 110.0, "alice"), (120.0, 130.0, None)])
            self.assertIsNone(last_user)

    def test_event_backfill_is_gap_safe_uncovered_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(host, gpu_index, busy, first_seen_ts, last_seen_ts)
                    values ('host', 0, 0, 100, 300)
                    """
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users, note) values (?, 'host', 0, ?, 'alice', ?)",
                    [
                        (100.0, "busy_start", None),
                        (200.0, "observation_gap", "after observation gap 50s"),
                        (300.0, "free_start", None),
                    ],
                )
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('host', 0, ?, ?, 'alice', ?)
                    """,
                    [(100.0, 150.0, 50.0), (200.0, 300.0, 100.0)],
                )
                gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
                config = {
                    "recent_usage_window_seconds": 1000,
                    "recent_usage_event_backfill_max_seconds": 1000,
                }
                store._backfill_recent_usage_intervals(conn, gpu_rows, 400.0, config)
                first = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts, end_ts"
                )]
                store._backfill_recent_usage_intervals(conn, gpu_rows, 400.0, config)
                second = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts, end_ts"
                )]
            self.assertEqual(first, [(100.0, 150.0, "alice"), (200.0, 300.0, "alice")])
            self.assertEqual(second, first)

    def test_gap_noted_transition_does_not_bridge_unknown_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_runtime(host, gpu_index, busy, first_seen_ts, last_seen_ts)
                    values (?, 0, 0, 100, 300)
                    """,
                    [("stopped",), ("started",)],
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users, note) values (?, ?, 0, ?, ?, ?)",
                    [
                        (100.0, "stopped", "busy_start", "alice", None),
                        (200.0, "stopped", "free_start", None, "after observation gap 50s"),
                        (100.0, "started", "free_start", None, None),
                        (200.0, "started", "busy_start", "bob", "after observation gap 50s"),
                        (300.0, "started", "free_start", None, None),
                    ],
                )
                conn.execute(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('stopped', 0, 100, 150, 'alice', 50)
                    """
                )
                gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
                store._backfill_recent_usage_intervals(
                    conn,
                    gpu_rows,
                    400.0,
                    {
                        "recent_usage_window_seconds": 1000,
                        "recent_usage_event_backfill_max_seconds": 1000,
                    },
                )
                intervals = [tuple(row) for row in conn.execute(
                    "select host, start_ts, end_ts, users from gpu_usage_interval order by host, start_ts"
                )]
            self.assertEqual(
                intervals,
                [("started", 200.0, 300.0, "bob"), ("stopped", 100.0, 150.0, "alice")],
            )

    def test_event_backfill_fills_only_uncovered_spans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(host, gpu_index, busy, first_seen_ts, last_seen_ts)
                    values ('host', 0, 0, 100, 300)
                    """
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, ?, ?)",
                    [(100.0, "busy_start", "alice"), (300.0, "free_start", None)],
                )
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('host', 0, ?, ?, ?, ?)
                    """,
                    [(100.0, 180.0, "alice", 80.0), (220.0, 300.0, "alice", 80.0)],
                )
                gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
                config = {
                    "recent_usage_window_seconds": 1000,
                    "recent_usage_event_backfill_max_seconds": 1000,
                }
                store._backfill_recent_usage_intervals(conn, gpu_rows, 400.0, config)
                first = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts, end_ts"
                )]
                store._backfill_recent_usage_intervals(conn, gpu_rows, 400.0, config)
                second = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts, end_ts"
                )]
            self.assertEqual(
                first,
                [
                    (100.0, 180.0, "alice"),
                    (180.0, 220.0, "alice"),
                    (220.0, 300.0, "alice"),
                ],
            )
            self.assertEqual(second, first)

    def test_event_backfill_preserves_legacy_user_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(host, gpu_index, busy, first_seen_ts, last_seen_ts)
                    values ('host', 0, 0, 100, 300)
                    """
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, ?, ?)",
                    [(100.0, "busy_start", "alice"), (200.0, "user_change", "bob"), (300.0, "free_start", None)],
                )
                gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
                store._backfill_recent_usage_intervals(
                    conn,
                    gpu_rows,
                    400.0,
                    {
                        "recent_usage_window_seconds": 1000,
                        "recent_usage_event_backfill_max_seconds": 1000,
                    },
                )
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts"
                )]
            self.assertEqual(
                intervals,
                [(100.0, 200.0, "alice"), (200.0, 300.0, "bob")],
            )

    def test_event_backfill_uses_busy_capacity_as_observation_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(host, gpu_index, busy, first_seen_ts, last_seen_ts)
                    values ('host', 0, 0, 100, 300)
                    """
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, ?, ?)",
                    [(100.0, "busy_start", "alice"), (300.0, "free_start", None)],
                )
                conn.executemany(
                    """
                    insert into gpu_capacity_interval(host, gpu_index, start_ts, end_ts, busy, seconds)
                    values ('host', 0, ?, ?, ?, ?)
                    """,
                    [
                        (100.0, 150.0, 1, 50.0),
                        (150.0, 200.0, 0, 50.0),
                        (250.0, 300.0, 1, 50.0),
                    ],
                )
                gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
                store._backfill_recent_usage_intervals(
                    conn,
                    gpu_rows,
                    400.0,
                    {
                        "recent_usage_window_seconds": 1000,
                        "recent_usage_event_backfill_max_seconds": 1000,
                    },
                )
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users from gpu_usage_interval order by start_ts"
                )]
            self.assertEqual(
                intervals,
                [(100.0, 150.0, "alice"), (250.0, 300.0, "alice")],
            )

    def test_recent_event_backfill_runs_only_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            config = {
                "recent_usage_window_seconds": 1000,
                "recent_usage_event_backfill_max_seconds": 1000,
                "usage_gap_seconds": 60,
                "poll_interval_seconds": 10,
            }
            with mock.patch.object(
                store,
                "_backfill_recent_usage_intervals",
                wraps=store._backfill_recent_usage_intervals,
            ) as backfill:
                store.prepare(config)
                store.prepare(config)
            with closing(store.connect()) as conn:
                marker = conn.execute(
                    "select value from maintenance_meta where key=?",
                    (server.RECENT_USAGE_BACKFILL_KEY,),
                ).fetchone()[0]
            self.assertEqual(backfill.call_count, 1)
            self.assertEqual(marker, "complete")

    def test_v1_0_1_migration_repairs_overlap_masks_capacity_and_scrubs_unknown_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('host', 0, ?, ?, ?, ?)
                    """,
                    [(0.0, 100.0, "?, alice", 100.0), (20.0, 80.0, "alice", 60.0)],
                )
                conn.executemany(
                    """
                    insert into gpu_capacity_interval(host, gpu_index, start_ts, end_ts, busy, seconds)
                    values ('host', 0, ?, ?, ?, ?)
                    """,
                    [(10.0, 50.0, 1, 40.0), (50.0, 60.0, 0, 10.0), (60.0, 90.0, 1, 30.0)],
                )
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, ?, ?)",
                    [
                        (1.0, "busy_start", "alice"),
                        (2.0, "user_change", "?, alice"),
                        (3.0, "user_change", "?"),
                        (4.0, "user_change", "bob"),
                    ],
                )
                conn.execute(
                    "delete from maintenance_meta where key=?",
                    (server.EVENT_USAGE_MIGRATION_KEY,),
                )
            del store
            reopened = server.Store(path)
            with closing(reopened.connect()) as conn:
                intervals = [tuple(row) for row in conn.execute(
                    "select start_ts, end_ts, users, seconds from gpu_usage_interval order by start_ts"
                )]
                events = [tuple(row) for row in conn.execute(
                    "select event, users from events order by ts, id"
                )]
                marker = conn.execute(
                    "select value from maintenance_meta where key=?",
                    (server.EVENT_USAGE_MIGRATION_KEY,),
                ).fetchone()[0]
            self.assertEqual(
                intervals,
                [(0.0, 50.0, "alice", 50.0), (60.0, 100.0, "alice", 40.0)],
            )
            self.assertEqual(events, [("busy_start", "alice"), ("user_change", "bob")])
            self.assertEqual(marker, "complete")

    def test_v1_0_1_migration_aborts_on_conflicting_user_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('host', 0, ?, ?, ?, ?)
                    """,
                    [(0.0, 100.0, "alice", 100.0), (50.0, 60.0, "bob", 10.0)],
                )
                conn.execute(
                    "delete from maintenance_meta where key=?",
                    (server.EVENT_USAGE_MIGRATION_KEY,),
                )
            del store
            with self.assertRaisesRegex(RuntimeError, "conflicting usage intervals"):
                server.Store(path)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("select count(*) from gpu_usage_interval").fetchone()[0], 2)
                self.assertIsNone(conn.execute(
                    "select value from maintenance_meta where key=?",
                    (server.EVENT_USAGE_MIGRATION_KEY,),
                ).fetchone())

    def test_events_reject_invalid_date_and_escape_like_wildcards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, 'busy_start', ?)",
                    [(100.0, "alice%literal"), (101.0, "aliceXliteral")],
                )
            with self.assertRaises(ValueError):
                store.events(date="2026-99-99")
            result = store.events(user="%")
            self.assertEqual([event["users"] for event in result["events"]], ["alice%literal"])

    def test_public_events_include_only_free_and_busy_with_stable_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    "insert into events(ts, host, gpu_index, event, users) values (?, 'host', 0, ?, ?)",
                    [
                        (100.0, "busy_start", "alice"),
                        (101.0, "user_change", "bob"),
                        (102.0, "observation_gap", "bob"),
                        (103.0, "hardware_change", None),
                        (104.0, "free_start", None),
                    ],
                )
            first = store.events(limit=1)
            second = store.events(limit=1, offset=1)
            self.assertEqual([event["event"] for event in first["events"]], ["free_start"])
            self.assertTrue(first["has_more"])
            self.assertEqual(first["next_offset"], 1)
            self.assertEqual([event["event"] for event in second["events"]], ["busy_start"])
            self.assertFalse(second["has_more"])
            self.assertEqual(second["prev_offset"], 0)
            with mock.patch.object(server, "MAX_EVENT_OFFSET", 1):
                boundary = store.events(limit=1, offset=1)
                self.assertFalse(boundary["has_more"])
                self.assertIsNone(boundary["next_offset"])
            with self.assertRaises(ValueError):
                store.events(offset=server.MAX_EVENT_OFFSET + 1)

    def test_snapshot_does_not_prune_raw_intervals_on_the_read_path(self):
        class TraceStore(server.Store):
            def __init__(self, path):
                self.statements = []
                super().__init__(path)

            def connect(self):
                conn = super().connect()
                conn.set_trace_callback(self.statements.append)
                return conn

        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir) / "gpu_watch.sqlite3")
            stale_end = time.time() - 10 * 86400
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, seconds) "
                    "values ('host', 0, ?, ?, 10)",
                    (stale_end - 10, stale_end),
                )
                conn.execute(
                    "insert into gpu_capacity_interval(host, gpu_index, start_ts, end_ts, busy, seconds) "
                    "values ('host', 0, ?, ?, 0, 10)",
                    (stale_end - 10, stale_end),
                )
            store.statements.clear()

            store.snapshot({
                "build_version": "test",
                "hosts": [],
                "labs": [],
                "poll_interval_seconds": 10,
                "disk_poll_interval_seconds": 1800,
                "activity_policy": {},
                "recent_usage_window_seconds": 7 * 86400,
            })

            writes = [
                statement for statement in store.statements
                if statement.lstrip().upper().startswith(("DELETE ", "INSERT ", "UPDATE ", "REPLACE "))
            ]
            self.assertEqual(writes, [])
            with closing(store.connect()) as conn:
                self.assertEqual(conn.execute("select count(*) from gpu_usage_interval").fetchone()[0], 1)
                self.assertEqual(conn.execute("select count(*) from gpu_capacity_interval").fetchone()[0], 1)

    def test_lab_insights_use_observed_vram_hours(self):
        current_hour_start = 1_700_002_800.0
        ts = current_hour_start + 1800
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                for host, gpu_index in (("nll-a", 0), ("nll-a", 1), ("nlp-a", 0)):
                    conn.execute(
                        """
                        insert into gpu_runtime(host, gpu_index, gpu_name, busy, first_seen_ts, last_seen_ts, last_snapshot_json)
                        values (?, ?, 'GPU', 0, ?, ?, '{"memory_total":1024}')
                        """,
                        (host, gpu_index, ts - 7200, ts),
                    )
                store._record_capacity_interval(
                    conn, "nll-a", 0, current_hour_start - 3600, current_hour_start, False, 0, 1024
                )
                store._record_capacity_interval(
                    conn, "nll-a", 0, current_hour_start, ts, True, 512, 1024
                )
                store._record_capacity_interval(
                    conn, "nll-a", 1, current_hour_start - 3600, ts, False, 0, 1024
                )
                store._record_capacity_interval(
                    conn, "nlp-a", 0, current_hour_start - 3600, ts, False, 0, 1024
                )
            config = {
                "hosts": [
                    {"name": "nll-a", "lab": "nll"},
                    {"name": "nlp-a", "lab": "nlp"},
                ],
                "labs": [
                    {"id": "nll", "label": "NLL LAB"},
                    {"id": "nlp", "label": "NLP LAB"},
                ],
                "recent_usage_window_seconds": 7 * 86400,
                "insights_cache_seconds": 0,
            }
            insights = store.insights(config, ts)
            nll_hour = insights["labs"][0]["hourly"][-1]
            nlp_hour = insights["labs"][1]["hourly"][-1]
            self.assertEqual(nll_hour["index"], 0.5)
            self.assertEqual(
                [nll_hour[key] for key in ("open", "high", "low", "close")],
                [0.5, 0.5, 0.5, 0.5],
            )
            self.assertEqual(insights["labs"][0]["total_gpu_gb"], 2.0)
            self.assertEqual(insights["labs"][0]["max_index"], 2.0)
            self.assertEqual(insights["labs"][0]["daily_index"], 0.17)
            self.assertIsNone(insights["labs"][0]["previous_daily_index"])
            self.assertIsNone(insights["labs"][0]["previous_rolling_index"])
            self.assertEqual(insights["labs"][0]["observed_hours"], 1.5)
            self.assertFalse(insights["labs"][0]["comparison_ready"])
            self.assertEqual(len(insights["labs"][0]["daily_trend"]), 30)
            self.assertIsNone(insights["labs"][0]["daily_trend"][-2]["index"])
            self.assertEqual(insights["labs"][0]["daily_trend"][-1]["index"], 0.17)
            self.assertLessEqual(nll_hour["high"], insights["labs"][0]["max_index"])
            self.assertEqual(nlp_hour["index"], 0.0)
            self.assertEqual(
                [nlp_hour[key] for key in ("open", "high", "low", "close")],
                [0.0, 0.0, 0.0, 0.0],
            )
            self.assertEqual(insights["labs"][1]["max_index"], 1.0)
            self.assertEqual(insights["labs"][1]["daily_index"], 0.0)
            self.assertEqual(insights["range_hours"], 24)
            self.assertEqual(insights["range_days"], 30)
            self.assertEqual(insights["change_period_seconds"], 86400)

            later_insights = store.insights(config, ts + 120)
            self.assertEqual(
                insights["labs"][0]["hourly"][-2],
                later_insights["labs"][0]["hourly"][-2],
                "A completed wall-clock candle must not move when the dashboard refreshes.",
            )
            with closing(store.connect()) as conn, conn:
                conn.execute("delete from gpu_capacity_interval")
            persisted = store.insights(config, ts + 240)
            self.assertEqual(persisted["labs"][0]["daily_index"], 0.17)
            del store
            gc.collect()

    def test_recent_capacity_uses_observed_gpu_time_and_wall_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                store._record_capacity_interval(conn, "test-host", 0, 100.0, 500.0, False)
                store._record_capacity_interval(conn, "test-host", 0, 500.0, 800.0, True)
                store._record_capacity_interval(conn, "test-host", 1, 200.0, 600.0, True)
                capacity = store._recent_capacity_by_host(
                    conn,
                    1000.0,
                    {"recent_usage_window_seconds": 1000},
                )

            self.assertEqual(capacity["test-host"]["observed_gpu_seconds"], 1100.0)
            self.assertEqual(capacity["test-host"]["busy_gpu_seconds"], 700.0)
            self.assertEqual(capacity["test-host"]["observed_wall_seconds"], 700.0)
            del store
            gc.collect()

    def test_snapshot_tracks_age_of_the_complete_current_gpu_set(self):
        ts = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into host_runtime(host, label, online, last_seen_ts) "
                    "values ('test-host', 'Test host', 1, ?)",
                    (ts,),
                )
                conn.executemany(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_name, active, busy,
                        first_seen_ts, last_seen_ts, last_snapshot_json
                    )
                    values ('test-host', ?, 'GPU', 1, 0, ?, ?, ?)
                    """,
                    [
                        (0, ts - 10 * 86400, ts, '{"index":0,"memory_total":1024}'),
                        (1, ts - 8 * 86400, ts, '{"index":1,"memory_total":1024}'),
                    ],
                )
            config = {
                "build_version": "test",
                "hosts": [{"name": "test-host", "lab": "nll"}],
                "labs": [{"id": "nll", "label": "NLL LAB"}],
                "poll_interval_seconds": 10,
                "disk_poll_interval_seconds": 1800,
                "activity_policy": {"cold_min_session_seconds": 60},
                "recent_usage_window_seconds": 7 * 86400,
            }
            with mock.patch.object(server, "now_ts", return_value=ts):
                recent = store.snapshot(config)["hosts"][0]["recent_usage"]

            self.assertEqual(recent["tracking_started_at"], server.iso(ts - 8 * 86400))
            self.assertEqual(recent["tracking_span_seconds"], 8 * 86400)
            self.assertEqual(recent["tracking_for"], "8d 0h")
            del store
            gc.collect()

    def test_lab_insights_normalize_partial_current_quarter_by_observed_time(self):
        current_hour_start = 1_700_002_800.0
        ts = current_hour_start + 310
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_name, busy, first_seen_ts, last_seen_ts, last_snapshot_json
                    )
                    values ('nlp-a', 0, 'GPU', 1, ?, ?, '{"memory_total":1024,"memory_used":512}')
                    """,
                    (ts - 10, ts),
                )
                store._record_capacity_interval(
                    conn, "nlp-a", 0, ts - 10, ts, True, 512, 1024
                )
            config = {
                "hosts": [{"name": "nlp-a", "lab": "nlp"}],
                "labs": [{"id": "nlp", "label": "NLP LAB"}],
                "insights_cache_seconds": 0,
            }
            latest = store.insights(config, ts)["labs"][0]["hourly"][-1]
            self.assertEqual(latest["index"], 0.5)
            self.assertEqual(
                [latest[key] for key in ("open", "high", "low", "close")],
                [0.5, 0.5, 0.5, 0.5],
            )
            del store
            gc.collect()

    def test_lab_insights_compare_kst_calendar_days(self):
        today_start = datetime(2026, 7, 13, tzinfo=server.KST).timestamp()
        ts = today_start + 3600
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_name, busy, first_seen_ts, last_seen_ts, last_snapshot_json
                    )
                    values ('nll-a', 0, 'GPU', 1, ?, ?, '{"memory_total":1024,"memory_used":512}')
                    """,
                    (today_start - 86400, ts),
                )
                store._record_capacity_interval(
                    conn, "nll-a", 0, today_start - 86400, today_start - 82800, True, 256, 1024
                )
                store._record_capacity_interval(
                    conn, "nll-a", 0, today_start, ts, True, 512, 1024
                )
            config = {
                "hosts": [{"name": "nll-a", "lab": "nll"}],
                "labs": [{"id": "nll", "label": "NLL LAB"}],
                "insights_cache_seconds": 0,
            }
            lab = store.insights(config, ts)["labs"][0]
            self.assertEqual(lab["daily_index"], 0.5)
            self.assertEqual(lab["previous_daily_index"], 0.25)
            self.assertTrue(lab["comparison_ready"])
            self.assertEqual(lab["daily_trend"][-2]["index"], 0.25)
            self.assertEqual(lab["daily_trend"][-1]["index"], 0.5)
            self.assertEqual(lab["daily_trend"][-2]["day"], "2026-07-12")
            self.assertEqual(lab["daily_trend"][-2]["observed_hours"], 1.0)
            self.assertTrue(lab["daily_trend"][-2]["complete"])
            self.assertFalse(lab["daily_trend"][-1]["complete"])
            del store
            gc.collect()

    def test_lab_daily_observation_uses_wall_clock_union_across_gpus(self):
        today_start = datetime(2026, 7, 13, tzinfo=server.KST).timestamp()
        previous_start = today_start - 86400
        ts = today_start + 60
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_name, active, busy,
                        first_seen_ts, last_seen_ts, last_snapshot_json
                    ) values ('nll-a', ?, 'GPU', 1, 0, ?, ?, '{"memory_total":1024,"memory_used":0}')
                    """,
                    [
                        (0, previous_start, ts),
                        (1, previous_start, ts),
                    ],
                )
                store._record_capacity_interval(
                    conn, "nll-a", 0, previous_start, previous_start + 3600, True, 256, 1024
                )
                store._record_capacity_interval(
                    conn, "nll-a", 1, previous_start + 3600, previous_start + 7200, True, 512, 1024
                )
            config = {
                "hosts": [{"name": "nll-a", "lab": "nll"}],
                "labs": [{"id": "nll", "label": "NLL LAB"}],
                "insights_cache_seconds": 0,
            }
            previous = store.insights(config, ts)["labs"][0]["daily_trend"][-2]
            self.assertEqual(previous["observed_hours"], 2.0)
            self.assertEqual(previous["index"], 0.75)
            self.assertTrue(previous["complete"])
            del store
            gc.collect()

    def test_recent_server_active_time_merges_overlapping_gpu_intervals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('test-host', ?, ?, ?, 'researcher', ?)
                    """,
                    [
                        (0, 100.0, 500.0, 400.0),
                        (1, 200.0, 600.0, 400.0),
                        (2, 700.0, 800.0, 100.0),
                    ],
                )
                recent = store._recent_usage_by_host(
                    conn,
                    [],
                    {},
                    1000.0,
                    {"poll_interval_seconds": 10, "recent_usage_window_seconds": 1000},
                )

            self.assertEqual(recent["test-host"]["busy_seconds"], 900.0)
            self.assertEqual(recent["test-host"]["active_seconds"], 600.0)
            del store
            gc.collect()

    def test_recent_long_idle_ignores_only_merged_sessions_shorter_than_sixty_seconds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values ('test-host', ?, ?, ?, 'researcher', ?)
                    """,
                    [
                        (0, 100.0, 130.0, 30.0),
                        (1, 110.0, 150.0, 40.0),
                        (0, 300.0, 330.0, 30.0),
                        (1, 320.0, 360.0, 40.0),
                    ],
                )
                recent = store._recent_usage_by_host(
                    conn,
                    [],
                    {},
                    1000.0,
                    {
                        "poll_interval_seconds": 10,
                        "recent_usage_window_seconds": 1000,
                        "activity_policy": {"cold_min_session_seconds": 60},
                    },
                )["test-host"]

            self.assertEqual(recent["busy_seconds"], 140.0)
            self.assertEqual(recent["active_seconds"], 110.0)
            self.assertEqual(recent["meaningful_active_seconds"], 60.0)
            self.assertEqual(recent["last_meaningful_used_ts"], 360.0)
            del store
            gc.collect()

    def test_recent_user_share_ignores_only_short_unassigned_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                    values (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("single-host", 0, 50.0, 61.7, None, 11.7),
                        ("single-host", 0, 200.0, 300.0, "carol", 100.0),
                        ("short-host", 0, 100.0, 110.0, None, 10.0),
                        ("short-host", 1, 100.0, 110.0, None, 10.0),
                        ("short-host", 2, 100.0, 110.0, None, 10.0),
                        ("short-host", 0, 200.0, 300.0, "carol", 100.0),
                        ("long-host", 0, 400.0, 460.0, None, 60.0),
                        ("mixed-host", 0, 500.0, 510.0, None, 10.0),
                        ("mixed-host", 0, 510.0, 620.0, "alice", 110.0),
                    ],
                )
                recent = store._recent_usage_by_host(
                    conn,
                    [],
                    {},
                    1000.0,
                    {
                        "poll_interval_seconds": 10,
                        "recent_usage_window_seconds": 1000,
                        "activity_policy": {"cold_min_session_seconds": 60},
                    },
                )

            single = recent["single-host"]
            self.assertEqual(single["reportable_unassigned_seconds"], 0.0)
            self.assertEqual(
                server.recent_user_share_rows(
                    single["user_seconds"],
                    single["reportable_unassigned_seconds"],
                ),
                [{"user": "carol", "seconds": 100.0, "share_percent": 100}],
            )

            short = recent["short-host"]
            self.assertEqual(short["busy_seconds"], 130.0)
            self.assertEqual(short["reportable_unassigned_seconds"], 0.0)
            self.assertEqual(
                server.recent_user_share_rows(
                    short["user_seconds"],
                    short["reportable_unassigned_seconds"],
                ),
                [{"user": "carol", "seconds": 100.0, "share_percent": 100}],
            )

            long = recent["long-host"]
            self.assertEqual(long["busy_seconds"], 60.0)
            self.assertEqual(long["reportable_unassigned_seconds"], 60.0)
            self.assertEqual(
                server.recent_user_share_rows(
                    long["user_seconds"],
                    long["reportable_unassigned_seconds"],
                ),
                [{"user": "사용자 미상", "seconds": 60.0, "share_percent": 100}],
            )
            mixed = recent["mixed-host"]
            self.assertEqual(mixed["meaningful_active_seconds"], 120.0)
            self.assertEqual(mixed["reportable_unassigned_seconds"], 10.0)
            self.assertEqual(
                server.recent_user_share_rows(
                    mixed["user_seconds"],
                    mixed["reportable_unassigned_seconds"],
                ),
                [
                    {"user": "alice", "seconds": 110.0, "share_percent": 92},
                    {"user": "사용자 미상", "seconds": 10.0, "share_percent": 8},
                ],
            )
            high_usage_rows = server.recent_user_share_rows({"carol": 100_000.0}, 60.0)
            self.assertEqual(
                [row["user"] for row in high_usage_rows],
                ["carol", "사용자 미상"],
            )
            del store
            gc.collect()

    def test_deadlines_migrate_sort_and_support_six_stable_tone_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            config = {
                "default_deadline": {
                    "title": "AAAI 2027 · Full paper",
                    "deadline_at": "2026-07-29T20:59:59+09:00",
                    "url": "https://aaai.org/conference/aaai/aaai-27/",
                }
            }
            initial = store.deadlines(config)
            self.assertEqual(len(initial), 1)
            self.assertEqual(initial[0]["id"], "primary")
            self.assertEqual(initial[0]["tone"], "silver")
            self.assertEqual(initial[0]["title"], "AAAI 2027 · Full paper")

            with closing(store.connect()) as conn, conn:
                conn.execute(
                    """
                    insert into dashboard_settings(key, value, updated_ts)
                    values('conference_deadline', ?, ?)
                    """,
                    (
                        json.dumps(
                            {
                                "title": "Stored title",
                                "deadline_at": "not-a-date",
                                "url": "javascript:alert(1)",
                            }
                        ),
                        time.time(),
                    ),
                )
            sanitized = store.deadlines(config)
            self.assertEqual(sanitized[0]["title"], "Stored title")
            self.assertEqual(sanitized[0]["deadline_at"], "2026-07-29T20:59:59+09:00")
            self.assertEqual(sanitized[0]["url"], "https://aaai.org/conference/aaai/aaai-27/")

            updated = store.update_deadline(
                "ACL 2027 · Main",
                "2027-01-15T12:00:00+09:00",
                "https://www.aclweb.org/",
                deadline_id="primary",
                config=config,
            )
            self.assertEqual(updated["title"], "ACL 2027 · Main")
            self.assertEqual(updated["tone"], "silver")

            second = store.update_deadline(
                "ICML 2027",
                "2027-02-15T12:00:00+09:00",
                "https://icml.cc/",
                config=config,
            )
            third = store.update_deadline(
                "NeurIPS 2027",
                "2027-03-15T12:00:00+09:00",
                "https://neurips.cc/",
                config=config,
            )
            fourth = store.update_deadline(
                "CVPR 2027",
                "2027-04-15T12:00:00+09:00",
                "https://cvpr.thecvf.com/",
                config=config,
            )
            fifth = store.update_deadline(
                "KDD 2027",
                "2027-05-15T12:00:00+09:00",
                "https://kdd.org/",
                config=config,
            )
            sixth = store.update_deadline(
                "EMNLP 2027",
                "2027-06-15T12:00:00+09:00",
                "https://aclanthology.org/",
                config=config,
            )
            self.assertEqual(
                [item["tone"] for item in store.deadlines(config)],
                ["silver", "gold", "emerald", "diamond", "master", "grandmaster"],
            )
            self.assertEqual(second["tone"], "gold")
            self.assertEqual(third["tone"], "emerald")
            self.assertEqual(fourth["tone"], "diamond")
            self.assertEqual(fifth["tone"], "master")
            self.assertEqual(sixth["tone"], "grandmaster")

            store.update_deadline(
                "EMNLP 2027",
                "2026-12-15T12:00:00+09:00",
                "https://aclanthology.org/",
                deadline_id=sixth["id"],
                config=config,
            )
            self.assertEqual(store.deadlines(config)[0]["id"], sixth["id"])

            with self.assertRaisesRegex(ValueError, "최대 6개"):
                store.update_deadline(
                    "ACL 2028",
                    "2028-01-15T12:00:00+09:00",
                    "https://www.aclweb.org/",
                    config=config,
                )

            remaining = store.delete_deadline(second["id"], config)
            self.assertEqual(
                [item["tone"] for item in remaining],
                ["grandmaster", "silver", "emerald", "diamond", "master"],
            )
            replacement = store.update_deadline(
                "ACL 2028",
                "2028-01-15T12:00:00+09:00",
                "https://www.aclweb.org/",
                config=config,
            )
            self.assertEqual(replacement["tone"], "gold")
            persisted = store.deadlines(config)
            self.assertEqual(len({item["id"] for item in persisted}), 6)

            with closing(store.connect()) as conn, conn:
                stored = json.loads(
                    conn.execute(
                        "select value from dashboard_settings where key='conference_deadlines'"
                    ).fetchone()[0]
                )
            self.assertEqual(stored, persisted)

            with self.assertRaisesRegex(ValueError, "http 또는 https"):
                store.update_deadline(
                    "Bad link",
                    "2027-01-15T12:00:00+09:00",
                    "https://user:password@example.com/",
                    config=config,
                )

            for item in list(store.deadlines(config)):
                store.delete_deadline(item["id"], config)
            self.assertEqual(store.deadlines(config), [])
            self.assertEqual(store.deadline(config), {})
            del store
            gc.collect()

    def test_deadline_palette_migration_assigns_sorted_once_then_stays_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            legacy_deadlines = [
                {
                    "id": deadline_id,
                    "tone": tone,
                    "title": title,
                    "deadline_at": deadline_at,
                    "url": f"https://example.com/{deadline_id}",
                    "updated_at": "2026-08-13T12:00:00+09:00",
                }
                for deadline_id, tone, title, deadline_at in (
                    ("third", "violet", "Third", "2027-03-01T12:00:00+09:00"),
                    ("first", "gold", "First", "2027-01-01T12:00:00+09:00"),
                    ("sixth", "platinum", "Sixth", "2027-06-01T12:00:00+09:00"),
                    ("second", "teal", "Second", "2027-02-01T12:00:00+09:00"),
                    ("fifth", "pink", "Fifth", "2027-05-01T12:00:00+09:00"),
                    ("fourth", "diamond", "Fourth", "2027-04-01T12:00:00+09:00"),
                )
            ]
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values(?, ?, ?)",
                    (
                        server.CONFERENCE_DEADLINES_KEY,
                        json.dumps(legacy_deadlines),
                        time.time(),
                    ),
                )
                conn.execute(
                    "delete from maintenance_meta where key=?",
                    (server.CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
                )
            del store
            gc.collect()

            migrated_store = server.Store(path)
            migrated = migrated_store.deadlines({})
            self.assertEqual(
                [item["id"] for item in migrated],
                ["first", "second", "third", "fourth", "fifth", "sixth"],
            )
            self.assertEqual(
                [item["tone"] for item in migrated],
                ["silver", "gold", "emerald", "diamond", "master", "grandmaster"],
            )
            original_tones = {item["id"]: item["tone"] for item in migrated}

            migrated_store.update_deadline(
                "First moved",
                "2028-01-01T12:00:00+09:00",
                "https://example.com/first",
                deadline_id="first",
                config={},
            )
            reordered = migrated_store.deadlines({})
            self.assertEqual(reordered[-1]["id"], "first")
            self.assertEqual(
                {item["id"]: item["tone"] for item in reordered},
                original_tones,
            )

            migrated_store.delete_deadline("third", {})
            replacement = migrated_store.update_deadline(
                "Replacement",
                "2028-02-01T12:00:00+09:00",
                "https://example.com/replacement",
                config={},
            )
            self.assertEqual(replacement["tone"], "emerald")
            with closing(migrated_store.connect()) as conn:
                marker = conn.execute(
                    "select value from maintenance_meta where key=?",
                    (server.CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
                ).fetchone()
            self.assertEqual(marker["value"], "complete")
            del migrated_store
            gc.collect()

    def test_deadline_palette_migration_recovers_valid_legacy_from_corrupt_current_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values(?, ?, ?) ",
                    (server.CONFERENCE_DEADLINES_KEY, "{not-json", time.time()),
                )
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values(?, ?, ?)",
                    (
                        server.LEGACY_CONFERENCE_DEADLINE_KEY,
                        json.dumps(
                            {
                                "title": "Recovered",
                                "deadline_at": "2027-01-01T12:00:00+09:00",
                                "url": "https://example.com/recovered",
                            }
                        ),
                        time.time(),
                    ),
                )
                conn.execute(
                    "delete from maintenance_meta where key=?",
                    (server.CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
                )
            del store
            gc.collect()

            migrated_store = server.Store(path)
            recovered = migrated_store.deadlines({})
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["id"], "primary")
            self.assertEqual(recovered[0]["tone"], "silver")
            self.assertEqual(recovered[0]["title"], "Recovered")
            self.assertEqual(recovered[0]["deadline_at"], "2027-01-01T12:00:00+09:00")
            self.assertEqual(recovered[0]["url"], "https://example.com/recovered")
            self.assertTrue(recovered[0]["updated_at"])
            with closing(migrated_store.connect()) as conn:
                legacy_row = conn.execute(
                    "select 1 from dashboard_settings where key=?",
                    (server.LEGACY_CONFERENCE_DEADLINE_KEY,),
                ).fetchone()
            self.assertIsNone(legacy_row)
            del migrated_store
            gc.collect()

    def test_deadline_palette_migration_does_not_treat_an_all_invalid_list_as_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            store = server.Store(path)
            with closing(store.connect()) as conn, conn:
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values(?, ?, ?)",
                    (
                        server.CONFERENCE_DEADLINES_KEY,
                        json.dumps([{"unexpected": "shape"}]),
                        time.time(),
                    ),
                )
                conn.execute(
                    "insert into dashboard_settings(key, value, updated_ts) values(?, ?, ?)",
                    (
                        server.LEGACY_CONFERENCE_DEADLINE_KEY,
                        json.dumps(
                            {
                                "title": "Recovered from malformed list",
                                "deadline_at": "2027-02-01T12:00:00+09:00",
                                "url": "https://example.com/recovered-list",
                            }
                        ),
                        time.time(),
                    ),
                )
                conn.execute(
                    "delete from maintenance_meta where key=?",
                    (server.CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
                )
            del store
            gc.collect()

            migrated_store = server.Store(path)
            recovered = migrated_store.deadlines({})
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["title"], "Recovered from malformed list")
            self.assertEqual(recovered[0]["tone"], "silver")
            with closing(migrated_store.connect()) as conn:
                legacy_row = conn.execute(
                    "select 1 from dashboard_settings where key=?",
                    (server.LEGACY_CONFERENCE_DEADLINE_KEY,),
                ).fetchone()
            self.assertIsNone(legacy_row)
            del migrated_store
            gc.collect()

    def test_schema_upgrade_adds_permanent_notice_flag_and_update_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gpu_watch.sqlite3"
            created_ts = time.time() - 120
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(
                    """
                    create table announcements (
                        id integer primary key autoincrement,
                        created_ts real not null,
                        expires_ts real not null,
                        author text not null,
                        message text not null,
                        pin_hash text not null,
                        deleted_ts real,
                        deleted_by text
                    )
                    """
                )
                conn.execute(
                    """
                    insert into announcements(
                        created_ts, expires_ts, author, message, pin_hash
                    )
                    values (?, ?, 'legacy', 'kept', 'hash')
                    """,
                    (created_ts, created_ts + 3600),
                )

            store = server.Store(path)
            with closing(store.connect()) as conn:
                columns = {
                    row["name"] for row in conn.execute("pragma table_info(announcements)")
                }
                updated_ts = conn.execute(
                    "select updated_ts from announcements where author='legacy'"
                ).fetchone()[0]
                permanent = conn.execute(
                    "select permanent from announcements where author='legacy'"
                ).fetchone()[0]
                schema_version = conn.execute(
                    "select value from schema_meta where key='schema_version'"
                ).fetchone()[0]
            self.assertIn("updated_ts", columns)
            self.assertIn("permanent", columns)
            self.assertEqual(updated_ts, created_ts)
            self.assertEqual(permanent, 0)
            self.assertEqual(schema_version, "6")
            del store
            gc.collect()

    def test_announcement_passphrase_work_runs_outside_the_store_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            notice = store.add_announcement("owner", "message", None, None, "owner-pass")
            real_verify = server.verify_pin
            lock_checks = []

            def verify_without_store_lock(pin, encoded):
                acquired = store.lock.acquire(blocking=False)
                lock_checks.append(acquired)
                if acquired:
                    store.lock.release()
                return real_verify(pin, encoded)

            with mock.patch.object(server, "verify_pin", side_effect=verify_without_store_lock):
                updated, status = store.update_announcement(
                    notice["id"],
                    "owner",
                    "updated",
                    None,
                    None,
                    "owner-pass",
                    None,
                )
                self.assertEqual(status, "updated")
                self.assertIsNotNone(updated)
                deleted, status = store.delete_announcement(
                    notice["id"], "owner-pass", None
                )
                self.assertTrue(deleted)
                self.assertEqual(status, "deleted")
            self.assertTrue(lock_checks)
            self.assertTrue(all(lock_checks))

    def test_permanent_announcements_survive_expiry_and_maintenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_backup_dir = server.BACKUP_DIR
            server.BACKUP_DIR = root / "backups"
            try:
                store = server.Store(root / "gpu_watch.sqlite3")
                permanent = store.add_announcement(
                    "researcher",
                    "no expiry",
                    None,
                    None,
                    "owner-password-2468",
                )
                self.assertTrue(permanent["permanent"])
                self.assertIsNone(permanent["expires_at"])
                self.assertIsNone(permanent["remaining_seconds"])

                with closing(store.connect()) as conn, conn:
                    conn.execute(
                        "update announcements set created_ts=?, expires_ts=? where id=?",
                        (time.time() - 200 * 86400, time.time() - 200 * 86400, permanent["id"]),
                    )
                store.run_maintenance({"announcement_retention_days": 90})
                visible = store.announcements()
                self.assertEqual([item["id"] for item in visible], [permanent["id"]])
                self.assertTrue(visible[0]["permanent"])
            finally:
                server.BACKUP_DIR = original_backup_dir

    def test_backup_retention_and_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_backup_dir = server.BACKUP_DIR
            server.BACKUP_DIR = root / "backups"
            try:
                store = server.Store(root / "gpu_watch.sqlite3")
                server.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                expired_backup = server.BACKUP_DIR / "gpu_watch-20000101.sqlite3"
                expired_backup.write_bytes(b"expired")
                Path(f"{expired_backup}-wal").write_bytes(b"")
                Path(f"{expired_backup}-shm").write_bytes(b"\0" * 32768)
                expired_time = time.time() - 20 * 86400
                os.utime(expired_backup, (expired_time, expired_time))
                with closing(store.connect()) as conn, conn:
                    conn.execute(
                        "insert into events(ts, host, gpu_index, event) values (?, 'old', 0, 'free_start')",
                        (time.time() - 400 * 86400,),
                    )
                    conn.execute(
                        "insert into events(ts, host, gpu_index, event) values (?, 'new', 0, 'busy_start')",
                        (time.time(),),
                    )
                result = store.run_maintenance({
                    "event_retention_days": 180,
                    "announcement_retention_days": 90,
                    "backup_retention_days": 14,
                })
                self.assertEqual(result["integrity"], "ok")
                self.assertTrue(result["backup_created"])
                backup_path = server.BACKUP_DIR / result["backup"]
                self.assertTrue(backup_path.exists())
                with closing(sqlite3.connect(server.standalone_database_uri(backup_path), uri=True)) as backup:
                    self.assertEqual(backup.execute("pragma journal_mode").fetchone()[0], "delete")
                    self.assertEqual(backup.execute("pragma quick_check").fetchone()[0], "ok")
                    self.assertEqual(backup.execute("pragma foreign_key_check").fetchall(), [])
                for path in (backup_path, backup_path.with_suffix(".sqlite3.tmp"), expired_backup):
                    self.assertFalse(Path(f"{path}-wal").exists())
                    self.assertFalse(Path(f"{path}-shm").exists())
                self.assertFalse(expired_backup.exists())
                with closing(store.connect()) as conn, conn:
                    hosts = [row[0] for row in conn.execute("select host from events order by host")]
                self.assertEqual(hosts, ["new"])
                healthy_disk = mock.Mock(total=1000, used=100, free=900)
                with mock.patch("server.shutil.disk_usage", return_value=healthy_disk):
                    self.assertTrue(store.health()["ok"])
                del store
                gc.collect()
            finally:
                server.BACKUP_DIR = original_backup_dir

    def test_active_announcement_limit_prevents_permanent_row_displacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = server.Store(Path(temp_dir) / "gpu_watch.sqlite3")
            ts = time.time()
            with closing(store.connect()) as conn, conn:
                conn.executemany(
                    """
                    insert into announcements(
                        created_ts, expires_ts, permanent, author, message,
                        pin_hash, updated_ts
                    ) values (?, ?, 1, 'owner', ?, 'hash', ?)
                    """,
                    [
                        (ts + index, ts, f"message-{index}", ts + index)
                        for index in range(server.MAX_ACTIVE_ANNOUNCEMENTS)
                    ],
                )
            with mock.patch.object(
                server,
                "bounded_hash_passphrase",
                side_effect=AssertionError("full-cap request must not hash"),
            ):
                with self.assertRaisesRegex(ValueError, "최대 20개"):
                    store.add_announcement("owner", "overflow", None, None, "pass")
            self.assertEqual(len(store.announcements()), server.MAX_ACTIVE_ANNOUNCEMENTS)
            del store
            gc.collect()

    def test_backup_is_verified_before_atomic_publish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_backup_dir = server.BACKUP_DIR
            server.BACKUP_DIR = root / "backups"
            original_connect = server.sqlite3.connect
            original_replace = server.os.replace
            operations = []

            def tracked_connect(database, *args, **kwargs):
                if str(database).endswith(".sqlite3.tmp?mode=ro&immutable=1"):
                    operations.append("verify")
                return original_connect(database, *args, **kwargs)

            def tracked_replace(source, destination):
                operations.append("publish")
                return original_replace(source, destination)

            try:
                store = server.Store(root / "gpu_watch.sqlite3")
                with (
                    mock.patch.object(server.sqlite3, "connect", side_effect=tracked_connect),
                    mock.patch.object(server.os, "replace", side_effect=tracked_replace),
                ):
                    result = store.run_maintenance({})
                self.assertTrue(result["backup_created"])
                self.assertEqual(operations, ["verify", "publish"])
                del store
                gc.collect()
            finally:
                server.BACKUP_DIR = original_backup_dir

    def test_daily_backup_name_uses_kst_calendar_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_backup_dir = server.BACKUP_DIR
            server.BACKUP_DIR = root / "backups"
            try:
                store = server.Store(root / "gpu_watch.sqlite3")
                kst_just_after_midnight = datetime(
                    2026, 7, 17, 0, 5, tzinfo=server.KST
                ).timestamp()
                with mock.patch.object(server, "now_ts", return_value=kst_just_after_midnight):
                    result = store.run_maintenance({})
                self.assertEqual(result["backup"], "gpu_watch-20260717.sqlite3")
                del store
                gc.collect()
            finally:
                server.BACKUP_DIR = original_backup_dir


if __name__ == "__main__":
    unittest.main()
