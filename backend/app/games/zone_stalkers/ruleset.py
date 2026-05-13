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
        from app.core.state_cache.service import load_context_state, save_context_state
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

        # Load state from Redis (or DB fallback on cache miss).
        state = load_context_state(zone_ctx.id, zone_ctx)
        # Capture old state for delta computation (tick_zone_map does not mutate state in place).
        old_state = state

        # ── Tick the world map ────────────────────────────────────────
        new_state, map_events = tick_zone_map(state)

        # Increment state revision so frontends can detect stale deltas.
        new_state["state_revision"] = int(state.get("state_revision", 0)) + 1
        new_state["_debug_revision"] = int(state.get("_debug_revision", 0)) + 1

        emitted = []
        new_events_for_ws = []  # serializable copies for WebSocket broadcast
        if map_events:
            import uuid as _uuid_mod
            from datetime import datetime as _dt_cls
            seq_range = allocate_sequence_numbers(zone_ctx.id, len(map_events), db)
            for ev_data, seq in zip(map_events, seq_range):
                _ev_id = _uuid_mod.uuid4()
                _ev_created = _dt_cls.utcnow()
                ev = GameEvent(
                    id=_ev_id,
                    match_id=match.id,
                    context_id=zone_ctx.id,
                    event_type=ev_data.get("event_type", "tick"),
                    payload=ev_data.get("payload", {}),
                    sequence_no=seq,
                    created_at=_ev_created,
                )
                db.add(ev)
                emitted.append(ev_data)
                new_events_for_ws.append({
                    "id": str(_ev_id),
                    "match_id": str(match.id),
                    "context_id": str(zone_ctx.id),
                    "event_type": ev_data.get("event_type", "tick"),
                    "payload": ev_data.get("payload", {}),
                    "sequence_no": seq,
                    "created_at": _ev_created.isoformat(),
                })

        # ── Process active zone_event child contexts ──────────────────
        if new_state.get("active_events"):
            event_ctxs = db.query(GameContext).filter(
                GameContext.match_id == match.id,
                GameContext.context_type == "zone_event",
                GameContext.status == ContextStatus.ACTIVE,
            ).all()
        else:
            event_ctxs = []

        for evt_ctx in event_ctxs:
            evt_state = load_context_state(evt_ctx.id, evt_ctx)

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
                    from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3  # noqa: PLC0415
                    world_turn = new_state.get("world_turn", 1)
                    for pid in evt_state.get("participants", {}):
                        agent_id = new_state.get("player_agents", {}).get(pid)
                        if agent_id and agent_id in new_state.get("agents", {}):
                            agent = new_state["agents"][agent_id]
                            write_memory_event_to_v3(
                                agent_id=agent_id, agent=agent,
                                legacy_entry={**memory_template, "world_turn": world_turn},
                                world_turn=world_turn,
                                context_id=str(new_state.get("context_id") or new_state.get("_context_id") or "default"),
                                cold_store_enabled=bool(new_state.get("cpu_cold_memory_store_enabled", False)),
                            )
                active_events = new_state.get("active_events", [])
                event_ctx_id = str(evt_ctx.id)
                if event_ctx_id in active_events:
                    active_events.remove(event_ctx_id)
                new_state["active_events"] = active_events
                evt_ctx.status = ContextStatus.FINISHED
                evt_ctx.finished_at = datetime.utcnow()

            # Event contexts are small — always persist to DB for reliability.
            save_context_state(evt_ctx.id, evt_state, evt_ctx, force_persist=True)

        # Save zone_map state (includes memory updates from ended events).
        # force_persist=True on game-over so the final state is always durable.
        game_over = bool(new_state.get("game_over"))
        save_context_state(zone_ctx.id, new_state, zone_ctx, force_persist=game_over)

        if game_over:
            match.status = MatchStatus.FINISHED
            match.finished_at = datetime.utcnow()

        db.commit()

        # Build compact WebSocket delta (avoids full HTTP projection round-trip on frontend).
        # Try dirty-set delta first (faster); fall back to full diff on failure/empty sets.

        # Pick up last tick runtime for profiler data + dirty sets.
        _runtime = None
        profiler_data = None
        try:
            from app.games.zone_stalkers.rules.tick_rules import _last_tick_runtime
            _runtime = _last_tick_runtime
            if _runtime and _runtime.profiler:
                profiler_data = _runtime.profiler.to_dict()
                profiler_data["counters"].update(_runtime.to_debug_counters())
        except Exception:
            _runtime = None

        # Store profiler data in performance metrics
        try:
            from app.games.zone_stalkers.performance_metrics import record_tick_metrics
            record_tick_metrics(
                str(match.id),
                {
                    "world_turn": new_state.get("world_turn"),
                    "profiler": profiler_data,
                },
            )
        except Exception:
            pass

        zone_delta = None
        try:
            if _should_use_dirty_ws_delta(new_state, _runtime):
                from app.games.zone_stalkers.delta_dirty import build_zone_delta_from_dirty
                zone_delta = build_zone_delta_from_dirty(
                    state=new_state,
                    runtime=_runtime,
                    events=new_events_for_ws,
                    old_state=old_state,
                )
            if zone_delta is None:
                from app.games.zone_stalkers.delta import build_zone_delta
                zone_delta = build_zone_delta(
                    old_state=old_state,
                    new_state=new_state,
                    events=new_events_for_ws,
                )
        except Exception:
            zone_delta = None

        return {
            "context_id": str(zone_ctx.id),
            "world_turn": new_state.get("world_turn"),
            "world_hour": new_state.get("world_hour"),
            "world_day": new_state.get("world_day"),
            "world_minute": new_state.get("world_minute"),
            "events_emitted": len(emitted),
            "new_events": new_events_for_ws,
            "zone_delta": zone_delta,
            "old_state": old_state,
            "new_state": new_state,
        }

    def tick_many(self, match_id: str, db: Any, max_ticks: int) -> dict:
        """Advance Zone Stalkers world by up to max_ticks in one load/save/commit cycle."""
        import time
        from datetime import datetime
        from app.core.contexts.models import GameContext, ContextStatus
        from app.core.events.models import GameEvent
        from app.core.events.service import allocate_sequence_numbers
        from app.core.matches.models import Match, MatchStatus
        from app.core.state_cache.service import load_context_state, save_context_state
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map_many
        from app.games.zone_stalkers.rules.event_rules import start_event, bot_choose_option

        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return {"error": "match not found"}

        zone_ctx = db.query(GameContext).filter(
            GameContext.match_id == match.id,
            GameContext.context_type == "zone_map",
            GameContext.status == ContextStatus.ACTIVE,
        ).first()
        if not zone_ctx:
            return {"error": "no active zone_map context found"}

        _load_started = time.perf_counter()
        state = load_context_state(zone_ctx.id, zone_ctx)
        batch_load_state_ms = (time.perf_counter() - _load_started) * 1000.0
        old_state = state
        _tick_started = time.perf_counter()
        new_state, all_map_events, ticks_advanced, stop_reason = tick_zone_map_many(
            state,
            max(0, int(max_ticks)),
        )
        batch_tick_logic_ms = (time.perf_counter() - _tick_started) * 1000.0

        new_state["state_revision"] = int(state.get("state_revision", 0)) + 1
        new_state["_debug_revision"] = int(state.get("_debug_revision", 0)) + 1

        emitted = []
        new_events_for_ws = []
        if all_map_events:
            import uuid as _uuid_mod
            from datetime import datetime as _dt_cls
            seq_range = allocate_sequence_numbers(zone_ctx.id, len(all_map_events), db)
            for ev_data, seq in zip(all_map_events, seq_range):
                _ev_id = _uuid_mod.uuid4()
                _ev_created = _dt_cls.utcnow()
                ev = GameEvent(
                    id=_ev_id,
                    match_id=match.id,
                    context_id=zone_ctx.id,
                    event_type=ev_data.get("event_type", "tick"),
                    payload=ev_data.get("payload", {}),
                    sequence_no=seq,
                    created_at=_ev_created,
                )
                db.add(ev)
                emitted.append(ev_data)
                new_events_for_ws.append({
                    "id": str(_ev_id),
                    "match_id": str(match.id),
                    "context_id": str(zone_ctx.id),
                    "event_type": ev_data.get("event_type", "tick"),
                    "payload": ev_data.get("payload", {}),
                    "sequence_no": seq,
                    "created_at": _ev_created.isoformat(),
                })

        if new_state.get("active_events"):
            event_ctxs = db.query(GameContext).filter(
                GameContext.match_id == match.id,
                GameContext.context_type == "zone_event",
                GameContext.status == ContextStatus.ACTIVE,
            ).all()
        else:
            event_ctxs = []

        for evt_ctx in event_ctxs:
            evt_state = load_context_state(evt_ctx.id, evt_ctx)
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
                active_events = new_state.get("active_events", [])
                event_ctx_id = str(evt_ctx.id)
                if event_ctx_id in active_events:
                    active_events.remove(event_ctx_id)
                new_state["active_events"] = active_events
                evt_ctx.status = ContextStatus.FINISHED
                evt_ctx.finished_at = datetime.utcnow()

            save_context_state(evt_ctx.id, evt_state, evt_ctx, force_persist=True)

        game_over = bool(new_state.get("game_over"))
        _save_started = time.perf_counter()
        state_db_written = save_context_state(zone_ctx.id, new_state, zone_ctx, force_persist=game_over)
        batch_save_state_ms = (time.perf_counter() - _save_started) * 1000.0
        if game_over:
            match.status = MatchStatus.FINISHED
            match.finished_at = datetime.utcnow()
        _db_started = time.perf_counter()
        if emitted or state_db_written or game_over or event_ctxs:
            db.commit()
        else:
            db.rollback()
        batch_db_ms = (time.perf_counter() - _db_started) * 1000.0

        zone_delta = None
        try:
            from app.games.zone_stalkers.delta import build_zone_delta
            zone_delta = build_zone_delta(old_state=old_state, new_state=new_state, events=new_events_for_ws)
        except Exception:
            zone_delta = None

        return {
            "context_id": str(zone_ctx.id),
            "ticks_advanced": ticks_advanced,
            "stop_reason": stop_reason,
            "world_turn": new_state.get("world_turn"),
            "world_hour": new_state.get("world_hour"),
            "world_day": new_state.get("world_day"),
            "world_minute": new_state.get("world_minute"),
            "events_emitted": len(emitted),
            "new_events": new_events_for_ws,
            "zone_delta": zone_delta,
            "old_state": old_state,
            "new_state": new_state,
            "metrics": {
                "batch_load_state_ms": round(batch_load_state_ms, 3),
                "batch_tick_logic_ms": round(batch_tick_logic_ms, 3),
                "batch_save_state_ms": round(batch_save_state_ms, 3),
                "batch_db_ms": round(batch_db_ms, 3),
            },
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


def _should_use_dirty_ws_delta(state: dict[str, Any], runtime: Any | None) -> bool:
    """Dirty WS delta is opt-in and disabled by default for safety."""
    if not bool((state or {}).get("cpu_dirty_delta_enabled", False)):
        return False
    if runtime is None:
        return False
    return bool(
        getattr(runtime, "dirty_agents", None)
        or getattr(runtime, "dirty_locations", None)
        or getattr(runtime, "dirty_traders", None)
    )
