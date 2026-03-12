from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

class GameDefinition(ABC):
    """Abstract contract that all game definitions must implement."""

    @property
    @abstractmethod
    def game_id(self) -> str:
        """Unique identifier for this game."""
        ...

    @property
    @abstractmethod
    def game_version(self) -> str:
        """Semantic version of this game definition."""
        ...

    @abstractmethod
    def get_initial_state(self, match_config: Dict[str, Any]) -> Dict[str, Any]:
        """Return initial context state for a new match."""
        ...

    @abstractmethod
    def get_initial_entities(self, match_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return list of entity specs to spawn at game start."""
        ...

    @abstractmethod
    def validate_command(
        self,
        command_type: str,
        payload: Dict[str, Any],
        state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> "ValidationResult":
        """Validate a command against current game state."""
        ...

    @abstractmethod
    def resolve_command(
        self,
        command_type: str,
        payload: Dict[str, Any],
        state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Apply a command and return (new_state, emitted_events)."""
        ...

class ValidationResult:
    def __init__(self, valid: bool, error: Optional[str] = None):
        self.valid = valid
        self.error = error

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def fail(cls, error: str) -> "ValidationResult":
        return cls(valid=False, error=error)
