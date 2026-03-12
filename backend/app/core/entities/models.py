import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, JSON
from app.database import Base, UUIDType

class Entity(Base):
    __tablename__ = "entities"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    owner_id = Column(UUIDType, ForeignKey("users.id"), nullable=True)
    archetype = Column(String, nullable=False)
    components = Column(JSON, default=dict)
    tags = Column(JSON, default=list)
    visibility = Column(String, default="public")
    version = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
