from pydantic import BaseModel
from typing import Optional, Any

class GeneratorConfig(BaseModel):
    generator_id: str
    version: str = "1.0"
    seed: str
    config: dict = {}
