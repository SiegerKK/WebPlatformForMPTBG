"""
AI policy for NPC stalkers — basic wander/survive behaviour.
"""
import random
from typing import List, Dict, Any
from sdk.bot_policy import BotPolicy


class StalkerBotPolicy(BotPolicy):
    """
    Simple stalker bot that:
    - Picks up nearby artifacts
    - Moves to adjacent safe locations
    - Ends turn if nothing to do
    """

    def decide(
        self,
        context_projection: Dict[str, Any],
        available_actions: List[Any],
        turn_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        context_type = context_projection.get("context_type", "zone_map")

        if context_type == "zone_map":
            return self._decide_zone_map(context_projection)
        if context_type == "location_exploration":
            return self._decide_exploration(context_projection)
        if context_type == "encounter_combat":
            return self._decide_combat(context_projection)

        return {"command_type": "end_turn", "payload": {}}

    def _decide_zone_map(self, state: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = state.get("bot_agent_id")
        if not agent_id:
            return {"command_type": "end_turn", "payload": {}}

        agents = state.get("agents", {})
        agent = agents.get(agent_id, {})
        if not agent:
            return {"command_type": "end_turn", "payload": {}}

        loc_id = agent.get("location_id")
        locations = state.get("locations", {})
        loc = locations.get(loc_id, {})

        # Pick up artifact if available
        artifacts = loc.get("artifacts", [])
        if artifacts:
            return {
                "command_type": "pick_up_artifact",
                "payload": {"artifact_id": artifacts[0]["id"]},
            }

        # Move to a connected location
        connections = loc.get("connections", [])
        if connections:
            target = random.choice(connections)
            return {
                "command_type": "move_agent",
                "payload": {"target_location_id": target["to"]},
            }

        return {"command_type": "end_turn", "payload": {}}

    def _decide_exploration(self, state: Dict[str, Any]) -> Dict[str, Any]:
        directions = ["n", "s", "e", "w"]
        direction = random.choice(directions)
        return {"command_type": "explore_move", "payload": {"direction": direction}}

    def _decide_combat(self, state: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = state.get("bot_agent_id")
        participants = state.get("participants", {})
        agent = participants.get(agent_id, {})

        # Find an enemy to attack
        my_side = agent.get("side", "stalker")
        enemies = [
            pid for pid, p in participants.items()
            if p.get("side") != my_side and p.get("is_alive", True)
        ]
        if enemies:
            target = random.choice(enemies)
            return {"command_type": "attack", "payload": {"target_id": target}}

        return {"command_type": "end_turn", "payload": {}}
