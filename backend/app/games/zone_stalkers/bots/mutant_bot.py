"""
AI policy for mutant agents — aggressive/territorial behaviour.
"""
import random
from typing import List, Dict, Any
from sdk.bot_policy import BotPolicy


class MutantBotPolicy(BotPolicy):
    """
    Mutant bot that:
    - Always attacks the weakest nearby enemy
    - Falls back to end_turn if no enemies
    """

    def decide(
        self,
        context_projection: Dict[str, Any],
        available_actions: List[Any],
        turn_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        context_type = context_projection.get("context_type", "encounter_combat")

        if context_type == "encounter_combat":
            return self._decide_combat(context_projection)

        return {"command_type": "end_turn", "payload": {}}

    def _decide_combat(self, state: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = state.get("bot_agent_id")
        participants = state.get("participants", {})
        agent = participants.get(agent_id, {})
        my_side = agent.get("side", "mutant")

        # Attack the enemy with the lowest HP
        enemies = [
            (pid, p) for pid, p in participants.items()
            if p.get("side") != my_side and p.get("is_alive", True)
        ]
        if enemies:
            weakest = min(enemies, key=lambda x: x[1].get("hp", 9999))
            return {"command_type": "attack", "payload": {"target_id": weakest[0]}}

        return {"command_type": "end_turn", "payload": {}}
