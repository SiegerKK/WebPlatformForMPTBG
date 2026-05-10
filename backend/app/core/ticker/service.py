"""
Ticker service — drives real-time turn advancement for all game matches.

Called either by the background asyncio task (production) or by the manual
POST /api/matches/{id}/tick endpoint (testing / admin).

Game-specific tick logic lives in each game's RuleSet.tick() implementation.
"""
import logging
import time
import json
from typing import Dict

from sqlalchemy.orm import Session

from app.core.matches.models import Match, MatchStatus

logger = logging.getLogger(__name__)

# ── Debug auto-tick context cache ────────────────────────────────────────────
# Maps context_id → match_id for all active zone_map contexts.
# Refreshed from the DB at most every _DEBUG_CACHE_TTL seconds to avoid
# hammering the database from the 500 ms debug ticker.
_debug_ctx_cache: Dict[str, str] = {}
_debug_ctx_cache_ts: float = 0.0
_DEBUG_CACHE_TTL: float = 5.0  # seconds between DB refreshes

# ── Per-speed tick throttle ───────────────────────────────────────────────────
# In-memory map of context_id → monotonic timestamp of the last tick.
# Not persisted — resets on server restart (acceptable for a debug feature).
#
# Thread-safety note: tick_debug_auto_matches() is invoked exclusively from
# the single _debug_auto_ticker background task (via asyncio.to_thread), so
# only one call runs at a time.  No locking is required.
_tick_last: Dict[str, float] = {}

# Minimum real-time gap (seconds) between consecutive ticks at each named speed.
# 1 tick == 1 game-minute, so "realtime" maps game-time 1:1 to real time.
# Keys must match AUTO_TICK_VALID_SPEEDS in app.core.commands.pipeline.
_TICK_INTERVALS: Dict[str, float] = {
    "realtime": 60.0,  # 1 tick/min — 1 game-minute per real minute (true 1:1)
    "x10":       6.0,  # 10× realtime — 1 tick per 6 real seconds
    "x100":      0.6,  # 100× realtime — 1 tick per 0.6 real seconds
    "x600":      0.1,  # 600× realtime — 1 tick per 0.1 real seconds
}


def tick_match(match_id_str: str, db: Session) -> dict:
    """
    Advance the world by one game-turn for the given match.

    Delegates all game-specific logic to the match's registered RuleSet.
    Returns a summary dict with events emitted (or an ``"error"`` key on
    failure).
    """
    match = db.query(Match).filter(Match.id == match_id_str).first()
    if not match:
        return {"error": "match not found"}
    if match.status != MatchStatus.ACTIVE:
        return {"error": f"match status is {match.status}, not active"}

    from app.core.commands.pipeline import get_ruleset
    ruleset = get_ruleset(match.game_id)
    if not ruleset:
        return {"error": f"no ruleset registered for game '{match.game_id}'"}

    _started = time.perf_counter()
    result = ruleset.tick(match_id_str, db)
    tick_total_ms = (time.perf_counter() - _started) * 1000.0

    # Notify connected WebSocket clients that the state changed.
    if "error" not in result:
        from app.core.ws.manager import ws_manager
        ws_payload = {
            "type": "ticked",
            "match_id": match_id_str,
            "world_turn": result.get("world_turn"),
            "world_hour": result.get("world_hour"),
            "world_day": result.get("world_day"),
            "world_minute": result.get("world_minute"),
            "new_events": result.get("new_events", []),
        }
        ws_manager.notify(match_id_str, ws_payload)

        try:
            from app.games.zone_stalkers.performance_metrics import record_tick_metrics
            metrics_payload: dict = {
                "tick_total_ms": round(tick_total_ms, 3),
                "events_emitted": len(result.get("new_events", []) or []),
                "response_size_bytes": len(
                    json.dumps(ws_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ),
            }
            if match.game_id == "zone_stalkers":
                from app.core.contexts.models import ContextStatus, GameContext
                zone_ctx = db.query(GameContext).filter(
                    GameContext.match_id == match.id,
                    GameContext.context_type == "zone_map",
                    GameContext.status == ContextStatus.ACTIVE,
                ).first()
                if zone_ctx:
                    metrics_payload["context_id"] = str(zone_ctx.id)
            record_tick_metrics(match_id_str, metrics_payload)
        except Exception as exc:
            logger.debug("tick metric collection skipped for %s: %s", match_id_str, exc)

    return result


def tick_all_active_matches(db: Session) -> dict:
    """Tick all active matches. Called by the background scheduler."""
    matches = db.query(Match).filter(Match.status == MatchStatus.ACTIVE).all()

    results = []
    for match in matches:
        try:
            result = tick_match(str(match.id), db)
            results.append({"match_id": str(match.id), **result})
        except Exception as exc:
            logger.error("Ticker failed for match %s: %s", match.id, exc)
            results.append({"match_id": str(match.id), "error": str(exc)})

    return {"ticked": len(results), "results": results}


# ── Debug auto-tick (fast background ticker) ──────────────────────────────────

def _refresh_debug_context_cache(db: Session) -> Dict[str, str]:
    """
    Return {context_id: match_id} for all active zone_map contexts.
    Result is cached for up to *_DEBUG_CACHE_TTL* seconds.
    """
    global _debug_ctx_cache, _debug_ctx_cache_ts
    now = time.monotonic()
    if now - _debug_ctx_cache_ts < _DEBUG_CACHE_TTL:
        return _debug_ctx_cache

    try:
        from app.core.contexts.models import GameContext, ContextStatus

        rows = (
            db.query(GameContext.id, GameContext.match_id)
            .join(Match, Match.id == GameContext.match_id)
            .filter(
                GameContext.context_type == "zone_map",
                GameContext.status == ContextStatus.ACTIVE,
                Match.status == MatchStatus.ACTIVE,
            )
            .all()
        )
        _debug_ctx_cache = {str(r.id): str(r.match_id) for r in rows}
        _debug_ctx_cache_ts = now
    except Exception as exc:
        logger.warning("debug ctx cache refresh failed: %s", exc)
    return _debug_ctx_cache


def tick_debug_auto_matches() -> dict:
    """
    Tick every active context that has the auto-tick flag set in its game
    state.  Checks for ``auto_tick_enabled`` (core generic flag set by the
    ``set_auto_tick`` platform meta-command) or the legacy
    ``debug_auto_tick`` flag (Zone Stalkers backward compat).

    Tick cadence is controlled by ``auto_tick_speed`` in the context state:
    * ``"realtime"`` — throttle to 1 tick/second
    * ``"x10"``      — throttle to 1 tick/100 ms
    * ``"x100"``     — no throttle (limited only by loop cadence)
    Backward compat: ``auto_tick_slow_mode=True`` maps to ``"realtime"``.

    Designed to be called from a fast background task (every ~100 ms) via
    ``asyncio.to_thread`` so that the asyncio event loop is not blocked.

    Opens and closes its own database session so that the SQLAlchemy session
    lifecycle is entirely contained within the worker thread.

    Uses ``get_context_flag`` for an efficient single Redis GET per match
    (no full state deserialisation unless the flag is set).
    """
    from app.core.state_cache.service import get_context_flag
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ctx_map = _refresh_debug_context_cache(db)
        if not ctx_map:
            return {"ticked": 0}

        ticked = 0
        for ctx_id, match_id in ctx_map.items():
            try:
                # Support both the generic core flag and the legacy game flag.
                if not (get_context_flag(ctx_id, "auto_tick_enabled", default=False)
                        or get_context_flag(ctx_id, "debug_auto_tick", default=False)):
                    continue

                # Determine the desired speed and apply throttle accordingly.
                speed = get_context_flag(ctx_id, "auto_tick_speed", default=None)
                if speed is None:
                    # Backward compat: legacy slow_mode boolean
                    if get_context_flag(ctx_id, "auto_tick_slow_mode", default=False):
                        speed = "realtime"
                    else:
                        speed = "x100"

                interval = _TICK_INTERVALS.get(speed, 0.0)
                if interval > 0.0:
                    now = time.monotonic()
                    if now - _tick_last.get(ctx_id, 0.0) < interval:
                        continue
                    _tick_last[ctx_id] = now

                result = tick_match(match_id, db)
                if "error" not in result:
                    ticked += 1
                else:
                    # Match or context no longer valid — force cache refresh.
                    global _debug_ctx_cache_ts
                    _debug_ctx_cache_ts = 0.0
            except Exception as exc:
                logger.warning("debug auto-tick failed for match %s: %s", match_id, exc)

        return {"ticked": ticked}
    finally:
        db.close()
