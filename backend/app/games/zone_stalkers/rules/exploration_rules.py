"""
Rules for the location_exploration context.

Supported commands:
- explore_move(direction)  - move within the location
- pick_up_item(item_id)    - pick up an item
- interact(target_id)      - interact with an object (open container, etc.)
- leave_location           - return to zone_map
- end_turn
"""
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult


def validate_exploration_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    agent_id = _get_player_agent(state, player_id)
    if agent_id is None:
        return RuleCheckResult(valid=False, error="No exploration agent for this player")

    agents = state.get("local_agents", {})
    agent = agents.get(agent_id)
    if not agent or not agent.get("is_alive", True):
        return RuleCheckResult(valid=False, error="Agent not found or dead")

    if command_type == "end_turn":
        return RuleCheckResult(valid=True)

    if command_type == "leave_location":
        return RuleCheckResult(valid=True)

    if command_type == "explore_move":
        direction = payload.get("direction")
        valid_directions = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
        if direction not in valid_directions:
            return RuleCheckResult(valid=False, error=f"Invalid direction '{direction}'")
        # Check bounds
        pos = agent.get("position", {"x": 0, "y": 0})
        grid_size = state.get("grid_size", 8)
        new_x, new_y = _apply_direction(pos["x"], pos["y"], direction)
        if not (0 <= new_x < grid_size and 0 <= new_y < grid_size):
            return RuleCheckResult(valid=False, error="Move would go outside the location")
        return RuleCheckResult(valid=True)

    if command_type == "pick_up_item":
        item_id = payload.get("item_id")
        if not item_id:
            return RuleCheckResult(valid=False, error="item_id is required")
        items = state.get("local_items", [])
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            return RuleCheckResult(valid=False, error="Item not found in location")
        # Must be on same cell
        agent_pos = agent.get("position", {})
        item_pos = item.get("position", {})
        if agent_pos.get("x") != item_pos.get("x") or agent_pos.get("y") != item_pos.get("y"):
            return RuleCheckResult(valid=False, error="Item is not at your position")
        return RuleCheckResult(valid=True)

    if command_type == "interact":
        target_id = payload.get("target_id")
        if not target_id:
            return RuleCheckResult(valid=False, error="target_id is required")
        containers = state.get("containers", [])
        container = next((c for c in containers if c["id"] == target_id), None)
        if not container:
            return RuleCheckResult(valid=False, error="Container not found")
        return RuleCheckResult(valid=True)

    return RuleCheckResult(valid=False, error=f"Unknown exploration command: {command_type}")


def resolve_exploration_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import copy
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []
    agent_id = _get_player_agent(state, player_id)

    if command_type == "end_turn":
        events.append({"event_type": "turn_submitted", "payload": {"participant_id": player_id}})
        return state, events

    agents = state.get("local_agents", {})
    agent = agents.get(agent_id, {})

    if command_type == "leave_location":
        events.append({
            "event_type": "location_left",
            "payload": {"agent_id": agent_id, "location_id": state.get("location_id")},
        })
        state["exploration_over"] = True
        return state, events

    if command_type == "explore_move":
        direction = payload["direction"]
        pos = agent.get("position", {"x": 0, "y": 0})
        new_x, new_y = _apply_direction(pos["x"], pos["y"], direction)
        agent["position"] = {"x": new_x, "y": new_y}

        # Check for anomaly at new position
        for anom in state.get("local_anomalies", []):
            anom_pos = anom.get("position", {})
            if anom_pos.get("x") == new_x and anom_pos.get("y") == new_y:
                from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
                anom_info = ANOMALY_TYPES.get(anom["type"], {})
                dmg = anom_info.get("damage", 10)
                agent["hp"] = max(0, agent.get("hp", 100) - dmg)
                events.append({
                    "event_type": "anomaly_damage",
                    "payload": {
                        "agent_id": agent_id,
                        "anomaly_type": anom["type"],
                        "damage": dmg,
                        "hp_remaining": agent["hp"],
                    },
                })
                if agent["hp"] <= 0:
                    agent["is_alive"] = False
                    events.append({"event_type": "agent_died", "payload": {"agent_id": agent_id, "cause": "anomaly"}})

        events.append({
            "event_type": "agent_moved",
            "payload": {
                "agent_id": agent_id,
                "position": agent["position"],
                "direction": direction,
            },
        })

    elif command_type == "pick_up_item":
        item_id = payload["item_id"]
        items = state.get("local_items", [])
        item = next((i for i in items if i["id"] == item_id), None)
        if item:
            state["local_items"] = [i for i in items if i["id"] != item_id]
            agent.setdefault("inventory", []).append(item)
            events.append({
                "event_type": "item_picked_up",
                "payload": {"agent_id": agent_id, "item_id": item_id, "item_type": item["type"]},
            })

    elif command_type == "interact":
        target_id = payload["target_id"]
        containers = state.get("containers", [])
        container = next((c for c in containers if c["id"] == target_id), None)
        if container:
            container_items = container.get("inventory", [])
            agent.setdefault("inventory", []).extend(container_items)
            container["inventory"] = []
            container["opened"] = True
            events.append({
                "event_type": "container_opened",
                "payload": {
                    "agent_id": agent_id,
                    "container_id": target_id,
                    "items_found": len(container_items),
                },
            })

    return state, events


# ──────────────────────────────
# Private helpers
# ──────────────────────────────

def _get_player_agent(state: Dict[str, Any], player_id: str) -> str | None:
    return state.get("player_agents", {}).get(player_id)


def _apply_direction(x: int, y: int, direction: str):
    offsets = {
        "n": (0, -1), "s": (0, 1),
        "e": (1, 0),  "w": (-1, 0),
        "ne": (1, -1), "nw": (-1, -1),
        "se": (1, 1),  "sw": (-1, 1),
    }
    dx, dy = offsets.get(direction, (0, 0))
    return x + dx, y + dy
