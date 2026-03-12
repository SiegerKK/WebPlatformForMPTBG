import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import GameContext, ContextStatus
from .schemas import GameContextCreate

def create_context(data: GameContextCreate, db: Session) -> GameContext:
    ctx = GameContext(
        match_id=data.match_id,
        parent_context_id=data.get_parent_context_id(),
        context_type=data.context_type,
        label=data.label,
        state_blob=data.state_blob,
        status=ContextStatus.ACTIVE,
    )
    db.add(ctx)
    db.commit()
    db.refresh(ctx)
    return ctx

def get_context(context_id: uuid.UUID, db: Session) -> GameContext:
    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")
    return ctx

def get_match_contexts(match_id: uuid.UUID, db: Session) -> List[GameContext]:
    return db.query(GameContext).filter(GameContext.match_id == match_id).all()

def get_context_tree(match_id: uuid.UUID, db: Session) -> List[GameContext]:
    return db.query(GameContext).filter(GameContext.match_id == match_id).all()
