import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Boolean, DateTime, Enum, ForeignKey, JSON, Integer, Text
from app.database import Base, UUIDType

class MatchStatus(str, PyEnum):
    DRAFT = "draft"
    WAITING_FOR_PLAYERS = "waiting_for_players"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    ARCHIVED = "archived"
    FAILED = "failed"

class ParticipantKind(str, PyEnum):
    HUMAN = "human"
    BOT = "bot"
    NEUTRAL = "neutral"
    SYSTEM = "system"

class ParticipantStatus(str, PyEnum):
    INVITED = "invited"
    JOINED = "joined"
    READY = "ready"
    ACTIVE = "active"
    ELIMINATED = "eliminated"
    LEFT = "left"
    TIMED_OUT = "timed_out"

class Match(Base):
    __tablename__ = "matches"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    game_id = Column(String, nullable=False)
    game_version = Column(String, nullable=True)
    title = Column(String, nullable=True)
    status = Column(Enum(MatchStatus), default=MatchStatus.DRAFT, nullable=False)
    created_by_user_id = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    root_context_id = Column(UUIDType, nullable=True)
    mode = Column(String, nullable=True)
    visibility_mode = Column(String, default="private")
    seed = Column(String, nullable=False, default=lambda: str(uuid.uuid4()))
    is_ranked = Column(Boolean, default=False)
    max_players = Column(Integer, nullable=True)
    current_phase = Column(String, nullable=True)
    winner_side_id = Column(String, nullable=True)
    settings = Column(JSON, default=dict)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

class Participant(Base):
    __tablename__ = "participants"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    kind = Column(Enum(ParticipantKind), default=ParticipantKind.HUMAN, nullable=False)
    user_id = Column(UUIDType, ForeignKey("users.id"), nullable=True)
    side_id = Column(String, nullable=True)
    role = Column(String, default="player")
    status = Column(Enum(ParticipantStatus), default=ParticipantStatus.JOINED, nullable=False)
    display_name = Column(String, nullable=True)
    is_ready = Column(Boolean, default=False)
    color = Column(String, nullable=True)
    fallback_policy_id = Column(UUIDType, nullable=True)
    bot_policy_id = Column(UUIDType, nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow)
    meta = Column(JSON, default=dict)
