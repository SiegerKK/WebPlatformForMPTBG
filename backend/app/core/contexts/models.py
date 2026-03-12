import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, JSON, Text
from app.database import Base, UUIDType

class ContextStatus(str, PyEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    RESOLVING = "resolving"
    SUSPENDED = "suspended"
    FINISHED = "finished"
    FAILED = "failed"
    ARCHIVED = "archived"

class GameContext(Base):
    __tablename__ = "game_contexts"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    parent_context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=True)
    context_type = Column(String, nullable=False)
    label = Column(String, nullable=True)
    status = Column(Enum(ContextStatus), default=ContextStatus.CREATED, nullable=False)
    state_blob = Column("state_blob", JSON, default=dict)
    state_version = Column(Integer, default=0)
    depth = Column(Integer, default=0)
    sequence_in_parent = Column(Integer, nullable=True)
    turn_policy_id = Column(UUIDType, nullable=True)
    time_policy_id = Column(UUIDType, nullable=True)
    visibility_policy_id = Column(UUIDType, nullable=True)
    generator_meta = Column(JSON, default=dict)
    resolution_state = Column(JSON, default=dict)
    result_blob = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
