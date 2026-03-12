from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any

@dataclass
class Entity:
    id: uuid.UUID
    match_id: uuid.UUID
    context_id: uuid.UUID
    archetype_id: str
    components: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    visibility_scope: str = "public"
    alive: bool = True
    state_version: int = 0
    owner_participant_id: Optional[uuid.UUID] = None
    controller_participant_id: Optional[uuid.UUID] = None
    display_name: Optional[str] = None
    parent_entity_id: Optional[uuid.UUID] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
