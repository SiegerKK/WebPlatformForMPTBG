from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import MatchStatus

class MatchCreate(BaseModel):
    game_id: str
    config: dict = {}
    seed: Optional[str] = None

class MatchRead(BaseModel):
    id: uuid.UUID
    game_id: str
    status: MatchStatus
    created_by: uuid.UUID
    config: dict
    seed: str
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    model_config = {"from_attributes": True}

class MatchParticipantCreate(BaseModel):
    role: str = "player"
    faction: Optional[str] = None

class MatchParticipantRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    faction: Optional[str] = None
    is_active: bool
    joined_at: datetime
    model_config = {"from_attributes": True}
