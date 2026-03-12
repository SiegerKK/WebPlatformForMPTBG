from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

@dataclass
class RuleCheckResult:
    valid: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    events: List[dict] = field(default_factory=list)

class RuleResolver(ABC):
    """Contract for resolving game rules after commands are validated."""

    def validate(
        self,
        command_type: str,
        payload: Dict[str, Any],
        context_state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> RuleCheckResult:
        return RuleCheckResult(valid=True)

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
