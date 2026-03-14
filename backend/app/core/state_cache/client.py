"""
Lazy Redis client for the platform state cache.

Returns ``None`` (instead of raising) when Redis is not configured or not
reachable, so that every caller can transparently fall back to DB-only
operation without special-casing the missing dependency.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import redis as redis_lib

logger = logging.getLogger(__name__)

_client: Optional["redis_lib.Redis"] = None
_client_checked: bool = False


def get_redis() -> Optional["redis_lib.Redis"]:
    """Return the shared Redis client, or ``None`` if unavailable."""
    global _client, _client_checked
    if _client_checked:
        return _client

    _client_checked = True
    try:
        import redis  # type: ignore[import]
        from app.config import settings

        r: redis_lib.Redis = redis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        r.ping()
        _client = r
        logger.info("Redis connected: %s", settings.REDIS_URL)
    except Exception as exc:
        logger.warning(
            "Redis unavailable — falling back to DB-only state storage: %s", exc
        )
        _client = None

    return _client


def _reset_for_testing() -> None:
    """Reset the singleton — for use in unit tests only."""
    global _client, _client_checked
    _client = None
    _client_checked = False
