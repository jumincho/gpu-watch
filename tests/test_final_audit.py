"""Behavioral regressions found during the v2 final review."""
import ast
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, closing
from pathlib import Path
from unittest import mock

import server


class FinalAuditTests(unittest.TestCase):
    def test_disk_probe_succeeds_without_invoking_nvidia_or_procfs(self):
        calls = []
        class Process:
            returncode = 0
            def __init__(self, command, **kwargs):
                calls.append(command)
                if command[0] != "df":
                    raise AssertionError("Disk-only collection invoked " + command[0])
            def communicate(self, timeout=None):
                return b"Filesystem Type 1B-blocks Used Available Use% Mounted on\n/dev/sda ext4 100000 40000 60000 40% /\n", b""
        output = io.StringIO()
        with mock.patch.dict("sys.modules", {"pwd": mock.Mock()}), mock.patch("sys.argv", ["probe", "--disk"]), mock.patch("subprocess.Popen", Process), mock.patch("os.path.isdir", return_value=False), redirect_stdout(output):
            exec(compile(server.REMOTE_DISK_PROBE, "<disk-only>", "exec"), {})
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["disk"]["filesystems"][0]["used_bytes"], 40000)
        self.assertEqual(len(calls), 1)

    def test_df_failure_is_explicit_and_never_publishes_empty_capacity_as_success(self):
        class Process:
            returncode = 1
            def __init__(self, command, **kwargs):
                pass
            def communicate(self, timeout=None):
                return b"", b"filesystem unavailable"
        output = io.StringIO()
        with mock.patch.dict("sys.modules", {"pwd": mock.Mock()}), mock.patch("sys.argv", ["probe", "--disk"]), mock.patch("subprocess.Popen", Process), mock.patch("os.path.isdir", return_value=False), redirect_stdout(output):
            exec(compile(server.REMOTE_DISK_PROBE, "<disk-failure>", "exec"), {})
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_first_disk_failure_is_recorded_and_later_failure_preserves_good_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = server.Store(Path(temporary) / "data.sqlite3")
            with mock.patch("server.now_ts", return_value=100):
                store.mark_disk_error("test", "disk capacity query failed")
            with closing(store.connect()) as conn:
                first = dict(conn.execute("select * from host_disk").fetchone())
            self.assertEqual(first["updated_ts"], 0)
            self.assertEqual(first["last_attempt_ts"], 100)
            disk = {"filesystems": [{"filesystem": "/dev/a", "mount": "/", "type": "ext4",
                    "total_bytes": 100, "used_bytes": 20, "available_bytes": 80, "use_percent": 20}],
                    "users": [], "errors": []}
            with mock.patch("server.now_ts", return_value=200):
                store.update_disk("test", disk)
            with mock.patch("server.now_ts", return_value=300):
                store.mark_disk_error("test", "disk capacity query failed")
            with closing(store.connect()) as conn:
                last = dict(conn.execute("select * from host_disk").fetchone())
            self.assertEqual(last["updated_ts"], 200)
            self.assertEqual(json.loads(last["snapshot_json"])["filesystems"][0]["used_bytes"], 20)
            self.assertTrue(server.disk_collection_status(last, 300, 1800)["disk_stale"])

    def test_bind_alias_user_roots_are_scanned_once(self):
        tree = ast.parse(server.REMOTE_PROBE)
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "user_roots"]
        namespace = {"os": os, "STANDARD_USER_ROOTS": ["/home", "/alias"],
                     "is_user_data_mount": lambda *args: False}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<roots>", "exec"), namespace)
        metadata = mock.Mock(st_dev=10, st_ino=20)
        with mock.patch("os.path.isdir", return_value=True), mock.patch("os.path.realpath", side_effect=lambda p: p), mock.patch("os.stat", return_value=metadata):
            self.assertEqual(namespace["user_roots"]([]), ["/home"])

    def test_failed_bulk_query_stops_without_extra_remote_work(self):
        branch=next(n for n in ast.parse(server.REMOTE_PROBE).body if isinstance(n,ast.If) and ast.unparse(n.test)=='not bulk_complete')
        run=mock.Mock(side_effect=AssertionError('unexpected fallback'))
        namespace={'bulk_complete':False,'rc':255,'err':'query failed','out':'',
                   'first_error':lambda *a:a[0],'json':json,'run':run}
        output=io.StringIO()
        with redirect_stdout(output),self.assertRaises(SystemExit):
            exec(compile(ast.Module(body=[branch],type_ignores=[]),'<bulk-failure>','exec'),namespace)
        self.assertEqual(json.loads(output.getvalue()),{'ok':False,'error':'query failed'})
        run.assert_not_called()
