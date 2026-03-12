from pydantic import BaseModel
from typing import Optional, List, Any

class BotConfig(BaseModel):
    bot_type: str = "scripted"
    policy: str = "pass"
    config: dict = {}

class BotDecision(BaseModel):
    command_type: str
    payload: dict = {}
