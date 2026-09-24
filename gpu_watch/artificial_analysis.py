from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gpu_watch import __version__
from gpu_watch.opslog import audit


API_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
SOURCE_URL = "https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index"
METHODOLOGY_URL = "https://artificialanalysis.ai/methodology/intelligence-benchmarking"
CACHE_SCHEMA_VERSION = 1
TOP_MODEL_LIMIT = 29
REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
STALE_AFTER_SECONDS = 48 * 60 * 60
FAILURE_RETRY_INTERVAL_SECONDS = 60 * 60
MAX_FETCH_ATTEMPTS = 3
MAX_INLINE_RETRY_DELAY_SECONDS = 5.0
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60
MAX_PAGES = 20
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024

_INDEX_VERSION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3})?")
_MODEL_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")


class ArtificialAnalysisFetchError(RuntimeError):
    """A deliberately detail-free fetch failure safe for operational logs."""

    def __init__(self, category: str, *, transient: bool, retry_after: float | None = None):
        super().__init__(category)
        self.category = category
        self.transient = transient
        self.retry_after = retry_after


class _RejectRedirects(HTTPRedirectHandler):
    """Keep the API key on the single configured origin, even on redirects."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def unavailable_payload(
    *,
    stale_after_seconds: float = STALE_AFTER_SECONDS,
    last_attempt_at: str | None = None,
) -> dict[str, Any]:
    """Return the stable public shape without exposing an internal failure reason."""
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "status": "unavailable",
        "index_version": None,
        "total_models": 0,
        "last_success_at": None,
        "last_attempt_at": last_attempt_at,
        "stale_after_seconds": int(stale_after_seconds),
        "source": {
            "name": "Artificial Analysis",
            "url": SOURCE_URL,
            "methodology_url": METHODOLOGY_URL,
        },
        "models": [],
    }


def _iso_utc(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finite_number(value: Any, *, minimum: float, maximum: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtificialAnalysisFetchError(field, transient=False)
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ArtificialAnalysisFetchError(field, transient=False)
    return result


def _integer(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ArtificialAnalysisFetchError(field, transient=False)
    return value


def _bounded_text(value: Any, *, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise ArtificialAnalysisFetchError(field, transient=False)
    text = " ".join(value.split()).strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ArtificialAnalysisFetchError(field, transient=False)
    return text


class ArtificialAnalysisIndex:
    """Periodic, last-known-good cache for the Artificial Analysis Intelligence Index.

    Network refreshes run outside request threads. A missing key, upstream outage,
    invalid response, or unwritable cache can therefore never affect GPU Watch's
    collector or health endpoint.
    """

    def __init__(
        self,
        cache_path: Path,
        secret_path: Path,
        *,
        api_url: str = API_URL,
        refresh_interval_seconds: float = REFRESH_INTERVAL_SECONDS,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
        failure_retry_interval_seconds: float = FAILURE_RETRY_INTERVAL_SECONDS,
        request_timeout_seconds: float = 10.0,
        max_fetch_attempts: int = MAX_FETCH_ATTEMPTS,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.secret_path = Path(secret_path)
        self.api_url = str(api_url)
        self.refresh_interval_seconds = max(60.0, float(refresh_interval_seconds))
        self.stale_after_seconds = max(self.refresh_interval_seconds, float(stale_after_seconds))
        self.failure_retry_interval_seconds = max(60.0, float(failure_retry_interval_seconds))
        self.request_timeout_seconds = min(30.0, max(1.0, float(request_timeout_seconds)))
        self.max_fetch_attempts = min(3, max(1, int(max_fetch_attempts)))
        # urllib forwards custom headers while following redirects. The data API
        # has one canonical HTTPS endpoint, so rejecting redirects entirely is
        # both simpler and safer than risking a cross-origin x-api-key leak.
        self._opener = opener or build_opener(_RejectRedirects()).open
        self._clock = clock or time.time
        self._sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_attempt_at: float | None = None
        self._cache = self._load_cache()
        if self._cache is not None:
            self._last_attempt_at = float(self._cache["fetched_at"])
            self._next_attempt_at = float(self._cache["fetched_at"]) + self.refresh_interval_seconds
        else:
            self._next_attempt_at = 0.0

    def _read_api_key(self) -> str | None:
        """Read on every attempt so key rotation never requires a service restart."""
        descriptor: int | None = None
        try:
            if stat.S_ISLNK(self.secret_path.lstat().st_mode):
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.secret_path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 4096:
                return None
            if os.name == "posix" and (
                metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                return None
            raw = os.read(descriptor, 4097)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not raw or len(raw) > 4096:
            return None
        try:
            key = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not 8 <= len(key) <= 512 or any(char.isspace() or ord(char) < 33 for char in key):
            return None
        return key

    def _load_cache(self) -> dict[str, Any] | None:
        try:
            if self.cache_path.stat().st_size > MAX_CACHE_BYTES:
                return None
            raw = self.cache_path.read_text(encoding="utf-8")
            value = json.loads(raw)
            return self._validate_cache(value)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        except (ArtificialAnalysisFetchError, TypeError, ValueError):
            return None

    def _validate_cache(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise ArtificialAnalysisFetchError("cache_schema", transient=False)
        fetched_at = _finite_number(
            value.get("fetched_at"), minimum=1, maximum=self._clock() + 300, field="cache_timestamp"
        )
        index_version = value.get("index_version")
        if not isinstance(index_version, str) or not _INDEX_VERSION.fullmatch(index_version):
            raise ArtificialAnalysisFetchError("cache_version", transient=False)
        total_models = _integer(value.get("total_models"), minimum=0, maximum=20_000, field="cache_total")
        raw_models = value.get("models")
        if not isinstance(raw_models, list) or not 1 <= len(raw_models) <= TOP_MODEL_LIMIT:
            raise ArtificialAnalysisFetchError("cache_models", transient=False)
        if total_models < len(raw_models):
            raise ArtificialAnalysisFetchError("cache_total", transient=False)
        models: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for position, raw_model in enumerate(raw_models, start=1):
            if not isinstance(raw_model, dict):
                raise ArtificialAnalysisFetchError("cache_model", transient=False)
            rank = _integer(raw_model.get("rank"), minimum=1, maximum=TOP_MODEL_LIMIT, field="cache_rank")
            if rank != position:
                raise ArtificialAnalysisFetchError("cache_rank", transient=False)
            model_id = _bounded_text(raw_model.get("id"), maximum=160, field="cache_id")
            if not _MODEL_ID.fullmatch(model_id) or model_id in seen_ids:
                raise ArtificialAnalysisFetchError("cache_id", transient=False)
            seen_ids.add(model_id)
            models.append({
                "rank": rank,
                "id": model_id,
                "name": _bounded_text(raw_model.get("name"), maximum=240, field="cache_name"),
                "provider": _bounded_text(raw_model.get("provider"), maximum=160, field="cache_provider"),
                "score": _finite_number(raw_model.get("score"), minimum=0, maximum=100, field="cache_score"),
            })
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fetched_at": fetched_at,
            "index_version": index_version,
            "total_models": total_models,
            "models": models,
        }

    def _persist_cache(self, value: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_name(
            f".{self.cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_CACHE_BYTES:
            raise ArtificialAnalysisFetchError("cache_size", transient=False)
        try:
            with temporary.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.cache_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _retry_after(headers: Any, *, now: float | None = None) -> float | None:
        try:
            value = headers.get("Retry-After")
        except AttributeError:
            return None
        try:
            delay = float(value)
        except (TypeError, ValueError):
            try:
                target = parsedate_to_datetime(str(value))
                if target.tzinfo is None:
                    return None
                delay = max(0.0, target.timestamp() - (time.time() if now is None else now))
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(delay) or delay < 0:
            return None
        return min(MAX_RETRY_AFTER_SECONDS, delay)

    def _read_page(self, api_key: str, page: int) -> dict[str, Any]:
        separator = "&" if "?" in self.api_url else "?"
        url = f"{self.api_url}{separator}{urlencode({'page': page})}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"GPU-Watch/{__version__}",
                "x-api-key": api_key,
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self.request_timeout_seconds)
        except HTTPError as exc:
            status = int(exc.code)
            failure = ArtificialAnalysisFetchError(
                "upstream_http",
                transient=status == 429 or 500 <= status <= 599,
                retry_after=self._retry_after(exc.headers, now=self._clock()),
            )
            try:
                exc.close()
            except OSError:
                pass
            raise failure from None
        except (URLError, TimeoutError, OSError):
            raise ArtificialAnalysisFetchError("upstream_transport", transient=True) from None

        try:
            try:
                status = int(getattr(response, "status", None) or response.getcode())
                if status != 200:
                    raise ArtificialAnalysisFetchError(
                        "upstream_http",
                        transient=status == 429 or 500 <= status <= 599,
                        retry_after=self._retry_after(getattr(response, "headers", None), now=self._clock()),
                    )
                headers = getattr(response, "headers", None)
                try:
                    declared_size = int(headers.get("Content-Length")) if headers is not None else 0
                except (TypeError, ValueError):
                    declared_size = 0
                if declared_size < 0 or declared_size > MAX_PAGE_BYTES:
                    raise ArtificialAnalysisFetchError("response_size", transient=False)
                body = response.read(MAX_PAGE_BYTES + 1)
            except ArtificialAnalysisFetchError:
                raise
            except (URLError, TimeoutError, OSError, HTTPException):
                raise ArtificialAnalysisFetchError("upstream_transport", transient=True) from None
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
        if not isinstance(body, bytes) or len(body) > MAX_PAGE_BYTES:
            raise ArtificialAnalysisFetchError("response_size", transient=False)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArtificialAnalysisFetchError("response_json", transient=False) from None
        if not isinstance(value, dict):
            raise ArtificialAnalysisFetchError("response_shape", transient=False)
        return value

    def _validate_page(
        self,
        value: dict[str, Any],
        requested_page: int,
        *,
        expected_pages: int | None,
        expected_version: str | None,
    ) -> tuple[list[dict[str, Any]], int, str]:
        if value.get("tier") not in {"free", "pro", "commercial"}:
            raise ArtificialAnalysisFetchError("response_tier", transient=False)
        raw_version = _finite_number(
            value.get("intelligence_index_version"), minimum=0.1, maximum=999, field="response_version"
        )
        index_version = format(raw_version, "g")
        if not _INDEX_VERSION.fullmatch(index_version) or (
            expected_version is not None and index_version != expected_version
        ):
            raise ArtificialAnalysisFetchError("response_version", transient=False)

        pagination = value.get("pagination")
        if not isinstance(pagination, dict):
            raise ArtificialAnalysisFetchError("response_pagination", transient=False)
        page = _integer(pagination.get("page"), minimum=1, maximum=MAX_PAGES, field="response_page")
        page_size = _integer(pagination.get("page_size"), minimum=1, maximum=1000, field="response_page_size")
        total_pages = _integer(pagination.get("total_pages"), minimum=1, maximum=MAX_PAGES, field="response_pages")
        has_more = pagination.get("has_more")
        if (
            page != requested_page
            or page > total_pages
            or not isinstance(has_more, bool)
            or has_more != (page < total_pages)
            or (expected_pages is not None and total_pages != expected_pages)
        ):
            raise ArtificialAnalysisFetchError("response_pagination", transient=False)

        raw_models = value.get("data")
        if not isinstance(raw_models, list) or len(raw_models) > page_size:
            raise ArtificialAnalysisFetchError("response_models", transient=False)
        models: list[dict[str, Any]] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                raise ArtificialAnalysisFetchError("response_model", transient=False)
            model_id = _bounded_text(raw_model.get("id"), maximum=160, field="response_id")
            if not _MODEL_ID.fullmatch(model_id):
                raise ArtificialAnalysisFetchError("response_id", transient=False)
            name = _bounded_text(raw_model.get("name"), maximum=240, field="response_name")
            creator = raw_model.get("model_creator")
            evaluations = raw_model.get("evaluations")
            if not isinstance(creator, dict) or not isinstance(evaluations, dict):
                raise ArtificialAnalysisFetchError("response_model", transient=False)
            provider = _bounded_text(creator.get("name"), maximum=160, field="response_provider")
            raw_score = evaluations.get("artificial_analysis_intelligence_index")
            if raw_score is None:
                continue
            score = _finite_number(raw_score, minimum=0, maximum=100, field="response_score")
            models.append({"id": model_id, "name": name, "provider": provider, "score": score})
        return models, total_pages, index_version

    def _fetch_all(self, api_key: str) -> dict[str, Any]:
        all_models: list[dict[str, Any]] = []
        expected_pages: int | None = None
        expected_version: str | None = None
        for page in range(1, MAX_PAGES + 1):
            raw_page = self._read_page(api_key, page)
            page_models, total_pages, index_version = self._validate_page(
                raw_page,
                page,
                expected_pages=expected_pages,
                expected_version=expected_version,
            )
            if expected_pages is None:
                expected_pages = total_pages
                expected_version = index_version
            all_models.extend(page_models)
            if page == total_pages:
                break
        if expected_pages is None or expected_version is None or not all_models:
            raise ArtificialAnalysisFetchError("response_empty", transient=False)

        unique_models: dict[str, dict[str, Any]] = {}
        identical_duplicates = 0
        for model in all_models:
            existing = unique_models.get(model["id"])
            if existing is None:
                unique_models[model["id"]] = model
                continue
            if existing != model:
                # A moving pagination boundary can briefly return conflicting
                # revisions. Retry the complete snapshot; never choose one.
                raise ArtificialAnalysisFetchError("response_duplicate", transient=True)
            identical_duplicates += 1
        if identical_duplicates:
            # The upstream API can repeat the exact same model at a page
            # boundary. Identical rows carry no ambiguity and are safe to
            # collapse while conflicting rows remain a validation error.
            audit("artificial_analysis_duplicates_ignored", count=identical_duplicates)
        all_models = list(unique_models.values())
        all_models.sort(
            key=lambda model: (
                -model["score"],
                model["name"].casefold(),
                model["provider"].casefold(),
                model["id"],
            )
        )
        top_models = [
            {"rank": rank, **model}
            for rank, model in enumerate(all_models[:TOP_MODEL_LIMIT], start=1)
        ]
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fetched_at": self._clock(),
            "index_version": expected_version,
            "total_models": len(all_models),
            "models": top_models,
        }

    def _fetch_with_retry(self, api_key: str) -> dict[str, Any]:
        for attempt in range(1, self.max_fetch_attempts + 1):
            try:
                return self._fetch_all(api_key)
            except ArtificialAnalysisFetchError as exc:
                if not exc.transient or attempt >= self.max_fetch_attempts:
                    raise
                # A long Retry-After belongs in the scheduler, not a sleeping
                # daemon thread. Propagate it so _run_refresh can defer safely.
                if (exc.retry_after or 0.0) > MAX_INLINE_RETRY_DELAY_SECONDS:
                    raise
                exponential = float(2 ** (attempt - 1))
                self._sleeper(
                    min(MAX_INLINE_RETRY_DELAY_SECONDS, max(exponential, exc.retry_after or 0.0))
                )
        raise ArtificialAnalysisFetchError("retry_exhausted", transient=False)

    def _run_refresh(self) -> bool:
        attempted_at = self._clock()
        with self._lock:
            self._last_attempt_at = attempted_at
        try:
            api_key = self._read_api_key()
            if api_key is None:
                raise ArtificialAnalysisFetchError("key_unavailable", transient=False)
            fresh = self._fetch_with_retry(api_key)
            fresh = self._validate_cache(fresh)
            self._persist_cache(fresh)
            with self._lock:
                self._cache = fresh
                self._next_attempt_at = float(fresh["fetched_at"]) + self.refresh_interval_seconds
            audit(
                "artificial_analysis_refreshed",
                models=fresh["total_models"],
                index_version=fresh["index_version"],
            )
            return True
        except Exception as exc:
            # Log only a fixed category or exception type; upstream bodies and the
            # dynamically read key never enter logs or public responses.
            category = exc.category if isinstance(exc, ArtificialAnalysisFetchError) else type(exc).__name__
            retry_delay = self.failure_retry_interval_seconds
            if isinstance(exc, ArtificialAnalysisFetchError) and exc.retry_after is not None:
                retry_delay = max(retry_delay, exc.retry_after)
            with self._lock:
                self._next_attempt_at = attempted_at + retry_delay
            audit("artificial_analysis_refresh_failed", category=category)
            return False
        finally:
            with self._lock:
                self._refreshing = False

    def refresh_now(self, *, force: bool = False) -> bool:
        """Synchronously refresh (primarily for tests and explicit maintenance)."""
        with self._lock:
            if self._refreshing or (not force and self._clock() < self._next_attempt_at):
                return False
            self._refreshing = True
        return self._run_refresh()

    def refresh_if_due(self) -> bool:
        """Start one daemon refresh when due; concurrent callers are coalesced."""
        with self._lock:
            if self._refreshing or self._clock() < self._next_attempt_at:
                return False
            self._refreshing = True
        worker = threading.Thread(
            target=self._run_refresh,
            name="gpu-watch-artificial-analysis",
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            with self._lock:
                self._refreshing = False
                self._next_attempt_at = self._clock() + self.failure_retry_interval_seconds
            audit("artificial_analysis_refresh_failed", category="thread_start")
            return False
        return True

    def payload(self, *, schedule_refresh: bool = True) -> dict[str, Any]:
        if schedule_refresh:
            self.refresh_if_due()
        now = self._clock()
        with self._lock:
            cached = self._cache
            last_attempt_at = self._last_attempt_at
        if cached is None:
            return unavailable_payload(
                stale_after_seconds=self.stale_after_seconds,
                last_attempt_at=_iso_utc(last_attempt_at),
            )
        age = max(0.0, now - float(cached["fetched_at"]))
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "status": "stale" if age > self.stale_after_seconds else "fresh",
            "index_version": cached["index_version"],
            "total_models": cached["total_models"],
            "last_success_at": _iso_utc(float(cached["fetched_at"])),
            "last_attempt_at": _iso_utc(last_attempt_at),
            "stale_after_seconds": int(self.stale_after_seconds),
            "source": {
                "name": "Artificial Analysis",
                "url": SOURCE_URL,
                "methodology_url": METHODOLOGY_URL,
            },
            "models": [dict(model) for model in cached["models"]],
        }
