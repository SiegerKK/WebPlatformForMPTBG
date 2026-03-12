from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from .models import ContextStatus

class GameContextCreate(BaseModel):
    match_id: uuid.UUID
    parent_context_id: Optional[uuid.UUID] = None
    # backward-compat alias: accept parent_id too
    parent_id: Optional[uuid.UUID] = None
    context_type: str
    label: Optional[str] = None
    state_blob: dict = {}

    def get_parent_context_id(self) -> Optional[uuid.UUID]:
        return self.parent_context_id or self.parent_id

class GameContextRead(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    parent_context_id: Optional[uuid.UUID] = None
    # backward-compat: expose as parent_id too
    parent_id: Optional[uuid.UUID] = None
    context_type: str
    label: Optional[str] = None
    status: ContextStatus
    state_blob: dict
    state_version: int
    depth: int
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result_blob: Optional[dict] = None
    model_config = {"from_attributes": True}

    from pydantic import model_validator

    @model_validator(mode="after")
    def sync_parent_id(self):
        if self.parent_id is None and self.parent_context_id is not None:
            self.parent_id = self.parent_context_id
        return self
