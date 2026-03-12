from pydantic import BaseModel
from typing import List

class ActionDefinition(BaseModel):
    action_type: str
    display_name: str
    description: str = ""
    payload_schema: dict = {}
    applicable_archetypes: List[str] = []
    context_types: List[str] = []
