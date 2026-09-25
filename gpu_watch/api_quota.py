"""Persist only quota metadata; never API keys or response bodies."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


class ApiQuota:
    def __init__(self, path: Path, clock: Any) -> None:
        self.path, self.clock = Path(path), clock
        self.limit = self.remaining = self.reset = None
        self.pages = 20  # Safe preflight before the first known pagination size.
        self.not_before = 0.0
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 4096:
                return
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self._accept(value.get("limit"), value.get("remaining"), value.get("reset"))
            if type(value.get("pages")) is int and 1 <= value["pages"] <= 20:
                self.pages = value["pages"]
            stamp = float(value.get("not_before", 0))
            if math.isfinite(stamp) and 0 <= stamp <= self.clock() + 86400:
                self.not_before = stamp
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _accept(self, limit: Any, remaining: Any, reset: Any) -> None:
        try:
            limit, remaining, reset = int(limit), int(remaining), float(reset)
        except (ValueError, TypeError, OverflowError):
            return
        if (1 <= limit <= 1_000_000 and 0 <= remaining <= limit
                and math.isfinite(reset) and self.clock() - 86400 <= reset <= self.clock() + 86460):
            self.limit, self.remaining, self.reset = limit, remaining, reset

    def _roll_window(self) -> None:
        if self.reset is not None and self.clock() >= self.reset:
            self.remaining = self.limit
            self.reset = self.clock() + 86400

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"limit": self.limit, "remaining": self.remaining,
                           "reset": self.reset, "pages": self.pages,
                           "not_before": self.not_before}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def observe(self, headers: Any) -> None:
        if headers is None:
            return
        values = {str(k).lower(): v for k, v in headers.items()}
        self._accept(*(values.get("x-ratelimit-" + k) for k in ("limit", "remaining", "reset")))
        self.save()

    def required_wait(self, requests: int, *, reserve: bool = True) -> float:
        self._roll_window()
        if self.remaining is None:
            return 0.0
        margin = min(8, max(1, self.limit // 10)) if reserve else 0
        if self.remaining < requests + margin:
            return max(1.0, self.reset - self.clock() + 1)
        return 0.0

    def reserve_request(self) -> None:
        self._roll_window()
        if self.remaining is not None:
            self.remaining = max(0, self.remaining - 1)
            self.save()  # A transport failure or restart cannot refund a request.

    def next_delay(self, minimum: float, fallback: float = 6 * 3600) -> float:
        self._roll_window()
        if self.remaining is None:
            return max(minimum, fallback)
        wait = self.required_wait(self.pages)
        if wait:
            return max(minimum, wait)
        reserve = min(8, max(1, self.limit // 10))
        batches = max(0, (self.remaining - reserve) // self.pages)
        # Spread complete refreshes through the exact fixed quota window.
        return max(minimum, math.ceil(max(0, self.reset - self.clock()) / (batches + 1)))

    def schedule(self, timestamp: float) -> None:
        self.not_before = timestamp
        self.save()
