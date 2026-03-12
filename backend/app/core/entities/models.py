import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, JSON, Text
from app.database import Base, UUIDType

class Entity(Base):
    __tablename__ = "entities"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    match_id = Column(UUIDType, ForeignKey("matches.id"), nullable=False)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    archetype_id = Column(String, nullable=False)
    owner_participant_id = Column(UUIDType, nullable=True)
    controller_participant_id = Column(UUIDType, nullable=True)
    display_name = Column(String, nullable=True)
    components = Column(JSON, default=dict)
    tags = Column(JSON, default=list)
    visibility_scope = Column(String, default="public")
    spawn_source = Column(String, nullable=True)
    parent_entity_id = Column(UUIDType, ForeignKey("entities.id"), nullable=True)
    alive = Column(Boolean, default=True)
    state_version = Column(Integer, default=0)
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
