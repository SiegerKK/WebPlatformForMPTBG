import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, JSON
from app.database import Base, UUIDType

class CommandStatus(str, PyEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"

class Command(Base):
    __tablename__ = "commands"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    player_id = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    command_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    status = Column(Enum(CommandStatus), default=CommandStatus.PENDING, nullable=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
