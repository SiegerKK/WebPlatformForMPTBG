import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Boolean, DateTime, Enum, ForeignKey, JSON
from app.database import Base, UUIDType

class MatchStatus(str, PyEnum):
    WAITING = "waiting"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    CANCELLED = "cancelled"

class Match(Base):
    __tablename__ = "matches"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    game_id = Column(String, nullable=False)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    created_by = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    config = Column(JSON, default=dict)
    seed = Column(String, nullable=False, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

class MatchParticipant(Base):
    __tablename__ = "match_participants"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    user_id = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    role = Column(String, default="player")
    faction = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    joined_at = Column(DateTime, default=datetime.utcnow)
