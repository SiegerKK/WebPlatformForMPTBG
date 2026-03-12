from __future__ import annotations
from abc import abstractmethod
from typing import Any, Dict, List, Tuple, Optional
from packages.core.contracts.game_definition import GameDefinition, ValidationResult

class BaseGameDefinition(GameDefinition):
    """
    Convenience base class for game definitions.
    Subclass this and implement the abstract methods to define a game.
    """

    @property
    def game_version(self) -> str:
        return "1.0.0"

    def get_initial_state(self, match_config: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def get_initial_entities(self, match_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def validate_command(
        self,
        command_type: str,
        payload: Dict[str, Any],
        state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> ValidationResult:
        handler = getattr(self, f"_validate_{command_type}", None)
        if handler:
            return handler(payload, state, entities, participant_id)
        return ValidationResult.ok()

    def resolve_command(
        self,
        command_type: str,
        payload: Dict[str, Any],
        state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        handler = getattr(self, f"_resolve_{command_type}", None)
        if handler:
            return handler(payload, state, entities, participant_id)
        return state, []
