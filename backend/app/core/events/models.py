import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class GameEvent(Base):
    __tablename__ = "game_events"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    caused_by_command_id = Column(UUIDType, ForeignKey("commands.id"), nullable=True)
    sequence_number = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
