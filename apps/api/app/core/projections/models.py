import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class ProjectionType:
    SUMMARY = "summary"
    CONTEXT = "context"
    PLAYER_VISIBLE = "player_visible"
    UI = "ui"
    EVENT_FEED = "event_feed"

class Projection(Base):
    __tablename__ = "projections"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    projection_type = Column(String, nullable=False)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, nullable=True)
    participant_id = Column(UUIDType, nullable=True)
    source_event_sequence = Column(Integer, default=0)
    version = Column(Integer, default=0)
    payload = Column(JSON, default=dict)
    generated_at = Column(DateTime, default=datetime.utcnow)
