from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import TurnMode, TurnStatus

class TurnStateCreate(BaseModel):
    context_id: uuid.UUID
    mode: TurnMode = TurnMode.STRICT
    active_player_id: Optional[uuid.UUID] = None
    deadline: Optional[datetime] = None

class TurnStateRead(BaseModel):
    id: uuid.UUID
    context_id: uuid.UUID
    turn_number: int
    mode: TurnMode
    status: TurnStatus
    active_player_id: Optional[uuid.UUID] = None
    deadline: Optional[datetime] = None
    submitted_players: List
    created_at: datetime
    resolved_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
