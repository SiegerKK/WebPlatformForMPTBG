from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import TurnMode, TurnStatus, TurnPhase

class TurnStateCreate(BaseModel):
    context_id: uuid.UUID
    mode: TurnMode = TurnMode.STRICT
    active_side_id: Optional[str] = None
    deadline_at: Optional[datetime] = None

class TurnStateRead(BaseModel):
    id: uuid.UUID
    context_id: uuid.UUID
    turn_number: int
    mode: TurnMode
    phase: TurnPhase
    status: TurnStatus
    active_side_id: Optional[str] = None
    deadline_at: Optional[datetime] = None
    fallback_policy_id: Optional[uuid.UUID] = None
    submitted_players: List
    opened_at: Optional[datetime] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
