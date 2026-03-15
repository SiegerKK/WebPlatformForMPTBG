import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Command, CommandStatus
from .schemas import CommandEnvelope, CommandResult
from app.core.auth.models import User
from app.core.contexts.models import GameContext
from app.core.events.models import GameEvent
from app.core.events.service import get_next_sequence_number, allocate_sequence_numbers

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
            from app.core.state_cache.service import load_context_state, save_context_state
            state = load_context_state(context.id, context)
            new_state = state

            # ── Core platform meta-commands ────────────────────────────────────
            # Handled directly by the pipeline — no game ruleset involvement.
            # Any game can use these commands without implementing them itself.
            if envelope.command_type == "set_auto_tick":
                import copy as _copy
                enabled = bool(envelope.payload.get("enabled", False))
                slow_mode = bool(envelope.payload.get("slow_mode", False)) if enabled else False
                new_state = _copy.deepcopy(state)
                new_state["auto_tick_enabled"] = enabled
                new_state["auto_tick_slow_mode"] = slow_mode
                new_events = [{"event_type": "auto_tick_changed", "payload": {"enabled": enabled, "slow_mode": slow_mode}}]
                save_context_state(context.id, new_state, context, force_persist=True)

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
                ws_manager.notify(str(envelope.match_id), {
                    "type": "state_updated",
                    "match_id": str(envelope.match_id),
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

                # 7. Resolve
                new_state, new_events = ruleset.resolve_command(
                    envelope.command_type,
                    envelope.payload,
                    state,
                    entities_data,
                    str(player.id)
                )
            else:
                # Default handler for end_turn
                if envelope.command_type == "end_turn":
                    new_events = [{"event_type": "turn_submitted", "payload": {"participant_id": str(player.id)}}]
                else:
                    new_events = [{"event_type": f"{envelope.command_type}_executed", "payload": envelope.payload}]

            # 8. Persist state — user-initiated commands always write to DB
            # (force_persist=True) so that the change is immediately durable.
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
            from app.core.ws.manager import ws_manager
            ws_manager.notify(str(envelope.match_id), {
                "type": "state_updated",
                "match_id": str(envelope.match_id),
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
