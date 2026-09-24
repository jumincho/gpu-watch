from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class MaintenanceWorker:
    """Runs bounded database maintenance away from the request path."""

    def __init__(
        self,
        callback: Callable[[], dict[str, Any]],
        *,
        initial_delay_seconds: float = 30,
        interval_seconds: float = 3600,
    ) -> None:
        self.callback = callback
        self.initial_delay_seconds = max(0.0, float(initial_delay_seconds))
        self.interval_seconds = max(60.0, float(interval_seconds))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="gpu-watch-maintenance", daemon=True)
        self._lock = threading.Lock()
        self.last_started_at: float | None = None
        self.last_completed_at: float | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] = {}

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(max(0.0, float(timeout)))

    def run(self) -> None:
        if self.stop_event.wait(self.initial_delay_seconds):
            return
        while not self.stop_event.is_set():
            started = time.time()
            with self._lock:
                self.last_started_at = started
            try:
                result = self.callback()
                error = None
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                result = {}
                error = str(exc)
            with self._lock:
                self.last_completed_at = time.time()
                self.last_error = error
                self.last_result = result
            if self.stop_event.wait(self.interval_seconds):
                return

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "alive": self.thread.is_alive(),
                "last_started_at": self.last_started_at,
                "last_completed_at": self.last_completed_at,
                "last_error": self.last_error,
                "last_result": dict(self.last_result),
            }
