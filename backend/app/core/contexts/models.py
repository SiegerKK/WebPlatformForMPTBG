import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, JSON
from app.database import Base, UUIDType

class ContextStatus(str, PyEnum):
    PENDING = "pending"
    ACTIVE = "active"
    RESOLVED = "resolved"
    ARCHIVED = "archived"

class GameContext(Base):
    __tablename__ = "game_contexts"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    parent_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=True)
    context_type = Column(String, nullable=False)
    status = Column(Enum(ContextStatus), default=ContextStatus.PENDING, nullable=False)
    state = Column(JSON, default=dict)
    state_version = Column(Integer, default=0)
    config = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
