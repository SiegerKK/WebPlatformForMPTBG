from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime
import uuid

class GameEventRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    event_type: str
    payload: dict
    causation_command_id: Optional[uuid.UUID] = None
    sequence_no: int
    correlation_id: Optional[str] = None
    visibility_scope: str = "public"
    producer: Optional[str] = None
    tags: List[Any] = []
    created_at: datetime
    model_config = {"from_attributes": True}
