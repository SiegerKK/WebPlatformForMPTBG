import uuid
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON, Enum
from app.database import Base, UUIDType

class FallbackStrategy(str, PyEnum):
    SKIP_TURN = "skip_turn"
    END_TURN = "end_turn"
    DEFENSIVE_BOT = "defensive_bot"
    REPEAT_LAST_DOCTRINE = "repeat_last_doctrine"
    FULL_AI_CONTROL = "full_ai_control"

class TurnPolicy(Base):
    __tablename__ = "turn_policies"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    mode = Column(String, nullable=False, default="strict")
    deadline_seconds = Column(Integer, nullable=True)
    auto_advance = Column(Boolean, default=True)
    require_all_players_ready = Column(Boolean, default=False)
    fallback_on_timeout = Column(Boolean, default=True)
    resolution_order = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

class FallbackPolicy(Base):
    __tablename__ = "fallback_policies"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    strategy = Column(Enum(FallbackStrategy), default=FallbackStrategy.END_TURN, nullable=False)
    bot_policy_id = Column(UUIDType, nullable=True)
    config = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
