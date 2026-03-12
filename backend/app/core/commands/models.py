import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, JSON, Integer
from app.database import Base, UUIDType

class CommandStatus(str, PyEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Command(Base):
    __tablename__ = "commands"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    participant_id = Column(UUIDType, nullable=False)
    command_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    client_request_id = Column(String, nullable=True, index=True)
    status = Column(Enum(CommandStatus), default=CommandStatus.RECEIVED, nullable=False)
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    submitted_via = Column(String, nullable=True)
    expected_context_version = Column(Integer, nullable=True)
    causation_ui_action = Column(String, nullable=True)
    debug_meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
