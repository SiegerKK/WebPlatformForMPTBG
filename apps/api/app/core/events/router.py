import uuid
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .schemas import GameEventRead
from .service import get_match_events, get_context_events
from app.database import get_db

router = APIRouter(tags=["events"])

@router.get("/matches/{match_id}/events", response_model=List[GameEventRead])
def list_match_events(match_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_match_events(match_id, db)

@router.get("/contexts/{context_id}/events", response_model=List[GameEventRead])
def list_context_events(context_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_context_events(context_id, db)
