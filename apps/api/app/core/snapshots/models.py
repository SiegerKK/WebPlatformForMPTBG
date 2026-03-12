import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class Snapshot(Base):
    __tablename__ = "snapshots"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, nullable=True)
    event_sequence_up_to = Column(Integer, nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
