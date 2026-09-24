import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from unittest import mock

import server
from gpu_watch.collection import retry_reason


class CollectionResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(Path(self.temp.name) / "test.sqlite3")
        self.config = server.load_config()
        self.host = {"name": "test", "lab": "nll", "expected_gpu_count": 1}
        self.config["hosts"] = [self.host]
        self.config["disk_poll_interval_seconds"] = 0
        self.collector = server.Collector(self.store, self.config)
        self.addCleanup(self.collector.stop)

    def payload(self):
        return {"ok": True, "gpus": [{
            "index": 0, "uuid": "GPU-" + "a" * 36, "name": "GPU",
            "memory_total": 8192, "memory_used": 1024, "utilization": 10,
            "temperature": 30, "processes": [{"pid": 10, "user": "alice", "process_name": "python"}],
        }]}

    def apply(self, ts, payload=None):
        with mock.patch("server.now_ts", return_value=ts):
            self.collector.handle_payload(self.host, payload or self.payload())

    def fail(self, ts):
        self.apply(ts, {"ok": False, "error": "ssh probe timeout after 45s"})

    def observation_events(self):
        with closing(self.store.connect()) as conn:
            return [{**dict(row), "details": server.event_details(row["note"])} for row in conn.execute(
                "select * from events where event in ('host_down','host_recovered') order by ts desc,id desc")]


    def test_fast_transient_retry_recovers_without_public_failure_or_fake_usage(self):
        self.apply(100)
        self.apply(110)
        with (
            mock.patch.object(self.collector, "_probe_host_once", side_effect=[
                {"ok": False, "error": "ssh exit 255: Connection reset by peer"}, self.payload(),
            ]) as probe,
            mock.patch("server.time.monotonic", side_effect=[0, 8, 8.5]),
            mock.patch.object(self.collector.stop_event, "wait", return_value=False),
            mock.patch("server.audit") as audit,
        ):
            payload = self.collector.probe_host(self.host)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 36.5)
        self.assertTrue(payload["_collection_retried"])
        self.assertEqual(audit.call_args.kwargs["cause"], "connection")
        self.apply(120, payload)
        with mock.patch("server.now_ts", return_value=120):
            host = self.store.snapshot(self.config)["hosts"][0]
        self.assertTrue(host["online"])
        self.assertEqual(host["usage"]["observed_seconds"], 10)
        self.assertEqual(self.observation_events(), [])
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute("select count(*) from events where event='observation_gap'").fetchone()[0], 1)

    def test_healthy_probe_has_no_retry_and_cannot_forge_retry_provenance(self):
        payload = {**self.payload(), "_collection_retried": True}
        with mock.patch.object(self.collector, "_probe_host_once", return_value=payload) as probe:
            result = self.collector.probe_host(self.host)
        self.assertEqual(probe.call_count, 1)
        self.assertNotIn("_collection_retried", result)

    def test_full_timeout_budget_never_starts_a_second_probe(self):
        with (
            mock.patch.object(self.collector, "_probe_host_once", return_value={
                "ok": False, "error": "ssh probe timeout after 45s",
            }) as probe,
            mock.patch("server.time.monotonic", side_effect=[0, 45]),
            mock.patch.object(self.collector.stop_event, "wait") as wait,
        ):
            self.assertFalse(self.collector.probe_host(self.host)["ok"])
        self.assertEqual(probe.call_count, 1)
        wait.assert_not_called()

    def test_retry_is_bounded_to_once_even_if_it_fails_again(self):
        failure = {"ok": False, "error": "ssh exit 255: Connection refused"}
        with (
            mock.patch.object(self.collector, "_probe_host_once", side_effect=[failure.copy(), failure.copy()]) as probe,
            mock.patch.object(self.collector.stop_event, "wait", return_value=False),
            mock.patch("server.audit"),
        ):
            result = self.collector.probe_host(self.host)
        self.assertEqual(probe.call_count, 2)
        self.assertFalse(result["ok"])
        self.assertLessEqual(probe.call_args.kwargs["timeout_seconds"], 44.5)

    def test_retry_does_not_repeat_disk_auth_tool_or_security_failures(self):
        for error in [
            "SSH credential unavailable", "ssh exit 255: Permission denied (publickey)",
            "ssh exit 255: Host key verification failed. Connection closed",
            "ssh exit 255: REMOTE HOST IDENTIFICATION HAS CHANGED",
            "ssh exit 255: no matching host key type found",
            "ssh unavailable: FileNotFoundError", "ssh probe output exceeded limit",
            "bad json: JSONDecodeError", "ssh exit 1: Connection refused",
        ]:
            self.assertIsNone(retry_reason({"ok": False, "error": error}), error)
        with mock.patch.object(self.collector, "_probe_host_once", return_value={
            "ok": False, "error": "ssh probe timeout after 720s",
        }) as probe:
            self.collector.probe_host(self.host, True)
        self.assertEqual(probe.call_count, 1)

    def test_successful_payload_does_not_use_retired_health_cache(self):
        self.assertIsNone(retry_reason({'ok':True,'gpu_health':{'0':{'state':'unknown','cause':'probe_timeout'}}}))
        self.assertFalse(hasattr(self.collector,'usability_cache'))

    def test_stop_during_retry_wait_does_not_launch_second_process(self):
        with (
            mock.patch.object(self.collector, "_probe_host_once", return_value={
                "ok": False, "error": "ssh exit 255: Connection reset",
            }) as probe,
            mock.patch.object(self.collector.stop_event, "wait", return_value=True),
        ):
            self.collector.probe_host(self.host)
        self.assertEqual(probe.call_count, 1)

    def test_short_failure_burst_emits_down_up_immediately(self):
        self.apply(100)
        for ts in [110,120,130]:self.fail(ts)
        with mock.patch('server.now_ts',return_value=131):host=self.store.snapshot(self.config)['hosts'][0]
        self.assertEqual(host['availability_state'],'down')
        self.assertEqual(host['usage']['observed_seconds'],0)
        self.apply(140)
        self.assertEqual([e['event'] for e in reversed(self.observation_events())],['host_down','host_recovered'])

    def test_sustained_loss_emits_one_pair_across_restart_with_cause(self):
        self.apply(100)
        for ts in [110, 120, 130, 159]:
            self.fail(ts)
        self.assertEqual(len(self.observation_events()), 1)
        self.fail(160)
        self.fail(180)
        self.assertEqual(len(self.observation_events()), 1)
        self.store = server.Store(self.store.path)
        collector = server.Collector(self.store, self.config)
        self.addCleanup(collector.stop)
        with mock.patch.object(collector.thread, "start"):
            collector.start()
        self.assertEqual(collector.host_failures["test"], 6)
        with mock.patch("server.now_ts", return_value=200):
            collector.handle_payload(self.host, self.payload())
        self.assertEqual([e["event"] for e in reversed(self.observation_events())],
                         ["host_down", "host_recovered"])
        self.assertTrue(all(e["details"]["cause"] == "timeout" for e in self.observation_events()))

    def test_initial_and_intermittent_failure_pairs_have_no_seen_event(self):
        for ts in [10,20,30,80]:self.fail(ts)
        self.apply(100)
        for ts in [110,130,150,170]:self.fail(ts);self.apply(ts+10)
        self.assertEqual([e['event'] for e in reversed(self.observation_events())],['host_down','host_recovered']*5)

    def test_known_gpu_fault_is_published_without_collection_grace(self):
        self.apply(100)
        self.apply(110, {"ok": True, "gpus": [], "gpu_errors": {"0": "device inaccessible"},
                        "gpu_health": {"0": {"state": "down", "cause": "nvml_inaccessible",
                                            "code": 15, "source": "nvml"}}})
        event = self.store.events()["events"][0]
        self.assertEqual(event["event"], "host_down")
        self.assertEqual(event["gpu_indices"], [])

    def test_scheduler_does_not_wait_for_slow_host_and_bounds_queue(self):
        hosts = [{"name": name, "expected_gpu_count": 1} for name in ["slow", "fast", "other"]]
        self.collector.config["hosts"] = hosts
        self.collector.gpu_workers = 2
        futures = {}
        def submit(fn, host, disk):
            future = Future()
            futures.setdefault(host["name"], []).append(future)
            return future
        with (
            mock.patch.object(self.collector.gpu_pool, "submit", side_effect=submit),
            mock.patch("server.time.monotonic", return_value=100) as clock,
            mock.patch.object(self.collector, "handle_payload") as handle,
        ):
            self.collector.collect_tick()
            self.assertEqual(set(self.collector.gpu_futures), {"slow", "fast"})
            futures["fast"][0].set_result(self.payload())
            self.collector.collect_tick()
            self.assertIn("other", self.collector.gpu_futures)
            self.assertEqual(handle.call_count, 1)
            futures["other"][0].set_result(self.payload())
            self.collector.collect_tick()
            clock.return_value = 111
            self.collector.collect_tick()
            self.assertEqual(len(futures["fast"]), 2)
            self.assertFalse(futures["slow"][0].done())
            self.assertLessEqual(len(self.collector.gpu_futures), 2)
            self.assertEqual(len(futures["slow"]), 1)
        for host, future, started in self.collector.gpu_futures.values():
            future.cancel()

    def test_worker_exception_uses_failure_audit_and_backoff(self):
        self.apply(100)
        with mock.patch("server.audit") as audit:
            for _ in range(3):
                future = Future()
                future.set_exception(ValueError("invalid GPU payload"))
                self.collector.consume_gpu_result(self.host, future)
        self.assertEqual(self.collector.host_failures["test"], 3)
        self.assertGreater(self.collector.host_next_probe_at["test"], time.monotonic())
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(audit.call_args.kwargs["cause"], "probe_failure")

    def test_scheduler_stop_does_not_publish_shutdown_as_failure(self):
        future = Future()
        future.set_result({"ok": False, "error": "collector stopping"})
        self.collector.gpu_futures["test"] = (self.host, future, 0)
        self.collector.stop_event.set()
        with mock.patch.object(self.collector, "handle_payload") as handle:
            self.collector.collect_tick()
        handle.assert_not_called()
        self.assertEqual(self.collector.gpu_futures, {})


    def test_real_worker_hang_does_not_stop_other_hosts_or_shutdown(self):
        self.collector.config["hosts"] = [
            {"name": "slow", "expected_gpu_count": 1},
            {"name": "fast", "expected_gpu_count": 1},
        ]
        self.collector.config["poll_interval_seconds"] = 1
        fast_twice = threading.Event()
        slow_started = threading.Event()
        calls = []
        def probe(host, disk=False):
            if host["name"] == "slow":
                slow_started.set()
                self.collector.stop_event.wait(8)
                return {"ok": False, "error": "collector stopping"}
            return self.payload()
        def consume(host, payload):
            calls.append(host["name"])
            if calls.count("fast") >= 2:
                fast_twice.set()
        with (
            mock.patch.object(self.collector, "probe_host", side_effect=probe),
            mock.patch.object(self.collector, "handle_payload", side_effect=consume),
        ):
            self.collector.start()
            try:
                self.assertTrue(slow_started.wait(2))
                self.assertTrue(fast_twice.wait(6))
            finally:
                self.collector.stop()
        self.assertFalse(self.collector.thread.is_alive())
        self.assertNotIn("slow", calls)

    def test_retry_remaining_budget_reaches_bounded_reader(self):
        process = mock.Mock(returncode=0)
        with (
            mock.patch("server.trusted_ssh_executable", return_value="/trusted/ssh"),
            mock.patch("server.subprocess.Popen", return_value=process),
            mock.patch("server._communicate_bounded", return_value=(json.dumps(self.payload()), "")) as reader,
        ):
            self.collector._probe_host_once(self.host, timeout_seconds=17.25)
        self.assertEqual(reader.call_args.kwargs["timeout"], 17.25)
        self.assertEqual(self.collector.running_processes, set())

    def test_loss_event_requires_both_count_and_duration(self):
        self.apply(100)
        self.fail(200)
        self.fail(250)
        self.assertEqual(self.observation_events(), [])
        self.fail(300)
        self.assertEqual(len(self.observation_events()), 1)

    def test_initial_disk_work_waits_for_first_successful_gpu_result(self):
        future = Future()
        with (
            mock.patch.object(self.collector.gpu_pool, "submit", return_value=future),
            mock.patch.object(self.collector, "start_due_disk_probes") as disks,
            mock.patch("server.time.monotonic", return_value=100),
        ):
            self.collector.collect_tick()
            disks.assert_called_once_with([])
            future.set_result(self.payload())
            self.collector.collect_tick()
            self.assertEqual(disks.call_args.args[0], [self.host])



    def test_failed_host_recheck_wait_is_capped_at_one_minute(self):
        self.apply(100)
        with mock.patch("server.time.monotonic", return_value=1000), mock.patch("server.audit"):
            for failures in range(1, 12):
                self.fail(110 + failures * 10)
                if failures >= 3:
                    expected = min(60, 10 * 2 ** min(5, failures - 3))
                    self.assertEqual(self.collector.host_next_probe_at["test"], 1000 + expected)
        with mock.patch("server.now_ts", return_value=300):
            self.collector.handle_payload(self.host, self.payload())
        self.assertNotIn("test", self.collector.host_failures)
        self.assertNotIn("test", self.collector.host_next_probe_at)


if __name__ == "__main__":
    unittest.main()
