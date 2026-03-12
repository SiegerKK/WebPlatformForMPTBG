from pydantic import BaseModel
from typing import List

class ComponentSchema(BaseModel):
    name: str
    required: bool
    schema: dict = {}

class EntityArchetype(BaseModel):
    archetype_id: str
    display_name: str
    allowed_components: List[ComponentSchema] = []
    default_tags: List[str] = []
    default_visibility: str = "public"
