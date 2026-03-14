"""
Fallback bot policy for human players who don't submit an action in time.

Conservative strategy:
- Heal if HP is low
- Eat/drink if needs are critical
- Retreat from combat
- Move toward a safe location
- Otherwise end turn
"""
import random
from typing import List, Dict, Any
from sdk.bot_policy import BotPolicy
from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES

_LOW_HP_THRESHOLD = 30
_HIGH_HUNGER_THRESHOLD = 70
_HIGH_THIRST_THRESHOLD = 70
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

        inventory = agent.get("inventory", [])

        # P1 — Heal if HP critically low
        if agent.get("hp", 100) < _LOW_HP_THRESHOLD:
            medkit = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
            if medkit:
                return {"command_type": "consume_item", "payload": {"item_id": medkit["id"]}}

        # P2 — Eat if hungry
        if agent.get("hunger", 0) >= _HIGH_HUNGER_THRESHOLD:
            food = next((i for i in inventory if i["type"] in FOOD_ITEM_TYPES), None)
            if food:
                return {"command_type": "consume_item", "payload": {"item_id": food["id"]}}

        # P2b — Drink if thirsty
        if agent.get("thirst", 0) >= _HIGH_THIRST_THRESHOLD:
            nourishment = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
            if nourishment:
                return {"command_type": "consume_item", "payload": {"item_id": nourishment["id"]}}

        # P3 — Move toward a safe hub if not already there
        loc_id = agent.get("location_id")
        locations = state.get("locations", {})
        current_loc = locations.get(loc_id, {})
        if current_loc.get("type") not in _SAFE_LOCATION_TYPES:
            connections = current_loc.get("connections", [])
            for conn in connections:
                connected_loc = locations.get(conn["to"], {})
                if connected_loc.get("type") in _SAFE_LOCATION_TYPES:
                    return {
                        "command_type": "move_agent",
                        "payload": {"target_location_id": conn["to"]},
                    }
            if connections:
                return {
                    "command_type": "move_agent",
                    "payload": {"target_location_id": connections[0]["to"]},
                }

        return {"command_type": "end_turn", "payload": {}}

    def _decide_combat(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Conservative: always retreat from combat
        return {"command_type": "retreat", "payload": {}}
