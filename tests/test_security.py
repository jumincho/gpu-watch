import io
import logging
import unittest

import server
from gpu_watch import opslog
from gpu_watch.auth import (
    hash_needs_upgrade,
    hash_passphrase,
    is_valid_announcement_passphrase,
    is_valid_pin,
    verify_passphrase,
)
from gpu_watch.processes import sanitize_disk_snapshot, sanitize_gpu_snapshot, sanitize_processes
from gpu_watch.security import (
    SlidingWindowRateLimiter,
    client_ip_from_proxy,
    is_host_allowed,
    is_ip_allowed,
    is_same_origin,
    parse_allowed_networks,
    parse_allowed_hosts,
    redact_sensitive_text,
)


class SecurityTests(unittest.TestCase):
    def test_sensitive_assignment_redacts_the_complete_remaining_value(self):
        cases = (
            "Authorization: Bearer top-secret trailing-value",
            "password=my secret value",
            'token="quoted secret value" next-field',
            "access_token=DEMO_SECRET trailing-value",
            "GPU_WATCH_SSH_PASSWORD=DEMO_SECRET trailing-value",
            '{"safe":1,"token":"DEMO_SECRET","later":2}',
            "api_key=DEMO_SECRET trailing-value",
            "--token DEMO_SECRET --safe value",
            "--api-key=DEMO_SECRET --safe value",
        )
        for value in cases:
            with self.subTest(value=value):
                redacted = redact_sensitive_text(value)
                self.assertIn("[redacted]", redacted)
                self.assertNotIn("top-secret", redacted)
                self.assertNotIn("secret value", redacted)
                self.assertNotIn("Bearer", redacted)
                self.assertNotIn("DEMO_SECRET", redacted)

        self.assertEqual(redact_sensitive_text("ordinary failure code=1"), "ordinary failure code=1")

    def test_ip_allowlist(self):
        networks = parse_allowed_networks("127.0.0.0/8,192.0.2.0/24,::1/128")
        self.assertTrue(is_ip_allowed("192.0.2.42", networks))
        self.assertTrue(is_ip_allowed("127.0.0.1", networks))
        self.assertFalse(is_ip_allowed("203.0.113.42", networks))

    def test_host_allowlist_is_exact_and_rejects_ambiguous_authorities(self):
        hosts = parse_allowed_hosts("192.0.2.10:8787,127.0.0.1:8787,[::1]:8787")
        self.assertTrue(is_host_allowed("192.0.2.10:8787", hosts))
        self.assertTrue(is_host_allowed("[::1]:8787", hosts))
        self.assertFalse(is_host_allowed("attacker.invalid", hosts))
        self.assertFalse(is_host_allowed("192.0.2.10:8788", hosts))
        for invalid in ("", "host/path", "user@host", "host\r\nX-Test: 1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_allowed_hosts(invalid)
        with self.assertRaises(ValueError):
            parse_allowed_hosts(["host,evil"])

    def test_origin_validation(self):
        self.assertTrue(is_same_origin({"Host": "example.test", "Origin": "https://example.test"}))
        self.assertTrue(is_same_origin({"Host": "example.test"}))
        self.assertFalse(is_same_origin({"Host": "example.test", "Origin": "https://evil.test"}))

    def test_sliding_window_limit(self):
        limiter = SlidingWindowRateLimiter()
        self.assertTrue(limiter.allow("key", 2, 10, now=1))
        self.assertTrue(limiter.allow("key", 2, 10, now=2))
        self.assertFalse(limiter.allow("key", 2, 10, now=3))
        self.assertTrue(limiter.allow("key", 2, 10, now=12))

        self.assertTrue(limiter.check("failures", 2, 10, now=1))
        self.assertTrue(limiter.check("failures", 2, 10, now=2))
        limiter.record("failures", 10, now=2)
        limiter.record("failures", 10, now=3)
        self.assertFalse(limiter.check("failures", 2, 10, now=4))
        self.assertTrue(limiter.check("failures", 2, 10, now=13))

    def test_failed_auth_budget_is_subnet_scoped_and_slow(self):
        self.assertEqual(server.AUTH_FAILURE_SUBNET_LIMIT, 10)
        self.assertEqual(server.AUTH_FAILURE_GLOBAL_LIMIT, 20)
        self.assertEqual(server.AUTH_FAILURE_WINDOW_SECONDS, 3600)
        self.assertEqual(server.client_network_scope("192.0.2.42"), "192.0.2.0/24")
        self.assertEqual(server.client_network_scope("2001:db8::42"), "2001:db8::/64")

    def test_admin_hash_and_legacy_upgrade_detection(self):
        encoded = hash_passphrase("correct horse battery staple")
        legacy = hash_passphrase("correct horse battery staple", iterations=240_000)
        self.assertTrue(verify_passphrase("correct horse battery staple", encoded))
        self.assertTrue(verify_passphrase("correct horse battery staple", legacy))
        self.assertFalse(verify_passphrase("wrong", encoded))
        self.assertFalse(hash_needs_upgrade(encoded))
        self.assertTrue(hash_needs_upgrade(legacy))
        excessive = encoded.replace("$600000$", "$999999999$", 1)
        self.assertFalse(verify_passphrase("correct horse battery staple", excessive))
        self.assertTrue(hash_needs_upgrade(excessive))
        self.assertTrue(hash_needs_upgrade("pbkdf2_sha256$600000$validsalt$not-base64"))
        self.assertTrue(is_valid_pin("8642"))
        self.assertTrue(is_valid_pin("0000"))
        self.assertFalse(is_valid_pin("12345"))
        self.assertFalse(is_valid_pin("abcd"))

    def test_announcement_passphrase_validation(self):
        self.assertTrue(is_valid_announcement_passphrase("2468"))
        self.assertTrue(is_valid_announcement_passphrase("owner-password"))
        self.assertTrue(is_valid_announcement_passphrase("한글 비밀번호"))
        self.assertFalse(is_valid_announcement_passphrase("abc"))
        self.assertFalse(is_valid_announcement_passphrase(" " * 4))
        self.assertFalse(is_valid_announcement_passphrase("line\nbreak"))
        self.assertFalse(is_valid_announcement_passphrase("x" * 65))
        self.assertFalse(is_valid_announcement_passphrase(2468))

    def test_process_sanitizer_drops_command_arguments(self):
        result = sanitize_processes([{
            "pid": "42",
            "user": "researcher",
            "process_name": "/usr/bin/python",
            "command_summary": "python train.py",
            "cmd": "python train.py --token top-secret",
            "used_memory": "1024",
            "container_name": "experiment",
        }])
        self.assertEqual(result[0]["process_name"], "python")
        self.assertEqual(result[0]["command_summary"], "python train.py")
        self.assertNotIn("cmd", result[0])
        self.assertNotIn("top-secret", str(result))

        generation = sanitize_processes([{
            "pid": 50,
            "process_name": "python",
            "start_identity": "123456",
        }])[0]
        self.assertEqual(generation["start_identity"], "123456")
        self.assertNotIn("start_identity", sanitize_processes([{
            "pid": 51,
            "process_name": "python",
            "start_identity": "not-numeric",
        }])[0])

        rejected = sanitize_processes([{
            "pid": 43,
            "process_name": "python",
            "command_summary": "python train.py --token top-secret",
            "used_memory": 1,
        }])
        self.assertEqual(rejected[0]["command_summary"], "python")
        self.assertNotIn("top-secret", str(rejected))

        module = sanitize_processes([{
            "pid": 44,
            "process_name": "python3",
            "command_summary": "python3 -m torch.distributed.run",
            "used_memory": 1,
        }])
        self.assertEqual(module[0]["command_summary"], "python3 -m torch.distributed.run")

        sensitive_title = sanitize_processes([{
            "pid": 45,
            "user": "API_TOKEN=owner-secret",
            "process_name": "/tmp/API_TOKEN=process-secret",
            "command_summary": "API_TOKEN=process-secret",
            "container_name": "password=container-secret",
            "used_memory": 1,
        }])[0]
        self.assertEqual(sensitive_title["user"], "")
        self.assertEqual(sensitive_title["process_name"], "process")
        self.assertEqual(sensitive_title["command_summary"], "process")
        self.assertEqual(sensitive_title["container_name"], "container")
        self.assertNotIn("secret", str(sensitive_title))

        unresolved_user = sanitize_processes([{
            "pid": 48,
            "process_name": "python",
            "command_summary": "python worker.py",
            "used_memory": 1,
        }])[0]
        self.assertEqual(unresolved_user["user"], "")
        self.assertNotIn("?", unresolved_user.values())

        synthetic_unknown_user = sanitize_processes([{
            "pid": 49,
            "user": "?",
            "process_name": "python",
            "command_summary": "python worker.py",
            "used_memory": 1,
        }])[0]
        self.assertEqual(synthetic_unknown_user["user"], "")
        self.assertNotIn("?", synthetic_unknown_user.values())

        ordinary_title = sanitize_processes([{
            "pid": 46,
            "user": "researcher",
            "process_name": "ray::WorkerDict",
            "command_summary": "ray::WorkerDict",
            "used_memory": 1,
        }])[0]
        self.assertEqual(ordinary_title["process_name"], "ray::WorkerDict")

        trailing_secret = sanitize_processes([{
            "pid": 47,
            "process_name": ("x" * 116) + "_API_TOKEN=hidden",
            "command_summary": "process",
            "used_memory": 1,
        }])[0]
        self.assertEqual(trailing_secret["process_name"], "process")
        self.assertNotIn("hidden", str(trailing_secret))

    def test_process_and_gpu_sanitizers_reject_non_finite_and_unknown_fields(self):
        self.assertEqual(sanitize_processes([{"pid": "inf", "used_memory": "inf"}]), [])
        self.assertEqual(sanitize_processes([{"pid": -1, "used_memory": 1}]), [])
        gpu = sanitize_gpu_snapshot({
            "index": 0,
            "name": "GPU",
            "uuid": "GPU-test",
            "utilization": "inf",
            "memory_used": "nan",
            "memory_total": 8192,
            "temperature": 44,
            "command": "python train.py --token top-secret",
            "processes": [{"pid": 42, "user": "user", "process_name": "/usr/bin/python"}],
        })
        self.assertIsNotNone(gpu)
        self.assertIsNone(gpu["utilization"])
        self.assertIsNone(gpu["memory_used"])
        self.assertNotIn("command", gpu)
        self.assertNotIn("top-secret", str(gpu))

    def test_disk_sanitizer_bounds_shape_values_and_errors(self):
        disk = sanitize_disk_snapshot({
            "filesystems": [{
                "filesystem": "/dev/nvme0n1p1",
                "type": "ext4",
                "mount": "/",
                "total_bytes": 1000,
                "used_bytes": 400,
                "available_bytes": 600,
                "use_percent": 40,
                "unknown": "drop-me",
            }, {"mount": "/bad", "total_bytes": "inf", "use_percent": 101}],
            "users": [{
                "user": "researcher",
                "bytes": 400,
                "path": "/untrusted/fallback",
                "locations": [{"path": "/home/researcher", "bytes": 400, "unknown": True}],
            }],
            "errors": [
                "token=top-secret\npartial failure",
                "Authorization: Bearer second-secret trailing-value",
            ],
            "unknown": ["drop-me"],
        })
        self.assertEqual(set(disk), {"filesystems", "users", "errors"})
        self.assertNotIn("unknown", disk["filesystems"][0])
        self.assertIsNone(disk["filesystems"][1]["total_bytes"])
        self.assertIsNone(disk["filesystems"][1]["use_percent"])
        self.assertEqual(disk["users"][0]["path"], "/home/researcher")
        self.assertNotIn("top-secret", str(disk))
        self.assertNotIn("second-secret", str(disk))
        self.assertNotIn("Bearer", str(disk))
        self.assertIn("[redacted]", disk["errors"][0])

        oversized = sanitize_disk_snapshot({
            "filesystems": [{"mount": f"/{index}"} for index in range(200)],
            "users": [{"user": str(index), "bytes": index} for index in range(100)],
            "errors": [str(index) for index in range(100)],
        })
        self.assertEqual(len(oversized["filesystems"]), 128)
        self.assertEqual(len(oversized["users"]), 64)
        self.assertEqual(len(oversized["errors"]), 32)

    def test_audit_log_redacts_nested_sensitive_values(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        opslog._LOGGER.addHandler(handler)
        try:
            opslog.audit(
                "test_event",
                detail={
                    "token": "top-secret",
                    "nested": {"password": "also-secret"},
                    "api_key": "api-key-secret",
                    "private_key": "private-key-secret",
                    "key": "generic-key-secret",
                },
                message="safe\nline",
            )
        finally:
            opslog._LOGGER.removeHandler(handler)
        rendered = stream.getvalue()
        self.assertIn("test_event", rendered)
        self.assertIn("safe line", rendered)
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("also-secret", rendered)
        self.assertNotIn("api-key-secret", rendered)
        self.assertNotIn("private-key-secret", rendered)
        self.assertNotIn("generic-key-secret", rendered)

    def test_forwarded_ip_only_from_trusted_proxy(self):
        trusted = parse_allowed_networks("172.16.0.0/12")
        headers = {"X-Forwarded-For": "192.0.2.42"}
        self.assertEqual(client_ip_from_proxy("172.18.0.2", headers, trusted), "192.0.2.42")
        self.assertEqual(client_ip_from_proxy("203.0.113.9", headers, trusted), "203.0.113.9")

    def test_forwarded_ip_is_canonical_and_cannot_split_rate_limit_keys(self):
        trusted = parse_allowed_networks("::1/128")
        peer = "0:0:0:0:0:0:0:1"
        expanded = client_ip_from_proxy(
            peer,
            {"X-Forwarded-For": "2001:0db8:0000:0000:0000:0000:0000:0001"},
            trusted,
        )
        scoped = client_ip_from_proxy(
            peer,
            {"X-Forwarded-For": "2001:db8::1%attacker-selected-zone"},
            trusted,
        )
        self.assertEqual(expanded, "2001:db8::1")
        self.assertEqual(scoped, expanded)

        limiter = SlidingWindowRateLimiter()
        self.assertTrue(limiter.allow(f"read:{expanded}", 1, 60.0, now=1.0))
        self.assertFalse(limiter.allow(f"read:{scoped}", 1, 60.0, now=1.1))

    def test_ambiguous_forwarded_ipv4_fails_closed_to_proxy_address(self):
        trusted = parse_allowed_networks("172.16.0.0/12")
        self.assertEqual(
            client_ip_from_proxy(
                "172.18.0.2",
                {"X-Forwarded-For": "010.000.002.042"},
                trusted,
            ),
            "172.18.0.2",
        )


if __name__ == "__main__":
    unittest.main()
