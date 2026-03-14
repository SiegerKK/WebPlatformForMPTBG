"""
AI policy for NPC stalkers — prioritised survive/wander behaviour.
"""
import random
from typing import List, Dict, Any
from sdk.bot_policy import BotPolicy
from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES


class StalkerBotPolicy(BotPolicy):
    """
    Stalker bot that follows a priority hierarchy (GDD §11):
      P1 – Heal if HP critical
      P2 – Eat/drink if needs are high
      P3 – Pick up nearby artifacts
      P4 – Move to adjacent location
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

        inventory = agent.get("inventory", [])
        loc_id = agent.get("location_id")
        locations = state.get("locations", {})
        loc = locations.get(loc_id, {})

        # P1 — Heal if HP ≤ 30
        if agent.get("hp", 100) <= 30:
            heal_item = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
            if heal_item:
                return {"command_type": "consume_item", "payload": {"item_id": heal_item["id"]}}

        # P2 — Eat if hungry
        if agent.get("hunger", 0) >= 70:
            food = next((i for i in inventory if i["type"] in FOOD_ITEM_TYPES), None)
            if food:
                return {"command_type": "consume_item", "payload": {"item_id": food["id"]}}

        # P2b — Drink if thirsty
        if agent.get("thirst", 0) >= 70:
            nourishment = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
            if nourishment:
                return {"command_type": "consume_item", "payload": {"item_id": nourishment["id"]}}

        # P3 — Pick up artifact if available
        artifacts = loc.get("artifacts", [])
        if artifacts:
            return {
                "command_type": "pick_up_artifact",
                "payload": {"artifact_id": artifacts[0]["id"]},
            }

        # P4 — Move to a connected location
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
