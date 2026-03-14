"""
Main RuleSet for Zone Stalkers.

Dispatches commands to the appropriate sub-ruleset based on context_type
stored in the state_blob.
"""
from typing import Any, List, Tuple
from sdk.rule_set import RuleSet, RuleCheckResult

from app.games.zone_stalkers.rules.world_rules import (
    validate_world_command,
    resolve_world_command,
)
from app.games.zone_stalkers.rules.combat_rules import (
    validate_combat_command,
    resolve_combat_command,
)
from app.games.zone_stalkers.rules.trade_rules import (
    validate_trade_command,
    resolve_trade_command,
)
from app.games.zone_stalkers.rules.exploration_rules import (
    validate_exploration_command,
    resolve_exploration_command,
)
from app.games.zone_stalkers.rules.event_rules import (
    validate_event_command,
    resolve_event_command,
)


class ZoneStalkerRuleSet(RuleSet):
    """Dispatches to context-specific rule modules."""

    # ── Context initialisation ────────────────────────────────────────────────

    def create_initial_context_state(
        self,
        context_type: str,
        match_id: Any,
        db: Any,
    ):
        """Auto-generate deterministic world state for zone_map contexts."""
        if context_type != "zone_map":
            return None

        from app.core.matches.models import Match, Participant
        from app.games.zone_stalkers.generators.zone_generator import generate_zone

        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None

        parts = (
            db.query(Participant)
            .filter(Participant.match_id == match_id)
            .order_by(Participant.joined_at, Participant.id)
            .all()
        )
        user_parts = [p for p in parts if p.user_id is not None]
        num_players = max(1, len(user_parts))

        seed = match.seed if match.seed is not None else 42
        state = generate_zone(seed=seed, num_players=num_players)

        # Bind human participant IDs to pre-generated player agent slots
        for i, part in enumerate(user_parts):
            agent_id = f"agent_p{i}"
            participant_id = str(part.user_id)
            if agent_id in state["agents"]:
                state["agents"][agent_id]["controller"]["participant_id"] = participant_id
                state["player_agents"][participant_id] = agent_id

        return state

    # ── Tick (world-time advancement) ─────────────────────────────────────────

    def tick(self, match_id: str, db: Any) -> dict:
        """Advance the world by one game-turn for a Zone Stalkers match."""
        from datetime import datetime
        from app.core.contexts.models import GameContext, ContextStatus
        from app.core.events.models import GameEvent
        from app.core.events.service import allocate_sequence_numbers
        from app.core.matches.models import Match, MatchStatus
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        from app.games.zone_stalkers.rules.event_rules import start_event, bot_choose_option

        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return {"error": "match not found"}

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
        if map_events:
            seq_range = allocate_sequence_numbers(zone_ctx.id, len(map_events), db)
            for ev_data, seq in zip(map_events, seq_range):
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

            if evt_state.get("phase") == "waiting":
                participants = evt_state.get("participants", {})
                if participants:
                    evt_state, start_evs = start_event(evt_state)
                    _emit_events(evt_ctx, match, start_evs, emitted, db)

            if evt_state.get("phase") == "active":
                for pid, p in evt_state.get("participants", {}).items():
                    if p.get("status") == "active" and p.get("choice") is None:
                        agent_id = new_state.get("player_agents", {}).get(pid)
                        if agent_id:
                            agent = new_state.get("agents", {}).get(agent_id, {})
                            if agent.get("controller", {}).get("kind") == "bot":
                                evt_state, bot_evs = bot_choose_option(evt_state, pid)
                                _emit_events(evt_ctx, match, bot_evs, emitted, db)

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
                            }
                            agent.setdefault("memory", []).append(entry)
                            if len(agent["memory"]) > 50:
                                agent["memory"] = agent["memory"][-50:]
                active_events = new_state.get("active_events", [])
                event_ctx_id = str(evt_ctx.id)
                if event_ctx_id in active_events:
                    active_events.remove(event_ctx_id)
                new_state["active_events"] = active_events
                evt_ctx.status = ContextStatus.FINISHED
                evt_ctx.finished_at = datetime.utcnow()

            evt_ctx.state_blob = evt_state
            evt_ctx.state_version += 1

        # Save updated zone_map state (includes memory updates from events)
        zone_ctx.state_blob = new_state

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

    # ── Command validation / resolution ──────────────────────────────────────

    def validate_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> RuleCheckResult:
        ctx_type = (context_state or {}).get("context_type", "zone_map")
        state = context_state or {}

        if ctx_type == "zone_map":
            return validate_world_command(command_type, payload, state, player_id)
        if ctx_type == "encounter_combat":
            return validate_combat_command(command_type, payload, state, player_id)
        if ctx_type == "trade_session":
            return validate_trade_command(command_type, payload, state, player_id)
        if ctx_type == "location_exploration":
            return validate_exploration_command(command_type, payload, state, player_id)
        if ctx_type == "zone_event":
            return validate_event_command(command_type, payload, state, player_id)

        # Fallback: always allow end_turn
        if command_type == "end_turn":
            return RuleCheckResult(valid=True)
        return RuleCheckResult(valid=False, error=f"Unknown context type: {ctx_type}")

    def resolve_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> Tuple[dict, List[dict]]:
        ctx_type = (context_state or {}).get("context_type", "zone_map")
        state = context_state or {}

        if ctx_type == "zone_map":
            return resolve_world_command(command_type, payload, state, player_id)
        if ctx_type == "encounter_combat":
            return resolve_combat_command(command_type, payload, state, player_id)
        if ctx_type == "trade_session":
            return resolve_trade_command(command_type, payload, state, player_id)
        if ctx_type == "location_exploration":
            return resolve_exploration_command(command_type, payload, state, player_id)
        if ctx_type == "zone_event":
            return resolve_event_command(command_type, payload, state, player_id)

        # Fallback
        return state, [{"event_type": f"{command_type}_executed", "payload": payload}]


# ── Private helpers ────────────────────────────────────────────────────────────

def _emit_events(ctx, match, event_dicts: list, emitted: list, db) -> None:
    from app.core.events.models import GameEvent
    from app.core.events.service import allocate_sequence_numbers
    if not event_dicts:
        return
    seq_range = allocate_sequence_numbers(ctx.id, len(event_dicts), db)
    for ev_data, seq in zip(event_dicts, seq_range):
        ev = GameEvent(
            match_id=match.id,
            context_id=ctx.id,
            event_type=ev_data.get("event_type", "event"),
            payload=ev_data.get("payload", {}),
            sequence_no=seq,
        )
        db.add(ev)
        emitted.append(ev_data)
