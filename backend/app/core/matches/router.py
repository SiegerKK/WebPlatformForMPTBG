import uuid
from typing import List
from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from .schemas import MatchCreate, MatchRead, ParticipantCreate, ParticipantRead
from .service import create_match, list_matches, get_match, join_match, start_match, delete_match
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.database import get_db
from .models import Participant

router = APIRouter(prefix="/matches", tags=["matches"])

@router.post("", response_model=MatchRead)
def create(data: MatchCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_match(data, current_user.id, db)

@router.get("", response_model=List[MatchRead])
def list_all(db: Session = Depends(get_db)):
    return list_matches(db)

@router.get("/{match_id}", response_model=MatchRead)
def get(match_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_match(match_id, db)

@router.get("/{match_id}/participants", response_model=List[ParticipantRead])
def list_participants(match_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Participant).filter(Participant.match_id == match_id).all()

@router.post("/{match_id}/join", response_model=ParticipantRead)
def join(match_id: uuid.UUID, data: ParticipantCreate = Body(default=ParticipantCreate()), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return join_match(match_id, current_user.id, data, db)

@router.post("/{match_id}/start", response_model=MatchRead)
def start(match_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return start_match(match_id, current_user.id, db)

@router.delete("/{match_id}", status_code=204)
def delete(match_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_match(match_id, current_user.id, db)
