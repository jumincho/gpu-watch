import ast
import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
import server
from gpu_watch.temporary_driver import validate_temporary_driver_fallback

BOOT = "7688cb16-639f-4b55-adbf-de1099ebd608"
CONFIG = {"boot_id": BOOT, "kernel_version": "580.173.02"}

class TemporaryDriverConfigTests(unittest.TestCase):
    def test_configuration_is_closed_and_not_a_command_or_path(self):
        validate_temporary_driver_fallback(None)
        validate_temporary_driver_fallback(CONFIG)
        for value in [{}, [], "bad", {**CONFIG, "path": "/tmp/lib"},
                      {**CONFIG, "boot_id": BOOT + ";id"}, {**CONFIG, "kernel_version": "580x173x02"},
                      {**CONFIG, "boot_id": 1}, {**CONFIG, "kernel_version": None}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_temporary_driver_fallback(value)

    def test_transport_is_scoped_to_configured_host_and_bounded_on_windows(self):
        cfg = server.load_config()
        collector = server.Collector(None, cfg)
        try:
            for host in cfg["hosts"]:
                command, _ = collector._probe_invocation(host, False)
                self.assertLess(len(command), 30000)
                self.assertEqual("--temporary-driver-b64=" in command, bool(host.get("temporary_driver_fallback")))
        finally:
            collector.stop()

    def test_normal_query_is_attempted_before_any_fallback(self):
        source = server.REMOTE_PROBE
        begin = source.index("rc, out, err = run(gpu_query_command(), timeout=remote_cmd_timeout)")
        end = source.index("gpus, driver_versions = parse_gpus(out)", begin)
        run = mock.Mock(side_effect=[(18, "mismatch", ""), (0, "GPU data", "")])
        prefix = ["/usr/bin/env", "LD_PRELOAD=", "LD_LIBRARY_PATH=/private"]
        ns = {"run": run, "remote_cmd_timeout": 8, "nvidia_smi_command": ["nvidia-smi"],
              "temporary_driver_prefix": mock.Mock(return_value=prefix)}
        ns["gpu_query_command"] = lambda: ns["nvidia_smi_command"] + ["query"]
        exec(source[begin:end], ns)
        self.assertEqual(run.call_args_list[0].args[0], ["nvidia-smi", "query"])
        self.assertEqual(run.call_args_list[1].args[0], prefix + ["nvidia-smi", "query"])
        self.assertNotIn("docker", run.call_args_list[1].args[0])
        self.assertEqual(ns["out"], "GPU data")
        run.reset_mock(side_effect=True); run.return_value = (0, "native", "")
        ns["nvidia_smi_command"] = ["nvidia-smi"]
        ns["temporary_driver_prefix"].return_value = []
        exec(source[begin:end], ns)
        run.assert_called_once()
        self.assertEqual(ns["out"], "native")

@unittest.skipUnless(os.name == "posix", "Linux remote-library ownership contract")
class TemporaryDriverGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.directory = self.home / ".cache" / ("gpu-watch-driver-" + BOOT)
        self.directory.mkdir(parents=True, mode=0o700)
        for name in ("libnvidia-ml.so.1", "libcuda.so.1"):
            p = self.directory / name
            p.write_bytes(bytes.fromhex("7f454c46") + b"unit-test-library")
            p.chmod(0o400)
        self.boot = BOOT
        self.version = "580.173.02"
        self.config = dict(CONFIG)
        node = next(n for n in ast.parse(server.REMOTE_PROBE).body
                    if isinstance(n, ast.FunctionDef) and n.name == "temporary_driver_prefix")
        ns = dict(os=os, re=re, pwd=types.SimpleNamespace(getpwuid=lambda uid: types.SimpleNamespace(pw_dir=str(self.home))),
                  arg_json_b64=lambda *args: self.config)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<driver-guard>", "exec"), ns)
        self.prefix = ns["temporary_driver_prefix"]
        real_read = Path.read_text
        def read(p, *args, **kwargs):
            if str(p) == "/proc/sys/kernel/random/boot_id":
                return self.boot
            if str(p) == "/proc/driver/nvidia/version":
                return "NVRM version: NVIDIA UNIX x86_64 Kernel Module  " + self.version
            return real_read(p, *args, **kwargs)
        patcher = mock.patch.object(Path, "read_text", read)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_only_exact_mismatch_uses_owned_private_libraries(self):
        expected = ["/usr/bin/env", "LD_PRELOAD=", "LD_LIBRARY_PATH=" + str(self.directory)]
        self.assertEqual(self.prefix(18), expected)
        for code in (0, 9, 15, 124, 999):
            with self.subTest(code=code):
                self.assertEqual(self.prefix(code), [])

    def test_reboot_or_loaded_driver_change_disables_workaround(self):
        self.boot = "11111111-2222-3333-4444-555555555555"
        self.assertEqual(self.prefix(18), [])
        self.boot = BOOT
        self.version = "580.178.04"
        self.assertEqual(self.prefix(18), [])

    def test_missing_untrusted_or_non_library_files_are_rejected(self):
        p = self.directory / "libcuda.so.1"
        p.chmod(0o644)
        self.assertEqual(self.prefix(18), [])
        p.chmod(0o600); p.write_bytes(b"not a driver"); p.chmod(0o400)
        self.assertEqual(self.prefix(18), [])
        p.unlink()
        self.assertEqual(self.prefix(18), [])
        p.symlink_to(self.directory / "libnvidia-ml.so.1")
        self.assertEqual(self.prefix(18), [])

    def test_public_or_symlinked_directory_is_rejected(self):
        self.directory.chmod(0o755)
        self.assertEqual(self.prefix(18), [])
        self.directory.chmod(0o700)
        moved = self.directory.with_name("moved")
        self.directory.rename(moved)
        self.directory.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.prefix(18), [])

    def test_absent_or_malformed_remote_configuration_is_disabled(self):
        for value in (None, {}, [], {**CONFIG, "kernel_version": "bad"}, {**CONFIG, "directory": "/tmp"}):
            with self.subTest(value=value):
                self.config = value
                self.assertEqual(self.prefix(18), [])
