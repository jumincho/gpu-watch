from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Mapping
from typing import Any


_LOGGER = logging.getLogger("gpu_watch")
if not _LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False

_SENSITIVE_KEY = re.compile(
    r"(?:pass|pin|secret|token|authorization|cookie|command|cmd|api[_-]?key|private[_-]?key|(?:^|[_-])key(?:$|[_-]))",
    re.I,
)


def _safe(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if depth >= 3:
        return "[truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            safe_key = re.sub(r"[\r\n\t]+", " ", str(key)).strip()[:80]
            result[safe_key] = "[redacted]" if _SENSITIVE_KEY.search(safe_key) else _safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe(item, depth=depth + 1) for item in list(value)[:20]]
    return re.sub(r"[\r\n\t]+", " ", str(value)).strip()[:300]


def audit(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"ts": round(time.time(), 3), "event": str(event)[:80]}
    for key, value in fields.items():
        if _SENSITIVE_KEY.search(str(key)):
            continue
        payload[str(key)[:80]] = _safe(value)
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
