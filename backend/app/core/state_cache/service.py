"""
Context state cache — Redis write-through cache for ``GameContext.state_blob``.

**Framework layer**: game code calls these helpers instead of reading/writing
``context.state_blob`` directly.  When Redis is available the authoritative
state lives there (compressed), and the DB is written every
``STATE_PERSIST_INTERVAL_TICKS`` ticks instead of every tick.  When Redis is
unavailable every call falls through to DB-only behaviour, so there is no
data-loss risk.

Usage
-----
    # Read (Redis first, DB fallback):
    state = load_context_state(context_id, context_orm_obj)

    # Write (always to Redis; to DB based on interval / force flag):
    save_context_state(context_id, new_state, context_orm_obj)

    # Force a DB write (e.g. on match end):
    save_context_state(context_id, final_state, context_orm_obj, force_persist=True)

The caller must still call ``db.commit()`` after ``save_context_state``.
``save_context_state`` only assigns ``context_obj.state_blob`` (and increments
``context_obj.state_version``) when it decides to flush to the DB; otherwise
the ORM object is left untouched and the ``db.commit()`` will not write
``state_blob``.

Compression
-----------
States are stored in Redis as zlib-compressed UTF-8 JSON (level 6).  For a
typical saturated Zone Stalkers state (~5.8 MB JSON) this reduces to ~400 KB,
cutting Redis memory usage and network I/O by ~14x.
"""
from __future__ import annotations

import copy
import json
import logging
import zlib
from typing import Any, Dict

from app.core.state_cache.client import get_redis

logger = logging.getLogger(__name__)

# Redis key prefixes
_STATE_KEY_PREFIX = "ctx:state:"
_TICKS_KEY_PREFIX = "ctx:ticks:"
_AUTO_TICK_KEY_PREFIX = "ctx:auto_tick:"

# Default TTL: 24 hours.  Enough to survive overnight without a tick.
_STATE_TTL = 86400


# ── Internal helpers ──────────────────────────────────────────────────────────


def _state_key(context_id: Any) -> str:
    return f"{_STATE_KEY_PREFIX}{context_id}"


def _ticks_key(context_id: Any) -> str:
    return f"{_TICKS_KEY_PREFIX}{context_id}"


def _auto_tick_key(context_id: Any) -> str:
    return f"{_AUTO_TICK_KEY_PREFIX}{context_id}"


def _compress(state: Dict[str, Any]) -> bytes:
    level = 6
    try:
        from app.config import settings
        level = int(getattr(settings, "STATE_CACHE_COMPRESSION_LEVEL", 6))
    except Exception:
        level = 6
    level = max(1, min(9, level))
    return zlib.compress(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        level=level,
    )


def _decompress(data: bytes) -> Dict[str, Any]:
    return json.loads(zlib.decompress(data).decode("utf-8"))


def _get_persist_interval() -> int:
    try:
        from app.config import settings

        return max(1, settings.STATE_PERSIST_INTERVAL_TICKS)
    except Exception:
        return 1


# ── Public API ────────────────────────────────────────────────────────────────


def load_context_state(
    context_id: Any,
    context_obj: Any,
) -> Dict[str, Any]:
    """
    Load the state for *context_id* from Redis if possible, otherwise fall
    back to ``context_obj.state_blob``.

    :param context_id:  UUID of the context (str or UUID object).
    :param context_obj: SQLAlchemy ``GameContext`` ORM object used as DB
                        fallback when Redis is unavailable or the key is cold.
    :returns:           A fresh mutable state dict (never ``None``).
    """
    r = get_redis()
    if r is not None:
        try:
            raw = r.get(_state_key(str(context_id)))
            if raw is not None:
                return _decompress(raw)
        except Exception as exc:
            logger.warning(
                "Redis read failed for context %s: %s", context_id, exc
            )

    # DB fallback — deep-copy so callers can mutate freely (mirrors the
    # fresh deserialization that the Redis path gives via json.loads).
    return copy.deepcopy(context_obj.state_blob or {})


def save_context_state(
    context_id: Any,
    state: Dict[str, Any],
    context_obj: Any,
    *,
    force_persist: bool = False,
) -> bool:
    """
    Persist *state* for *context_id*.

    **Always** writes to Redis when available (fast, compressed).
    Writes to the DB (via ``context_obj.state_blob``) either:
    * every ``STATE_PERSIST_INTERVAL_TICKS`` ticks, or
    * immediately when Redis is unavailable (safe fallback), or
    * immediately when *force_persist* is ``True`` (e.g. match end,
      user-initiated commands).

    :param context_id:   UUID of the context.
    :param state:        New state dict to persist.
    :param context_obj:  SQLAlchemy ``GameContext`` ORM object.  Its
                         ``state_blob`` and ``state_version`` are set only
                         when a DB write is scheduled so that ``db.commit()``
                         skips the expensive JSON column update on other ticks.
    :param force_persist: When ``True``, always write to the DB regardless of
                          the configured interval.
    :returns:            ``True`` if ``context_obj.state_blob`` was assigned
                         (i.e. a DB write will occur on next ``db.commit()``).
    """
    r = get_redis()
    should_write_db: bool

    if r is None:
        # Redis unavailable — always persist to DB for safety
        should_write_db = True
    else:
        # Write compressed state to Redis
        redis_ok = True
        try:
            r.set(_state_key(str(context_id)), _compress(state), ex=_STATE_TTL)
        except Exception as exc:
            logger.warning(
                "Redis write failed for context %s: %s", context_id, exc
            )
            redis_ok = False

        if not redis_ok or force_persist:
            should_write_db = True
        else:
            interval = _get_persist_interval()
            if interval <= 1:
                should_write_db = True
            else:
                try:
                    tick_no = int(r.incr(_ticks_key(str(context_id))))
                    r.expire(_ticks_key(str(context_id)), _STATE_TTL)
                    should_write_db = tick_no % interval == 0
                except Exception as exc:
                    logger.warning(
                        "Redis tick counter failed for context %s: %s",
                        context_id,
                        exc,
                    )
                    should_write_db = True

    if should_write_db:
        context_obj.state_blob = state
        context_obj.state_version += 1

    return should_write_db


def invalidate_context_state(context_id: Any) -> None:
    """
    Remove *context_id* from the Redis cache.

    Call this whenever the state is modified outside the normal
    ``save_context_state`` path (e.g. admin resets, test seed).
    """
    r = get_redis()
    if r is not None:
        try:
            r.delete(_state_key(str(context_id)), _ticks_key(str(context_id)))
        except Exception as exc:
            logger.warning(
                "Redis invalidate failed for context %s: %s", context_id, exc
            )


def get_context_flag(context_id: Any, flag_name: str, default: Any = None) -> Any:
    """
    Read a single flag from the context state stored in Redis.

    Uses the compressed Redis cache for an efficient O(1) read.
    Returns *default* if Redis is unavailable or the key is not cached.
    Does **not** fall back to the DB — callers that need a DB fallback should
    use ``load_context_state`` instead.
    """
    r = get_redis()
    if r is None:
        return default
    try:
        raw = r.get(_state_key(str(context_id)))
        if raw is None:
            return default
        state = _decompress(raw)
        return state.get(flag_name, default)
    except Exception as exc:
        logger.warning(
            "get_context_flag failed for context %s flag %s: %s",
            context_id, flag_name, exc,
        )
        return default


def set_auto_tick_runtime(
    context_id: Any,
    *,
    enabled: bool,
    speed: str | None,
    updated_at: float,
) -> None:
    """Persist lightweight auto-tick runtime settings in Redis."""
    r = get_redis()
    if r is None:
        return
    try:
        payload = {
            "enabled": bool(enabled),
            "speed": speed,
            "updated_at": float(updated_at),
        }
        r.set(_auto_tick_key(str(context_id)), json.dumps(payload, separators=(",", ":")), ex=_STATE_TTL)
    except Exception as exc:
        logger.warning("set_auto_tick_runtime failed for context %s: %s", context_id, exc)


def get_auto_tick_runtime(context_id: Any) -> dict[str, Any] | None:
    """Read lightweight auto-tick runtime settings from Redis."""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_auto_tick_key(str(context_id)))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception as exc:
        logger.warning("get_auto_tick_runtime failed for context %s: %s", context_id, exc)
        return None
