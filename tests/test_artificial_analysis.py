import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from gpu_watch import __version__
from gpu_watch.artificial_analysis import (
    ArtificialAnalysisIndex,
    METHODOLOGY_URL,
    SOURCE_URL,
    TOP_MODEL_LIMIT,
)


class MutableClock:
    def __init__(self, value=1_800_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class FakeResponse:
    def __init__(self, payload, *, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def close(self):
        self.closed = True


class FailingReadResponse(FakeResponse):
    def read(self, limit=-1):
        raise TimeoutError("upstream read timed out")


class QueueOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append({
            "url": request.full_url,
            "headers": {name.lower(): value for name, value in request.header_items()},
            "timeout": timeout,
        })
        if not self.results:
            raise AssertionError("unexpected upstream request")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def model(model_id, name, provider, score):
    return {
        "id": model_id,
        "name": name,
        "slug": model_id,
        "release_date": "2026-01-01",
        "model_creator": {"id": f"creator-{provider}", "name": provider},
        "evaluations": {"artificial_analysis_intelligence_index": score},
        "artificial_analysis_intelligence_index_cost": {"total_cost": 1},
        "pricing": {},
        "performance": {},
    }


def page(data, *, number=1, total_pages=1, version=4.1, has_more=None):
    return {
        "tier": "free",
        "intelligence_index_version": version,
        "pagination": {
            "page": number,
            "page_size": 200,
            "total_pages": total_pages,
            "has_more": number < total_pages if has_more is None else has_more,
        },
        "data": data,
    }


class ArtificialAnalysisIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cache_path = self.root / "data" / "artificial-analysis.json"
        self.secret_path = self.root / "secrets" / "artificial_analysis_api_key"
        self.secret_path.parent.mkdir(parents=True)
        self.secret_path.write_text("test-api-key-123\n", encoding="utf-8")
        self.secret_path.chmod(0o600)
        self.clock = MutableClock()

    def tearDown(self):
        self.temp_dir.cleanup()

    def service(self, opener, **kwargs):
        return ArtificialAnalysisIndex(
            self.cache_path,
            self.secret_path,
            opener=opener,
            clock=self.clock,
            sleeper=kwargs.pop("sleeper", lambda _delay: None),
            **kwargs,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX secret mode contract")
    def test_api_key_rejects_group_readable_or_symlinked_secret(self):
        service = self.service(QueueOpener())
        self.secret_path.chmod(0o640)
        self.assertIsNone(service._read_api_key())
        self.secret_path.chmod(0o600)
        link = self.secret_path.with_name("linked-key")
        link.symlink_to(self.secret_path)
        linked = ArtificialAnalysisIndex(
            self.cache_path,
            link,
            opener=QueueOpener(),
            clock=self.clock,
            sleeper=lambda _delay: None,
        )
        self.assertIsNone(linked._read_api_key())

    def test_paginates_normalizes_sorts_ties_and_persists_top_29(self):
        rows = [
            model("tie-z", "Tie Z", "Provider Z", 60),
            model("tie-b", "Tie Alpha", "Provider B", 60),
            model("tie-a", "Tie Alpha", "Provider A", 60),
        ]
        rows.extend(
            model(f"model-{number:02d}", f"Model {number:02d}", "Provider", 59 - number)
            for number in range(28)
        )
        rows.append(model("unscored", "Unscored", "Provider", None))
        opener = QueueOpener(
            FakeResponse(page(rows[:16], number=1, total_pages=2)),
            FakeResponse(page(rows[16:], number=2, total_pages=2)),
        )
        service = self.service(opener)

        self.assertTrue(service.refresh_now(force=True))
        payload = service.payload(schedule_refresh=False)

        self.assertEqual([call["url"].rsplit("=", 1)[-1] for call in opener.calls], ["1", "2"])
        self.assertTrue(all(call["headers"]["x-api-key"] == "test-api-key-123" for call in opener.calls))
        self.assertTrue(all(call["headers"]["user-agent"] == f"GPU-Watch/{__version__}" for call in opener.calls))
        self.assertEqual(payload["status"], "fresh")
        self.assertEqual(payload["index_version"], "4.1")
        self.assertEqual(payload["total_models"], 31)
        self.assertEqual(len(payload["models"]), TOP_MODEL_LIMIT)
        self.assertEqual([item["rank"] for item in payload["models"]], list(range(1, 30)))
        self.assertEqual(
            [(item["name"], item["provider"]) for item in payload["models"][:3]],
            [("Tie Alpha", "Provider A"), ("Tie Alpha", "Provider B"), ("Tie Z", "Provider Z")],
        )
        self.assertEqual(payload["source"]["url"], SOURCE_URL)
        self.assertEqual(payload["source"]["methodology_url"], METHODOLOGY_URL)
        self.assertTrue(self.cache_path.is_file())
        self.assertFalse(list(self.cache_path.parent.glob("*.tmp")))
        persisted = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertNotIn("test-api-key-123", json.dumps(persisted))

        reloaded = self.service(QueueOpener())
        self.assertEqual(reloaded.payload(schedule_refresh=False), payload)

    def test_success_is_gated_for_six_hours_and_rotated_key_is_read_dynamically(self):
        first = QueueOpener(FakeResponse(page([model("one", "One", "Lab", 40)])))
        service = self.service(first)
        self.assertTrue(service.refresh_now(force=True))
        self.assertFalse(service.refresh_now())
        self.assertEqual(len(first.calls), 1)

        self.clock.value += 6 * 60 * 60 - 1
        self.assertFalse(service.refresh_now())
        self.clock.value += 2
        self.secret_path.write_text("rotated-api-key-456\n", encoding="utf-8")
        second = QueueOpener(FakeResponse(page([model("two", "Two", "Lab", 41)])))
        service._opener = second
        self.assertTrue(service.refresh_now())
        self.assertEqual(second.calls[0]["headers"]["x-api-key"], "rotated-api-key-456")
        self.assertEqual(service.payload(schedule_refresh=False)["models"][0]["id"], "two")

    def test_transient_failures_retry_with_a_strict_bound(self):
        sleeps = []
        opener = QueueOpener(
            URLError("temporary one"),
            URLError("temporary two"),
            FakeResponse(page([model("ok", "Recovered", "Lab", 33)])),
        )
        service = self.service(opener, sleeper=sleeps.append)

        self.assertTrue(service.refresh_now(force=True))
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

        always_failing = QueueOpener(*(URLError("temporary") for _ in range(5)))
        failed = self.service(always_failing)
        self.assertFalse(failed.refresh_now(force=True))
        self.assertEqual(len(always_failing.calls), 3)

        read_sleeps = []
        read_timeout = QueueOpener(
            FailingReadResponse(page([model("late", "Late", "Lab", 20)])),
            FakeResponse(page([model("ok", "Recovered", "Lab", 33)])),
        )
        recovered = self.service(read_timeout, sleeper=read_sleeps.append)
        self.assertTrue(recovered.refresh_now(force=True))
        self.assertEqual(len(read_timeout.calls), 2)
        self.assertTrue(read_timeout.results == [])
        self.assertEqual(read_sleeps, [1.0])

        rate_limit_sleeps = []
        rate_limited = QueueOpener(HTTPError(
            "https://example.invalid",
            429,
            "rate limited",
            {"Retry-After": "7200"},
            None,
        ))
        deferred = self.service(rate_limited, sleeper=rate_limit_sleeps.append)
        self.assertFalse(deferred.refresh_now(force=True))
        self.assertEqual(len(rate_limited.calls), 1)
        self.assertEqual(rate_limit_sleeps, [])
        self.assertEqual(deferred._next_attempt_at, self.clock.value + 7200)

    def test_refresh_thread_start_failure_releases_the_gate(self):
        class BrokenThread:
            def start(self):
                raise RuntimeError("thread unavailable")

        service = self.service(QueueOpener())
        with patch("gpu_watch.artificial_analysis.threading.Thread", return_value=BrokenThread()):
            self.assertFalse(service.refresh_if_due())

        self.assertFalse(service._refreshing)
        self.assertEqual(service._next_attempt_at, self.clock.value + 3600)
        self.assertFalse(service.refresh_if_due())

    def test_missing_key_and_invalid_cache_return_safe_unavailable_payload(self):
        self.secret_path.unlink()
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text('{"schema_version":1,"models":"invalid"}', encoding="utf-8")
        opener = QueueOpener()
        service = self.service(opener)

        self.assertFalse(service.refresh_now(force=True))
        payload = service.payload(schedule_refresh=False)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIsNone(payload["index_version"])
        self.assertEqual(payload["models"], [])
        self.assertIsNotNone(payload["last_attempt_at"])
        self.assertEqual(opener.calls, [])
        self.assertNotIn("error", payload)

    def test_failed_refresh_keeps_last_known_good_and_becomes_stale(self):
        successful = QueueOpener(FakeResponse(page([model("safe", "Safe", "Lab", 52)])))
        service = self.service(successful)
        self.assertTrue(service.refresh_now(force=True))
        last_success = service.payload(schedule_refresh=False)["last_success_at"]

        self.clock.value += 172_801
        denied = HTTPError("https://example.invalid", 401, "unauthorized", {}, None)
        failing = QueueOpener(denied)
        service._opener = failing
        self.assertFalse(service.refresh_now(force=True))
        payload = service.payload(schedule_refresh=False)

        self.assertEqual(len(failing.calls), 1)
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["last_success_at"], last_success)
        self.assertNotEqual(payload["last_attempt_at"], last_success)
        self.assertEqual(payload["models"][0]["id"], "safe")
        self.assertNotIn("unauthorized", json.dumps(payload))

    def test_strict_validation_rejects_inconsistent_or_duplicate_pages(self):
        cases = {
            "wrong_page": [FakeResponse(page([model("a", "A", "Lab", 1)], number=2))],
            "bad_has_more": [FakeResponse(page([model("a", "A", "Lab", 1)], has_more=True))],
            "bad_score": [FakeResponse(page([model("a", "A", "Lab", 101)]))],
            "duplicate": [FakeResponse(page([
                model("same", "A", "Lab", 2),
                model("same", "B", "Lab", 1),
            ]))],
            "changed_version": [
                FakeResponse(page([model("a", "A", "Lab", 2)], number=1, total_pages=2, version=4.1)),
                FakeResponse(page([model("b", "B", "Lab", 1)], number=2, total_pages=2, version=4.2)),
            ],
        }
        for name, responses in cases.items():
            with self.subTest(name=name):
                case_cache = self.root / f"{name}.json"
                opener = QueueOpener(*responses)
                service = ArtificialAnalysisIndex(
                    case_cache,
                    self.secret_path,
                    opener=opener,
                    clock=self.clock,
                    sleeper=lambda _delay: None,
                    max_fetch_attempts=1,
                )
                self.assertFalse(service.refresh_now(force=True))
                self.assertEqual(service.payload(schedule_refresh=False)["status"], "unavailable")
                self.assertFalse(case_cache.exists())

    def test_identical_page_boundary_duplicate_is_deduplicated(self):
        duplicate = model("same", "Same", "Lab", 42)
        service = self.service(QueueOpener(FakeResponse(page([duplicate, dict(duplicate)]))))

        self.assertTrue(service.refresh_now(force=True))
        payload = service.payload(schedule_refresh=False)
        self.assertEqual(payload["total_models"], 1)
        self.assertEqual([item["id"] for item in payload["models"]], ["same"])

    def test_conflicting_duplicate_retries_the_complete_snapshot(self):
        sleeps = []
        opener = QueueOpener(
            FakeResponse(page([
                model("same", "First", "Lab", 42),
                model("same", "Conflicting", "Lab", 41),
            ])),
            FakeResponse(page([model("stable", "Stable", "Lab", 40)])),
        )
        service = self.service(opener, sleeper=sleeps.append)

        self.assertTrue(service.refresh_now(force=True))
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(service.payload(schedule_refresh=False)["models"][0]["id"], "stable")

    def test_default_client_rejects_cross_origin_redirect_without_forwarding_key(self):
        class DestinationHandler(BaseHTTPRequestHandler):
            requests = 0
            received_key = None

            def do_GET(self):
                type(self).requests += 1
                type(self).received_key = self.headers.get("x-api-key")
                self.send_response(200)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        class RedirectHandler(BaseHTTPRequestHandler):
            destination = ""

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", type(self).destination)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        RedirectHandler.destination = f"http://127.0.0.1:{destination.server_address[1]}/capture"
        threads = [
            threading.Thread(target=destination.serve_forever, daemon=True),
            threading.Thread(target=redirect.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            service = ArtificialAnalysisIndex(
                self.cache_path,
                self.secret_path,
                api_url=f"http://127.0.0.1:{redirect.server_address[1]}/free",
                clock=self.clock,
                sleeper=lambda _delay: None,
            )
            self.assertFalse(service.refresh_now(force=True))
            self.assertEqual(DestinationHandler.requests, 0)
            self.assertIsNone(DestinationHandler.received_key)
        finally:
            redirect.shutdown()
            destination.shutdown()
            redirect.server_close()
            destination.server_close()
            for thread in threads:
                thread.join(5)


if __name__ == "__main__":
    unittest.main()
