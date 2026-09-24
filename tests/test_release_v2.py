"""Behavioral regressions retained by the canonical v2 release."""
import ast
import csv
import http.client
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import server
from gpu_watch.artificial_analysis import ArtificialAnalysisIndex
from gpu_watch.security import parse_allowed_networks


def probe_function(name):
    nodes = [n for n in ast.parse(server.REMOTE_PROBE).body
             if isinstance(n, ast.FunctionDef) and n.name in {"to_int", "parse_csv", name}]
    scope = {"csv": csv, "re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<probe-fixture>", "exec"), scope)
    return scope[name]


class DeviceEvidenceTests(unittest.TestCase):
    def test_gpu_query_has_no_per_device_health_classification(self):
        self.assertNotIn('nvml_health',server.REMOTE_PROBE)
        self.assertNotIn('cuInit',server.REMOTE_PROBE)

    def test_unreadable_memory_cannot_be_published_as_free(self):
        parse = probe_function("parse_gpus")
        for used, total in [("N/A", "24576"), ("0", "N/A"), ("0", "0"), ("-1", "24576")]:
            with self.subTest(used=used, total=total):
                gpus, _ = parse(f"0, GPU, GPU-uuid, 0, {used}, {total}, 30, 560.35.03")
                self.assertEqual(gpus, [])
        self.assertEqual(len(parse("0, GPU, GPU-uuid, N/A, 0, 24576, N/A, 560.35.03")[0]), 1)

    def test_multi_gpu_host_failure_freezes_entire_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=server.Store(Path(tmp)/'test.sqlite3');cfg=server.load_config()
            cfg['hosts']=[{'name':'test','lab':'nll','expected_gpu_count':8}]
            def publish(ts,failed=False):
                gpus=[(dict(index=i,uuid='GPU-'+str(i)*36,name='GPU',memory_total=49140,
                            memory_used=15,utilization=0,temperature=30,processes=[]),False)
                      for i in range(8) if not failed or i!=3]
                with mock.patch('server.now_ts',return_value=ts):
                    store.apply_host_payload('test','test',gpus,expected_gpu_indices=set(range(8)),
                        gpu_errors={3:'NVML handle query failed'} if failed else {},max_observation_gap_seconds=120)
            publish(100);publish(110);publish(120,True);publish(130,True)
            with mock.patch('server.now_ts',return_value=140):h=store.snapshot(cfg)['hosts'][0]
            self.assertEqual(h['availability_state'],'down')
            self.assertTrue(all(not g['available'] for g in h['gpus']))
            self.assertEqual(h['recent_capacity']['observed_gpu_seconds'],80)
            publish(150);publish(160)
            events=[e for e in reversed(store.events()['events']) if e['event'].startswith('host_')]
            self.assertEqual([e['event'] for e in events],['host_down','host_recovered'])
            self.assertTrue(all(e['gpu_index'] is None for e in events))


class StaticBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.static = self.root / "static"; self.static.mkdir()
        (self.static / "app.js").write_text("/* asset */")
        (self.static / "operator-note.txt").write_text("PRIVATE-FIXTURE")
        (self.root / "private.txt").write_text("PRIVATE-FIXTURE")
        (self.static / "flag-icons").mkdir()
        (self.static / "flag-icons/us.svg").write_text("<svg/>")
        patch = mock.patch("server.STATIC_DIR", self.static); patch.start(); self.addCleanup(patch.stop)
        config = dict(allowed_networks=parse_allowed_networks("127.0.0.0/8"),
                      trusted_proxy_networks=parse_allowed_networks("127.0.0.0/8"), http_read_timeout_seconds=2)
        handler = type("FixtureHandler", (server.Handler,), {"config":config, "log_message":lambda *a:None})
        self.httpd = server.DashboardHTTPServer(("127.0.0.1",0), handler)
        self.port = self.httpd.server_address[1]; config["allowed_hosts"] = {f"127.0.0.1:{self.port}"}
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.worker.join(3)

    def get(self, path, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1",self.port,timeout=3)
        try:
            conn.request(method,path); response=conn.getresponse(); return response.status,response.read()
        finally: conn.close()

    def test_only_explicit_assets_are_served_for_get_and_head(self):
        for method in ["GET", "HEAD"]:
            for path in ["/app.js?v=audit", "/flag-icons/us.svg"]:
                with self.subTest(method=method,path=path): self.assertEqual(self.get(path,method)[0],200)
            for path in ["/operator-note.txt", "/flag-icons/", "/../private.txt", "/%2e%2e/private.txt", "/flag-icons/../operator-note.txt"]:
                with self.subTest(method=method,path=path):
                    status,body=self.get(path,method); self.assertEqual(status,404); self.assertNotIn(b"PRIVATE-FIXTURE",body)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_allowlisted_filename_cannot_follow_symlink_outside_static(self):
        (self.static / "app.js").unlink(); (self.static / "app.js").symlink_to(self.root / "private.txt")
        self.assertEqual(self.get("/app.js")[0],404)
        (self.static / "flag-icons/us.svg").unlink(); (self.static / "flag-icons").rmdir()
        (self.root / "outside").mkdir(); (self.root / "outside/us.svg").write_text("PRIVATE-FIXTURE")
        (self.static / "flag-icons").symlink_to(self.root / "outside", target_is_directory=True)
        self.assertEqual(self.get("/flag-icons/us.svg")[0],404)


class RetryAfterTests(unittest.TestCase):
    def test_numeric_and_http_date_retry_after(self):
        parse=ArtificialAnalysisIndex._retry_after
        self.assertEqual(parse({"Retry-After":"3600"},now=0),3600)
        self.assertEqual(parse({"Retry-After":"Thu, 01 Jan 1970 02:00:00 GMT"},now=3600),3600)
        self.assertEqual(parse({"Retry-After":"Thu, 01 Jan 1970 00:00:00 GMT"},now=1),0)
        self.assertEqual(parse({"Retry-After":"172800"},now=0),86400)
        for value in ["nan","inf","-1","bad","Wed, 99 Jan 2026 00:00:00 GMT"]:
            with self.subTest(value=value): self.assertIsNone(parse({"Retry-After":value},now=0))


class DiskMountBoundaryTests(unittest.TestCase):
    def test_system_mount_filters_match_path_components_not_prefixes(self):
        tree = ast.parse(server.REMOTE_PROBE)
        names = {"is_user_data_mount", "is_capacity_filesystem"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"SYSTEM_FS_TYPES": {"tmpfs", "overlay", "vfat"}}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<mount-filter>", "exec"), namespace)
        for name in names:
            classify = namespace[name]
            for mount in ("/bootstrap", "/system-data", "/projects", "/runtime-data", "/devices", "/snapshots"):
                with self.subTest(function=name, mount=mount):
                    self.assertTrue(classify(mount, "ext4"))
            for mount in ("/boot", "/boot/efi", "/sys", "/proc", "/run", "/dev", "/snap/core"):
                self.assertFalse(classify(mount, "ext4"))
        self.assertFalse(namespace["is_capacity_filesystem"]("/var/lib/docker/overlay2/x", "ext4"))
        self.assertTrue(namespace["is_capacity_filesystem"]("/var/lib/docker/overlay2-backup", "ext4"))
