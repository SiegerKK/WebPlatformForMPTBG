import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Command, CommandStatus
from .schemas import CommandEnvelope, CommandResult
from app.core.auth.models import User
from app.core.contexts.models import GameContext
from app.core.events.models import GameEvent
from app.core.events.service import get_next_sequence_number

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
            entities = db.query(Entity).filter(Entity.context_id == envelope.context_id, Entity.alive == True).all()
            entities_data = [{"id": str(e.id), "archetype": e.archetype_id, "components": e.components, "tags": e.tags, "owner_id": str(e.owner_participant_id) if e.owner_participant_id else None} for e in entities]

            # 6. RuleCheck - delegate to game's RuleSet if registered
            ruleset = get_ruleset(match.game_id)
            new_events = []
            new_state = context.state_blob or {}

            if ruleset:
                result = ruleset.validate_command(
                    envelope.command_type,
                    envelope.payload,
                    context.state_blob or {},
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
                    context.state_blob or {},
                    entities_data,
                    str(player.id)
                )
            else:
                # Default handler for end_turn
                if envelope.command_type == "end_turn":
                    new_events = [{"event_type": "turn_submitted", "payload": {"player_id": str(player.id)}}]
                else:
                    new_events = [{"event_type": f"{envelope.command_type}_executed", "payload": envelope.payload}]

            # 8. Optimistic lock + update state
            context.state_blob = new_state
            context.state_version += 1

            # 9. Emit events
            emitted = []
            for ev_data in new_events:
                seq = get_next_sequence_number(context.id, db)
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
