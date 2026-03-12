import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=True)
    context_id = Column(UUIDType, nullable=True)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(String, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
