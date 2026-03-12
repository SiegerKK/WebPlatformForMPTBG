from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class Generator(ABC):
    """Contract for procedural content generators."""

    @property
    @abstractmethod
    def generator_id(self) -> str:
        """Unique identifier for this generator."""
        ...

    @abstractmethod
    def generate(self, config: Dict[str, Any], seed: str) -> Dict[str, Any]:
        """Generate content and return result blob."""
        ...

    @abstractmethod
    def get_entities(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract entity specs from a generation result."""
        ...
