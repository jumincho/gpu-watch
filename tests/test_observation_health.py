import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
import server
from gpu_watch.health import migrate_host_availability

class ObservationHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=server.Store(Path(self.temp.name)/"test.sqlite3")
        self.cfg=server.load_config();self.cfg["hosts"]=[{"name":"test","lab":"nll","expected_gpu_count":2}]
    def snapshot(self,ts):
        with mock.patch("server.now_ts",return_value=ts):return self.store.snapshot(self.cfg)["hosts"][0]
    def test_migration_retires_cached_partial_state_preserves_history_and_is_idempotent(self):
        with closing(self.store.connect()) as conn,conn:
            conn.execute("delete from maintenance_meta where key='simple_host_availability_v1'")
            conn.execute("insert into host_runtime(host,label,online,gpu_errors_json) values ('test','test',1,?)", ('{"1":"fault"}',))
            for event in ['host_down','host_recovered','gpu_down','gpu_recovered','observation_lost','observation_resumed']:
                conn.execute("insert into events(ts,host,event,note) values(100,'test',?,'original')",(event,))
            before=[tuple(r) for r in conn.execute('select * from events')]
            migrate_host_availability(conn);migrate_host_availability(conn)
            self.assertEqual([tuple(r) for r in conn.execute('select * from events')],before)
            row=conn.execute("select online,gpu_errors_json,usable_gpu_indices_json,gpu_health_json from host_runtime").fetchone()
            self.assertEqual(tuple(row),(0,'{}',None,'{}'))
        self.assertEqual(self.snapshot(101)['availability_state'],'down')
        with mock.patch('server.now_ts',return_value=102):self.store.mark_host('test','test',True)
        self.store=server.Store(self.store.path)
        self.assertTrue(self.snapshot(103)['online'])
    def test_retired_usability_probe_is_not_executed_or_serialized(self):
        for text in ['cuInit','check_gpu_usability','nvml_health','usability-cache-b64','gpu_health']:
            self.assertNotIn(text,server.REMOTE_PROBE)
        self.assertNotIn('observation_loss_grace_seconds',self.cfg)
        self.assertNotIn('offline_after_failures',self.cfg)
    def test_disk_failure_keeps_sample_time_and_marks_stale_until_success(self):
        with mock.patch("server.now_ts",return_value=100):
            self.store.update_disk("test",{"filesystems":[],"users":[],"errors":[]})
        with mock.patch("server.now_ts",return_value=200):self.store.mark_disk_error("test","timeout")
        h=self.snapshot(201)
        self.assertTrue(h["disk_stale"]);self.assertEqual(h["disk_updated_at"],server.iso(100))
        with mock.patch("server.now_ts",return_value=220):self.store.update_disk("test",{})
        self.assertFalse(self.snapshot(221)["disk_stale"])
