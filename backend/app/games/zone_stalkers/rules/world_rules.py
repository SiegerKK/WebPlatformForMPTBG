"""
Rules for the zone_map context (world-level movement and interactions).

Supported commands:
- move_agent(target_location_id)
- pick_up_artifact(artifact_id, location_id)
- pick_up_item(item_id, location_id)
- end_turn
"""
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult


def validate_world_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    agent_id = _get_player_agent(state, player_id)
    if agent_id is None:
        return RuleCheckResult(valid=False, error="No agent found for this player")

    agents = state.get("agents", {})
    agent = agents.get(agent_id)
    if not agent:
        return RuleCheckResult(valid=False, error="Agent data missing")
    if not agent.get("is_alive", True):
        return RuleCheckResult(valid=False, error="Your agent is dead")

    if command_type == "end_turn":
        return RuleCheckResult(valid=True)

    if agent.get("action_used"):
        return RuleCheckResult(valid=False, error="You have already acted this turn")

    if command_type == "move_agent":
        return _validate_move(payload, state, agent)

    if command_type == "pick_up_artifact":
        return _validate_pick_up_artifact(payload, state, agent)

    if command_type == "pick_up_item":
        return _validate_pick_up_item(payload, state, agent)

    return RuleCheckResult(valid=False, error=f"Unknown command for zone_map: {command_type}")


def resolve_world_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import copy
    state = copy.deepcopy(state)
    agent_id = _get_player_agent(state, player_id)
    events: List[Dict[str, Any]] = []

    if command_type == "end_turn":
        # Advance world turn when all players have submitted
        events.append({"event_type": "turn_submitted", "payload": {"participant_id": player_id}})
        return state, events

    agent = state["agents"][agent_id]
    agent["action_used"] = True

    if command_type == "move_agent":
        target_loc_id = payload["target_location_id"]
        old_loc = agent["location_id"]
        # Remove from old location
        old_loc_data = state["locations"].get(old_loc, {})
        if agent_id in old_loc_data.get("agents", []):
            old_loc_data["agents"].remove(agent_id)
        # Add to new location
        agent["location_id"] = target_loc_id
        new_loc_data = state["locations"].get(target_loc_id, {})
        if agent_id not in new_loc_data.get("agents", []):
            new_loc_data.setdefault("agents", []).append(agent_id)
        events.append({
            "event_type": "agent_moved",
            "payload": {
                "agent_id": agent_id,
                "player_id": player_id,
                "from": old_loc,
                "to": target_loc_id,
            },
        })
        # Apply anomaly damage for entering a location
        loc_anomalies = new_loc_data.get("anomalies", [])
        if loc_anomalies:
            from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
            for anom in loc_anomalies:
                anom_info = ANOMALY_TYPES.get(anom["type"], {})
                dmg = anom_info.get("damage", 0) // 2  # half damage on passing through
                if dmg > 0:
                    agent["hp"] = max(0, agent["hp"] - dmg)
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

    elif command_type == "pick_up_artifact":
        artifact_id = payload["artifact_id"]
        loc_id = agent["location_id"]
        loc = state["locations"][loc_id]
        artifact = next((a for a in loc.get("artifacts", []) if a["id"] == artifact_id), None)
        if artifact:
            loc["artifacts"] = [a for a in loc["artifacts"] if a["id"] != artifact_id]
            agent.setdefault("inventory", []).append(artifact)
            events.append({
                "event_type": "artifact_picked_up",
                "payload": {"agent_id": agent_id, "artifact_id": artifact_id, "artifact_type": artifact["type"]},
            })

    elif command_type == "pick_up_item":
        item_id = payload["item_id"]
        loc_id = agent["location_id"]
        loc = state["locations"][loc_id]
        item = next((i for i in loc.get("items", []) if i["id"] == item_id), None)
        if item:
            loc["items"] = [i for i in loc["items"] if i["id"] != item_id]
            agent.setdefault("inventory", []).append(item)
            events.append({
                "event_type": "item_picked_up",
                "payload": {"agent_id": agent_id, "item_id": item_id, "item_type": item["type"]},
            })

    return state, events


# ──────────────────────────────
# Private helpers
# ──────────────────────────────

def _get_player_agent(state: Dict[str, Any], player_id: str) -> str | None:
    return state.get("player_agents", {}).get(player_id)


def _validate_move(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    target_loc_id = payload.get("target_location_id")
    if not target_loc_id:
        return RuleCheckResult(valid=False, error="target_location_id is required")
    current_loc_id = agent.get("location_id")
    locations = state.get("locations", {})
    if target_loc_id not in locations:
        return RuleCheckResult(valid=False, error=f"Location '{target_loc_id}' does not exist")
    current_loc = locations.get(current_loc_id, {})
    connected = {c["to"] for c in current_loc.get("connections", [])}
    if target_loc_id not in connected:
        return RuleCheckResult(valid=False, error=f"Location '{target_loc_id}' is not adjacent")
    return RuleCheckResult(valid=True)


def _validate_pick_up_artifact(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    artifact_id = payload.get("artifact_id")
    if not artifact_id:
        return RuleCheckResult(valid=False, error="artifact_id is required")
    loc_id = agent.get("location_id")
    loc = state.get("locations", {}).get(loc_id, {})
    artifact = next((a for a in loc.get("artifacts", []) if a["id"] == artifact_id), None)
    if not artifact:
        return RuleCheckResult(valid=False, error="Artifact not found in current location")
    return RuleCheckResult(valid=True)


def _validate_pick_up_item(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    item_id = payload.get("item_id")
    if not item_id:
        return RuleCheckResult(valid=False, error="item_id is required")
    loc_id = agent.get("location_id")
    loc = state.get("locations", {}).get(loc_id, {})
    item = next((i for i in loc.get("items", []) if i["id"] == item_id), None)
    if not item:
        return RuleCheckResult(valid=False, error="Item not found in current location")
    return RuleCheckResult(valid=True)
