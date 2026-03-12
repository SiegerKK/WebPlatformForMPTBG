from abc import ABC, abstractmethod
from typing import List, TYPE_CHECKING
if TYPE_CHECKING:
    from sdk.action_definition import ActionDefinition

class BotPolicy(ABC):
    @abstractmethod
    def decide(self, context_projection: dict, available_actions: List, turn_state: dict) -> dict:
        """Returns command payload"""
        ...

class PassBotPolicy(BotPolicy):
    """Always ends turn - fallback/default policy"""
    def decide(self, context_projection: dict, available_actions: List, turn_state: dict) -> dict:
        return {"command_type": "end_turn", "payload": {}}
