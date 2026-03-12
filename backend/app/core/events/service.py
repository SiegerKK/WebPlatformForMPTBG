import uuid
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import GameEvent

def emit_event(match_id: uuid.UUID, context_id: uuid.UUID, event_type: str, payload: dict, caused_by: uuid.UUID, db: Session) -> GameEvent:
    seq = get_next_sequence_number(context_id, db)
    event = GameEvent(
        match_id=match_id,
        context_id=context_id,
        event_type=event_type,
        payload=payload,
        causation_command_id=caused_by,
        sequence_no=seq,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event

def get_next_sequence_number(context_id: uuid.UUID, db: Session) -> int:
    result = db.query(func.max(GameEvent.sequence_no)).filter(GameEvent.context_id == context_id).scalar()
    return (result or 0) + 1

def get_match_events(match_id: uuid.UUID, db: Session) -> List[GameEvent]:
    return db.query(GameEvent).filter(GameEvent.match_id == match_id).order_by(GameEvent.sequence_no).all()

def get_context_events(context_id: uuid.UUID, db: Session) -> List[GameEvent]:
    return db.query(GameEvent).filter(GameEvent.context_id == context_id).order_by(GameEvent.sequence_no).all()
