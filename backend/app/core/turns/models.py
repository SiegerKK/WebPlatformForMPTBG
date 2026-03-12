import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, JSON
from app.database import Base, UUIDType

class TurnMode(str, PyEnum):
    STRICT = "strict"
    SIMULTANEOUS = "simultaneous"
    WEGO = "wego"
    HYBRID = "hybrid"

class TurnPhase(str, PyEnum):
    OPENING = "opening"
    COLLECTING = "collecting"
    RESOLVING = "resolving"
    CLOSED = "closed"

class TurnStatus(str, PyEnum):
    WAITING_FOR_PLAYERS = "waiting_for_players"
    RESOLVING = "resolving"
    RESOLVED = "resolved"

class TurnState(Base):
    __tablename__ = "turn_states"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    turn_number = Column(Integer, default=1)
    mode = Column(Enum(TurnMode), default=TurnMode.STRICT, nullable=False)
    phase = Column(Enum(TurnPhase), default=TurnPhase.COLLECTING, nullable=False)
    status = Column(Enum(TurnStatus), default=TurnStatus.WAITING_FOR_PLAYERS, nullable=False)
    active_side_id = Column(String, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    fallback_policy_id = Column(UUIDType, nullable=True)
    resolution_mode = Column(String, nullable=True)
    submitted_players = Column(JSON, default=list)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
