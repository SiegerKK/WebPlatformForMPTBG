"""
Ticker service — drives real-time turn advancement for zone_stalkers matches.

Called either by the background asyncio task (production) or by the manual
POST /api/matches/{id}/tick endpoint (testing / admin).
"""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.core.contexts.models import GameContext, ContextStatus
from app.core.events.models import GameEvent
from app.core.events.service import get_next_sequence_number
from app.core.matches.models import Match, MatchStatus

logger = logging.getLogger(__name__)


def tick_match(match_id_str: str, db: Session) -> dict:
    """
    Advance the world by one game-turn for the given zone_stalkers match.

    Returns a summary dict with events emitted.
    """
    from app.core.contexts.models import GameContext
    from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
    from app.games.zone_stalkers.rules.event_rules import start_event, bot_choose_option

    match = db.query(Match).filter(Match.id == match_id_str).first()
    if not match:
        return {"error": "match not found"}
    if match.status != MatchStatus.ACTIVE:
        return {"error": f"match status is {match.status}, not active"}
    if match.game_id != "zone_stalkers":
        return {"error": "not a zone_stalkers match"}

    # Find zone_map context
    zone_ctx = db.query(GameContext).filter(
        GameContext.match_id == match.id,
        GameContext.context_type == "zone_map",
        GameContext.status == ContextStatus.ACTIVE,
    ).first()

    if not zone_ctx:
        return {"error": "no active zone_map context found"}

    state = zone_ctx.state_blob or {}

    # ── Tick the world map ────────────────────────────────────────
    new_state, map_events = tick_zone_map(state)
    zone_ctx.state_blob = new_state
    zone_ctx.state_version += 1

    emitted = []
    for ev_data in map_events:
        seq = get_next_sequence_number(zone_ctx.id, db)
        ev = GameEvent(
            match_id=match.id,
            context_id=zone_ctx.id,
            event_type=ev_data.get("event_type", "tick"),
            payload=ev_data.get("payload", {}),
            sequence_no=seq,
        )
        db.add(ev)
        emitted.append(ev_data)

    # ── Process active zone_event child contexts ──────────────────
    event_ctxs = db.query(GameContext).filter(
        GameContext.match_id == match.id,
        GameContext.context_type == "zone_event",
        GameContext.status == ContextStatus.ACTIVE,
    ).all()

    for evt_ctx in event_ctxs:
        evt_state = evt_ctx.state_blob or {}

        # Start event if still in waiting phase
        if evt_state.get("phase") == "waiting":
            participants = evt_state.get("participants", {})
            if participants:
                evt_state, start_evs = start_event(evt_state)
                _emit_events(evt_ctx, match, start_evs, emitted, db)

        # Auto-choose for bot participants that haven't chosen yet
        if evt_state.get("phase") == "active":
            for pid, p in evt_state.get("participants", {}).items():
                if p.get("status") == "active" and p.get("choice") is None:
                    # Determine if this participant is a bot
                    agent_id = new_state.get("player_agents", {}).get(pid)
                    if agent_id:
                        agent = new_state.get("agents", {}).get(agent_id, {})
                        if agent.get("controller", {}).get("kind") == "bot":
                            evt_state, bot_evs = bot_choose_option(evt_state, pid)
                            _emit_events(evt_ctx, match, bot_evs, emitted, db)

        # If event ended: record memories in zone_map, update event context status
        if evt_state.get("phase") == "ended":
            memory_template = evt_state.get("memory_template")
            if memory_template:
                for pid in evt_state.get("participants", {}):
                    agent_id = new_state.get("player_agents", {}).get(pid)
                    if agent_id and agent_id in new_state.get("agents", {}):
                        agent = new_state["agents"][agent_id]
                        entry = {
                            **memory_template,
                            "world_turn": new_state.get("world_turn", 1),
                            "world_day": new_state.get("world_day", 1),
                            "world_hour": new_state.get("world_hour", 0),
                        }
                        agent.setdefault("memory", []).append(entry)
                        # Keep only last 50
                        if len(agent["memory"]) > 50:
                            agent["memory"] = agent["memory"][-50:]
            # Remove from active_events list
            active_events = new_state.get("active_events", [])
            event_ctx_id = str(evt_ctx.id)
            if event_ctx_id in active_events:
                active_events.remove(event_ctx_id)
            new_state["active_events"] = active_events
            # Mark the event context as finished
            evt_ctx.status = ContextStatus.FINISHED
            evt_ctx.finished_at = datetime.utcnow()

        evt_ctx.state_blob = evt_state
        evt_ctx.state_version += 1

    # Save updated zone_map state (includes memory updates from events)
    zone_ctx.state_blob = new_state

    # Mark match as finished if game_over
    if new_state.get("game_over"):
        match.status = MatchStatus.FINISHED
        match.finished_at = datetime.utcnow()

    db.commit()

    return {
        "world_turn": new_state.get("world_turn"),
        "world_hour": new_state.get("world_hour"),
        "world_day": new_state.get("world_day"),
        "events_emitted": len(emitted),
    }


def tick_all_active_matches(db: Session) -> dict:
    """Tick all active zone_stalkers matches. Called by the background scheduler."""
    matches = db.query(Match).filter(
        Match.status == MatchStatus.ACTIVE,
        Match.game_id == "zone_stalkers",
    ).all()

    results = []
    for match in matches:
        try:
            result = tick_match(str(match.id), db)
            results.append({"match_id": str(match.id), **result})
        except Exception as exc:
            logger.error("Ticker failed for match %s: %s", match.id, exc)
            results.append({"match_id": str(match.id), "error": str(exc)})

    return {"ticked": len(results), "results": results}


# ─────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────

def _emit_events(
    ctx: GameContext,
    match: Match,
    event_dicts: list,
    emitted: list,
    db: Session,
) -> None:
    for ev_data in event_dicts:
        seq = get_next_sequence_number(ctx.id, db)
        ev = GameEvent(
            match_id=match.id,
            context_id=ctx.id,
            event_type=ev_data.get("event_type", "event"),
            payload=ev_data.get("payload", {}),
            sequence_no=seq,
        )
        db.add(ev)
        emitted.append(ev_data)
