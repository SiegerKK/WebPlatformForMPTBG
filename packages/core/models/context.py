from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from packages.core.enums.context import ContextStatus

@dataclass
class GameContext:
    id: uuid.UUID
    match_id: uuid.UUID
    context_type: str
    status: ContextStatus = ContextStatus.CREATED
    state_blob: Dict[str, Any] = field(default_factory=dict)
    state_version: int = 0
    depth: int = 0
    label: Optional[str] = None
    parent_context_id: Optional[uuid.UUID] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result_blob: Optional[Dict[str, Any]] = None
