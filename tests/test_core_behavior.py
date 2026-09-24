import ast
import builtins
import io
import json
import os
import re
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest import mock
import server

class CoreBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(Path(self.temp.name) / "gpu_watch.sqlite3")
        self.config = server.load_config()
        self.config["hosts"] = [{"name": "test", "label": "test", "lab": "nll", "expected_gpu_count": 2}]
        self.config["labs"] = [{"id": "nll", "label": "NLL"}]
        self.config["default_deadline"] = {}

    def gpu(self, index, busy=True):
        return {"index": index, "uuid": "GPU-%s" % index, "name": "Test GPU",
                "memory_total": 8192, "memory_used": 1024 if busy else 0,
                "utilization": 0, "temperature": 40,
                "processes": [{"pid": 123 + index, "user": "alice", "process_name": "python",
                               "started": "stable", "start_identity": "1000"}] if busy else []}

    def test_tba_round_trip_sort_palette_reuse_and_scheduled_conversion(self):
        tba = self.store.update_deadline("🇺🇸 Conference", None, "https://example.org",
                                        mode="tba", tba_text="2027년 1월 예정")
        scheduled = self.store.update_deadline("Scheduled", "2027-02-01T12:00:00+09:00", "https://example.org")
        timers = self.store.deadlines(self.config)
        self.assertEqual([x["id"] for x in timers], [scheduled["id"], tba["id"]])
        self.assertEqual(timers[1]["mode"], "tba")
        self.assertIsNone(timers[1]["deadline_at"])
        self.assertEqual(timers[1]["tba_text"], "2027년 1월 예정")
        reopened = server.Store(Path(self.temp.name) / "gpu_watch.sqlite3")
        self.assertEqual(reopened.deadlines(self.config), timers)
        changed = self.store.update_deadline("🇺🇸 Conference", "2027-01-02T12:00:00+09:00", "https://example.org",
                                            deadline_id=tba["id"])
        self.assertEqual(changed["tone"], tba["tone"])
        self.assertEqual(changed["mode"], "scheduled")
        self.assertEqual(changed["tba_text"], "")
        self.assertEqual(self.store.deadlines(self.config)[0]["id"], tba["id"])
        changed = self.store.update_deadline("🇺🇸 Conference", None, "https://example.org",
                                            deadline_id=tba["id"], mode="tba", tba_text="2027년 예정")
        self.assertEqual(changed["tone"], tba["tone"])
        self.store.delete_deadline(tba["id"], self.config)
        replacement = self.store.update_deadline("New", None, "https://example.org", mode="tba", tba_text="예정")
        self.assertEqual(replacement["tone"], tba["tone"])

    def test_tba_validation_and_legacy_timer_default(self):
        for mode, text in [("bogus", "예정"), ("tba", ""), ("tba", " " * 3), ("tba", "x" * 33), ("tba", 1)]:
            with self.subTest(mode=mode, text=text), self.assertRaises(ValueError):
                self.store.update_deadline("Name", None, "https://example.org", mode=mode, tba_text=text)
        legacy = server.Store._deadline_fields({"title":"Legacy","deadline_at":"2027-01-02T12:00:00+09:00","url":"https://example.org"})
        self.assertEqual(legacy["mode"], "scheduled")
        self.assertIsNone(server.Store._deadline_fields({"title":"TBA","mode":"tba","url":"https://example.org"}))

    def test_equal_deadline_keeps_new_timer_behind_existing_timer(self):
        deadline_at = "2027-01-02T12:00:00+09:00"
        existing = self.store.update_deadline(
            "Existing", deadline_at, "https://example.org/existing", config=self.config
        )
        newer = self.store.update_deadline(
            "New", deadline_at, "https://example.org/new", config=self.config
        )
        self.assertEqual(
            [item["id"] for item in self.store.deadlines(self.config)],
            [existing["id"], newer["id"]],
        )

    def test_partial_failure_live_tail_freezes_and_recovery_restores_online(self):
        with mock.patch("server.now_ts", return_value=100):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True), (self.gpu(1), True)], expected_gpu_indices={0,1})
        with mock.patch("server.now_ts", return_value=110):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True)], expected_gpu_indices={0,1}, gpu_errors={1:"Unknown Error"})
        with mock.patch("server.now_ts", return_value=120):
            host = self.store.snapshot(self.config)["hosts"][0]
        self.assertFalse(host["online"])
        self.assertNotIn("reachable",host)
        self.assertEqual(host["gpus"][1]["usage_observed_seconds"], 0)
        self.assertEqual(host["gpus"][1]["usage_busy_seconds"], 0)
        self.assertEqual(host["recent_usage"]["busy_seconds"], 0)
        with mock.patch("server.now_ts", return_value=140):
            self.store.apply_host_payload("test", "test", [(self.gpu(0,False),False),(self.gpu(1,False),False)],
                                          expected_gpu_indices={0,1}, max_observation_gap_seconds=30)
            host = self.store.snapshot(self.config)["hosts"][0]
        self.assertTrue(host["online"])
        self.assertNotIn("degraded",host)
        self.assertTrue(all(x["available"] for x in host["gpus"]))

    def test_brief_explicit_gpu_failure_is_never_filled_on_recovery(self):
        with mock.patch("server.now_ts", return_value=100):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True), (self.gpu(1), True)],
                                          expected_gpu_indices={0,1}, max_observation_gap_seconds=120)
        with mock.patch("server.now_ts", return_value=110):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True)],
                                          expected_gpu_indices={0,1}, gpu_errors={1:"Unknown Error"},
                                          max_observation_gap_seconds=120)
        with mock.patch("server.now_ts", return_value=120):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True), (self.gpu(1), True)],
                                          expected_gpu_indices={0,1}, max_observation_gap_seconds=120)
            host = self.store.snapshot(self.config)["hosts"][0]
        self.assertTrue(host["online"])
        self.assertEqual(host["gpus"][0]["usage_observed_seconds"], 0)
        self.assertEqual(host["gpus"][1]["usage_observed_seconds"], 0)
        self.assertEqual(host["recent_usage"]["busy_seconds"], 0)

    def test_display_only_drain_owner_is_not_charged_in_live_statistics(self):
        with mock.patch("server.now_ts", return_value=100):
            self.store.apply_host_payload("test", "test", [(self.gpu(0),True)], expected_gpu_indices={0})
        empty = self.gpu(0)
        empty["processes"] = []
        with mock.patch("server.now_ts", return_value=170):
            self.store.apply_host_payload("test", "test", [(empty,True)], expected_gpu_indices={0}, max_observation_gap_seconds=120)
        with mock.patch("server.now_ts", return_value=180):
            host = self.store.snapshot(self.config)["hosts"][0]
        self.assertEqual(host["gpus"][0]["last_user"], "alice")
        self.assertEqual(host["recent_usage"]["busy_seconds"], 80)
        alice = next(x for x in host["recent_usage"]["top_users"] if x["user"] == "alice")
        self.assertEqual(alice["seconds"], 70)
        self.assertEqual(host["usage"]["top_users"][0]["seconds"],70)

    def test_read_only_audit_detects_overlapping_intervals(self):
        import hashlib
        import runpy
        audit = runpy.run_path(str(Path(server.__file__).parent / "scripts/audit-data.py"))["audit_database"]
        config_path = Path(self.temp.name) / "hosts.json"
        config_path.write_text(json.dumps({"hosts": self.config["hosts"]}), encoding="utf-8")
        with mock.patch("server.now_ts", return_value=100):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True)])
        with mock.patch("server.now_ts", return_value=110):
            self.store.apply_host_payload("test", "test", [(self.gpu(0), True)])
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        self.assertTrue(audit(self.store.path, config_path)["ok"])
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), before)
        with closing(self.store.connect()) as conn, conn:
            conn.execute("insert into gpu_usage_interval(host,gpu_index,start_ts,end_ts,users,seconds) values ('test',0,102,107,'alice',5)")
        result = audit(self.store.path, config_path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["gpu_usage_interval"]["overlaps"], 1)

    def test_command_summary_stops_at_entrypoint_and_never_scans_arguments(self):
        tree = ast.parse(server.REMOTE_PROBE)
        node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="command_summary_for_pid")
        namespace = {"os":os, "SAFE_COMMAND_TOKEN_RE":re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"),
                     "SAFE_MODULE_NAME_RE":re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"),
                     "SAFE_SCRIPT_NAME_RE":re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.(?:py|sh|js)$")}
        exec(compile(ast.Module(body=[node],type_ignores=[]),"<summary>","exec"),namespace)
        for argv, expected in [
            (["python","/private/train.py","--api-key","secret.py"],"python train.py"),
            (["python","train.py","-m","secret_token"],"python train.py"),
            (["python","-c","code","secret.py"],"python"),
            (["python","-u","-X","dev","-m","package.run","--token","x"],"python -m package.run"),
            (["python","-W","ignore","/private/train.py"],"python train.py"),
            (["python","--unrecognized","private.py"],"python"),
            (["python","--","train.py","secret.py"],"python train.py"),
            (["python","-","secret.py"],"python"),
            (["node","--eval","code","secret.js"],"node")]:
            with self.subTest(argv=argv), mock.patch.object(builtins,"open",return_value=io.BytesIO(("\0".join(argv)+"\0").encode())):
                self.assertEqual(namespace["command_summary_for_pid"](123,argv[0]),expected)
