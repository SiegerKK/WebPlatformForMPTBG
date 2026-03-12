from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime
import uuid

class EntityCreate(BaseModel):
    context_id: uuid.UUID
    owner_id: Optional[uuid.UUID] = None
    archetype: str
    components: dict = {}
    tags: List[str] = []
    visibility: str = "public"

class EntityUpdate(BaseModel):
    components: Optional[dict] = None
    tags: Optional[List[str]] = None
    visibility: Optional[str] = None
    is_active: Optional[bool] = None

class EntityRead(BaseModel):
    id: uuid.UUID
    context_id: uuid.UUID
    owner_id: Optional[uuid.UUID] = None
    archetype: str
    components: dict
    tags: List[Any]
    visibility: str
    version: int
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}
