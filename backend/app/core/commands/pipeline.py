import logging
import uuid
import time
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Command, CommandStatus
from .schemas import CommandEnvelope, CommandResult
from app.core.auth.models import User
from app.core.contexts.models import GameContext
from app.core.events.models import GameEvent
from app.core.events.service import get_next_sequence_number, allocate_sequence_numbers

logger = logging.getLogger(__name__)

# Valid speed values for the ``set_auto_tick`` meta-command.
# Must stay in sync with the ticker service and the frontend.
AUTO_TICK_VALID_SPEEDS: frozenset = frozenset({"realtime", "x10", "x100", "x600"})

# Game rule registry: game_id -> RuleSet instance
_rule_registry: dict = {}

def register_ruleset(game_id: str, ruleset):
    _rule_registry[game_id] = ruleset

def get_ruleset(game_id: str):
    return _rule_registry.get(game_id)


class CommandPipeline:
    """
    Pipeline: Receive -> Auth -> Validate -> RuleCheck -> Reserve -> Resolve -> EmitEvents -> ProjectState -> NotifyClients
    """

    def process(self, envelope: CommandEnvelope, player: User, db: Session) -> CommandResult:
        # 1. Create command record
        command = Command(
            match_id=envelope.match_id,
            context_id=envelope.context_id,
            participant_id=player.id,
            command_type=envelope.command_type,
            payload=envelope.payload,
            status=CommandStatus.RECEIVED,
        )
        db.add(command)
        db.flush()

        try:
            # 2. Load context
            context = db.query(GameContext).filter(GameContext.id == envelope.context_id).first()
            if not context:
                raise HTTPException(status_code=404, detail="Context not found")

            # 3. Load match to get game_id
            from app.core.matches.models import Match
            match = db.query(Match).filter(Match.id == envelope.match_id).first()
            if not match:
                raise HTTPException(status_code=404, detail="Match not found")

            # 4. Basic validation
            command.status = CommandStatus.VALIDATED

            # 5. Load entities in context
            from app.core.entities.models import Entity
            entities = db.query(Entity).filter(
                Entity.context_id == envelope.context_id,
                Entity.alive == True,
            ).all()
            entities_data = [
                {
                    "id": str(e.id),
                    "archetype_id": e.archetype_id,
                    "components": e.components,
                    "tags": e.tags,
                    "owner_participant_id": str(e.owner_participant_id) if e.owner_participant_id else None,
                }
                for e in entities
            ]

            # 6. RuleCheck - delegate to game's RuleSet if registered
            ruleset = get_ruleset(match.game_id)
            new_events = []

            # Load state once from Redis (or DB fallback) so all three passes
            # (validate, resolve, persist) see the same authoritative state.
            from app.core.state_cache.service import (
                load_context_state,
                save_context_state,
                set_auto_tick_runtime,
            )
            state = load_context_state(context.id, context)
            new_state = state

            # ── Core platform meta-commands ────────────────────────────────────
            # Handled directly by the pipeline — no game ruleset involvement.
            # Any game can use these commands without implementing them itself.
            if envelope.command_type == "set_auto_tick":
                import copy as _copy
                enabled = bool(envelope.payload.get("enabled", False))
                # Accept ``speed`` ("realtime" | "x10" | "x100") as the
                # canonical parameter.  Fall back to legacy ``slow_mode``
                # boolean for backward compatibility.
                if not enabled:
                    speed = None
                else:
                    speed = envelope.payload.get("speed") or None
                    if speed is None:
                        # legacy slow_mode support
                        if envelope.payload.get("slow_mode", False):
                            speed = "realtime"
                        else:
                            speed = "x100"
                _VALID_SPEEDS = AUTO_TICK_VALID_SPEEDS
                if speed is not None and speed not in _VALID_SPEEDS:
                    logger.warning(
                        "set_auto_tick: unknown speed %r — defaulting to 'x100'. "
                        "Valid values: %s",
                        speed, sorted(_VALID_SPEEDS),
                    )
                    speed = "x100"
                new_state = _copy.deepcopy(state)
                new_state["auto_tick_enabled"] = enabled
                new_state["auto_tick_speed"] = speed
                # Keep legacy field for backward compat with old clients
                new_state["auto_tick_slow_mode"] = (speed == "realtime") if enabled else False
                new_events = [{"event_type": "auto_tick_changed", "payload": {
                    "enabled": enabled,
                    "speed": speed,
                    # legacy field
                    "slow_mode": new_state["auto_tick_slow_mode"],
                }}]
                save_context_state(context.id, new_state, context, force_persist=True)
                set_auto_tick_runtime(
                    context.id,
                    enabled=enabled,
                    speed=speed,
                    updated_at=time.monotonic(),
                )

                seq_range = allocate_sequence_numbers(context.id, len(new_events), db)
                for ev_data, seq in zip(new_events, seq_range):
                    event = GameEvent(
                        match_id=envelope.match_id,
                        context_id=envelope.context_id,
                        event_type=ev_data.get("event_type", "unknown"),
                        payload=ev_data.get("payload", {}),
                        causation_command_id=command.id,
                        sequence_no=seq,
                    )
                    db.add(event)

                command.status = CommandStatus.RESOLVED
                command.executed_at = datetime.utcnow()
                db.commit()

                from app.core.ws.manager import ws_manager
                # Broadcast the new speed directly so all clients can update
                # their UI without an extra HTTP round-trip.
                ws_manager.notify(str(envelope.match_id), {
                    "type": "auto_tick_changed",
                    "match_id": str(envelope.match_id),
                    "auto_tick_enabled": enabled,
                    "auto_tick_speed": speed,
                })
                return CommandResult(command_id=command.id, status=CommandStatus.RESOLVED, events=new_events)
            # ── End core meta-commands ─────────────────────────────────────────

            if ruleset:
                result = ruleset.validate_command(
                    envelope.command_type,
                    envelope.payload,
                    state,
                    entities_data,
                    str(player.id)
                )
                if not result.valid:
                    command.status = CommandStatus.REJECTED
                    command.error_message = result.error
                    db.commit()
                    return CommandResult(command_id=command.id, status=CommandStatus.REJECTED, error=result.error)

                # Capture old_state BEFORE resolve so we can build a delta afterward
                old_state = state

                # 7. Resolve
                new_state, new_events = ruleset.resolve_command(
                    envelope.command_type,
                    envelope.payload,
                    state,
                    entities_data,
                    str(player.id)
                )
            else:
                old_state = state
                # Default handler for end_turn
                if envelope.command_type == "end_turn":
                    new_events = [{"event_type": "turn_submitted", "payload": {"participant_id": str(player.id)}}]
                else:
                    new_events = [{"event_type": f"{envelope.command_type}_executed", "payload": envelope.payload}]

            # 8. Persist state — user-initiated commands always write to DB
            # (force_persist=True) so that the change is immediately durable.
            # Increment state_revision for zone_map contexts so frontend deltas
            # can detect staleness after a command changes state.
            if isinstance(new_state, dict) and new_state.get("context_type") == "zone_map":
                new_state["state_revision"] = int(new_state.get("state_revision", 0)) + 1
                new_state["_debug_revision"] = int(new_state.get("_debug_revision", 0)) + 1
            save_context_state(context.id, new_state, context, force_persist=True)

            # 9. Emit events
            emitted = []
            if new_events:
                seq_range = allocate_sequence_numbers(context.id, len(new_events), db)
                for ev_data, seq in zip(new_events, seq_range):
                    event = GameEvent(
                        match_id=envelope.match_id,
                        context_id=envelope.context_id,
                        event_type=ev_data.get("event_type", "unknown"),
                        payload=ev_data.get("payload", {}),
                        causation_command_id=command.id,
                        sequence_no=seq,
                    )
                    db.add(event)
                    emitted.append(ev_data)

            command.status = CommandStatus.RESOLVED
            command.executed_at = datetime.utcnow()
            db.commit()

            # Notify connected WebSocket clients about the state change.
            # For Zone Stalkers zone_map contexts, send a compact zone_delta
            # so the frontend can update without an extra HTTP round-trip.
            from app.core.ws.manager import ws_manager
            match_id_str = str(envelope.match_id)
            context_id_str = str(envelope.context_id)

            _sent_delta = False
            if (
                match.game_id == "zone_stalkers"
                and isinstance(new_state, dict)
                and new_state.get("context_type") == "zone_map"
            ):
                try:
                    from app.games.zone_stalkers.delta import build_zone_delta
                    zone_delta = build_zone_delta(
                        old_state=old_state,
                        new_state=new_state,
                        events=emitted,
                    )
                    ws_manager.notify(match_id_str, {
                        "type": "zone_delta",
                        "match_id": match_id_str,
                        "context_id": context_id_str,
                        **zone_delta,
                    })
                    _sent_delta = True
                except Exception:
                    logger.exception(
                        "Failed to build zone_delta for match %s context %s; falling back to state_updated",
                        match_id_str, context_id_str,
                    )

            if _sent_delta:
                from app.core.ws.manager import get_debug_subscriptions
                debug_subs = get_debug_subscriptions(match_id_str)
                if debug_subs:
                    debug_resync_required = False
                    try:
                        from app.games.zone_stalkers.debug_delta import build_zone_debug_delta
                        debug_revision = int(new_state.get("_debug_revision", 0))
                        for conn_id, sub in debug_subs.items():
                            debug_delta = build_zone_debug_delta(
                                old_state=old_state,
                                new_state=new_state,
                                subscription=sub,
                                debug_revision=debug_revision,
                            )
                            if debug_delta:
                                ws_manager.notify_to_connection(conn_id, {
                                    "type": "zone_debug_delta",
                                    "match_id": match_id_str,
                                    "context_id": context_id_str,
                                    **debug_delta,
                                })
                    except Exception:
                        debug_resync_required = True
                        logger.exception(
                            "Failed to build zone_debug_delta after command for match %s context %s",
                            match_id_str, context_id_str,
                        )

                    if debug_resync_required:
                        for conn_id in debug_subs:
                            ws_manager.notify_to_connection(conn_id, {
                                "type": "debug_requires_resync",
                                "match_id": match_id_str,
                                "context_id": context_id_str,
                                "state_revision": new_state.get("state_revision"),
                                "debug_revision": new_state.get("_debug_revision"),
                            })

            if not _sent_delta:
                ws_manager.notify(match_id_str, {
                    "type": "state_updated",
                    "match_id": match_id_str,
                    "context_id": context_id_str,
                    "state_revision": new_state.get("state_revision") if isinstance(new_state, dict) else None,
                    "requires_resync": True,
                })

            return CommandResult(command_id=command.id, status=CommandStatus.RESOLVED, events=emitted)

        except HTTPException:
            command.status = CommandStatus.FAILED
            db.commit()
            raise
        except Exception as e:
            command.status = CommandStatus.FAILED
            command.error_message = str(e)
            db.commit()
            raise HTTPException(status_code=500, detail=str(e))
