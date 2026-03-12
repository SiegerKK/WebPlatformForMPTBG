"""
Fallback bot policy for human players who don't submit an action in time.

Conservative strategy:
- Heal if HP is low
- Retreat from combat
- Move toward a safe location
- Otherwise end turn
"""
import random
from typing import List, Dict, Any
from sdk.bot_policy import BotPolicy

_LOW_HP_THRESHOLD = 30
_SAFE_LOCATION_TYPES = {"safe_hub"}


class FallbackPlayerBotPolicy(BotPolicy):
    """
    Used when a human player times out. Conservative and survival-focused.
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
        if context_type == "encounter_combat":
            return self._decide_combat(context_projection)
        if context_type == "location_exploration":
            return {"command_type": "leave_location", "payload": {}}
        if context_type == "trade_session":
            return {"command_type": "end_trade", "payload": {}}

        return {"command_type": "end_turn", "payload": {}}

    def _decide_zone_map(self, state: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = state.get("bot_agent_id")
        agents = state.get("agents", {})
        agent = agents.get(agent_id, {})
        if not agent:
            return {"command_type": "end_turn", "payload": {}}

        # Heal if HP is critically low and have a medkit
        inventory = agent.get("inventory", [])
        if agent.get("hp", 100) < _LOW_HP_THRESHOLD:
            medkit = next((i for i in inventory if i["type"] in ("medkit", "bandage")), None)
            if medkit:
                return {"command_type": "use_item", "payload": {"item_id": medkit["id"]}}

        # Move toward a safe hub if not already there
        loc_id = agent.get("location_id")
        locations = state.get("locations", {})
        current_loc = locations.get(loc_id, {})
        if current_loc.get("type") not in _SAFE_LOCATION_TYPES:
            # Find a connected safe location
            connections = current_loc.get("connections", [])
            for conn in connections:
                connected_loc = locations.get(conn["to"], {})
                if connected_loc.get("type") in _SAFE_LOCATION_TYPES:
                    return {
                        "command_type": "move_agent",
                        "payload": {"target_location_id": conn["to"]},
                    }
            # No safe neighbor — move away from danger (first connection)
            if connections:
                return {
                    "command_type": "move_agent",
                    "payload": {"target_location_id": connections[0]["to"]},
                }

        return {"command_type": "end_turn", "payload": {}}

    def _decide_combat(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Conservative: always retreat from combat
        return {"command_type": "retreat", "payload": {}}
