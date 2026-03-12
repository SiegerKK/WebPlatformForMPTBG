import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class GameEvent(Base):
    __tablename__ = "game_events"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    sequence_no = Column(Integer, default=0)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    causation_command_id = Column(UUIDType, ForeignKey("commands.id"), nullable=True)
    correlation_id = Column(String, nullable=True)
    visibility_scope = Column(String, default="public")
    aggregate_version = Column(Integer, nullable=True)
    producer = Column(String, nullable=True)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
