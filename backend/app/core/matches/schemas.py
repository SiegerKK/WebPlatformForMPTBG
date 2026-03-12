from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import MatchStatus, ParticipantKind, ParticipantStatus

class MatchCreate(BaseModel):
    game_id: str
    title: Optional[str] = None
    mode: Optional[str] = None
    visibility_mode: str = "private"
    settings: dict = {}
    seed: Optional[str] = None

class MatchRead(BaseModel):
    id: uuid.UUID
    game_id: str
    game_version: Optional[str] = None
    title: Optional[str] = None
    status: MatchStatus
    created_by_user_id: uuid.UUID
    mode: Optional[str] = None
    visibility_mode: str
    seed: str
    is_ranked: bool
    settings: dict
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    model_config = {"from_attributes": True}

class ParticipantCreate(BaseModel):
    role: str = "player"
    side_id: Optional[str] = None
    kind: ParticipantKind = ParticipantKind.HUMAN
    display_name: Optional[str] = None

class ParticipantRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    kind: ParticipantKind
    side_id: Optional[str] = None
    role: str
    status: ParticipantStatus
    display_name: Optional[str] = None
    is_ready: bool
    joined_at: datetime
    model_config = {"from_attributes": True}

# Backward-compat aliases
MatchParticipantCreate = ParticipantCreate
MatchParticipantRead = ParticipantRead
