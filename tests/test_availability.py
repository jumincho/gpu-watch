"""Binary host availability: complete sample or DOWN, with one recovery event."""
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from unittest import mock
import server

class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=server.Store(Path(self.temp.name)/"test.sqlite3")
        self.config=server.load_config();self.config["hosts"]=[{"name":"test","lab":"nll","expected_gpu_count":2}]
        self.collector=server.Collector(self.store,self.config);self.addCleanup(self.collector.stop)
    def sample(self,ts,errors=None,busy=True):
        gpus=[(dict(index=i,uuid="GPU-"+str(i)*36,name="GPU",memory_total=8192,
                    memory_used=1024 if busy else 0,utilization=10 if busy else 0,temperature=30,
                    processes=[dict(pid=10+i,user="alice",process_name="python")] if busy else []),busy)
              for i in range(2) if not errors or i not in errors]
        with mock.patch("server.now_ts",return_value=ts):
            self.store.apply_host_payload("test","test",gpus,expected_gpu_indices={0,1},
                                          gpu_errors=errors,max_observation_gap_seconds=120)
    def snapshot(self,ts):
        with mock.patch("server.now_ts",return_value=ts):return self.store.snapshot(self.config)["hosts"][0]
    def fail_host(self,ts,error="ssh probe timeout after 45s"):
        with mock.patch("server.now_ts",return_value=ts):
            self.store.mark_host("test","test",False,error=error)
    def host_events(self):
        return [e["event"] for e in reversed(self.store.events()["events"]) if e["event"].startswith("host_")]
    def test_first_failure_is_immediately_down_and_recovery_emits_once(self):
        self.sample(100);self.sample(110);self.fail_host(120)
        h=self.snapshot(121)
        self.assertFalse(h["online"]);self.assertEqual(h["availability_state"],"down")
        self.assertTrue(all(not g["available"] and g["busy"] is None for g in h["gpus"]))
        self.assertEqual(h["usage"]["observed_seconds"],20)
        self.assertEqual(h["recent_capacity"]["observed_gpu_seconds"],20)
        for ts in [130,140]:self.fail_host(ts)
        self.store=server.Store(self.store.path)
        self.fail_host(150);self.sample(160);self.sample(170)
        self.assertEqual(self.host_events(),["host_down","host_recovered"])
        self.assertEqual(self.snapshot(170)["usage"]["observed_seconds"],40)
        self.assertEqual(self.snapshot(170)["recent_capacity"]["observed_gpu_seconds"],40)
        self.assertTrue(all(e["gpu_index"] is None for e in self.store.events()["events"] if e["event"].startswith("host_")))
    def test_short_outage_is_down_up_and_not_bridged(self):
        self.sample(100);self.fail_host(101);self.sample(102)
        self.assertEqual(self.host_events(),["host_down","host_recovered"])
        self.assertEqual(self.snapshot(102)["usage"]["observed_seconds"],0)
    def test_first_success_has_no_up_but_first_failure_has_down(self):
        self.sample(100);self.assertEqual(self.host_events(),[])
        with mock.patch("server.now_ts",return_value=101):self.store.mark_host("new","new",False,error="connection refused")
        with mock.patch("server.now_ts",return_value=102):self.store.mark_host("new","new",False,error="timeout")
        with mock.patch("server.now_ts",return_value=103):self.store.mark_host("new","new",True)
        self.assertEqual([e["event"] for e in reversed(self.store.events(host="new")["events"])],["host_down","host_recovered"])
    def test_partial_payload_fails_whole_host_without_recording_siblings(self):
        self.sample(100);self.sample(110)
        self.sample(120,{1:"GPU inaccessible"});self.sample(130,{1:"GPU inaccessible"})
        h=self.snapshot(135)
        self.assertFalse(h["online"]);self.assertEqual(h["usage"]["observed_seconds"],20)
        self.assertTrue(all(not g["available"] for g in h["gpus"]))
        self.assertFalse(any(k in h for k in ("degraded","reachable")))
        self.assertFalse(any("usable" in g or "health_state" in g for g in h["gpus"]))
        self.sample(140);self.sample(150)
        self.assertEqual(self.host_events(),["host_down","host_recovered"])
        self.assertEqual(self.snapshot(150)["usage"]["observed_seconds"],40)
    def test_initial_failed_inventory_has_placeholders_without_fake_history(self):
        self.fail_host(100)
        h=self.snapshot(101)
        self.assertEqual(len(h["gpus"]),2);self.assertFalse(h["online"])
        self.assertIsNone(h["recent_usage"]["tracking_started_at"])
        self.assertEqual(h["usage"]["observed_seconds"],0)
    def test_all_collection_failures_use_same_binary_state(self):
        for index,error in enumerate(["timeout","Permission denied (publickey)","Connection refused", "GPU inaccessible","invalid GPU payload"]):
            ts=100+index*20;self.sample(ts);self.fail_host(ts+1,error)
            self.assertEqual(self.snapshot(ts+2)["availability_state"],"down")
        self.assertEqual(self.host_events().count("host_down"),5)
    def test_invalid_success_payload_is_down_without_partial_writes(self):
        self.sample(100)
        future=Future();future.set_result({"ok":True,"gpus":[]})
        with mock.patch("server.now_ts",return_value=110),mock.patch("server.audit"):
            self.collector.consume_gpu_result(self.config["hosts"][0],future)
        self.assertEqual(self.snapshot(111)["availability_state"],"down")
        self.assertEqual(self.host_events(),["host_down"])
        self.assertEqual(self.snapshot(111)["usage"]["observed_seconds"],0)
    def test_disk_only_failure_does_not_change_host_state(self):
        self.sample(100)
        with mock.patch("server.now_ts",return_value=110):self.store.mark_disk_error("test","du timeout")
        self.assertTrue(self.snapshot(111)["online"]);self.assertEqual(self.host_events(),[])
    def test_public_history_filters_and_paginates_down_up(self):
        self.sample(100);self.fail_host(110);self.sample(120)
        page=self.store.events(limit=1,host="test")
        self.assertEqual(page["events"][0]["event"],"host_recovered");self.assertTrue(page["has_more"])
        self.assertFalse(any(e["event"].startswith("observation_") for e in self.store.events()["events"]))
    def test_down_has_no_new_usage_events_or_user_attribution(self):
        self.sample(100,busy=False);self.sample(110,{1:"fault"},busy=True)
        h=self.snapshot(115)
        self.assertIsNone(h["gpus"][0]["last_used_at"])
        self.assertFalse(any(e["event"]=="busy_start" for e in self.store.events()["events"]))


    def test_history_correction_clips_both_edges_is_atomic_and_idempotent(self):
        from gpu_watch.history import apply_verified_idle_outage
        self.sample(100,busy=False);self.sample(200,busy=False)
        evidence=dict(key="test-outage",host="test",start=120,end=180,
                      observed_seconds_removed={"0":60,"1":60},daily_rows=[])
        with closing(self.store.connect()) as conn, conn:
            self.assertTrue(apply_verified_idle_outage(conn,evidence))
            self.assertFalse(apply_verified_idle_outage(conn,evidence))
            rows=conn.execute("select gpu_index,start_ts,end_ts,seconds from gpu_capacity_interval order by gpu_index,start_ts").fetchall()
            self.assertEqual([tuple(x) for x in rows],[(0,100,120,20),(0,180,200,20),(1,100,120,20),(1,180,200,20)])
        self.assertEqual(self.snapshot(200)["usage"]["observed_seconds"],80)
        with self.assertRaises(ValueError),closing(self.store.connect()) as conn,conn:
            apply_verified_idle_outage(conn,{**evidence,"key":"invalid", "observed_seconds_removed":{"0":20,"1":999}})
        self.assertEqual(self.snapshot(200)["usage"]["observed_seconds"],80)

    def test_history_rejects_unproven_busy_correction(self):
        from gpu_watch.history import apply_verified_idle_outage
        self.sample(100);self.sample(200)
        with self.assertRaises(ValueError),closing(self.store.connect()) as conn,conn:
            apply_verified_idle_outage(conn,dict(key="bad",host="test",start=120,end=180,observed_seconds_removed={"0":60},daily_rows=[]))
        self.assertEqual(self.snapshot(200)["usage"]["observed_seconds"],200)
