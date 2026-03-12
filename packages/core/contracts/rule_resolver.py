from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

class RuleResolver(ABC):
    """Contract for resolving game rules after commands are validated."""

    @abstractmethod
    def resolve(
        self,
        command_type: str,
        payload: Dict[str, Any],
        state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Resolve command and return (new_state, events)."""
        ...
