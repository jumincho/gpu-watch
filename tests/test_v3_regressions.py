import ast
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import server
from gpu_watch.api_quota import ApiQuota
import test_artificial_analysis as aa
from test_artificial_analysis import FakeResponse, QueueOpener, page, model


class AuditIntegrityTests(unittest.TestCase):
    def test_testcases_never_override_unittest_failure_implementation(self):
        for path in (server.ROOT / "tests").glob("test_*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef):
                    self.assertNotIn("fail", [n.name for n in node.body if isinstance(n, ast.FunctionDef)], str(path))

    def test_effective_uid_wins_for_root_owned_nondumpable_proc_directory(self):
        tree = ast.parse(server.REMOTE_GPU_PROBE)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_metadata")
        os_mock = types.SimpleNamespace(stat=lambda _: types.SimpleNamespace(st_uid=0), path=server.os.path)
        pwd_mock = types.SimpleNamespace(getpwuid=lambda uid: types.SimpleNamespace(pw_name="alice" if uid == 1234 else "root"))
        namespace = {"os": os_mock, "pwd": pwd_mock, "process_start_identity": lambda _: "100"}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<metadata>", "exec"), namespace)
        def fake_open(path, *args, **kwargs):
            return io.StringIO("Uid:\t1234\t1234\t1234\t1234\n" if path.endswith("status") else "python\n")
        with mock.patch("builtins.open", side_effect=fake_open):
            self.assertEqual(namespace["process_metadata"](42)["user"], "alice")

    def test_fixed_helper_has_no_source_import_or_external_environment(self):
        path = server.ROOT / "scripts/provision-disk-helper.py"
        spec = importlib.util.spec_from_file_location("disk_provision", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        helper = module.helper_source((server.ROOT / "server.py").read_text())
        self.assertTrue(helper.startswith("#!/usr/bin/python3 -I\n"))
        self.assertNotIn("import server", helper)
        self.assertIn("len(sys.argv) != 1", helper)
        self.assertIn("os.environ.clear()", helper)
        self.assertIn("fcntl.LOCK_NB", helper)
        self.assertIn("os.nice(15)", helper)
        with self.assertRaises(ValueError): module.installer_source(helper, "user ALL=(ALL)")


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now = 1_800_000_000.0
        self.path = Path(self.temp.name) / "quota.json"
        self.quota = ApiQuota(self.path, lambda: self.now)

    def observe(self, remaining=96):
        self.quota.observe({"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": str(remaining),
                            "X-RateLimit-Reset": str(self.now + 86400)})
        self.quota.pages = 4

    def test_four_page_window_runs_faster_than_six_hours_and_reserves_quota(self):
        self.observe()
        delay = self.quota.next_delay(900)
        self.assertGreaterEqual(delay, 3600)
        self.assertLess(delay, 3900)
        self.assertEqual(self.quota.required_wait(4), 0)
        self.observe(11)
        self.assertGreaterEqual(self.quota.required_wait(4), 86400)

    def test_request_reservation_and_schedule_survive_restart(self):
        self.observe(); self.quota.reserve_request(); self.quota.schedule(self.now + 4200)
        reloaded = ApiQuota(self.path, lambda: self.now)
        self.assertEqual(reloaded.remaining, 95)
        self.assertEqual(reloaded.not_before, self.now + 4200)
        self.assertEqual(reloaded.pages, 4)

    def test_malformed_headers_cannot_reset_known_quota(self):
        self.observe(0)
        self.quota.observe({"X-RateLimit-Limit": "NaN", "X-RateLimit-Remaining": "1000000"})
        self.assertEqual(self.quota.remaining, 0)
        self.assertGreater(self.quota.required_wait(1), 0)

    def test_fixed_window_resets_and_missing_headers_use_conservative_fallback(self):
        self.assertEqual(self.quota.next_delay(900), 21600)
        self.observe(0); self.now += 86401
        self.assertEqual(self.quota.required_wait(4), 0)
        self.assertEqual(self.quota.remaining, 100)


class QuotaIntegrationTests(unittest.TestCase):
    setUp = aa.ArtificialAnalysisIndexTests.setUp
    tearDown = aa.ArtificialAnalysisIndexTests.tearDown
    service = aa.ArtificialAnalysisIndexTests.service
    def test_headers_set_cadence_and_restart_retains_it(self):
        opener = QueueOpener(FakeResponse(page([model("one", "One", "Lab", 40)]), headers={
            "X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "96",
            "X-RateLimit-Reset": str(self.clock.value + 86400)}))
        service = self.service(opener)
        self.assertTrue(service.refresh_now())
        self.assertLess(service._next_attempt_at - self.clock.value, 3600)
        next_attempt = service._next_attempt_at
        reloaded = self.service(QueueOpener())
        self.assertEqual(reloaded._next_attempt_at, next_attempt)
        self.assertFalse(reloaded.refresh_now())

    def test_insufficient_budget_never_publishes_partial_ranking(self):
        opener = QueueOpener(FakeResponse(page([model("one", "One", "Lab", 40)],total_pages=4), headers={
            "X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "2",
            "X-RateLimit-Reset": str(self.clock.value + 40000)}))
        service = self.service(opener)
        self.assertFalse(service.refresh_now())
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(service.payload(schedule_refresh=False)["status"], "unavailable")
        reloaded = self.service(QueueOpener())
        self.assertFalse(reloaded.refresh_now())



class DiskFallbackTests(unittest.TestCase):
    def test_unavailable_fixed_helper_retains_partial_df_within_budget(self):
        collector = server.Collector(None, {"ssh_disk_probe_timeout_seconds": 720})
        self.addCleanup(collector.stop)
        host = {"name": "test", "privileged_disk_helper": True}
        values = [{"ok": False, "error": "missing helper"}, {"ok": True, "disk": {"filesystems": [{"mount": "/"}], "errors": []}}]
        with mock.patch.object(collector, "_probe_host_once", side_effect=values) as probe, mock.patch("server.time.monotonic", side_effect=[0, 5]):
            result = collector.probe_host(host, True)
        self.assertTrue(result["ok"])
        self.assertEqual(probe.call_count, 2)
        self.assertFalse(probe.call_args.args[0]["privileged_disk_helper"])
        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 715)
        self.assertIn("user usage partial", result["disk"]["errors"][0])

    def test_spent_disk_budget_does_not_start_another_probe(self):
        collector = server.Collector(None, {"ssh_disk_probe_timeout_seconds": 720})
        self.addCleanup(collector.stop)
        with mock.patch.object(collector, "_probe_host_once", return_value={"ok": False}) as probe, mock.patch("server.time.monotonic", side_effect=[0, 720]):
            self.assertFalse(collector.probe_host({"privileged_disk_helper": True}, True)["ok"])
        self.assertEqual(probe.call_count, 1)
