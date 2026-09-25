import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from email.message import Message
from pathlib import Path
from unittest import mock

import server
from gpu_watch.auth import hash_passphrase
from gpu_watch.security import SlidingWindowRateLimiter, parse_allowed_hosts, parse_allowed_networks


class ServiceLifecycleTests(unittest.TestCase):
    @staticmethod
    def config():
        return {
            "artificial_analysis_api_key_file": "secrets/artificial_analysis_api_key",
            "maintenance_initial_delay_seconds": 120,
            "maintenance_interval_seconds": 3600,
        }

    def lifecycle_mocks(self):
        store = mock.Mock()
        collector = mock.Mock()
        maintenance = mock.Mock()
        intelligence = mock.Mock()
        patches = (
            mock.patch("server.load_config", return_value=self.config()),
            mock.patch("server.Store", return_value=store),
            mock.patch("server.Collector", return_value=collector),
            mock.patch("server.MaintenanceWorker", return_value=maintenance),
            mock.patch("server.ArtificialAnalysisIndex", return_value=intelligence),
            mock.patch("server.audit"),
            mock.patch.object(sys, "argv", ["server.py", "--host", "127.0.0.1", "--port", "8787"]),
        )
        return store, collector, maintenance, intelligence, patches

    def test_bind_failure_starts_no_background_worker(self):
        store, collector, maintenance, _intelligence, patches = self.lifecycle_mocks()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                mock.patch("server.DashboardHTTPServer", side_effect=OSError("address in use")):
            with self.assertRaisesRegex(OSError, "address in use"):
                server.main()
        store.prepare.assert_called_once()
        collector.start.assert_not_called()
        maintenance.start.assert_not_called()
        collector.stop.assert_not_called()
        maintenance.stop.assert_not_called()

    def test_partial_worker_start_is_cleaned_up(self):
        _store, collector, maintenance, intelligence, patches = self.lifecycle_mocks()
        httpd = mock.Mock()
        maintenance.start.side_effect = RuntimeError("maintenance start failed")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                mock.patch("server.DashboardHTTPServer", return_value=httpd), \
                mock.patch("server.signal.signal"):
            with self.assertRaisesRegex(RuntimeError, "maintenance start failed"):
                server.main()
        intelligence.refresh_if_due.assert_called_once()
        collector.start.assert_called_once()
        collector.stop.assert_called_once_with(timeout=7)
        maintenance.stop.assert_not_called()
        httpd.serve_forever.assert_not_called()
        httpd.server_close.assert_called_once()


class StatusStub:
    def __init__(self, payload):
        self.payload = payload

    def status(self):
        return dict(self.payload)


class IntelligenceIndexStub:
    def payload(self):
        return {
            "schema_version": 1,
            "status": "fresh",
            "index_version": "4.1",
            "total_models": 1,
            "last_success_at": "2026-07-18T00:00:00Z",
            "last_attempt_at": "2026-07-18T00:00:00Z",
            "stale_after_seconds": 172800,
            "source": {
                "name": "Artificial Analysis",
                "url": "https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index",
                "methodology_url": "https://artificialanalysis.ai/methodology/intelligence-benchmarking",
            },
            "models": [{"rank": 1, "id": "model-one", "name": "Model One", "provider": "Lab", "score": 60.0}],
        }


class HttpApiTests(unittest.TestCase):
    def test_notice_creation_obeys_hash_concurrency_limit(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        with mock.patch("server.AUTH_ATTEMPT_SEMAPHORE") as slots:
            slots.acquire.return_value = False
            with self.request("/api/announcements", method="POST", body=b"{}", headers=headers) as response:
                self.assertEqual(response.status, 429)
            slots.release.assert_not_called()


    def test_tba_api_creation_conversion_and_validation(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        body = {"title":"🇺🇸 TBA conference","mode":"tba","tba_text":"2027년 1월 예정",
                "deadline_at":None,"url":"https://example.org","pin":"8642"}
        with self.request("/api/deadline", method="POST", body=json.dumps(body).encode(), headers=headers) as response:
            self.assertEqual(response.status,201)
            created=json.load(response)["deadline"]
        self.assertEqual(created["mode"],"tba")
        self.assertIsNone(created["deadline_at"])
        body.update({"id":created["id"],"mode":"scheduled","deadline_at":"2027-01-15T12:00:00+09:00"})
        with self.request("/api/deadline", method="POST", body=json.dumps(body).encode(), headers=headers) as response:
            self.assertEqual(response.status,200)
            updated=json.load(response)["deadline"]
        self.assertEqual(updated["tone"],created["tone"])
        self.assertEqual(updated["tba_text"],"")
        body.update({"mode":"tba","tba_text":""})
        with self.request("/api/deadline", method="POST", body=json.dumps(body).encode(), headers=headers) as response:
            self.assertEqual(response.status,400)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        store = server.Store(Path(self.temp_dir.name) / "gpu_watch.sqlite3")
        now = time.time()
        config = {
            "build_version": "2-test",
            "release_fingerprint": "test-fingerprint",
            "runtime_mode": "production",
            "allowed_networks": parse_allowed_networks("127.0.0.0/8"),
            "trusted_proxy_networks": parse_allowed_networks("127.0.0.0/8"),
            "http_read_timeout_seconds": 5,
            "health_collector_max_age_seconds": 90,
            "announcement_admin_pin_hash": hash_passphrase("8642"),
            "default_deadline": {
                "title": "AAAI 2027 · Full paper",
                "deadline_at": "2027-07-29T20:59:59+09:00",
                "url": "https://aaai.org/conference/aaai/aaai-27/",
            },
        }
        server.Handler.store = store
        server.Handler.config = config
        server.Handler.collector = StatusStub({
            "alive": True,
            "last_cycle_completed_at": now,
            "last_cycle_error": None,
        })
        server.Handler.maintenance = StatusStub({"alive": True, "last_error": None})
        server.Handler.rate_limiter = SlidingWindowRateLimiter()
        server.Handler.artificial_analysis = IntelligenceIndexStub()
        self.httpd = server.DashboardHTTPServer(("127.0.0.1", 0), server.Handler)
        config["allowed_hosts"] = parse_allowed_hosts(
            f"127.0.0.1:{self.httpd.server_address[1]}"
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        self.temp_dir.cleanup()

    def request(self, path, *, method="GET", body=None, headers=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            return urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            return error

    def test_health_has_security_and_cache_headers(self):
        healthy_disk = mock.Mock(total=1000, used=100, free=900)
        with mock.patch("server.shutil.disk_usage", return_value=healthy_disk):
            with self.request("/api/health") as response:
                payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["build_version"], "2-test")
                self.assertEqual(payload["release_fingerprint"], "test-fingerprint")
                self.assertEqual(payload["runtime_mode"], "production")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertIsNone(response.headers.get("Cross-Origin-Opener-Policy"))
                self.assertEqual(response.headers["X-Permitted-Cross-Domain-Policies"], "none")
                self.assertIsNone(response.headers["Strict-Transport-Security"])

    def test_json_response_tolerates_client_disconnect(self):
        class FailingWriter:
            def __init__(self, error_type):
                self.error_type = error_type

            def write(self, _body):
                raise self.error_type()

        for error_type in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error_type=error_type.__name__):
                handler = object.__new__(server.Handler)
                handler.send_response = lambda _status: None
                handler.send_header = lambda _name, _value: None
                handler.end_headers = lambda: None
                handler.wfile = FailingWriter(error_type)
                handler.send_json({"ok": True})

    def test_request_loop_tolerates_client_disconnect(self):
        for error_type in (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            with self.subTest(error_type=error_type.__name__):
                handler = object.__new__(server.Handler)
                handler.close_connection = False
                with mock.patch.object(
                    server.SimpleHTTPRequestHandler,
                    "handle_one_request",
                    side_effect=error_type(),
                ):
                    handler.handle_one_request()
                self.assertTrue(handler.close_connection)

    def test_json_body_rejects_ambiguous_http_framing(self):
        handler = object.__new__(server.Handler)
        handler.rfile = io.BytesIO(b"{}")

        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Transfer-Encoding"] = "chunked"
        with self.assertRaisesRegex(ValueError, "Transfer-Encoding"):
            handler.read_json_body()

        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = "2"
        handler.headers["Content-Length"] = "2"
        with self.assertRaisesRegex(ValueError, "Content-Length"):
            handler.read_json_body()

    def test_legacy_favicon_probe_is_quiet(self):
        with self.request("/favicon.ico") as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["Content-Length"], "0")

    def test_index_renders_current_release_version(self):
        with self.request("/") as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn(
                f"GPU Watch · Release v2-test · {server.__release_date__}",
                body,
            )
            self.assertIn(
                f"{server.__release_model__} / Claude Opus 5.5 Max",
                body,
            )
            self.assertNotIn("__GPU_WATCH_BUILD_VERSION__", body)
            self.assertNotIn("__GPU_WATCH_ASSET_VERSION__", body)
            self.assertNotIn("__GPU_WATCH_RELEASE_MODEL__", body)
            self.assertNotIn("__GPU_WATCH_RELEASE_DATE__", body)
            asset_versions = re.findall(r"/(?:app\.js|styles\.css|favicon\.svg)\?v=([0-9a-f]{12})", body)
            self.assertEqual(len(asset_versions), 3)
            self.assertEqual(len(set(asset_versions)), 1)

    def test_unexpected_host_is_rejected_before_same_origin_mutation(self):
        headers = {
            "Content-Type": "application/json",
            "Host": "attacker.invalid",
            "Origin": "http://attacker.invalid",
        }
        with self.request("/api/snapshot", headers={"Host": "attacker.invalid"}) as response:
            self.assertEqual(response.status, 403)
        with self.request(
            "/api/announcements",
            method="POST",
            body=json.dumps({
                "author": "attacker",
                "message": "blocked",
                "pin": "1234",
            }).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)
        self.assertEqual(server.Handler.store.announcements(), [])

    def test_disallowed_ip_is_rejected(self):
        server.Handler.config["allowed_networks"] = parse_allowed_networks("203.0.113.0/24")
        with self.request("/api/health") as response:
            self.assertEqual(response.status, 403)
            self.assertEqual(json.load(response)["error"], "forbidden")
        with self.request("/api/health", method="OPTIONS") as response:
            self.assertEqual(response.status, 403)
            self.assertEqual(json.load(response)["error"], "forbidden")
        with self.request("/api/health", method="PROPFIND") as response:
            self.assertEqual(response.status, 403)
            self.assertEqual(json.load(response)["error"], "forbidden")

        server.Handler.config["allowed_networks"] = parse_allowed_networks("127.0.0.0/8")
        with self.request("/api/health", method="FROBULATE") as response:
            self.assertEqual(response.status, 501)

    def test_read_api_rate_limit_rejects_excess_requests_before_store_work(self):
        key = "read:snapshot:127.0.0.1"
        for _ in range(60):
            self.assertTrue(server.Handler.rate_limiter.allow(key, 60, 60))
        with self.request("/api/snapshot") as response:
            self.assertEqual(response.status, 429)
            self.assertIn("요청이 너무 많습니다", json.load(response)["error"])

    def test_intelligence_index_has_a_separate_safe_rate_limited_endpoint(self):
        with self.request("/api/intelligence-index") as response:
            payload = json.load(response)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(payload["status"], "fresh")
            self.assertEqual(payload["models"][0]["provider"], "Lab")

        server.Handler.rate_limiter = SlidingWindowRateLimiter()
        key = "read:intelligence-index:127.0.0.1"
        for _ in range(30):
            self.assertTrue(server.Handler.rate_limiter.allow(key, 30, 60))
        with self.request("/api/intelligence-index") as response:
            self.assertEqual(response.status, 429)
            self.assertIn("요청이 너무 많습니다", json.load(response)["error"])

    def test_optional_index_refresh_cannot_fail_core_maintenance(self):
        class StoreStub:
            def __init__(self):
                self.called = False

            def run_maintenance(self, config):
                self.called = True
                return {"ok": True, "marker": config["marker"]}

        class FailingIndex:
            def refresh_if_due(self):
                raise RuntimeError("optional upstream failed")

        store = StoreStub()
        result = server.run_maintenance_cycle(store, {"marker": "kept"}, FailingIndex())
        self.assertTrue(store.called)
        self.assertEqual(result, {"ok": True, "marker": "kept"})

    def test_mutation_rejects_cross_origin_and_oversized_body(self):
        # These checks must reject from headers before reading a body. Sending
        # rejected data concurrently with close can cause a Windows TCP reset
        # instead of an HTTP response, making the test transport-dependent.
        with self.request(
            "/api/announcements", method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        ) as response:
            self.assertEqual(response.status, 403)

        with self.request(
            "/api/announcements", method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base_url,
                     "Content-Length": "9002"},
        ) as response:
            self.assertEqual(response.status, 400)
            self.assertIn("너무 큽니다", json.load(response)["error"])

        with self.request(
            "/api/announcements", method="POST",
            headers={"Origin": self.base_url},
        ) as response:
            self.assertEqual(response.status, 400)
            self.assertIn("application/json", json.load(response)["error"])

    def test_announcement_owner_pin_and_admin_override(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        create_body = {
            "author": "researcher",
            "message": "owner-managed notice",
            "duration_seconds": 3600,
            "pin": "owner-password-2468",
        }
        with self.request(
            "/api/announcements",
            method="POST",
            body=json.dumps(create_body).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 201)
            notice_id = json.load(response)["announcement"]["id"]

        update_body = {
            **create_body,
            "message": "owner-updated notice",
            "pin": "wrong-password",
        }
        with self.request(
            f"/api/announcements/{notice_id}",
            method="POST",
            body=json.dumps(update_body).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)

        with self.request(
            f"/api/announcements/{notice_id}",
            method="POST",
            body=json.dumps({**update_body, "pin": "owner-password-2468"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)
            updated = json.load(response)["announcement"]
            self.assertEqual(updated["message"], "owner-updated notice")
            self.assertIsNotNone(updated["updated_at"])

        with self.request(
            f"/api/announcements/{notice_id}",
            method="POST",
            body=json.dumps({
                **update_body,
                "message": "admin-updated notice",
                "pin": "8642",
            }).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(
                json.load(response)["announcement"]["message"],
                "admin-updated notice",
            )

        with self.request(
            f"/api/announcements/{notice_id}",
            method="DELETE",
            body=json.dumps({"pin": "wrong-password"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)

        with self.request(
            f"/api/announcements/{notice_id}",
            method="DELETE",
            body=json.dumps({"pin": "owner-password-2468"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)

        with self.request(
            "/api/announcements",
            method="POST",
            body=json.dumps({**create_body, "message": "admin-removable notice"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 201)
            admin_notice_id = json.load(response)["announcement"]["id"]

        with self.request(
            f"/api/announcements/{admin_notice_id}",
            method="DELETE",
            body=json.dumps({"pin": "8642"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)

    def test_announcement_rejects_invalid_passphrase_lengths(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        for passphrase in ("abc", "x" * 65):
            with self.subTest(length=len(passphrase)):
                with self.request(
                    "/api/announcements",
                    method="POST",
                    body=json.dumps({
                        "author": "researcher",
                        "message": "invalid password",
                        "duration_seconds": 3600,
                        "pin": passphrase,
                    }).encode(),
                    headers=headers,
                ) as response:
                    self.assertEqual(response.status, 400)
                    self.assertIn("4~64", json.load(response)["error"])

    def test_announcement_supports_no_expiry_and_unbounded_future_expiry(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        create_body = {
            "author": "researcher",
            "message": "permanent notice",
            "pin": "owner-password-2468",
        }
        with self.request(
            "/api/announcements",
            method="POST",
            body=json.dumps(create_body).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 201)
            permanent = json.load(response)["announcement"]
        self.assertTrue(permanent["permanent"])
        self.assertIsNone(permanent["expires_at"])
        self.assertIsNone(permanent["remaining_seconds"])

        with self.request(
            f"/api/announcements/{permanent['id']}",
            method="POST",
            body=json.dumps({
                **create_body,
                "message": "long-lived notice",
                "expires_at": "2036-01-01T12:00",
            }).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)
            expiring = json.load(response)["announcement"]
        self.assertFalse(expiring["permanent"])
        self.assertTrue(expiring["expires_at"].startswith("2036-01-01T12:00:00"))
        self.assertGreater(expiring["remaining_seconds"], 30 * 86400)

        with self.request(
            "/api/announcements",
            method="POST",
            body=json.dumps({
                **create_body,
                "message": "invalid timestamp",
                "duration_seconds": "9" * 200,
            }).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 400)
            self.assertIn("만료 시각", json.load(response)["error"])

    def test_deadline_crud_requires_admin_pin_and_enforces_six_slots(self):
        headers = {"Content-Type": "application/json", "Origin": self.base_url}
        deadline = {
            "id": "primary",
            "title": "🇺🇸 ACL 2027 · Main",
            "deadline_at": "2027-01-15T12:00:00+09:00",
            "url": "https://www.aclweb.org/",
        }
        with self.request(
            "/api/deadline",
            method="POST",
            body=json.dumps({**deadline, "pin": "0000"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)

        with self.request(
            "/api/deadline",
            method="POST",
            body=json.dumps({**deadline, "pin": "8642"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)
            payload = json.load(response)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["deadline"]["title"], deadline["title"])
            self.assertEqual(payload["deadline"]["tone"], "silver")
            self.assertEqual(len(payload["deadlines"]), 1)

        persisted = server.Handler.store.deadline(server.Handler.config)
        self.assertEqual(persisted["title"], deadline["title"])
        self.assertEqual(persisted["url"], deadline["url"])

        created_ids = []
        for title, month, tone in (
            ("ICML 2027", 2, "gold"),
            ("NeurIPS 2027", 3, "emerald"),
            ("CVPR 2027", 4, "diamond"),
            ("KDD 2027", 5, "master"),
            ("EMNLP 2027", 6, "grandmaster"),
        ):
            with self.request(
                "/api/deadline",
                method="POST",
                body=json.dumps({
                    "title": title,
                    "deadline_at": f"2027-{month:02d}-15T12:00:00+09:00",
                    "url": "https://example.com/conference",
                    "pin": "8642",
                }).encode(),
                headers=headers,
            ) as response:
                self.assertEqual(response.status, 201)
                created = json.load(response)["deadline"]
                self.assertEqual(created["tone"], tone)
                created_ids.append(created["id"])

        with self.request(
            "/api/deadline",
            method="POST",
            body=json.dumps({
                "title": "CVPR 2027",
                "deadline_at": "2027-07-15T12:00:00+09:00",
                "url": "https://example.com/seventh",
                "pin": "8642",
            }).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 400)
            self.assertIn("최대 6개", json.load(response)["error"])

        with self.request(
            f"/api/deadline/{created_ids[0]}",
            method="DELETE",
            body=json.dumps({"pin": "0000"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)

        with self.request(
            f"/api/deadline/{created_ids[0]}",
            method="DELETE",
            body=json.dumps({"pin": "8642"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(len(json.load(response)["deadlines"]), 5)

        with self.request(
            f"/api/deadline/{created_ids[0]}",
            method="DELETE",
            body=json.dumps({"pin": "8642"}).encode(),
            headers=headers,
        ) as response:
            self.assertEqual(response.status, 404)

    def test_availability_filter_defaults_off_and_paginates_after_filtering(self):
        with closing(server.Handler.store.connect()) as conn, conn:
            for i, event in enumerate(["busy_start", "host_down", "gpu_down", "free_start", "host_recovered", "gpu_recovered", "user_change", "observation_gap"]):
                conn.execute("insert into events(ts,host,gpu_index,event,users) values(?, 'test', 0, ?, 'alice')",
                             (100 + i, event))
            conn.commit()
        with self.request("/api/events?limit=1") as response:
            self.assertEqual(response.status, 200)
            page = json.load(response)
        self.assertEqual([e["event"] for e in page["events"]], ["free_start"])
        self.assertEqual(page["next_offset"], 1)
        with self.request("/api/events?limit=1&offset=1&include_availability=0&host=test&user=alice") as response:
            page = json.load(response)
        self.assertEqual([e["event"] for e in page["events"]], ["busy_start"])
        self.assertFalse(page["has_more"])
        with self.request("/api/events?include_availability=1") as response:
            page = json.load(response)
        self.assertEqual([e["event"] for e in page["events"]],
                         ["gpu_recovered", "host_recovered", "free_start", "gpu_down", "host_down", "busy_start"])
        with self.request("/api/events?include_availability=invalid") as response:
            self.assertEqual(response.status, 400)
        with closing(server.Handler.store.connect()) as conn, conn:
            self.assertEqual(conn.execute("select count(*) from events").fetchone()[0], 8)

    def test_invalid_event_date_returns_400(self):
        with self.request("/api/events?date=2026-99-99") as response:
            self.assertEqual(response.status, 400)
            self.assertFalse(json.load(response)["ok"])
        with self.request("/api/events?offset=" + ("9" * 100)) as response:
            self.assertEqual(response.status, 400)
            self.assertFalse(json.load(response)["ok"])


if __name__ == "__main__":
    unittest.main()
