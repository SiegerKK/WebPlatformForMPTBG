from pydantic import BaseModel
from typing import Optional
import uuid


class UserProfileRead(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    is_bot: bool
    is_superuser: bool
    created_at: str
    matches_created: int
    matches_played: int

    model_config = {"from_attributes": False}
