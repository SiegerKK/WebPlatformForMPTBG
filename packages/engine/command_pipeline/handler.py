from __future__ import annotations
from typing import Any, Dict, List, Optional, Protocol

class CommandHandler(Protocol):
    """Protocol for command handler implementations."""
    def handle(
        self,
        command_type: str,
        payload: Dict[str, Any],
        context_state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> "CommandHandlerResult":
        ...

class CommandHandlerResult:
    def __init__(
        self,
        success: bool,
        new_state: Dict[str, Any],
        events: List[Dict[str, Any]],
        error: Optional[str] = None,
    ):
        self.success = success
        self.new_state = new_state
        self.events = events
        self.error = error

    @classmethod
    def ok(cls, new_state: Dict[str, Any], events: List[Dict[str, Any]]) -> "CommandHandlerResult":
        return cls(success=True, new_state=new_state, events=events)

    @classmethod
    def fail(cls, error: str, state: Dict[str, Any]) -> "CommandHandlerResult":
        return cls(success=False, new_state=state, events=[], error=error)
