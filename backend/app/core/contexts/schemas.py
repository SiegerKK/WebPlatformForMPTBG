from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import ContextStatus

class GameContextCreate(BaseModel):
    match_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    context_type: str
    config: dict = {}
    state: dict = {}

class GameContextRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    context_type: str
    status: ContextStatus
    state: dict
    state_version: int
    config: dict
    created_at: datetime
    resolved_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
