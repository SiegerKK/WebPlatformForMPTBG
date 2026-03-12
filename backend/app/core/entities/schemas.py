from pydantic import BaseModel, model_validator
from typing import Optional, List, Any
from datetime import datetime
import uuid

class EntityCreate(BaseModel):
    context_id: uuid.UUID
    match_id: Optional[uuid.UUID] = None
    owner_participant_id: Optional[uuid.UUID] = None
    # backward-compat: also accept archetype and owner_id
    archetype_id: Optional[str] = None
    archetype: Optional[str] = None
    owner_id: Optional[uuid.UUID] = None
    components: dict = {}
    tags: List[str] = []
    visibility_scope: str = "public"
    display_name: Optional[str] = None

    @model_validator(mode="after")
    def resolve_compat_fields(self):
        if self.archetype_id is None and self.archetype is not None:
            self.archetype_id = self.archetype
        if self.owner_participant_id is None and self.owner_id is not None:
            self.owner_participant_id = self.owner_id
        return self

class EntityUpdate(BaseModel):
    components: Optional[dict] = None
    tags: Optional[List[str]] = None
    visibility_scope: Optional[str] = None
    alive: Optional[bool] = None

class EntityRead(BaseModel):
    id: uuid.UUID
    context_id: uuid.UUID
    match_id: Optional[uuid.UUID] = None
    owner_participant_id: Optional[uuid.UUID] = None
    archetype_id: str
    # backward-compat alias
    archetype: Optional[str] = None
    components: dict
    tags: List[Any]
    visibility_scope: str
    state_version: int
    alive: bool
    created_at: datetime
    model_config = {"from_attributes": True}

    from pydantic import model_validator

    @model_validator(mode="after")
    def sync_archetype(self):
        if self.archetype is None:
            self.archetype = self.archetype_id
        return self
