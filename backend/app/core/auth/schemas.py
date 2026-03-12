from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserRead(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    is_bot: bool
    created_at: datetime

    model_config = {"from_attributes": True}

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[str] = None
