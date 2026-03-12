from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any

@dataclass
class GameEvent:
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    event_type: str
    sequence_no: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)
    causation_command_id: Optional[uuid.UUID] = None
    visibility_scope: str = "public"
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
