import base64
import builtins
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

import server


class CollectorProbeTests(unittest.TestCase):
    def test_remote_probe_builds_only_allowlisted_command_summaries(self):
        compile(server.REMOTE_PROBE, "<remote-probe>", "exec")
        self.assertNotIn("audit(", server.REMOTE_PROBE)
        self.assertIn("def command_summary_for_pid", server.REMOTE_PROBE)
        self.assertIn('return "%s -m %s" % (executable, module)', server.REMOTE_PROBE)
        self.assertIn('return "%s %s" % (executable, basename)', server.REMOTE_PROBE)
        self.assertNotIn('"command_summary": " ".join', server.REMOTE_PROBE)
        self.assertIn('uid = int(os.stat("/proc/%s" % pid).st_uid)', server.REMOTE_PROBE)
        self.assertIn('result["user"] = pwd.getpwuid(uid).pw_name', server.REMOTE_PROBE)
        self.assertIn('result["user"] = "uid:%s" % uid', server.REMOTE_PROBE)
        self.assertIn('if not meta or not meta.get("user"):\n        continue', server.REMOTE_PROBE)
        self.assertIn('user = meta.get("user", "")', server.REMOTE_PROBE)
        self.assertNotIn('meta.get("user", "?")', server.REMOTE_PROBE)
        self.assertNotIn("nvidia_smi_docker_container", server.REMOTE_PROBE)
        self.assertNotIn("--nvidia-smi-docker-b64", server.REMOTE_PROBE)
        self.assertIn("def has_missing_busy_processes():", server.REMOTE_PROBE)
        self.assertIn("if has_missing_busy_processes():", server.REMOTE_PROBE)
        self.assertIn("merge_compute_apps(parse_compute_apps(retry_out))", server.REMOTE_PROBE)
        self.assertIn(
            'if app["pid"] not in owners or not owners[app["pid"]].get("user")',
            server.REMOTE_PROBE,
        )
        self.assertIn("if missing_owner_pids:", server.REMOTE_PROBE)
        self.assertIn("verified_pairs = {", server.REMOTE_PROBE)
        self.assertIn('(app["pid"], app["gpu_uuid"]) in verified_pairs', server.REMOTE_PROBE)
        self.assertIn("driver_version", server.REMOTE_PROBE)
        self.assertIn("def gpu_query_command(selector=None):", server.REMOTE_PROBE)
        self.assertIn("def compute_query_command():", server.REMOTE_PROBE)
        self.assertNotIn('"gpu_errors": gpu_errors', server.REMOTE_PROBE)
        self.assertNotIn('nvidia_smi_command + ["--query-gpu=driver_version"', server.REMOTE_PROBE)
        self.assertIn(
            "merge_compute_apps(parse_compute_apps(owner_retry_out))",
            server.REMOTE_PROBE,
        )
        self.assertIn("def process_start_identity(pid):", server.REMOTE_PROBE)
        self.assertIn("def resolve_process_metadata(pid):", server.REMOTE_PROBE)
        self.assertIn('metadata.get("_proc_start_ticks")', server.REMOTE_PROBE)
        self.assertIn('"start_identity": meta.get("_proc_start_ticks", "")', server.REMOTE_PROBE)
        self.assertLess(
            server.REMOTE_PROBE.index('command_summary = command_summary_for_pid(app["pid"], process_name)'),
            server.REMOTE_PROBE.index(
                'if process_start_identity(app["pid"]) != meta.get("_proc_start_ticks"):'
            ),
        )

    def _execute_remote_probe_with_pid_identity(self, final_identity, *, uid_failures=0):
        state = {
            "compute_queries": 0,
            "nvidia_smi_calls": 0,
            "proc_identity_reads": 0,
            "proc_owner_reads": 0,
        }

        class FakeStat:
            st_uid = 1001

        class FakePopen:
            def __init__(self, command, stdout=None, stderr=None):
                self.command = command
                self.returncode = 0

            def communicate(self, timeout=None):
                command = self.command
                if command[0] == "nvidia-smi":
                    state["nvidia_smi_calls"] += 1
                    if any(str(argument).startswith("--query-gpu=") for argument in command):
                        output = "0, NVIDIA Test, GPU-AAA, 75, 1024, 8192, 50, 580.0\n"
                    else:
                        state["compute_queries"] += 1
                        output = "123, GPU-AAA, 1024, python\n"
                elif command[0] == "ps":
                    output = "123 alice Mon Aug 24 12:00:00 2026 python\n"
                elif command[0] == "hostname":
                    output = "mock-host\n"
                else:  # pragma: no cover - fails with a useful command dump
                    raise AssertionError(command)
                return output.encode("utf-8"), b""

            def kill(self):
                return None

        original_open = builtins.open
        original_stat = os.stat

        def fake_open(path, mode="r", *args, **kwargs):
            path_text = str(path)
            if path_text == "/proc/123/status":
                if state["proc_owner_reads"] <= uid_failures:
                    raise PermissionError("mock transient procfs denial")
                return io.StringIO("Uid:\t1001\t1001\t1001\t1001\n")
            if path_text == "/proc/123/stat":
                state["proc_identity_reads"] += 1
                identity = "1000" if state["proc_identity_reads"] == 1 else final_identity
                if identity is None:
                    raise PermissionError("mock procfs denial")
                # fields[19] after the final ')' is Linux proc stat field 22.
                suffix = ["S", *(["0"] * 18), str(identity)]
                return io.StringIO(f"123 (python)worker) {' '.join(suffix)}\n")
            if path_text == "/proc/123/comm":
                return io.StringIO("python\n")
            if path_text == "/proc/123/cmdline":
                return io.BytesIO(b"python\0train.py\0")
            if path_text == "/proc/123/cgroup":
                return io.StringIO("")
            return original_open(path, mode, *args, **kwargs)

        def fake_stat(path, *args, **kwargs):
            if str(path) == "/proc/123":
                state["proc_owner_reads"] += 1
                if state["proc_owner_reads"] <= uid_failures:
                    raise PermissionError("mock transient procfs denial")
                return FakeStat()
            return original_stat(path, *args, **kwargs)

        pwd_module = types.ModuleType("pwd")
        pwd_module.getpwuid = lambda _uid: types.SimpleNamespace(pw_name="alice")
        output = io.StringIO()
        with (
            mock.patch.dict(sys.modules, {"pwd": pwd_module}),
            mock.patch.object(subprocess, "Popen", FakePopen),
            mock.patch.object(builtins, "open", fake_open),
            mock.patch.object(os, "stat", fake_stat),
            mock.patch.object(sys, "argv", ["remote-probe"]),
            contextlib.redirect_stdout(output),
        ):
            exec(compile(server.REMOTE_PROBE, "<remote-probe-test>", "exec"), {})
        payload = json.loads(output.getvalue().splitlines()[-1])
        return payload, state

    def test_remote_probe_revalidates_pid_generation_after_final_gpu_pair_check(self):
        for final_identity, expected_users in (
            ("1000", ["alice"]),
            ("2000", []),
            (None, []),
        ):
            with self.subTest(final_identity=final_identity):
                payload, state = self._execute_remote_probe_with_pid_identity(final_identity)
                self.assertTrue(payload["ok"])
                self.assertEqual(
                    [process["user"] for process in payload["gpus"][0]["processes"]],
                    expected_users,
                )
                self.assertNotIn(
                    "_proc_start_ticks",
                    json.dumps(payload, ensure_ascii=False),
                )
                self.assertEqual(state["nvidia_smi_calls"], 3)
                self.assertEqual(state["compute_queries"], 2)
                self.assertEqual(state["proc_identity_reads"], 2)

    def test_remote_probe_retries_transient_owner_read_for_same_pid_generation(self):
        payload, state = self._execute_remote_probe_with_pid_identity(
            "1000",
            uid_failures=1,
        )
        self.assertEqual(
            [process["user"] for process in payload["gpus"][0]["processes"]],
            ["alice"],
        )
        self.assertEqual(state["proc_owner_reads"], 2)
        self.assertEqual(state["proc_identity_reads"], 3)
        self.assertEqual(state["nvidia_smi_calls"], 3)

    def test_remote_probe_fails_whole_host_without_gpu_subset_or_cuda_probes(self):
        for rc,out in [(255,b""),(0,b"0, GPU, GPU-AAA, 0, 0, 8192, 40, 580.0\n"),
                       (0,b"0, GPU, GPU-AAA, 0, N/A, 8192, 40, 580.0\n")]:
            calls=[]
            class FakePopen:
                def __init__(self,command,stdout=None,stderr=None):
                    self.returncode=rc;calls.append(command)
                    if command[0]!='nvidia-smi':raise AssertionError(command)
                def communicate(self,timeout=None):return out,b"GPU query failed" if rc else b""
                def kill(self):pass
            output=io.StringIO()
            with self.subTest(rc=rc,out=out),mock.patch.dict(sys.modules,{'pwd':types.ModuleType('pwd')}),mock.patch.object(subprocess,'Popen',FakePopen),mock.patch.object(sys,'argv',['probe','--expected-gpu-count=2']),contextlib.redirect_stdout(output),self.assertRaises(SystemExit):
                exec(compile(server.REMOTE_PROBE,'<binary-host-probe>','exec'),{})
            payload=json.loads(output.getvalue().splitlines()[-1])
            self.assertFalse(payload['ok']);self.assertEqual(len(calls),1)
            self.assertNotIn('-i',calls[0]);self.assertNotIn('usability',payload)

    def test_remote_probe_keeps_host_failed_when_no_gpu_is_usable(self):
        class FakePopen:
            def __init__(self, command, stdout=None, stderr=None):
                self.command = command
                self.returncode = 0

            def communicate(self, timeout=None):
                if self.command[0] == "nvidia-smi":
                    self.returncode = 255
                    return b"", b"NVIDIA driver unavailable\n"
                raise AssertionError(self.command)

            def kill(self):
                return None

        pwd_module = types.ModuleType("pwd")
        output = io.StringIO()
        with (
            mock.patch.dict(sys.modules, {"pwd": pwd_module}),
            mock.patch.object(subprocess, "Popen", FakePopen),
            mock.patch.object(sys, "argv", ["remote-probe", "--expected-gpu-count=2"]),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit):
                exec(compile(server.REMOTE_PROBE, "<remote-probe-all-failed-test>", "exec"), {})
        payload = json.loads(output.getvalue().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertIn("driver unavailable",payload["error"])
        self.assertNotIn("gpus",payload)

    def test_probe_invocation_uses_only_direct_nvidia_smi(self):
        collector = self.make_collector()
        try:
            command, _timeout = collector._probe_invocation(
                {
                    "name": "octans",
                    "lab": "nll",
                },
                False,
            )
            self.assertNotIn("--nvidia-smi-docker-b64", command)
            self.assertNotIn("docker-gpu-process-scan", command)
            self.assertIn("--busy-memory-threshold-mib=500", command)
            self.assertIn("--busy-utilization-threshold-percent=10", command)
            self.assertIn("--expected-gpu-count=0", command)
        finally:
            collector.stop()

    def test_probe_invocation_fits_the_windows_createprocess_limit(self):
        collector = self.make_collector()
        try:
            command, _timeout = collector._probe_invocation(
                {
                    "name": "lab22",
                    "lab": "nlp",
                    "ssh_host": "203.0.113.192",
                    "ssh_user": "gpuwatch",
                    "ssh_port": 22,
                    "disk_user_paths": [{"user": "gpuwatch", "path": "/tmp/gpuwatch"}],
                },
                True,
            )
            encoded = command.split("echo ", 1)[1].split(" | base64 -d", 1)[0]
            self.assertEqual(
                zlib.decompress(base64.b64decode(encoded)).decode("utf-8"),
                server.REMOTE_DISK_PROBE,
            )
            self.assertIn("zlib.decompress(sys.stdin.buffer.read())", command)
            with mock.patch(
                "server.trusted_ssh_executable",
                return_value=r"C:\Windows\System32\OpenSSH\ssh.exe",
            ):
                ssh_command = collector._ssh_command(
                    {
                        "name": "lab22",
                        "lab": "nlp",
                        "ssh_host": "203.0.113.192",
                        "ssh_user": "gpuwatch",
                        "ssh_port": 22,
                    },
                    command,
                    None,
                )
            self.assertLess(len(subprocess.list2cmdline(ssh_command)), 32_767)
        finally:
            collector.stop()

    def make_collector(self):
        return server.Collector(mock.Mock(), {
            "disk_probe_workers": 1,
            "remote_probe_command_timeout_seconds": 12,
            "docker_usage_timeout_seconds": 30,
            "ssh_gpu_probe_timeout_seconds": 45,
            "ssh_disk_probe_timeout_seconds": 720,
            "disk_du_timeout_seconds": 600,
            "ssh_timeout_seconds": 8,
            "offline_after_failures": 3,
            "poll_interval_seconds": 10,
            "usage_gap_seconds": 60,
            "busy_memory_threshold_mib": 500,
            "busy_utilization_threshold_percent": 10,
        })

    def test_disk_probe_uses_dedicated_docker_timeout(self):
        collector = self.make_collector()
        try:
            command, timeout = collector._probe_invocation(
                {"name": "cygnus", "lab": "nll", "collect_docker_usage": True},
                True,
            )
            self.assertIn("--remote-timeout=12", command)
            self.assertIn("--du-timeout=600", command)
            self.assertIn("--docker-usage --docker-timeout=30", command)
            self.assertEqual(timeout, 720.0)
            self.assertIn("docker_usage_timeout = max(10, arg_value(\"--docker-timeout=\", 30))", server.REMOTE_PROBE)
            self.assertIn("timeout=docker_usage_timeout", server.REMOTE_PROBE)
        finally:
            collector.stop()

    def test_probe_passes_timeout_to_bounded_reader_not_popen(self):
        collector = self.make_collector()
        process = mock.Mock()
        process.returncode = 0
        try:
            with (
                mock.patch("server.trusted_ssh_executable", return_value="/trusted/ssh"),
                mock.patch("server.subprocess.Popen", return_value=process) as popen,
                mock.patch(
                    "server._communicate_bounded",
                    return_value=(json.dumps({"ok": True, "gpus": []}), ""),
                ) as communicate,
            ):
                payload = collector.probe_host({
                    "name": "test-host",
                    "lab": "nll",
                    "ssh_host": "example.invalid",
                    "ssh_user": "gpuwatch",
                })
            self.assertTrue(payload["ok"])
            self.assertNotIn("timeout", popen.call_args.kwargs)
            communicate.assert_called_once_with(
                process,
                timeout=45.0,
                max_output_bytes=server.MAX_REMOTE_PROBE_OUTPUT_BYTES,
                stop_event=collector.stop_event,
            )
            self.assertEqual(collector.running_processes, set())
        finally:
            collector.stop()

    def test_probe_redacts_remote_error_and_hides_bad_json_output(self):
        collector = self.make_collector()
        try:
            failed = mock.Mock()
            failed.returncode = 255
            with (
                mock.patch("server.trusted_ssh_executable", return_value="/trusted/ssh"),
                mock.patch("server.subprocess.Popen", return_value=failed),
                mock.patch(
                    "server._communicate_bounded",
                    return_value=("", "Authorization: Bearer top-secret trailing-value\nconnection failed"),
                ),
            ):
                payload = collector.probe_host({"name": "test-host", "ssh_host": "example.invalid"})
            self.assertNotIn("top-secret", payload["error"])
            self.assertNotIn("Bearer", payload["error"])
            self.assertNotIn("trailing-value", payload["error"])
            self.assertIn("[redacted]", payload["error"])

            malformed = mock.Mock()
            malformed.returncode = 0
            with (
                mock.patch("server.trusted_ssh_executable", return_value="/trusted/ssh"),
                mock.patch("server.subprocess.Popen", return_value=malformed),
                mock.patch(
                    "server._communicate_bounded",
                    return_value=("token=top-secret\nnot json", ""),
                ),
            ):
                payload = collector.probe_host({"name": "test-host", "ssh_host": "example.invalid"})
            self.assertEqual(payload["error"], "bad json: JSONDecodeError")
            self.assertNotIn("top-secret", payload["error"])
        finally:
            collector.stop()

    def test_probe_maps_bounded_output_failure(self):
        collector = self.make_collector()
        process = mock.Mock()
        process.returncode = 0
        try:
            with (
                mock.patch("server.trusted_ssh_executable", return_value="/trusted/ssh"),
                mock.patch("server.subprocess.Popen", return_value=process),
                mock.patch(
                    "server._communicate_bounded",
                    side_effect=server.ProcessOutputLimitExceeded,
                ),
            ):
                payload = collector.probe_host({"name": "test-host", "ssh_host": "example.invalid"})
            self.assertEqual(payload, {"ok": False, "error": "ssh probe output exceeded limit"})
        finally:
            collector.stop()

    def test_bounded_reader_drains_both_streams_and_enforces_combined_limit(self):
        success_script = (
            "import sys\n"
            "chunk=b'x'*4096\n"
            "for _ in range(32):\n"
            " sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()\n"
            " sys.stderr.buffer.write(chunk); sys.stderr.buffer.flush()\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", success_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=server.SUBPROCESS_CREATIONFLAGS,
        )
        stdout, stderr = server._communicate_bounded(
            process, timeout=5, max_output_bytes=512 * 1024
        )
        self.assertEqual(len(stdout), 128 * 1024)
        self.assertEqual(len(stderr), 128 * 1024)

        oversized = subprocess.Popen(
            [sys.executable, "-c", success_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=server.SUBPROCESS_CREATIONFLAGS,
        )
        with self.assertRaises(server.ProcessOutputLimitExceeded):
            server._communicate_bounded(
                oversized, timeout=5, max_output_bytes=64 * 1024
            )
        self.assertIsNotNone(oversized.poll())

    def test_bounded_reader_timeout_and_stop_terminate_child(self):
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=server.SUBPROCESS_CREATIONFLAGS,
        )
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            server._communicate_bounded(sleeper, timeout=0.2, max_output_bytes=1024)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(sleeper.poll())

        stopped = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=server.SUBPROCESS_CREATIONFLAGS,
        )
        stop_event = threading.Event()
        timer = threading.Timer(0.1, stop_event.set)
        timer.start()
        try:
            with self.assertRaises(server.ProcessStopped):
                server._communicate_bounded(
                    stopped, timeout=5, max_output_bytes=1024, stop_event=stop_event
                )
        finally:
            timer.cancel()
        self.assertIsNotNone(stopped.poll())

    def test_handle_payload_rejects_duplicate_and_excessive_gpu_lists(self):
        collector = self.make_collector()
        gpu = {
            "index": 0,
            "name": "GPU",
            "uuid": "GPU-test",
            "utilization": 0,
            "memory_used": 0,
            "memory_total": 8192,
            "temperature": 40,
            "processes": [],
        }
        try:
            with self.assertRaisesRegex(ValueError, "duplicate GPU index"):
                collector.handle_payload(
                    {"name": "host", "expected_gpu_count": 2},
                    {"ok": True, "gpus": [gpu, gpu]},
                )
            with self.assertRaisesRegex(ValueError, "exceeds host limit"):
                collector.handle_payload(
                    {"name": "host"},
                    {"ok": True, "gpus": [{**gpu, "index": index} for index in range(129)]},
                )
        finally:
            collector.stop()

    def test_handle_payload_rejects_incomplete_or_noncontiguous_inventory(self):
        collector = self.make_collector()
        gpu = {
            "index": 0,
            "name": "GPU",
            "uuid": "GPU-test",
            "utilization": 0,
            "memory_used": 0,
            "memory_total": 8192,
            "temperature": 40,
            "processes": [],
        }
        host = {"name": "host", "expected_gpu_count": 2}
        try:
            for gpus, message in (
                ([], "does not match expected inventory"),
                ([gpu], "does not match expected inventory"),
                ([gpu, {**gpu, "index": 2, "uuid": "GPU-test-2"}], "indices do not match"),
            ):
                with self.subTest(gpus=gpus), self.assertRaisesRegex(ValueError, message):
                    collector.handle_payload(host, {"ok": True, "gpus": gpus})
            collector.store.apply_host_payload.assert_not_called()
        finally:
            collector.stop()

    def test_partial_inventory_fails_host_and_redacts_error(self):
        collector=self.make_collector()
        try:
            with mock.patch('server.audit'):
                collector.handle_payload({'name':'host','expected_gpu_count':2},
                    {'ok':True,'gpus':[],'gpu_errors':{'1':'token=top-secret GPU failed'}})
            collector.store.apply_host_payload.assert_not_called()
            args=collector.store.mark_host.call_args
            self.assertEqual(args.args,('host','host',False))
            self.assertNotIn('top-secret',args.kwargs['error'])
            self.assertIn('[redacted]',args.kwargs['error'])
        finally:collector.stop()

    def test_remote_payload_error_is_redacted_before_storage(self):
        collector = self.make_collector()
        try:
            collector.handle_payload(
                {"name": "test-host", "label": "Test host"},
                {"ok": False, "error": "token=top-secret\nremote failure"},
            )
            stored_error = collector.store.mark_host.call_args.kwargs["error"]
            self.assertNotIn("top-secret", stored_error)
            self.assertNotIn("\n", stored_error)
            self.assertIn("[redacted]", stored_error)
        finally:
            collector.stop()

    def test_config_validation_rejects_fractional_and_unsafe_runtime_values(self):
        current = server.load_config()
        self.assertEqual(sum(host["expected_gpu_count"] for host in current["hosts"]), 82)
        bad_workers = copy.deepcopy(current)
        bad_workers["collector_workers"] = 1.5
        with self.assertRaises(ValueError):
            server.validate_config(bad_workers)

        bad_port = copy.deepcopy(current)
        bad_port["hosts"][0]["ssh_port"] = 22.5
        with self.assertRaises(ValueError):
            server.validate_config(bad_port)

        for invalid_count in (None, True, 0, 2.5, 129):
            bad_inventory = copy.deepcopy(current)
            if invalid_count is None:
                bad_inventory["hosts"][0].pop("expected_gpu_count")
            else:
                bad_inventory["hosts"][0]["expected_gpu_count"] = invalid_count
            with self.subTest(invalid_count=invalid_count), self.assertRaises(ValueError):
                server.validate_config(bad_inventory)

        bad_poll = copy.deepcopy(current)
        bad_poll["disk_poll_interval_seconds"] = "not-a-number"
        with self.assertRaises(ValueError):
            server.validate_config(bad_poll)

        bad_docker_timeout = copy.deepcopy(current)
        bad_docker_timeout["docker_usage_timeout_seconds"] = 9
        with self.assertRaises(ValueError):
            server.validate_config(bad_docker_timeout)

        bad_lab_id = copy.deepcopy(current)
        bad_lab_id["labs"][0]["id"] = 123
        with self.assertRaises(ValueError):
            server.validate_config(bad_lab_id)

        bad_host_name = copy.deepcopy(current)
        bad_host_name["hosts"][0]["name"] = 123
        with self.assertRaises(ValueError):
            server.validate_config(bad_host_name)

        bad_worker_type = copy.deepcopy(current)
        bad_worker_type["collector_workers"] = True
        with self.assertRaises(ValueError):
            server.validate_config(bad_worker_type)

    def test_config_rejects_command_bearing_ssh_options(self):
        current = server.load_config()
        dangerous = [
            "ProxyCommand=sh -c id",
            "LocalCommand=sh -c id",
            "KnownHostsCommand=sh -c id",
            "RemoteCommand=sh -c id",
            "PermitLocalCommand=yes",
            "ServerAliveInterval=30",
        ]
        for option in dangerous:
            with self.subTest(option=option.split("=", 1)[0]):
                config = copy.deepcopy(current)
                config["hosts"][0]["ssh_options"] = [option]
                with self.assertRaisesRegex(ValueError, "unsafe SSH option"):
                    server.validate_config(config)

        self.assertEqual(
            server._validated_ssh_options([
                "PreferredAuthentications=password,keyboard-interactive",
                "PubkeyAuthentication=no",
            ], "host"),
            [
                "PreferredAuthentications=password,keyboard-interactive",
                "PubkeyAuthentication=no",
            ],
        )

    def test_config_has_no_legacy_nvidia_smi_docker_fallback(self):
        current = server.load_config()
        self.assertNotIn("docker_gpu_process_scan_limit", current)
        self.assertNotIn("docker_gpu_process_scan_budget_seconds", current)
        self.assertTrue(all(
            "nvidia_smi_docker_container" not in host
            for host in current["hosts"]
        ))
        self.assertNotIn("nvidia_smi_docker_container", server.REMOTE_PROBE)

    def test_password_file_is_authoritative_over_inherited_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            password_file = Path(temp_dir) / "ssh-password.txt"
            password_file.write_text("file-secret\n", encoding="utf-8")
            password_file.chmod(0o600)
            host = {
                "ssh_password_file": str(password_file),
                "ssh_password_env": "GPU_WATCH_TEST_STALE_PASSWORD",
            }
            with mock.patch.dict(
                "os.environ", {"GPU_WATCH_TEST_STALE_PASSWORD": "stale-env-secret"}
            ):
                password, source = server.Collector._probe_password(host)
                missing_password, missing_source = server.Collector._probe_password({
                    **host,
                    "ssh_password_file": str(Path(temp_dir) / "missing.txt"),
                })
            self.assertEqual(password, "file-secret")
            self.assertEqual(source, str(password_file))
            self.assertIsNone(missing_password)
            self.assertTrue(missing_source.endswith("missing.txt"))

    def test_probe_error_hides_private_key_paths(self):
        error = server.safe_probe_error(
            "Warning: Identity file /home/gpuwatch/.ssh/private-key not accessible"
        )
        self.assertIn("Identity file [redacted-path]", error)
        self.assertNotIn("/home/gpuwatch", error)
        permissions = server.safe_probe_error(
            "Permissions 0644 for '/home/gpuwatch/.ssh/private key' are too open"
        )
        self.assertEqual(permissions, "Permissions 0644 for [redacted-path]")
        self.assertNotIn("/home/gpuwatch", permissions)
        spaced = server.safe_probe_error(
            r"Identity file C:\Users\Jane Doe\.ssh\id key not accessible"
        )
        self.assertEqual(spaced, "Identity file [redacted-path]")
        unc = server.safe_probe_error(r"Identity file \\server\share\id not accessible")
        self.assertEqual(unc, "Identity file [redacted-path]")
        encoded = server.safe_probe_error("remote failed " + ("A" * 160))
        self.assertIn("[redacted-command]", encoded)
        self.assertNotIn("A" * 128, encoded)

    @unittest.skipUnless(os.name == "posix", "POSIX secret mode contract")
    def test_password_file_rejects_group_readable_or_symlinked_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            password_file = Path(temp_dir) / "ssh-password.txt"
            password_file.write_text("file-secret\n", encoding="utf-8")
            password_file.chmod(0o640)
            self.assertIsNone(server.read_password_file(str(password_file)))
            password_file.chmod(0o600)
            link = Path(temp_dir) / "linked-password.txt"
            link.symlink_to(password_file)
            self.assertIsNone(server.read_password_file(str(link)))

    def test_nlp_hosts_use_shared_password_without_exposing_it_in_argv(self):
        config = server.load_config()
        nlp_hosts = [host for host in config["hosts"] if host["lab"] == "nlp"]
        self.assertEqual(
            {host["name"] for host in nlp_hosts},
            {"lab21", "lab22", "lab23", "lab24", "lab25", "lab26", "lab27", "lab28"},
        )
        expected_options = [
            "PreferredAuthentications=password,keyboard-interactive",
            "PubkeyAuthentication=no",
        ]
        for host in nlp_hosts:
            with self.subTest(host=host["name"]):
                self.assertEqual(host.get("ssh_password_file"), "secrets/nlp_password")
                self.assertEqual(host.get("ssh_options"), expected_options)

        collector = self.make_collector()
        try:
            with (
                mock.patch.dict("os.environ", {"GPU_WATCH_SSH_TARGET_PREFIX": "gpuwatch-"}),
                mock.patch("server.trusted_ssh_executable", return_value="/usr/bin/ssh"),
            ):
                for host in nlp_hosts:
                    with self.subTest(target=host["name"]):
                        command = collector._ssh_command(host, "true", "not-in-argv")
                        self.assertEqual(command[-2], f"gpuwatch-{host['name']}")
                        self.assertNotIn("not-in-argv", command)
                        self.assertIn("BatchMode=no", command)
                        self.assertIn("NumberOfPasswordPrompts=1", command)
                        self.assertIn("PreferredAuthentications=password,keyboard-interactive", command)
                        self.assertIn("PubkeyAuthentication=no", command)
        finally:
            collector.stop()

    def test_lab22_disk_probe_preserves_user_path_without_docker_inventory(self):
        config = server.load_config()
        lab22 = next(host for host in config["hosts"] if host["name"] == "lab22")
        collector = self.make_collector()
        try:
            command, timeout = collector._probe_invocation(lab22, True)
            self.assertIn(" --disk ", command)
            self.assertNotIn("--docker-usage", command)
            encoded = command.split("--disk-user-paths-b64=", 1)[1].split()[0]
            decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
            self.assertEqual(decoded, [{"user": "gpuwatch", "path": "/tmp/gpuwatch"}])
            self.assertEqual(timeout, 720.0)
        finally:
            collector.stop()

    def test_ssh_command_uses_only_trusted_absolute_executable(self):
        collector = self.make_collector()
        try:
            with mock.patch("server.trusted_ssh_executable", return_value="/usr/bin/ssh"):
                command = collector._ssh_command(
                    {"name": "test-host", "ssh_host": "example.invalid"},
                    "true",
                    None,
                )
            self.assertEqual(command[0], "/usr/bin/ssh")
            self.assertNotIn("ssh", command[:1])
        finally:
            collector.stop()

    def test_ssh_command_uses_an_explicit_validated_config_when_configured(self):
        collector = self.make_collector()
        collector.config["ssh_config_file"] = "/trusted/gpu-watch-ssh-config"
        try:
            with mock.patch("server.trusted_ssh_executable", return_value="/usr/bin/ssh"):
                command = collector._ssh_command(
                    {"name": "test-host", "ssh_host": "example.invalid"},
                    "true",
                    None,
                )
            self.assertEqual(
                command[:7],
                [
                    "/usr/bin/ssh",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "ConnectionAttempts=1",
                    "-F",
                    "/trusted/gpu-watch-ssh-config",
                ],
            )
        finally:
            collector.stop()

    def test_probe_overrides_interactive_ssh_login_settings(self):
        collector = self.make_collector()
        try:
            try:
                executable = server.trusted_ssh_executable()
            except (RuntimeError, FileNotFoundError):
                self.skipTest("OpenSSH is unavailable")
            with tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / "ssh-config"
                config.write_text(
                    "Host *\n  RemoteCommand echo login-only-marker\n  RequestTTY force\n",
                    encoding="utf-8",
                )
                collector.config["ssh_config_file"] = str(config)
                command = collector._ssh_command(
                    {"name": "test-host", "ssh_host": "127.0.0.1"},
                    "true",
                    None,
                )
                effective = subprocess.run(
                    [executable, "-G", *command[1:]],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(effective.returncode, 0, effective.stderr)
                options = dict(
                    line.split(" ", 1) for line in effective.stdout.splitlines() if " " in line
                )
                self.assertEqual(options.get("remotecommand", "none"), "none")
                self.assertIn(options["requesttty"], ("false", "no"))
                self.assertNotIn("login-only-marker", effective.stdout)
        finally:
            collector.stop()

    def test_stop_terminates_and_kills_stuck_probe(self):
        collector = self.make_collector()
        process = mock.Mock()
        process.wait.side_effect = server.subprocess.TimeoutExpired(["ssh"], 0.01)
        collector.running_processes.add(process)
        collector.stop(timeout=0.01)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
