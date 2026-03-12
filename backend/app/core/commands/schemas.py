from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime
import uuid
from .models import CommandStatus

class CommandEnvelope(BaseModel):
    match_id: uuid.UUID
    context_id: uuid.UUID
    command_type: str
    payload: dict = {}

class CommandResult(BaseModel):
    command_id: uuid.UUID
    status: CommandStatus
    events: List[dict] = []
    error: Optional[str] = None

class CommandRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    player_id: uuid.UUID
    command_type: str
    payload: dict
    status: CommandStatus
    error_message: Optional[str] = None
    created_at: datetime
    executed_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
