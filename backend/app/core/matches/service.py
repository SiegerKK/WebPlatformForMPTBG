import uuid
from datetime import datetime
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Match, MatchParticipant, MatchStatus
from .schemas import MatchCreate, MatchParticipantCreate

def create_match(data: MatchCreate, user_id: uuid.UUID, db: Session) -> Match:
    match = Match(
        game_id=data.game_id,
        config=data.config,
        seed=data.seed or str(uuid.uuid4()),
        created_by=user_id,
    )
    db.add(match)
    db.flush()
    participant = MatchParticipant(match_id=match.id, user_id=user_id, role="player")
    db.add(participant)
    db.commit()
    db.refresh(match)
    return match

def list_matches(db: Session) -> List[Match]:
    return db.query(Match).all()

def get_match(match_id: uuid.UUID, db: Session) -> Match:
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match

def join_match(match_id: uuid.UUID, user_id: uuid.UUID, data: MatchParticipantCreate, db: Session) -> MatchParticipant:
    match = get_match(match_id, db)
    if match.status != MatchStatus.WAITING:
        raise HTTPException(status_code=400, detail="Match is not in waiting state")
    existing = db.query(MatchParticipant).filter(
        MatchParticipant.match_id == match_id,
        MatchParticipant.user_id == user_id,
        MatchParticipant.is_active == True
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already joined this match")
    participant = MatchParticipant(match_id=match_id, user_id=user_id, role=data.role, faction=data.faction)
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return participant

def start_match(match_id: uuid.UUID, user_id: uuid.UUID, db: Session) -> Match:
    match = get_match(match_id, db)
    if str(match.created_by) != str(user_id):
        raise HTTPException(status_code=403, detail="Only the creator can start the match")
    if match.status != MatchStatus.WAITING:
        raise HTTPException(status_code=400, detail="Match is not in waiting state")
    match.status = MatchStatus.ACTIVE
    match.started_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
    return match

def delete_match(match_id: uuid.UUID, user_id: uuid.UUID, db: Session):
    match = get_match(match_id, db)
    if str(match.created_by) != str(user_id):
        raise HTTPException(status_code=403, detail="Only the creator can delete the match")
    match.status = MatchStatus.CANCELLED
    db.commit()
