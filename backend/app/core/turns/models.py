import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, JSON
from app.database import Base, UUIDType

class TurnMode(str, PyEnum):
    STRICT = "strict"
    SIMULTANEOUS = "simultaneous"
    ASYNC_WINDOW = "async_window"

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
    status = Column(Enum(TurnStatus), default=TurnStatus.WAITING_FOR_PLAYERS, nullable=False)
    active_player_id = Column(UUIDType, ForeignKey("users.id"), nullable=True)
    deadline = Column(DateTime, nullable=True)
    submitted_players = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
