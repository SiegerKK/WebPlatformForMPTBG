from __future__ import annotations

import time
from collections import deque
from typing import Any

_MAX_METRICS = 500
_tick_metrics: deque[dict[str, Any]] = deque(maxlen=_MAX_METRICS)


def record_tick_metrics(match_id: str, payload: dict[str, Any]) -> None:
    _tick_metrics.append(
        {
            "timestamp": time.time(),
            "match_id": str(match_id),
            **payload,
        }
    )


def get_tick_metrics(*, match_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), _MAX_METRICS))
    values = list(_tick_metrics)
    if match_id:
        values = [item for item in values if item.get("match_id") == str(match_id)]
    return values[-safe_limit:]


def get_last_tick_metrics(*, match_id: str | None = None) -> dict[str, Any] | None:
    metrics = get_tick_metrics(match_id=match_id, limit=1)
    return metrics[-1] if metrics else None

