from typing import List, Optional
from .schemas import BotConfig, BotDecision

class BotService:
    """
    Bot uses same Command API as players.
    Input: player-visible projection of world state
    Output: chosen command(s)
    """
    def decide(self, context_projection: dict, available_actions: List[str], bot_config: dict) -> BotDecision:
        return BotDecision(command_type="end_turn", payload={})


class ScriptedBot(BotService):
    """Simple scripted bot - always submits end_turn"""
    def decide(self, context_projection: dict, available_actions: List[str], bot_config: dict) -> BotDecision:
        return BotDecision(command_type="end_turn", payload={})
