from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class Projection:
    id: uuid.UUID
    projection_type: str
    match_id: uuid.UUID
    payload: Dict[str, Any] = field(default_factory=dict)
    version: int = 0
    context_id: Optional[uuid.UUID] = None
    participant_id: Optional[uuid.UUID] = None
    source_event_sequence: int = 0
    generated_at: datetime = field(default_factory=datetime.utcnow)
