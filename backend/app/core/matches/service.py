import uuid
from datetime import datetime
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Match, Participant, MatchStatus, ParticipantStatus
from .schemas import MatchCreate, ParticipantCreate

def create_match(data: MatchCreate, user_id: uuid.UUID, db: Session) -> Match:
    match = Match(
        game_id=data.game_id,
        title=data.title,
        mode=data.mode,
        visibility_mode=data.visibility_mode,
        settings=data.settings,
        seed=data.seed or str(uuid.uuid4()),
        status=MatchStatus.WAITING_FOR_PLAYERS,
        created_by_user_id=user_id,
    )
    db.add(match)
    db.flush()
    participant = Participant(
        match_id=match.id,
        user_id=user_id,
        role="player",
    )
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

def join_match(match_id: uuid.UUID, user_id: uuid.UUID, data: ParticipantCreate, db: Session) -> Participant:
    match = get_match(match_id, db)
    if match.status not in (MatchStatus.DRAFT, MatchStatus.WAITING_FOR_PLAYERS):
        raise HTTPException(status_code=400, detail="Match is not open for joining")
    existing = db.query(Participant).filter(
        Participant.match_id == match_id,
        Participant.user_id == user_id,
        Participant.status.notin_([ParticipantStatus.LEFT, ParticipantStatus.ELIMINATED]),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already joined this match")
    participant = Participant(
        match_id=match_id,
        user_id=user_id,
        role=data.role,
        side_id=data.side_id,
        kind=data.kind,
        display_name=data.display_name,
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return participant

def start_match(match_id: uuid.UUID, user_id: uuid.UUID, db: Session) -> Match:
    match = get_match(match_id, db)
    if str(match.created_by_user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Only the creator can start the match")
    if match.status not in (MatchStatus.DRAFT, MatchStatus.WAITING_FOR_PLAYERS):
        raise HTTPException(status_code=400, detail="Match cannot be started from current status")
    match.status = MatchStatus.ACTIVE
    match.started_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
    return match

def delete_match(match_id: uuid.UUID, user_id: uuid.UUID, db: Session, is_superuser: bool = False):
    match = get_match(match_id, db)
    if not is_superuser and str(match.created_by_user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Only the creator or an admin can close this match")
    match.status = MatchStatus.ARCHIVED
    db.commit()
