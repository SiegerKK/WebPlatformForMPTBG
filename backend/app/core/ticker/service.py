"""
Ticker service — drives real-time turn advancement for all game matches.

Called either by the background asyncio task (production) or by the manual
POST /api/matches/{id}/tick endpoint (testing / admin).

Game-specific tick logic lives in each game's RuleSet.tick() implementation.
"""
import logging
import time
import json
from typing import Any, Dict

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
AUTO_TICK_SPEED_MULTIPLIERS: Dict[str, int] = {
    "realtime": 1,
    "x10": 10,
    "x100": 100,
    "x600": 600,
}
_MAX_TICKS_PER_BATCH = 30
_MAX_ACCUMULATED_TICKS = 60
_auto_tick_runtime: Dict[str, Dict[str, float | bool]] = {}
_last_ws_sent_ts: Dict[str, float] = {}
_MAX_WS_UPDATES_PER_SECOND = 4.0

# ── WebSocket tick payload compaction ─────────────────────────────────────────
# Limits the number of events sent inline with every WS tick notification.
# Full event history remains available via GET /matches/{id}/events.
WS_TICK_EVENT_PREVIEW_LIMIT = 10

_WS_EVENT_PAYLOAD_KEEP_FIELDS: frozenset[str] = frozenset({
    "agent_id",
    "location_id",
    "world_turn",
    "objective_key",
    "action_kind",
    "summary",
})


def _compact_event_payload(payload: dict) -> dict:
    """Return a compact subset of an event payload (drop heavy nested data)."""
    return {k: payload[k] for k in _WS_EVENT_PAYLOAD_KEEP_FIELDS if k in payload}


def _compact_tick_event(event: dict) -> dict:
    """Return a compact representation of one tick event for WS delivery."""
    return {
        "event_type": event.get("event_type"),
        "payload": _compact_event_payload(event.get("payload", {})),
    }


def tick_match(match_id_str: str, db: Session) -> dict:
    """
    Advance the world by one game-turn for the given match.

    Delegates all game-specific logic to the match's registered RuleSet.tick().
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

        all_events: list[Any] = result.get("new_events", []) or []
        zone_delta = result.get("zone_delta")

        # Resolve zone context id (needed for delta WS payload and metrics).
        context_id_str: str | None = result.get("context_id")
        if context_id_str is None and match.game_id == "zone_stalkers":
            from app.core.contexts.models import ContextStatus, GameContext
            zone_ctx_for_ws = db.query(GameContext).filter(
                GameContext.match_id == match.id,
                GameContext.context_type == "zone_map",
                GameContext.status == ContextStatus.ACTIVE,
            ).first()
            if zone_ctx_for_ws:
                context_id_str = str(zone_ctx_for_ws.id)

        if zone_delta is not None:
            ws_payload = {
                "type": "zone_delta",
                "match_id": match_id_str,
                "context_id": context_id_str,
                **zone_delta,
            }
        else:
            # Fallback to compact ticked message (non-zone-stalkers games, or on delta build failure).
            preview_events = [_compact_tick_event(e) for e in all_events[:WS_TICK_EVENT_PREVIEW_LIMIT]]
            ws_payload = {
                "type": "ticked",
                "match_id": match_id_str,
                "world_turn": result.get("world_turn"),
                "world_hour": result.get("world_hour"),
                "world_day": result.get("world_day"),
                "world_minute": result.get("world_minute"),
                "event_count": len(all_events),
                "new_events_preview": preview_events,
                # requires_resync is set only for Zone Stalkers when a delta was expected
                # but could not be built.  Other games don't use delta sync at all.
                "requires_resync": match.game_id == "zone_stalkers",
            }
        ws_manager.notify(match_id_str, ws_payload)

        # Send scoped zone_debug_delta to each subscribed connection
        if zone_delta is not None and context_id_str:
            try:
                from app.core.ws.manager import get_debug_subscriptions
                from app.games.zone_stalkers.debug_delta import build_zone_debug_delta

                debug_subs = get_debug_subscriptions(match_id_str)
                if debug_subs:
                    old_state = result.get("old_state")
                    new_state = result.get("new_state")
                    if old_state is not None and new_state is not None:
                        debug_revision = int(new_state.get("_debug_revision", 0))
                        if debug_revision <= 0:
                            debug_revision = int(old_state.get("_debug_revision", 0)) + 1
                            new_state["_debug_revision"] = debug_revision
                        for conn_id, sub in debug_subs.items():
                            debug_delta = build_zone_debug_delta(
                                old_state=old_state,
                                new_state=new_state,
                                subscription=sub,
                                debug_revision=debug_revision,
                            )
                            if debug_delta:
                                payload = {
                                    "type": "zone_debug_delta",
                                    "match_id": match_id_str,
                                    "context_id": context_id_str,
                                    **debug_delta,
                                }
                                ws_manager.notify_to_connection(conn_id, payload)
            except Exception as exc:
                logger.debug("zone_debug_delta send failed: %s", exc)

        try:
            from app.games.zone_stalkers.performance_metrics import record_tick_metrics
            metrics_payload: dict = {
                "tick_total_ms": round(tick_total_ms, 3),
                "events_emitted": len(all_events),
                "response_size_bytes": len(
                    json.dumps(ws_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ),
            }
            if context_id_str:
                metrics_payload["context_id"] = context_id_str
            record_tick_metrics(match_id_str, metrics_payload)
        except Exception as exc:
            logger.debug("tick metric collection skipped for %s: %s", match_id_str, exc)

    return result


def tick_match_many(match_id_str: str, db: Session, max_ticks: int) -> dict:
    match = db.query(Match).filter(Match.id == match_id_str).first()
    if not match:
        return {"error": "match not found"}
    if match.status != MatchStatus.ACTIVE:
        return {"error": f"match status is {match.status}, not active"}

    from app.core.commands.pipeline import get_ruleset
    ruleset = get_ruleset(match.game_id)
    if not ruleset:
        return {"error": f"no ruleset registered for game '{match.game_id}'"}

    if hasattr(ruleset, "tick_many"):
        _started = time.perf_counter()
        result = ruleset.tick_many(match_id_str, db, max_ticks=max(0, int(max_ticks)))
        tick_total_ms = (time.perf_counter() - _started) * 1000.0
        if "error" not in result:
            # Reuse same WS + metrics side-effects as single tick.
            from app.core.ws.manager import ws_manager
            all_events: list[Any] = result.get("new_events", []) or []
            zone_delta = result.get("zone_delta")
            context_id_str = result.get("context_id")
            if zone_delta is not None:
                ws_payload = {
                    "type": "zone_delta",
                    "match_id": match_id_str,
                    "context_id": context_id_str,
                    **zone_delta,
                }
            else:
                preview_events = [_compact_tick_event(e) for e in all_events[:WS_TICK_EVENT_PREVIEW_LIMIT]]
                ws_payload = {
                    "type": "ticked",
                    "match_id": match_id_str,
                    "world_turn": result.get("world_turn"),
                    "world_hour": result.get("world_hour"),
                    "world_day": result.get("world_day"),
                    "world_minute": result.get("world_minute"),
                    "event_count": len(all_events),
                    "new_events_preview": preview_events,
                    "requires_resync": match.game_id == "zone_stalkers",
                    "ticks_advanced": result.get("ticks_advanced", 0),
                }
            # Coalesce non-critical WS updates in high-speed auto-run mode.
            _now = time.monotonic()
            _min_interval = 1.0 / max(0.1, _MAX_WS_UPDATES_PER_SECOND)
            _last = _last_ws_sent_ts.get(match_id_str, 0.0)
            _critical = _is_critical_batch_result(result)
            if _critical or (_now - _last >= _min_interval):
                ws_manager.notify(match_id_str, ws_payload)
                _last_ws_sent_ts[match_id_str] = _now
            try:
                from app.games.zone_stalkers.performance_metrics import record_tick_metrics
                record_tick_metrics(match_id_str, {
                    "tick_total_ms": round(tick_total_ms, 3),
                    "events_emitted": len(all_events),
                    "ticks_advanced": int(result.get("ticks_advanced", 0)),
                })
            except Exception:
                pass
        return result

    total = 0
    last: dict = {}
    for _ in range(max(0, int(max_ticks))):
        last = tick_match(match_id_str, db)
        if "error" in last:
            break
        total += 1
    if not last:
        return {"ticks_advanced": 0}
    last["ticks_advanced"] = total
    return last


def _is_critical_batch_result(result: dict) -> bool:
    if bool(result.get("new_state", {}).get("game_over")):
        return True
    if bool(result.get("requires_resync", False)):
        return True
    for ev in (result.get("new_events") or []):
        et = ev.get("event_type")
        if et in {
            "game_over",
            "emission_warning",
            "emission_started",
            "emission_ended",
            "agent_died",
            "combat_started",
            "player_action_completed",
            "zone_event_choice_required",
        }:
            return True
    return False


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
    from app.core.state_cache.service import get_context_flag, get_auto_tick_runtime, set_auto_tick_runtime
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ctx_map = _refresh_debug_context_cache(db)
        if not ctx_map:
            return {"ticked": 0}

        ticked = 0
        now = time.monotonic()
        for ctx_id, match_id in ctx_map.items():
            try:
                auto_cfg = get_auto_tick_runtime(ctx_id)
                if auto_cfg is None:
                    enabled = bool(
                        get_context_flag(ctx_id, "auto_tick_enabled", default=False)
                        or get_context_flag(ctx_id, "debug_auto_tick", default=False)
                    )
                    speed = get_context_flag(ctx_id, "auto_tick_speed", default=None)
                    if speed is None and get_context_flag(ctx_id, "auto_tick_slow_mode", default=False):
                        speed = "realtime"
                    if speed is None:
                        speed = "x100"
                    set_auto_tick_runtime(ctx_id, enabled=enabled, speed=speed, updated_at=now)
                else:
                    enabled = bool(auto_cfg.get("enabled", False))
                    speed = auto_cfg.get("speed") or "x100"

                if not enabled:
                    _auto_tick_runtime.pop(ctx_id, None)
                    continue

                rt = _auto_tick_runtime.setdefault(ctx_id, {
                    "last_real_ts": now,
                    "game_seconds_accum": 0.0,
                    "running": False,
                })
                if rt["running"]:
                    continue

                elapsed = max(0.0, now - float(rt["last_real_ts"]))
                rt["last_real_ts"] = now
                speed_multiplier = AUTO_TICK_SPEED_MULTIPLIERS.get(str(speed), 100)
                due_ticks, new_accum, due_ticks_before_cap = _compute_due_ticks(
                    accumulated_game_seconds=float(rt["game_seconds_accum"]),
                    elapsed_real_seconds=elapsed,
                    speed_multiplier=speed_multiplier,
                    max_ticks_per_batch=_MAX_TICKS_PER_BATCH,
                    max_accumulated_ticks=_MAX_ACCUMULATED_TICKS,
                )
                rt["game_seconds_accum"] = new_accum
                if due_ticks <= 0:
                    continue

                rt["running"] = True
                try:
                    batch_started = time.perf_counter()
                    result = tick_match_many(match_id, db, due_ticks)
                    batch_elapsed = max(0.000001, time.perf_counter() - batch_started)
                finally:
                    rt["running"] = False
                if "error" not in result:
                    ticks_advanced = int(result.get("ticks_advanced", 0))
                    ticked += ticks_advanced
                    try:
                        from app.games.zone_stalkers.performance_metrics import record_tick_metrics
                        speed_multiplier = AUTO_TICK_SPEED_MULTIPLIERS.get(str(speed), 100)
                        effective_speed = (ticks_advanced * 60.0) / batch_elapsed
                        record_tick_metrics(match_id, {
                            "context_id": ctx_id,
                            "auto_tick_speed_target": speed_multiplier,
                            "auto_tick_effective_speed": round(effective_speed, 3),
                            "ticks_due": due_ticks_before_cap,
                            "ticks_advanced": ticks_advanced,
                            "ticks_dropped_or_capped": max(0, due_ticks_before_cap - due_ticks),
                            "batch_size": due_ticks,
                            "accumulator_game_seconds": round(float(rt["game_seconds_accum"]), 3),
                        })
                    except Exception:
                        pass
                else:
                    # Match or context no longer valid — force cache refresh.
                    global _debug_ctx_cache_ts
                    _debug_ctx_cache_ts = 0.0
            except Exception as exc:
                logger.warning("debug auto-tick failed for match %s: %s", match_id, exc)

        return {"ticked": ticked}
    finally:
        db.close()
def _compute_due_ticks(
    *,
    accumulated_game_seconds: float,
    elapsed_real_seconds: float,
    speed_multiplier: int,
    max_ticks_per_batch: int,
    max_accumulated_ticks: int,
) -> tuple[int, float, int]:
    """
    Pure accumulator math helper.
    Returns (due_ticks_after_cap, new_accumulated_game_seconds, due_before_cap).
    """
    accum = max(0.0, float(accumulated_game_seconds)) + max(0.0, float(elapsed_real_seconds)) * max(1, int(speed_multiplier))
    max_game_seconds = max(1, int(max_accumulated_ticks)) * 60.0
    if accum > max_game_seconds:
        accum = max_game_seconds
    due_before_cap = int(accum // 60.0)
    due = min(due_before_cap, max(1, int(max_ticks_per_batch)))
    accum -= due * 60.0
    return due, accum, due_before_cap

