import os
import re
import runpy
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class OperationalScriptTests(unittest.TestCase):
    def test_windows_fallback_keeps_the_server_unprivileged(self):
        fallback = (ROOT / "scripts" / "emergency-local-fallback.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "run-dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn('Invoke-FirewallOperation', fallback)
        self.assertIn('Only the firewall helper may run elevated.', fallback)
        self.assertIn('existing GPU Watch listener did not pass its local snapshot identity check', fallback)
        self.assertIn('restricted firewall rule was reconciled', fallback)
        self.assertIn('Clear-SensitiveProcessEnvironment', fallback)
        self.assertIn('icacls.exe', fallback)
        self.assertNotIn('"/T"', fallback)
        self.assertIn('Refusing a reparse-point GPU Watch runtime path', fallback)
        self.assertIn('does not publish a release fingerprint', fallback)
        self.assertNotIn('[string]::IsNullOrWhiteSpace([string]$snapshot.release_fingerprint) -or', fallback)
        self.assertIn('Test-GpuWatchCommandLine', fallback)
        self.assertIn('Test-LocalGpuWatchSnapshot', fallback)
        self.assertIn('$ServerScript', fallback)
        self.assertIn('Invoke-FirewallOperation "Remove"', fallback)
        self.assertNotIn('$firewallRuleExisted', fallback)
        self.assertNotIn('Invoke-SelfElevated', fallback)
        self.assertNotIn('Require-Administrator', fallback)
        self.assertIn('GPU Watch must run as a standard user.', runner)
        self.assertIn('Clear-SensitiveProcessEnvironment', runner)
        self.assertIn('Test-LocalSshContract', runner)
        self.assertIn('Direct local launch is disabled. Use emergency-local-fallback.ps1.', runner)
        self.assertIn('GPU_WATCH_EMERGENCY_LAUNCH_TOKEN', fallback)
        self.assertIn('$snapshot.runtime_mode -ne "emergency"', fallback)
        self.assertIn('$env:GPU_WATCH_RUNTIME_MODE = "emergency"', runner)
        self.assertIn('GPU_WATCH_SSH_TARGET_PREFIX', runner)
        self.assertIn('GPU_WATCH_SSH_CONFIG_FILE', runner)
        self.assertIn('Require-EffectivePath $alias $effective "userknownhostsfile" $KnownHostsPath', runner)
        self.assertIn('"${user}@${hostname}"', runner)
        self.assertIn('Reject-EffectiveKey $alias $effective "proxycommand"', runner)
        self.assertIn('Reject-EffectiveKey $alias $effective "proxyjump"', runner)
        protected_start = re.compile(
            r"Clear-SensitiveProcessEnvironment\s+"
            r"Protect-LocalRuntimeDirectories\s+"
            r"Ensure-NlpPasswordFile\s+"
            r"Protect-LocalRuntimeDirectories"
        )
        self.assertEqual(len(protected_start.findall(fallback)), 2)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "PowerShell predicate test requires Windows")
    def test_windows_fallback_requires_the_exact_server_script(self):
        command = r'''
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:GPU_WATCH_TEST_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { exit 10 }
$function = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Test-GpuWatchCommandLine"
}, $true)
if ($null -eq $function) { exit 11 }
Invoke-Expression $function.Extent.Text
$expected = [IO.Path]::GetFullPath($env:GPU_WATCH_TEST_SERVER)
$python = 'C:\trusted\python.exe'
$valid = '"' + $python + '" "' + $expected + '" --host 0.0.0.0 --port 8787'
$wrongPath = '"' + $python + '" "C:\other\server.py" --host 0.0.0.0 --port 8787'
$suffixSpoof = '"' + $python + '" "' + $expected + '.evil" --host 0.0.0.0 --port 8787'
$wrongPort = '"' + $python + '" "' + $expected + '" --host 0.0.0.0 --port 9999'
$wrongExecutable = '"C:\tmp\python.exe" "' + $expected + '" --host 0.0.0.0 --port 8787'
$wrongFirstScript = '"' + $python + '" C:\other\evil.py "' + $expected + '" --host 0.0.0.0 --port 8787'
$duplicatePort = '"' + $python + '" "' + $expected + '" --host 0.0.0.0 --port "9999" --port 8787'
$extraArgument = '"' + $python + '" "' + $expected + '" --host 0.0.0.0 --port 8787 C:\evil.py'
if (-not (Test-GpuWatchCommandLine $valid $expected $python)) { exit 12 }
if (Test-GpuWatchCommandLine $wrongPath $expected $python) { exit 13 }
if (Test-GpuWatchCommandLine $suffixSpoof $expected $python) { exit 14 }
if (Test-GpuWatchCommandLine $wrongPort $expected $python) { exit 15 }
if (Test-GpuWatchCommandLine $wrongExecutable $expected $python) { exit 16 }
if (Test-GpuWatchCommandLine $wrongFirstScript $expected $python) { exit 17 }
if (Test-GpuWatchCommandLine $duplicatePort $expected $python) { exit 18 }
if (Test-GpuWatchCommandLine $extraArgument $expected $python) { exit 19 }
'''
        for script in (ROOT / "run-dashboard.ps1", ROOT / "scripts" / "emergency-local-fallback.ps1"):
            with self.subTest(script=script.name):
                environment = os.environ.copy()
                environment["GPU_WATCH_TEST_SCRIPT"] = str(script)
                environment["GPU_WATCH_TEST_SERVER"] = str(ROOT / "server.py")
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=15,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows askpass execution requires Windows")
    def test_windows_askpass_does_not_execute_password_metacharacters(self):
        askpass = ROOT / "scripts" / "ssh-askpass.cmd"
        payloads = (
            "safe&echo GPU_WATCH_MARKER",
            "pipe|echo GPU_WATCH_MARKER",
            "redirect>GPU_WATCH_MARKER_FILE",
            "caret^percent%PATH%!bang",
            "한글 비밀번호 !&%",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for payload in payloads:
                with self.subTest(payload=payload):
                    environment = os.environ.copy()
                    environment["GPU_WATCH_SSH_PASSWORD"] = payload
                    result = subprocess.run(
                        [environment["COMSPEC"], "/d", "/c", str(askpass)],
                        cwd=temp_dir,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(result.stdout.rstrip("\r\n"), payload)
            self.assertFalse((Path(temp_dir) / "GPU_WATCH_MARKER_FILE").exists())

    def test_sanitizer_preserves_database_mtime(self):
        namespace = runpy.run_path(
            str(ROOT / "scripts" / "sanitize-databases.py"),
            run_name="gpu_watch_sanitize_ops_test",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "old.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("create table sample(value text)")
                connection.commit()
            old_mtime = time.time() - 60 * 86400
            os.utime(database, (old_mtime, old_mtime))

            namespace["sanitize_database"](database)

            self.assertAlmostEqual(database.stat().st_mtime, old_mtime, delta=1.0)

    def test_initial_pin_is_written_before_hash(self):
        namespace = runpy.run_path(
            str(ROOT / "scripts" / "admin-passphrase.py"),
            run_name="gpu_watch_admin_ops_test",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hash_path = root / "admin.hash"
            initial_path = root / "initial.txt"
            writes = []

            def record_write(path, text):
                writes.append((path, text))

            function_globals = namespace["ensure"].__globals__
            with mock.patch.dict(
                function_globals,
                {
                    "atomic_write": record_write,
                    "hash_passphrase": lambda value: f"hash:{value}",
                },
            ), mock.patch.object(function_globals["secrets"], "randbelow", return_value=2468):
                namespace["ensure"](hash_path, initial_path)

            self.assertEqual([path for path, _ in writes], [initial_path, hash_path])
            self.assertEqual(writes[0][1], "2468")
            self.assertEqual(writes[1][1], "hash:2468")

    def test_initial_pin_is_rolled_back_when_hash_write_fails(self):
        namespace = runpy.run_path(
            str(ROOT / "scripts" / "admin-passphrase.py"),
            run_name="gpu_watch_admin_rollback_ops_test",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hash_path = root / "admin.hash"
            initial_path = root / "initial.txt"
            initial_path.write_text("previous\n", encoding="utf-8")
            real_atomic_write = namespace["atomic_write"]

            def fail_hash_write(path, text):
                if path == hash_path:
                    raise OSError("simulated hash write failure")
                real_atomic_write(path, text)

            function_globals = namespace["ensure"].__globals__
            with mock.patch.dict(
                function_globals,
                {
                    "atomic_write": fail_hash_write,
                    "hash_passphrase": lambda value: f"hash:{value}",
                },
            ), mock.patch.object(function_globals["secrets"], "randbelow", return_value=1357):
                with self.assertRaisesRegex(OSError, "simulated hash write failure"):
                    namespace["ensure"](hash_path, initial_path)

            self.assertEqual(initial_path.read_text(encoding="utf-8"), "previous\n")
            self.assertFalse(hash_path.exists())

    def test_deploy_stages_ssh_and_avoids_normal_backup_rewrites(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn('RUNTIME_SSH_STAGE="$ROOT/.runtime-ssh.stage.$$"', deploy)
        self.assertIn("cleanup_stale_private_directories()", deploy)
        self.assertLess(
            deploy.index("cleanup_stale_private_directories\n"),
            deploy.index("docker build --pull --no-cache \\"),
        )
        self.assertIn("switch_runtime_ssh()", deploy)
        self.assertIn("restore_runtime_ssh()", deploy)
        self.assertIn("--network none", deploy)
        self.assertIn('$SSH_DIR/config:/source/config:ro', deploy)
        self.assertIn('staging the verified current runtime SSH snapshot', deploy)
        self.assertIn('UserKnownHostsFile /home/gpuwatch/.ssh/known_hosts', deploy)
        self.assertIn('scripts/normalize-ssh-config.py', deploy)
        self.assertIn("grep -c '^  UserKnownHostsFile /home/gpuwatch/.ssh/known_hosts$'", deploy)
        self.assertIn('grep -q \'StrictHostKeyChecking yes\'', deploy)
        self.assertIn("grep -q '/root/.ssh'", deploy)
        self.assertIn("validate_runtime_ssh_stage()", deploy)
        self.assertIn("validate_runtime_ssh_stage\n", deploy)
        self.assertLess(deploy.index("validate_runtime_ssh_stage\n"), deploy.index("docker network inspect"))
        self.assertIn('$RUNTIME_SSH_STAGE:/home/gpuwatch/.ssh:ro', deploy)
        self.assertIn('GPU_WATCH_SSH_CONFIG_FILE=/home/gpuwatch/.ssh/config', deploy)
        self.assertIn('GPU_WATCH_RUNTIME_MODE=production', deploy)
        self.assertIn('/usr/bin/ssh -G -F "$config" "$alias"', deploy)
        self.assertIn("for name in lab21 lab22 lab23 lab24 lab25 lab26 lab27 lab28", deploy)
        for expected in (
            "batchmode no",
            "numberofpasswordprompts 1",
            "preferredauthentications password,keyboard-interactive",
            "passwordauthentication yes",
            "kbdinteractiveauthentication yes",
            "pubkeyauthentication false",
            "stricthostkeychecking true",
            "updatehostkeys false",
            "proxyjump gpuwatch-lab21",
        ):
            self.assertIn(expected, deploy)
        self.assertIn('reject_key "$alias" "$effective" proxycommand', deploy)
        self.assertIn('require_line "$alias" "$effective" "userknownhostsfile $known_hosts"', deploy)
        self.assertIn('/usr/bin/ssh-keygen -F "$check_lookup"', deploy)
        self.assertIn('validate_nll atlas 192.0.2.11 22', deploy)
        self.assertIn('validate_nll pictor 192.0.2.23 22', deploy)
        self.assertIn('validate_nll fornax 192.0.2.16 22', deploy)
        self.assertIn('reject_key "$alias" "$effective" proxyjump', deploy)
        self.assertIn('unrewritten /root/.ssh path', deploy)
        self.assertIn(
            'python3 "$ROOT/scripts/sanitize-databases.py" "$APP_DATA_DIR/gpu_watch.sqlite3"',
            deploy,
        )
        self.assertIn('APP_DATA_DIR="$ROOT/data"', deploy)
        self.assertIn('-v "$APP_DATA_DIR:/app/data"', deploy)
        self.assertNotIn('runtime-data:/app/data', deploy)
        self.assertIn('find "$APP_DATA_DIR" -type l -print -quit', deploy)
        self.assertGreaterEqual(deploy.count("validate_application_data_directory"), 3)
        self.assertIn("unexpected entry in application data directory", deploy)
        first_mkdir = deploy.index('mkdir -p \\\n    "$APP_DATA_DIR/backups"')
        preflight_symlink = deploy.index('if [ -L "$protected_path" ]')
        self.assertLess(preflight_symlink, first_mkdir)
        app_switch = deploy.index("APP_SWITCHED=1")
        final_boundary_check = deploy.rindex("validate_application_data_directory", 0, app_switch)
        final_private_check = deploy.rindex("validate_private_directories", 0, app_switch)
        self.assertGreater(final_boundary_check, deploy.index("docker build --pull --no-cache"))
        self.assertGreater(final_private_check, final_boundary_check)
        self.assertIn('GPU_WATCH_SANITIZE_HISTORICAL_BACKUPS:-0', deploy)
        self.assertIn('wait_container_healthy "$CONTAINER" 36 5', deploy)
        self.assertIn('GPU_WATCH_ALLOW_LOW_DISK_DEPLOY:-0', deploy)
        self.assertIn('GPU_WATCH_ALLOW_LOW_DISK_DEPLOY must be 0 or 1', deploy)
        self.assertIn('--allow-low-disk-only --expected-build-version', deploy)
        self.assertIn('--health-only --expected-build-version', deploy)
        self.assertIn('check_container_health "$CONTAINER" "$BUILD_VERSION"', deploy)
        self.assertIn('docker image prune --force \\\n            --filter "label=org.opencontainers.image.title=$image_title"', deploy)
        self.assertNotIn("docker image prune --all", deploy)
        self.assertNotIn("docker system prune", deploy)
        self.assertIn("critical: GPU Watch rollback did not complete cleanly", deploy)
        self.assertIn('rm -f "$predeploy_temporary" "$predeploy_temporary-wal" "$predeploy_temporary-shm"', deploy)
        self.assertIn('pragma journal_mode=delete', deploy)
        self.assertIn("restore_predeploy_database()", deploy)
        self.assertIn("mode=ro&immutable=1", deploy)
        self.assertIn('os.replace(temporary_path, destination_path)', deploy)
        self.assertIn('failed to restore the pre-deploy database', deploy)

    def test_embedded_deploy_database_restore_is_atomic_and_verified(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        function_start = deploy.index("restore_predeploy_database()")
        payload_start = deploy.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        payload_end = deploy.index("\nPY\n", payload_start)
        payload = deploy[payload_start:payload_end]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "predeploy.sqlite3"
            current = root / "gpu_watch.sqlite3"
            temporary = root / ".rollback.sqlite3"
            for path, schema, marker in (
                (backup, 3, "before"),
                (current, 4, "after"),
            ):
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute("create table state(schema_version integer, marker text)")
                    conn.execute("insert into state values (?, ?)", (schema, marker))
                    conn.commit()
            Path(str(current) + "-wal").write_bytes(b"stale wal")
            Path(str(current) + "-shm").write_bytes(b"stale shm")

            subprocess.run(
                [sys.executable, "-c", payload, str(backup), str(current), str(temporary)],
                check=True,
                capture_output=True,
                text=True,
            )

            with closing(sqlite3.connect(current)) as conn:
                self.assertEqual(conn.execute("select * from state").fetchone(), (3, "before"))
                self.assertEqual(conn.execute("pragma quick_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("pragma journal_mode").fetchone()[0], "delete")
            self.assertFalse(temporary.exists())
            self.assertFalse(Path(str(current) + "-wal").exists())
            self.assertFalse(Path(str(current) + "-shm").exists())

            corrupt = root / "corrupt.sqlite3"
            corrupt.write_bytes(b"not a sqlite database")
            with closing(sqlite3.connect(current)) as conn:
                conn.execute("delete from state")
                conn.execute("insert into state values (4, 'keep')")
                conn.commit()
            Path(str(current) + "-wal").write_bytes(b"keep wal")
            Path(str(current) + "-shm").write_bytes(b"keep shm")
            failed = subprocess.run(
                [sys.executable, "-c", payload, str(corrupt), str(current), str(temporary)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(Path(str(current) + "-wal").read_bytes(), b"keep wal")
            self.assertEqual(Path(str(current) + "-shm").read_bytes(), b"keep shm")
            with closing(sqlite3.connect(current)) as conn:
                self.assertEqual(conn.execute("select * from state").fetchone(), (4, "keep"))

    def test_runtime_ssh_preamble_normalization_is_idempotent(self):
        namespace = runpy.run_path(str(ROOT / "scripts" / "normalize-ssh-config.py"))
        normalize = namespace["normalize_config"]
        preamble = namespace["MANAGED_PREAMBLE"]
        tail = (
            "Host *\n"
            "  StrictHostKeyChecking yes\n"
            "  UpdateHostKeys no\n\n"
            "Host gpuwatch-lab21\n"
            "  HostName 198.51.100.21\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(preamble * 6 + tail, encoding="utf-8")
            changed, removed = normalize(path)
            self.assertTrue(changed)
            self.assertEqual(removed, 6)
            self.assertEqual(path.read_text(encoding="utf-8"), preamble + tail)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            changed, removed = normalize(path)
            self.assertFalse(changed)
            self.assertEqual(removed, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), preamble + tail)

    def test_low_disk_deploy_gate_accepts_only_the_single_known_health_failure(self):
        namespace = runpy.run_path(str(ROOT / "scripts" / "check_local.py"))
        predicate = namespace["low_disk_is_the_only_health_failure"]
        version_matches = namespace["build_version_matches"]
        health = {
            "ok": False,
            "build_version": "1.0.1",
            "database": {
                "ok": False,
                "disk_warning": True,
                "disk_free_bytes": 13_000_000_000,
                "disk_free_percent": 2.8,
            },
            "collector": {"alive": True, "last_cycle_error": None},
            "collector_fresh": True,
            "maintenance": {"alive": True, "last_error": None},
        }
        self.assertTrue(predicate(health, "1.0.1"))
        self.assertTrue(version_matches(health, None))
        self.assertTrue(version_matches(health, "1.0.1"))
        self.assertFalse(version_matches(health, "1.0.0"))
        for dotted_key, value in (
            (("build_version",), "1.0.0"),
            (("database", "ok"), True),
            (("database", "disk_free_percent"), 5.0),
            (("collector", "alive"), False),
            (("collector_fresh",), False),
            (("maintenance", "last_error"), "failure"),
        ):
            with self.subTest(key=dotted_key):
                changed = {
                    **health,
                    "database": dict(health["database"]),
                    "collector": dict(health["collector"]),
                    "maintenance": dict(health["maintenance"]),
                }
                target = changed
                for part in dotted_key[:-1]:
                    target = target[part]
                target[dotted_key[-1]] = value
                self.assertFalse(predicate(changed, "1.0.1"))

    def test_restore_requires_gpu_watch_schema_and_checks_rollback_health(self):
        restore = (ROOT / "scripts" / "restore-backup.sh").read_text(encoding="utf-8")
        self.assertIn('APP_DATA_DIR="$ROOT/data"', restore)
        self.assertIn('find "$APP_DATA_DIR" -type l -print -quit', restore)
        self.assertIn("verify_gpu_watch_database()", restore)
        for table in ("gpu_runtime", "host_runtime", "events", "schema_meta"):
            self.assertIn(f'"{table}"', restore)
        self.assertIn("schema_version", restore)
        self.assertIn("not 1 <= schema_version <= supported_schema_version", restore)
        self.assertIn("?mode=ro&immutable=1", restore)
        self.assertIn('pragma query_only=on', restore)
        self.assertIn('pragma journal_mode=delete', restore)
        self.assertIn("elif ! wait_for_container_health", restore)
        self.assertIn("critical: database rollback did not complete cleanly", restore)

    def test_offsite_backup_uses_locked_atomic_checked_publish(self):
        offsite = (ROOT / "scripts" / "offsite-backup.sh").read_text(encoding="utf-8")
        self.assertIn('APP_DATA_DIR="$ROOT/data"', offsite)
        self.assertIn('find "$APP_DATA_DIR" -type l -print -quit', offsite)
        self.assertIn("flock -n 9", offsite)
        self.assertIn("StrictHostKeyChecking=yes", offsite)
        self.assertIn("sha256sum", offsite)
        self.assertIn("?mode=ro&immutable=1", offsite)
        self.assertIn('pragma query_only=on', offsite)
        self.assertIn("remote_partial=", offsite)
        self.assertIn("''|-*|*[!A-Za-z0-9_.@-]*|*@*@*", offsite)
        self.assertIn('mv -f "$partial" "$final"', offsite)
        self.assertIn('mv -f "$checksum_temporary" "$checksum"', offsite)

    @unittest.skipUnless(shutil.which("sh"), "POSIX sh is not installed on this host")
    def test_posix_shell_syntax(self):
        scripts = [
            ROOT / "deploy.sh",
            ROOT / "scripts" / "restore-backup.sh",
            ROOT / "scripts" / "offsite-backup.sh",
        ]
        for script in scripts:
            with self.subTest(script=script.name):
                subprocess.run(["sh", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
