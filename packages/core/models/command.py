from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from packages.core.enums.command import CommandStatus

@dataclass
class Command:
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    participant_id: uuid.UUID
    command_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: CommandStatus = CommandStatus.RECEIVED
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    executed_at: Optional[datetime] = None
