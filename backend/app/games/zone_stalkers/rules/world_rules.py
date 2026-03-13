"""
Rules for the zone_map context (world-level movement and interactions).

Supported commands:
- move_agent(target_location_id)      — instant adjacent move (1 action)
- travel(target_location_id)          — scheduled multi-turn travel (queues route)
- explore_location()                  — schedule 1-turn location exploration
- sleep(hours)                        — schedule multi-turn rest
- join_event(event_context_id)        — join an active zone_event
- pick_up_artifact(artifact_id)
- pick_up_item(item_id)
- end_turn
- take_control(agent_id)              — take over an AI-controlled stalker (meta, no action cost)
- debug_update_map(positions, connections)        — persist debug canvas layout (meta, no action cost)
- debug_update_location(loc_id, name, terrain_type?, anomaly_activity?, dominant_anomaly_type?) — edit location params in debug mode (meta)
- debug_create_location(name, position?) — add a new location in debug mode (meta)
- debug_spawn_stalker(loc_id, name?) — spawn an NPC stalker at a location in debug mode (meta)
- debug_spawn_mutant(loc_id, mutant_type) — spawn a mutant at a location in debug mode (meta)
"""
import collections
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult

# How many game-turns travel takes per hop based on danger_level
_TRAVEL_TURNS_PER_HOP: Dict[int, int] = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}
# Default sleep duration when the player just calls sleep with no hours argument
_DEFAULT_SLEEP_HOURS = 6
_MAX_SLEEP_HOURS = 10
_MIN_SLEEP_HOURS = 2

# Valid location types (shared between generator and debug commands)
_VALID_LOC_TYPES = frozenset([
    "safe_hub", "wild_area", "ruins", "military_zone", "anomaly_cluster", "underground",
])

# Valid terrain types (shared between generator and debug commands)
_VALID_TERRAIN_TYPES = frozenset([
    "plain", "hills", "slag_heaps", "industrial", "urban",
])

# Exploration rewards by location type (probability keys)
_EXPLORE_LOOT_CHANCE: Dict[str, float] = {
    "safe_hub": 0.15,
    "wild_area": 0.40,
    "ruins": 0.45,
    "military_zone": 0.35,
    "anomaly_cluster": 0.60,
    "underground": 0.50,
}


def validate_world_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    # take_control is a meta-command that works even without an existing agent
    if command_type == "take_control":
        return _validate_take_control(payload, state, player_id)

    # debug_update_map is a meta-command: persists canvas positions + connections
    if command_type == "debug_update_map":
        return _validate_debug_update_map(payload, state)

    # debug location CRUD meta-commands
    if command_type == "debug_update_location":
        return _validate_debug_update_location(payload, state)

    if command_type == "debug_create_location":
        return _validate_debug_create_location(payload)

    if command_type == "debug_spawn_stalker":
        return _validate_debug_spawn_stalker(payload, state)

    if command_type == "debug_spawn_mutant":
        return _validate_debug_spawn_mutant(payload, state)

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

    # Agents with an active scheduled_action can only end_turn
    if agent.get("scheduled_action"):
        return RuleCheckResult(valid=False, error="You already have an action in progress. Use end_turn to wait.")

    if agent.get("action_used"):
        return RuleCheckResult(valid=False, error="You have already acted this turn")

    if command_type == "move_agent":
        return _validate_move(payload, state, agent)

    if command_type == "travel":
        return _validate_travel(payload, state, agent)

    if command_type == "explore_location":
        return RuleCheckResult(valid=True)  # always valid if alive and no current action

    if command_type == "sleep":
        return _validate_sleep(payload, state, agent)

    if command_type == "join_event":
        return _validate_join_event(payload, state, agent)

    if command_type == "pick_up_artifact":
        return _validate_pick_up_artifact(payload, state, agent)

    if command_type == "pick_up_item":
        return _validate_pick_up_item(payload, state, agent)

    if command_type == "consume_item":
        return _validate_consume_item(payload, state, agent)

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

    # ── take_control: meta-command, no action cost ────────────────────────────
    if command_type == "take_control":
        target_agent_id = payload["agent_id"]
        # Release current agent back to AI if the player had one
        if agent_id and agent_id in state.get("agents", {}):
            state["agents"][agent_id]["controller"] = {"kind": "ai", "participant_id": None}
        # Assign the new agent to this player
        state.setdefault("player_agents", {})[player_id] = target_agent_id
        state["agents"][target_agent_id]["controller"] = {"kind": "human", "participant_id": player_id}
        events.append({
            "event_type": "agent_control_taken",
            "payload": {
                "player_id": player_id,
                "agent_id": target_agent_id,
                "previous_agent_id": agent_id,
            },
        })
        return state, events

    # ── debug_update_map: meta-command, persists canvas layout ────────────────
    if command_type == "debug_update_map":
        positions = payload.get("positions", {})
        connections = payload.get("connections", {})
        # Persist card positions
        state.setdefault("debug_layout", {})["positions"] = positions
        # Update location connections for each location provided
        locations = state.get("locations", {})
        for loc_id, conns in connections.items():
            if loc_id in locations:
                locations[loc_id]["connections"] = [
                    {"to": c["to"], "type": c.get("type", "normal")}
                    for c in conns
                    if "to" in c and c["to"] in locations
                ]
        events.append({"event_type": "debug_map_updated", "payload": {}})
        return state, events

    # ── debug_update_location: meta-command, edit location params ─────────────
    if command_type == "debug_update_location":
        loc_id = payload["loc_id"]
        loc = state["locations"][loc_id]
        loc["name"] = str(payload.get("name", loc["name"])).strip()
        if "terrain_type" in payload:
            loc["terrain_type"] = payload["terrain_type"]
        if "anomaly_activity" in payload:
            loc["anomaly_activity"] = int(payload["anomaly_activity"])
        if "dominant_anomaly_type" in payload:
            loc["dominant_anomaly_type"] = payload["dominant_anomaly_type"] or None
        events.append({"event_type": "debug_location_updated", "payload": {"loc_id": loc_id}})
        return state, events

    # ── debug_create_location: meta-command, add new location ─────────────────
    if command_type == "debug_create_location":
        existing_ids = set(state.get("locations", {}).keys())
        # Generate a collision-free id
        n = len(existing_ids)
        new_id = f"loc_debug_{n}"
        while new_id in existing_ids:
            n += 1
            new_id = f"loc_debug_{n}"
        new_loc = {
            "id": new_id,
            "name": str(payload["name"]).strip(),
            "type": "wild_area",
            "danger_level": 1,
            "terrain_type": payload.get("terrain_type", "plain"),
            "anomaly_activity": int(payload.get("anomaly_activity", 0)),
            "dominant_anomaly_type": payload.get("dominant_anomaly_type") or None,
            "connections": [],
            "anomalies": [],
            "artifacts": [],
            "agents": [],
            "items": [],
        }
        state["locations"][new_id] = new_loc
        # If a canvas position was provided, persist it immediately
        pos = payload.get("position")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            state.setdefault("debug_layout", {}).setdefault("positions", {})[new_id] = {
                "x": float(pos["x"]),
                "y": float(pos["y"]),
            }
        events.append({"event_type": "debug_location_created", "payload": {"loc_id": new_id}})
        return state, events

    # ── debug_spawn_stalker: meta-command, spawn NPC stalker ──────────────────
    if command_type == "debug_spawn_stalker":
        import random as _random
        from app.games.zone_stalkers.generators.zone_generator import _make_stalker_agent
        loc_id = payload["loc_id"]
        existing_agents = state.get("agents", {})
        n = len(existing_agents)
        new_agent_id = f"agent_debug_{n}"
        while new_agent_id in existing_agents:
            n += 1
            new_agent_id = f"agent_debug_{n}"
        name = str(payload.get("name", "")).strip() or f"Сталкер #{n}"
        rng = _random.Random(new_agent_id)
        agent = _make_stalker_agent(
            agent_id=new_agent_id,
            name=name,
            location_id=loc_id,
            controller_kind="ai",
            participant_id=None,
            rng=rng,
        )
        state.setdefault("agents", {})[new_agent_id] = agent
        state["locations"][loc_id]["agents"].append(new_agent_id)
        events.append({"event_type": "debug_stalker_spawned", "payload": {"agent_id": new_agent_id, "loc_id": loc_id}})
        return state, events

    # ── debug_spawn_mutant: meta-command, spawn a mutant ─────────────────────
    if command_type == "debug_spawn_mutant":
        from app.games.zone_stalkers.balance.mutants import MUTANT_TYPES
        loc_id = payload["loc_id"]
        mutant_type = payload["mutant_type"]
        mutant_info = MUTANT_TYPES[mutant_type]
        existing_mutants = state.get("mutants", {})
        n = len(existing_mutants)
        new_mutant_id = f"mutant_debug_{n}"
        while new_mutant_id in existing_mutants:
            n += 1
            new_mutant_id = f"mutant_debug_{n}"
        mutant = {
            "id": new_mutant_id,
            "archetype": "mutant_agent",
            "type": mutant_type,
            "name": mutant_info["name"],
            "location_id": loc_id,
            "hp": mutant_info["hp"],
            "max_hp": mutant_info["max_hp"],
            "damage": mutant_info["damage"],
            "defense": mutant_info["defense"],
            "aggression": mutant_info["aggression"],
            "is_alive": True,
            "loot_table": mutant_info["loot_table"],
            "money_drop": mutant_info["money_drop"],
        }
        state.setdefault("mutants", {})[new_mutant_id] = mutant
        state["locations"][loc_id]["agents"].append(new_mutant_id)
        events.append({"event_type": "debug_mutant_spawned", "payload": {"mutant_id": new_mutant_id, "loc_id": loc_id}})
        return state, events

    if command_type == "end_turn":
        # Mark this player's agent as having acted
        if agent_id and agent_id in state.get("agents", {}):
            state["agents"][agent_id]["action_used"] = True
        events.append({"event_type": "turn_submitted", "payload": {"participant_id": player_id}})

        # Check whether all alive human agents have now ended their turns.
        # If so, auto-advance the world by one tick.
        player_agents = state.get("player_agents", {})  # {user_id: agent_id}
        all_human_acted = True
        for _uid, aid in player_agents.items():
            agent_data = state.get("agents", {}).get(aid)
            if agent_data and agent_data.get("is_alive", True) and not agent_data.get("action_used"):
                all_human_acted = False
                break

        if all_human_acted:
            from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
            state, tick_events = tick_zone_map(state)
            events.extend(tick_events)

        return state, events

    agent = state["agents"][agent_id]
    agent["action_used"] = True

    if command_type == "move_agent":
        target_loc_id = payload["target_location_id"]
        old_loc = agent["location_id"]
        old_loc_data = state["locations"].get(old_loc, {})
        if agent_id in old_loc_data.get("agents", []):
            old_loc_data["agents"].remove(agent_id)
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
        loc_anomalies = new_loc_data.get("anomalies", [])
        if loc_anomalies:
            from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
            for anom in loc_anomalies:
                anom_info = ANOMALY_TYPES.get(anom["type"], {})
                dmg = anom_info.get("damage", 0) // 2
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

    elif command_type == "travel":
        target_loc_id = payload["target_location_id"]
        route = _bfs_route(state["locations"], agent["location_id"], target_loc_id)
        if not route:
            events.append({"event_type": "travel_failed", "payload": {"agent_id": agent_id, "reason": "no_route"}})
        else:
            turns = _route_travel_turns(route, state["locations"])
            agent["scheduled_action"] = {
                "type": "travel",
                "turns_remaining": turns,
                "turns_total": turns,
                "target_id": target_loc_id,
                "route": route,  # ordered list of loc IDs (excluding start)
                "started_turn": state.get("world_turn", 1),
            }
            events.append({
                "event_type": "travel_started",
                "payload": {
                    "agent_id": agent_id,
                    "player_id": player_id,
                    "destination": target_loc_id,
                    "turns_required": turns,
                    "route": route,
                },
            })

    elif command_type == "explore_location":
        loc_id = agent["location_id"]
        loc = state["locations"].get(loc_id, {})
        agent["scheduled_action"] = {
            "type": "explore",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": loc_id,
            "started_turn": state.get("world_turn", 1),
        }
        events.append({
            "event_type": "exploration_started",
            "payload": {
                "agent_id": agent_id,
                "player_id": player_id,
                "location_id": loc_id,
                "location_name": loc.get("name", loc_id),
            },
        })

    elif command_type == "sleep":
        hours = max(_MIN_SLEEP_HOURS, min(_MAX_SLEEP_HOURS, int(payload.get("hours", _DEFAULT_SLEEP_HOURS))))
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": hours,
            "turns_total": hours,
            "target_id": agent["location_id"],
            "started_turn": state.get("world_turn", 1),
        }
        events.append({
            "event_type": "sleep_started",
            "payload": {
                "agent_id": agent_id,
                "player_id": player_id,
                "hours": hours,
            },
        })

    elif command_type == "join_event":
        event_ctx_id = payload.get("event_context_id", "")
        agent["scheduled_action"] = {
            "type": "event",
            "turns_remaining": 1,     # updated once inside the event
            "turns_total": 1,
            "target_id": event_ctx_id,
            "started_turn": state.get("world_turn", 1),
        }
        events.append({
            "event_type": "event_joined",
            "payload": {
                "agent_id": agent_id,
                "player_id": player_id,
                "event_context_id": event_ctx_id,
            },
        })

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

    elif command_type == "consume_item":
        item_id = payload["item_id"]
        item = next((i for i in agent.get("inventory", []) if i["id"] == item_id), None)
        if item:
            from app.games.zone_stalkers.balance.items import ITEM_TYPES
            item_info = ITEM_TYPES.get(item["type"], {})
            effects = item_info.get("effects", {})
            _apply_item_effects(agent, effects)
            agent["inventory"] = [i for i in agent["inventory"] if i["id"] != item_id]
            events.append({
                "event_type": "item_consumed",
                "payload": {
                    "agent_id": agent_id,
                    "player_id": player_id,
                    "item_id": item_id,
                    "item_type": item["type"],
                    "effects": effects,
                },
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


def _validate_travel(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    target_loc_id = payload.get("target_location_id")
    if not target_loc_id:
        return RuleCheckResult(valid=False, error="target_location_id is required")
    locations = state.get("locations", {})
    if target_loc_id not in locations:
        return RuleCheckResult(valid=False, error=f"Location '{target_loc_id}' does not exist")
    if target_loc_id == agent.get("location_id"):
        return RuleCheckResult(valid=False, error="Already at destination")
    route = _bfs_route(locations, agent["location_id"], target_loc_id)
    if not route:
        return RuleCheckResult(valid=False, error=f"Location '{target_loc_id}' is unreachable")
    return RuleCheckResult(valid=True)


def _validate_sleep(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    loc_id = agent.get("location_id")
    loc = state.get("locations", {}).get(loc_id, {})
    if loc.get("type") not in ("safe_hub", "ruins"):
        return RuleCheckResult(valid=False, error="You can only sleep in a safe hub or ruins")
    return RuleCheckResult(valid=True)


def _validate_join_event(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    event_ctx_id = payload.get("event_context_id")
    if not event_ctx_id:
        return RuleCheckResult(valid=False, error="event_context_id is required")
    active_events = state.get("active_events", [])
    if event_ctx_id not in active_events:
        return RuleCheckResult(valid=False, error="This event is not active")
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


def _validate_consume_item(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    item_id = payload.get("item_id")
    if not item_id:
        return RuleCheckResult(valid=False, error="item_id is required")
    item = next((i for i in agent.get("inventory", []) if i["id"] == item_id), None)
    if not item:
        return RuleCheckResult(valid=False, error="Item not found in inventory")
    from app.games.zone_stalkers.balance.items import CONSUMABLE_ITEM_TYPES
    if item["type"] not in CONSUMABLE_ITEM_TYPES:
        return RuleCheckResult(valid=False, error="This item cannot be consumed")
    return RuleCheckResult(valid=True)


def _apply_item_effects(agent: Dict[str, Any], effects: Dict[str, Any]) -> None:
    """Apply item effect dict to agent stats (hp, radiation, stamina, hunger, thirst)."""
    if "hp" in effects:
        agent["hp"] = max(0, min(agent.get("max_hp", 100), agent.get("hp", 100) + effects["hp"]))
    if "radiation" in effects:
        agent["radiation"] = max(0, agent.get("radiation", 0) + effects["radiation"])
    if "stamina" in effects:
        agent["stamina"] = min(100, agent.get("stamina", 100) + effects["stamina"])
    if "hunger" in effects:
        agent["hunger"] = max(0, agent.get("hunger", 0) + effects["hunger"])
    if "thirst" in effects:
        agent["thirst"] = max(0, agent.get("thirst", 0) + effects["thirst"])


def _bfs_route(locations: Dict[str, Any], start: str, goal: str) -> List[str]:
    """Return ordered list of location IDs from start (exclusive) to goal (inclusive), or [] if unreachable."""
    if start == goal:
        return []
    visited = {start}
    queue: collections.deque = collections.deque([(start, [])])
    while queue:
        current, path = queue.popleft()
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn["to"]
            if nxt not in visited:
                new_path = path + [nxt]
                if nxt == goal:
                    return new_path
                visited.add(nxt)
                queue.append((nxt, new_path))
    return []


def _route_travel_turns(route: List[str], locations: Dict[str, Any]) -> int:
    """Sum up travel turns for each hop in the route."""
    total = 0
    for loc_id in route:
        danger = locations.get(loc_id, {}).get("danger_level", 2)
        total += _TRAVEL_TURNS_PER_HOP.get(danger, 2)
    return max(1, total)


def _validate_take_control(
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    target_agent_id = payload.get("agent_id")
    if not target_agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    agents = state.get("agents", {})
    agent = agents.get(target_agent_id)
    if not agent:
        return RuleCheckResult(valid=False, error="Agent not found")
    controller = agent.get("controller", {})
    if controller.get("kind") != "ai":
        return RuleCheckResult(valid=False, error="Agent is already controlled by a player")
    if not agent.get("is_alive", True):
        return RuleCheckResult(valid=False, error="Cannot take control of a dead agent")
    return RuleCheckResult(valid=True)


def _validate_debug_update_map(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    positions = payload.get("positions")
    connections = payload.get("connections")
    if positions is not None and not isinstance(positions, dict):
        return RuleCheckResult(valid=False, error="positions must be a dict")
    if connections is not None and not isinstance(connections, dict):
        return RuleCheckResult(valid=False, error="connections must be a dict")
    locations = state.get("locations", {})
    if connections:
        for loc_id, conns in connections.items():
            if loc_id not in locations:
                return RuleCheckResult(valid=False, error=f"Unknown location id: {loc_id}")
            if not isinstance(conns, list):
                return RuleCheckResult(valid=False, error=f"Connections for {loc_id} must be a list")
            for conn in conns:
                to_id = conn.get("to") if isinstance(conn, dict) else None
                if not to_id or to_id not in locations:
                    return RuleCheckResult(
                        valid=False,
                        error=f"Connection target '{to_id}' is not a valid location id",
                    )
    return RuleCheckResult(valid=True)


def _validate_debug_update_location(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    name = str(payload.get("name", "")).strip()
    if not name:
        return RuleCheckResult(valid=False, error="name must be non-empty")
    if "terrain_type" in payload:
        terrain_type = payload["terrain_type"]
        if terrain_type not in _VALID_TERRAIN_TYPES:
            return RuleCheckResult(valid=False, error=f"Invalid terrain_type '{terrain_type}'; must be one of {sorted(_VALID_TERRAIN_TYPES)}")
    if "anomaly_activity" in payload:
        aa = payload["anomaly_activity"]
        if not isinstance(aa, int) or not (0 <= aa <= 10):
            return RuleCheckResult(valid=False, error="anomaly_activity must be an integer between 0 and 10")
    return RuleCheckResult(valid=True)


def _validate_debug_create_location(
    payload: Dict[str, Any],
) -> RuleCheckResult:
    name = str(payload.get("name", "")).strip()
    if not name:
        return RuleCheckResult(valid=False, error="name must be non-empty")
    return RuleCheckResult(valid=True)


def _validate_debug_spawn_stalker(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    return RuleCheckResult(valid=True)


def _validate_debug_spawn_mutant(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    from app.games.zone_stalkers.balance.mutants import MUTANT_TYPES
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    mutant_type = payload.get("mutant_type")
    if not mutant_type or mutant_type not in MUTANT_TYPES:
        return RuleCheckResult(valid=False, error=f"Invalid mutant_type '{mutant_type}'; must be one of {sorted(MUTANT_TYPES.keys())}")
    return RuleCheckResult(valid=True)
