from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

class GameEventRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    event_type: str
    payload: dict
    caused_by_command_id: Optional[uuid.UUID] = None
    sequence_number: int
    created_at: datetime
    model_config = {"from_attributes": True}
