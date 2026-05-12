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
- consume_item(item_id)
- buy_from_trader(item_type)          — buy any item from catalogue at a trader (1 action)
- end_turn
- take_control(agent_id)              — take over an AI-controlled stalker (meta, no action cost)
- debug_update_map(positions, connections, regions?) — persist debug canvas layout (meta, no action cost)
- debug_update_location(loc_id, name, terrain_type?, anomaly_activity?, dominant_anomaly_type?, region?, exit_zone?) — edit location params in debug mode (meta)
- debug_create_location(name, position?) — add a new location in debug mode (meta)
- debug_delete_location(loc_id) — remove a location and all its connections in debug mode (meta)
- debug_spawn_stalker(loc_id, name?) — spawn an NPC stalker at a location in debug mode (meta)
- debug_spawn_mutant(loc_id, mutant_type) — spawn a mutant at a location in debug mode (meta)
- debug_spawn_trader(loc_id, name?) — spawn a trader NPC at a location in debug mode (meta)
- debug_spawn_artifact(loc_id, artifact_type?) — spawn an artifact at a location in debug mode (meta)
- debug_spawn_item_on_location(loc_id, item_type) — place a loose item on the ground at a location in debug mode (meta)
- debug_delete_all_npcs() — remove all bot-controlled stalker NPCs (meta)
- debug_delete_all_mutants() — remove all mutants (meta)
- debug_delete_all_artifacts() — remove all artifacts from all locations (meta)
- debug_delete_all_traders() — remove all traders (meta)
- debug_delete_all_items() — remove all loose items from location grounds and all agent inventories (meta)
- debug_delete_agent(agent_id) — remove any single agent/mutant/trader by id (meta)
- debug_set_time(day?, hour?, minute?) — override current world time (meta)
- debug_advance_turns(max_n?, stop_on_decision?) — advance up to max_n turns, optionally stopping when any bot makes a new decision (meta)
- debug_add_item(agent_id, item_type) — add an item to an agent's inventory in debug mode (meta)
- debug_remove_item(agent_id, item_id) — remove an item from an agent's inventory or equipment slot in debug mode (meta)
- debug_set_agent_threshold(agent_id, amount) — set agent's material_threshold (3000–10000) in debug mode (meta)
"""
import collections
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult

# Default sleep duration when the player just calls sleep with no hours argument
_DEFAULT_SLEEP_HOURS = 6
_MAX_SLEEP_HOURS = 10
_MIN_SLEEP_HOURS = 2

# Valid terrain types (shared between generator and debug commands)
_VALID_TERRAIN_TYPES = frozenset([
    "plain", "hills", "slag_heaps", "industrial", "buildings", "military_buildings",
    "hamlet", "farm", "field_camp", "dungeon", "x_lab", "bridge",
    # Additional types supported for custom imported maps
    "tunnel", "swamp", "scientific_bunker",
])

_VALID_GLOBAL_GOALS = frozenset([
    "get_rich",
    "unravel_zone_mystery",
    "kill_stalker",
])

# Image slot constants and helpers — single source of truth in location_images.py
from app.games.zone_stalkers.location_images import (
    VALID_LOCATION_IMAGE_SLOTS as _VALID_LOCATION_IMAGE_SLOTS,
    ORDERED_LOCATION_IMAGE_SLOTS as _ORDERED_IMAGE_SLOTS_TUPLE,
    sync_location_primary_image_url as _sync_location_primary_image_url,
    migrate_location_images,
)
# Legacy alias used inside this module
_ORDERED_IMAGE_SLOTS = list(_ORDERED_IMAGE_SLOTS_TUPLE)
# Keep VALID_LOCATION_IMAGE_SLOTS as the canonical name internally
_VALID_LOCATION_IMAGE_SLOTS = _VALID_LOCATION_IMAGE_SLOTS


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

    if command_type == "debug_delete_location":
        return _validate_debug_delete_location(payload, state)

    if command_type == "debug_spawn_stalker":
        return _validate_debug_spawn_stalker(payload, state)

    if command_type == "debug_spawn_mutant":
        return _validate_debug_spawn_mutant(payload, state)

    if command_type == "debug_spawn_trader":
        return _validate_debug_spawn_trader(payload, state)

    if command_type == "debug_spawn_artifact":
        return _validate_debug_spawn_artifact(payload, state)

    if command_type == "debug_spawn_item_on_location":
        return _validate_debug_spawn_item_on_location(payload, state)

    if command_type in ("debug_delete_all_npcs", "debug_delete_all_mutants",
                        "debug_delete_all_artifacts", "debug_delete_all_traders",
                        "debug_delete_all_items",
                        "debug_set_time", "debug_advance_turns",
                        "debug_trigger_emission", "debug_import_full_map"):
        return RuleCheckResult(valid=True)

    if command_type == "debug_set_agent_money":
        return _validate_debug_set_agent_money(payload, state)

    if command_type == "debug_set_agent_threshold":
        return _validate_debug_set_agent_threshold(payload, state)

    if command_type == "debug_delete_agent":
        return _validate_debug_delete_agent(payload, state)

    if command_type == "debug_preview_bot_decision":
        return _validate_debug_preview_bot_decision(payload, state)

    if command_type == "debug_explain_agent_v2":
        return _validate_debug_preview_bot_decision(payload, state)  # same validation

    if command_type == "debug_set_location_primary_image":
        return _validate_debug_set_location_primary_image(payload, state)

    if command_type == "debug_add_item":
        return _validate_debug_add_item(payload, state)

    if command_type == "debug_remove_item":
        return _validate_debug_remove_item(payload, state)

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

    if command_type == "buy_from_trader":
        return _validate_buy_from_trader(payload, state, agent)

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
        # Release current agent back to bot AI if the player had one
        if agent_id and agent_id in state.get("agents", {}):
            state["agents"][agent_id]["controller"] = {"kind": "bot", "participant_id": None}
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
        regions = payload.get("regions")
        # Persist card positions
        state.setdefault("debug_layout", {})["positions"] = positions
        # Persist region metadata if provided
        if regions is not None:
            state["debug_layout"]["regions"] = regions
        # Update location connections for each location provided
        locations = state.get("locations", {})
        for loc_id, conns in connections.items():
            if loc_id in locations:
                locations[loc_id]["connections"] = [
                    {
                        "to": c["to"],
                        "travel_time": int(c.get("travel_time", 15)),
                        "type": c.get("type", "normal"),
                        "closed": bool(c.get("closed", False)),
                    }
                    for c in conns
                    if "to" in c and c["to"] in locations
                ]
        events.append({"event_type": "debug_map_updated", "payload": {}})
        return state, events

    # ── debug_update_location: meta-command, edit location params ─────────────
    if command_type == "debug_update_location":
        loc_id = payload["loc_id"]
        loc = state["locations"][loc_id]
        # Ensure image_slots exists before applying any image changes (P1-4)
        migrate_location_images(loc)
        _map_bump_fields = {"name", "terrain_type", "region", "exit_zone",
                            "image_url", "image_slots", "primary_image_slot"}
        _needs_map_bump = bool(_map_bump_fields & set(payload.keys()))
        loc["name"] = str(payload.get("name", loc["name"])).strip()
        if "terrain_type" in payload:
            loc["terrain_type"] = payload["terrain_type"]
        if "anomaly_activity" in payload:
            loc["anomaly_activity"] = int(payload["anomaly_activity"])
        if "dominant_anomaly_type" in payload:
            loc["dominant_anomaly_type"] = payload["dominant_anomaly_type"] or None
        if "region" in payload:
            region_val = payload["region"]
            loc["region"] = region_val if region_val else None
        if "exit_zone" in payload:
            loc["exit_zone"] = bool(payload["exit_zone"])
        if "image_url" in payload:
            url = payload["image_url"] or None
            loc["image_url"] = url
            slots = loc.setdefault("image_slots", {})
            slots["clear"] = url
            if url and not loc.get("primary_image_slot"):
                loc["primary_image_slot"] = "clear"
            _sync_location_primary_image_url(loc)
        if "image_slots" in payload:
            incoming = payload.get("image_slots") or {}
            current = loc.setdefault("image_slots", {})
            for slot, url in incoming.items():
                if slot not in _VALID_LOCATION_IMAGE_SLOTS:
                    continue
                current[slot] = url or None
            _sync_location_primary_image_url(loc)
        if "primary_image_slot" in payload:
            slot = payload.get("primary_image_slot") or None
            if slot is None or slot in _VALID_LOCATION_IMAGE_SLOTS:
                loc["primary_image_slot"] = slot
                _sync_location_primary_image_url(loc)
        if _needs_map_bump:
            state["map_revision"] = int(state.get("map_revision", 0)) + 1
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
        region_val = payload.get("region")
        new_loc = {
            "id": new_id,
            "name": str(payload["name"]).strip(),
            "terrain_type": payload.get("terrain_type", "plain"),
            "anomaly_activity": int(payload.get("anomaly_activity", 0)),
            "dominant_anomaly_type": payload.get("dominant_anomaly_type") or None,
            "region": region_val if region_val else None,
            "exit_zone": bool(payload.get("exit_zone", False)),
            "connections": [],
            "artifacts": [],
            "agents": [],
            "items": [],
            "image_slots": {slot: None for slot in _ORDERED_IMAGE_SLOTS},
            "primary_image_slot": None,
            "image_url": None,
        }
        state["locations"][new_id] = new_loc
        # If a canvas position was provided, persist it immediately
        pos = payload.get("position")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            state.setdefault("debug_layout", {}).setdefault("positions", {})[new_id] = {
                "x": float(pos["x"]),
                "y": float(pos["y"]),
            }
        state["map_revision"] = int(state.get("map_revision", 0)) + 1
        events.append({"event_type": "debug_location_created", "payload": {"loc_id": new_id}})
        return state, events

    # ── debug_delete_location: meta-command, remove a location ────────────────
    if command_type == "debug_delete_location":
        loc_id_to_del = str(payload["loc_id"])
        # Remove all connections pointing TO this location from other locations
        for other_loc in state.get("locations", {}).values():
            other_loc["connections"] = [
                c for c in other_loc.get("connections", [])
                if c.get("to") != loc_id_to_del
            ]
        # Remove the location itself
        del state["locations"][loc_id_to_del]
        # Remove persisted canvas position if present
        state.get("debug_layout", {}).get("positions", {}).pop(loc_id_to_del, None)
        state["map_revision"] = int(state.get("map_revision", 0)) + 1
        events.append({"event_type": "debug_location_deleted", "payload": {"loc_id": loc_id_to_del}})
        return state, events
    if command_type == "debug_spawn_stalker":
        import random as _random
        from app.games.zone_stalkers.generators.zone_generator import _make_stalker_agent
        loc_id = payload["loc_id"]
        state.setdefault("agents", {})
        state.setdefault("locations", {})
        existing_agents = state["agents"]
        n = len(existing_agents)
        new_agent_id = f"agent_debug_{n}"
        while new_agent_id in existing_agents:
            n += 1
            new_agent_id = f"agent_debug_{n}"
        name = str(payload.get("name", "")).strip() or f"Сталкер #{n}"
        rng = _random.Random(new_agent_id)
        global_goal = str(payload.get("global_goal", "")).strip() or None
        kill_target_id = str(payload.get("kill_target_id", "")).strip() or None
        agent = _make_stalker_agent(
            agent_id=new_agent_id,
            name=name,
            location_id=loc_id,
            controller_kind="bot",
            participant_id=None,
            rng=rng,
            global_goal=global_goal,
            kill_target_id=kill_target_id,
        )
        # Ensure all UI-required fields are present
        agent.setdefault("id", new_agent_id)
        agent.setdefault("name", name)
        agent.setdefault("location_id", loc_id)
        agent.setdefault("hp", 100)
        agent.setdefault("max_hp", 100)
        agent.setdefault("is_alive", True)
        agent.setdefault("controller", {"kind": "bot", "participant_id": None})
        agent.setdefault("inventory", [])
        agent.setdefault("equipment", {"weapon": None, "armor": None, "detector": None})
        agent.setdefault("scheduled_action", None)
        agent.setdefault("action_queue", [])
        state["agents"][new_agent_id] = agent
        # Add to loc.agents without duplicates
        loc_agents = state["locations"][loc_id].setdefault("agents", [])
        if new_agent_id not in loc_agents:
            loc_agents.append(new_agent_id)
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

    # ── debug_spawn_trader: meta-command, spawn a trader NPC ─────────────────
    if command_type == "debug_spawn_trader":
        loc_id = payload["loc_id"]
        existing_traders = state.get("traders", {})
        n = len(existing_traders)
        new_trader_id = f"trader_debug_{n}"
        while new_trader_id in existing_traders:
            n += 1
            new_trader_id = f"trader_debug_{n}"
        trader_names = ["Sidorovich", "Barkeep", "Nimble", "Sakharov", "Wolf", "Petrenko"]
        name = str(payload.get("name", "")).strip() or trader_names[n % len(trader_names)]
        import random as _random
        from app.games.zone_stalkers.generators.zone_generator import _generate_trader_inventory
        rng = _random.Random(new_trader_id)
        trader_inv = _generate_trader_inventory(rng)
        trader = {
            "id": new_trader_id,
            "archetype": "trader_npc",
            "name": name,
            "location_id": loc_id,
            "inventory": trader_inv,
            "money": rng.randint(3000, 8000),
        }
        state.setdefault("traders", {})[new_trader_id] = trader
        state["locations"][loc_id]["agents"].append(new_trader_id)
        events.append({"event_type": "debug_trader_spawned", "payload": {"trader_id": new_trader_id, "loc_id": loc_id}})
        return state, events

    # ── debug_spawn_artifact: meta-command, place an artifact on the ground ──
    if command_type == "debug_spawn_artifact":
        import random as _random
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        loc_id = payload["loc_id"]
        artifact_type = str(payload.get("artifact_type", "")).strip()
        rng = _random.Random(str(state.get("world_turn", 1)) + loc_id + artifact_type)
        if not artifact_type:
            artifact_type = rng.choice(list(ARTIFACT_TYPES.keys()))
        art_info = ARTIFACT_TYPES[artifact_type]
        art_id = f"art_debug_{loc_id}_{rng.randint(1000, 9999)}"
        artifact = {
            "id": art_id,
            "type": artifact_type,
            "name": art_info["name"],
            "value": art_info["value"],
        }
        state["locations"][loc_id]["artifacts"].append(artifact)
        events.append({"event_type": "debug_artifact_spawned",
                       "payload": {"artifact_id": art_id, "artifact_type": artifact_type, "loc_id": loc_id}})
        return state, events

    # ── debug_spawn_item_on_location: place a loose item on the ground ────────
    if command_type == "debug_spawn_item_on_location":
        import random as _random
        from app.games.zone_stalkers.balance.items import ITEM_TYPES as _ITEM_TYPES
        loc_id = payload["loc_id"]
        item_type = str(payload["item_type"]).strip()
        item_info = _ITEM_TYPES[item_type]
        rng = _random.Random(str(state.get("world_turn", 1)) + loc_id + item_type)
        item_id = f"item_debug_{loc_id}_{rng.randint(1000, 9999)}"
        item = {
            "id": item_id,
            "type": item_type,
            "name": item_info["name"],
            "weight": item_info.get("weight", 0),
            "value": item_info.get("value", 0),
        }
        state["locations"][loc_id]["items"].append(item)
        events.append({"event_type": "debug_item_spawned_on_location",
                       "payload": {"item_id": item_id, "item_type": item_type, "loc_id": loc_id}})
        return state, events

    # ── debug_delete_all_npcs ─────────────────────────────────────────────────
    if command_type == "debug_delete_all_npcs":
        removed = []
        for aid in list(state.get("agents", {}).keys()):
            a = state["agents"][aid]
            if a.get("archetype") == "stalker_agent" and a.get("controller", {}).get("kind") != "human":
                for loc in state.get("locations", {}).values():
                    if aid in loc.get("agents", []):
                        loc["agents"].remove(aid)
                del state["agents"][aid]
                removed.append(aid)
        events.append({"event_type": "debug_npcs_deleted", "payload": {"removed": removed}})
        return state, events

    # ── debug_delete_all_mutants ──────────────────────────────────────────────
    if command_type == "debug_delete_all_mutants":
        removed = list(state.get("mutants", {}).keys())
        for mid in removed:
            m = state["mutants"][mid]
            loc_id = m.get("location_id")
            if loc_id and loc_id in state.get("locations", {}):
                loc = state["locations"][loc_id]
                if mid in loc.get("agents", []):
                    loc["agents"].remove(mid)
        state["mutants"] = {}
        events.append({"event_type": "debug_mutants_deleted", "payload": {"removed": removed}})
        return state, events

    # ── debug_delete_all_artifacts ────────────────────────────────────────────
    if command_type == "debug_delete_all_artifacts":
        total = 0
        for loc in state.get("locations", {}).values():
            total += len(loc.get("artifacts", []))
            loc["artifacts"] = []
        events.append({"event_type": "debug_artifacts_deleted", "payload": {"count": total}})
        return state, events

    # ── debug_delete_all_traders ──────────────────────────────────────────────
    if command_type == "debug_delete_all_traders":
        removed = list(state.get("traders", {}).keys())
        for tid in removed:
            t = state["traders"][tid]
            loc_id = t.get("location_id")
            if loc_id and loc_id in state.get("locations", {}):
                loc = state["locations"][loc_id]
                if tid in loc.get("agents", []):
                    loc["agents"].remove(tid)
        state["traders"] = {}
        events.append({"event_type": "debug_traders_deleted", "payload": {"removed": removed}})
        return state, events

    # ── debug_delete_all_items ────────────────────────────────────────────────
    if command_type == "debug_delete_all_items":
        ground_count = 0
        for loc in state.get("locations", {}).values():
            ground_count += len(loc.get("items", []))
            loc["items"] = []
        inv_count = 0
        for agent in state.get("agents", {}).values():
            inv_count += len(agent.get("inventory", []))
            agent["inventory"] = []
        events.append({
            "event_type": "debug_items_deleted",
            "payload": {"ground_count": ground_count, "inventory_count": inv_count},
        })
        return state, events

    # ── debug_set_time ────────────────────────────────────────────────────────
    if command_type == "debug_set_time":
        if "day" in payload:
            state["world_day"] = max(1, int(payload["day"]))
        if "hour" in payload:
            state["world_hour"] = max(0, min(23, int(payload["hour"])))
        if "minute" in payload:
            state["world_minute"] = max(0, min(59, int(payload["minute"])))
        # Keep world_turn in sync with the new time so that NPC memory
        # timestamps (written via _turn_to_time_label(world_turn)) match
        # the displayed clock.  Formula mirrors tick_rules._turn_to_time_label
        # with MINUTES_PER_TURN = 1 (1 turn = 1 in-game minute).
        new_turn = (
            (state.get("world_day", 1) - 1) * 24 * 60
            + state.get("world_hour", 0) * 60
            + state.get("world_minute", 0)
        )
        state["world_turn"] = new_turn
        events.append({
            "event_type": "debug_time_set",
            "payload": {
                "world_day": state["world_day"],
                "world_hour": state["world_hour"],
                "world_minute": state.get("world_minute", 0),
                "world_turn": new_turn,
            },
        })
        return state, events

    # ── debug_delete_agent ────────────────────────────────────────────────────
    if command_type == "debug_delete_agent":
        agent_id_to_del = str(payload["agent_id"])
        # Remove from agents dict
        if agent_id_to_del in state.get("agents", {}):
            for loc in state.get("locations", {}).values():
                if agent_id_to_del in loc.get("agents", []):
                    loc["agents"].remove(agent_id_to_del)
            del state["agents"][agent_id_to_del]
        # Remove from mutants dict
        elif agent_id_to_del in state.get("mutants", {}):
            m = state["mutants"].pop(agent_id_to_del)
            loc_id = m.get("location_id")
            if loc_id and loc_id in state.get("locations", {}):
                loc = state["locations"][loc_id]
                if agent_id_to_del in loc.get("agents", []):
                    loc["agents"].remove(agent_id_to_del)
        # Remove from traders dict
        elif agent_id_to_del in state.get("traders", {}):
            t = state["traders"].pop(agent_id_to_del)
            loc_id = t.get("location_id")
            if loc_id and loc_id in state.get("locations", {}):
                loc = state["locations"][loc_id]
                if agent_id_to_del in loc.get("agents", []):
                    loc["agents"].remove(agent_id_to_del)
        events.append({"event_type": "debug_agent_deleted", "payload": {"agent_id": agent_id_to_del}})
        return state, events

    # ── debug_set_agent_money: meta-command, set agent's money directly ─────
    if command_type == "debug_set_agent_money":
        target_id = str(payload["agent_id"])
        amount = int(payload["amount"])
        if target_id in state.get("agents", {}):
            state["agents"][target_id]["money"] = amount
        elif target_id in state.get("traders", {}):
            state["traders"][target_id]["money"] = amount
        events.append({
            "event_type": "debug_agent_money_set",
            "payload": {"agent_id": target_id, "amount": amount},
        })
        return state, events

    # ── debug_set_agent_threshold: meta-command, set material_threshold ──────
    if command_type == "debug_set_agent_threshold":
        target_id = str(payload["agent_id"])
        from app.games.zone_stalkers.rules.tick_rules import MATERIAL_THRESHOLD_MIN, MATERIAL_THRESHOLD_MAX
        amount = max(MATERIAL_THRESHOLD_MIN, min(MATERIAL_THRESHOLD_MAX, int(payload["amount"])))
        if target_id in state.get("agents", {}):
            state["agents"][target_id]["material_threshold"] = amount
        events.append({
            "event_type": "debug_agent_threshold_set",
            "payload": {"agent_id": target_id, "amount": amount},
        })
        return state, events

    # ── debug_add_item: meta-command, add an item to an agent's inventory ────
    if command_type == "debug_add_item":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES as _ITEM_TYPES
        target_id = str(payload["agent_id"])
        item_type = str(payload["item_type"])
        item_info = _ITEM_TYPES[item_type]
        world_turn = state.get("world_turn", 1)
        new_item: Dict[str, Any] = {
            "id": f"{item_type}_{target_id}_debug_{world_turn}",
            "type": item_type,
            "name": item_info.get("name", item_type),
            "weight": item_info.get("weight", 0),
            "value": item_info.get("value", 0),
        }
        state["agents"][target_id].setdefault("inventory", []).append(new_item)
        events.append({
            "event_type": "debug_item_added",
            "payload": {"agent_id": target_id, "item_type": item_type, "item_id": new_item["id"]},
        })
        return state, events

    # ── debug_remove_item: meta-command, remove an item from inventory/equipment ──
    if command_type == "debug_remove_item":
        target_id = str(payload["agent_id"])
        item_id = str(payload["item_id"])
        agent_obj = state["agents"][target_id]
        removed = False
        # Try inventory first
        inv = agent_obj.get("inventory", [])
        new_inv = [i for i in inv if i.get("id") != item_id]
        if len(new_inv) < len(inv):
            agent_obj["inventory"] = new_inv
            removed = True
        # Also check equipment slots — set to None rather than deleting so slot remains defined
        if not removed:
            for slot, eq_item in list(agent_obj.get("equipment", {}).items()):
                if eq_item and eq_item.get("id") == item_id:
                    agent_obj["equipment"][slot] = None
                    removed = True
                    break
        events.append({
            "event_type": "debug_item_removed",
            "payload": {"agent_id": target_id, "item_id": item_id, "removed": removed},
        })
        return state, events

    # ── debug_set_location_primary_image: meta-command ───────────────────────
    if command_type == "debug_set_location_primary_image":
        loc_id = str(payload["loc_id"])
        slot = str(payload["slot"])
        if loc_id not in state.get("locations", {}):
            events.append({"event_type": "debug_error", "payload": {"error": f"Location {loc_id!r} not found"}})
            return state, events
        if slot not in _VALID_LOCATION_IMAGE_SLOTS:
            events.append({"event_type": "debug_error", "payload": {"error": f"Invalid slot {slot!r}"}})
            return state, events
        loc = state["locations"][loc_id]
        loc.setdefault("image_slots", {})
        loc["primary_image_slot"] = slot
        _sync_location_primary_image_url(loc)
        state["map_revision"] = int(state.get("map_revision", 0)) + 1
        events.append({
            "event_type": "debug_location_primary_image_set",
            "payload": {"loc_id": loc_id, "slot": slot},
        })
        return state, events

    # ── debug_advance_turns ───────────────────────────────────────────────────
    if command_type == "debug_advance_turns":
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        max_n = int(payload.get("max_n", 100))
        stop_on_decision = bool(payload.get("stop_on_decision", True))
        turns_advanced = 0
        decision_event: Dict[str, Any] | None = None
        for _ in range(max(1, min(max_n, 500))):
            state, tick_evs = tick_zone_map(state)
            events.extend(tick_evs)
            turns_advanced += 1
            if stop_on_decision:
                for ev in tick_evs:
                    if ev.get("event_type") == "bot_decision":
                        decision_event = ev
                        break
                if decision_event:
                    break
        events.append({
            "event_type": "debug_turns_advanced",
            "payload": {
                "turns_advanced": turns_advanced,
                "stopped_on_decision": decision_event is not None,
                "decision_event": decision_event,
            },
        })
        return state, events

    # ── debug_trigger_emission ────────────────────────────────────────────────
    if command_type == "debug_trigger_emission":
        import random as _random
        from app.games.zone_stalkers.rules.tick_rules import (
            _add_memory,
            _EMISSION_WARNING_MIN_TURNS,
            _EMISSION_WARNING_MAX_TURNS,
        )
        world_turn = state.get("world_turn", 1)
        # Pick a random warning offset (same range as the live system: 10–15 turns).
        _rng = _random.Random(f"{state.get('seed', 0)}_debug_trigger_{world_turn}")
        _warn_offset = _rng.randint(_EMISSION_WARNING_MIN_TURNS, _EMISSION_WARNING_MAX_TURNS)
        _scheduled_turn = world_turn + _warn_offset
        state["emission_scheduled_turn"] = _scheduled_turn
        state["emission_active"] = False
        # Pre-write the warning state so the normal tick won't duplicate it.
        state["emission_warning_written_turn"] = world_turn
        state["emission_warning_offset"] = _warn_offset
        # Broadcast emission_imminent to every alive agent right now.
        for _dbg_agent in state.get("agents", {}).values():
            if not _dbg_agent.get("is_alive", True):
                continue
            _add_memory(
                _dbg_agent, world_turn, state, "observation",
                "⚠️ Скоро выброс!",
                f"Почувствовал приближение выброса. До начала примерно {_warn_offset} минут. Нужно найти укрытие!",
                {
                    "action_kind": "emission_imminent",
                    "turns_until": _warn_offset,
                    "emission_scheduled_turn": _scheduled_turn,
                },
            )
        events.append({
            "event_type": "debug_emission_triggered",
            "payload": {
                "emission_scheduled_turn": _scheduled_turn,
                "turns_until": _warn_offset,
                "emission_active": False,
            },
        })
        return state, events

    # ── debug_import_full_map ─────────────────────────────────────────────────
    if command_type == "debug_import_full_map":
        """
        Replaces the entire map state from a previously exported full-map JSON.
        Expected payload keys:
          locations: {id: {name, terrain_type, anomaly_activity, dominant_anomaly_type, region, connections, artifacts}}
          positions: {id: {x, y}}
          regions: {id: {name, colorIndex}}
          world_turn, world_day, world_hour, world_minute
          emission_active, emission_scheduled_turn, emission_ends_turn
        Agents, mutants, traders and player state are preserved as-is.
        """
        new_locs_data = payload.get("locations", {})
        new_positions = payload.get("positions", {})
        new_regions = payload.get("regions", {})

        # Build fresh locations dict, preserving agent/mutant lists from any
        # existing location with the same id (so live NPCs don't become orphans).
        old_locations = state.get("locations", {})
        new_locations: Dict[str, Any] = {}
        for loc_id, loc_data in new_locs_data.items():
            old = old_locations.get(loc_id, {})
            new_locations[loc_id] = {
                "id": loc_id,
                "name": str(loc_data.get("name", loc_id)).strip(),
                "terrain_type": loc_data.get("terrain_type", "plain"),
                "anomaly_activity": int(loc_data.get("anomaly_activity", 0)),
                "dominant_anomaly_type": loc_data.get("dominant_anomaly_type") or None,
                "region": loc_data.get("region") or None,
                "connections": [
                    {
                        "to": c["to"],
                        "travel_time": int(c.get("travel_time", 15)),
                        "type": c.get("type", "normal"),
                        "closed": bool(c.get("closed", False)),
                    }
                    for c in loc_data.get("connections", [])
                    if "to" in c and c["to"] in new_locs_data
                ],
                "artifacts": list(loc_data.get("artifacts", [])),
                "agents": list(old.get("agents", [])),
                "items": list(old.get("items", [])),
                "image_url": loc_data.get("image_url") or old.get("image_url") or None,
            }
        state["locations"] = new_locations

        # Canvas layout
        debug_layout = state.setdefault("debug_layout", {})
        debug_layout["positions"] = new_positions
        debug_layout["regions"] = new_regions

        # World time
        if "world_turn" in payload:
            state["world_turn"] = int(payload["world_turn"])
        if "world_day" in payload:
            state["world_day"] = max(1, int(payload["world_day"]))
        if "world_hour" in payload:
            state["world_hour"] = max(0, min(23, int(payload["world_hour"])))
        if "world_minute" in payload:
            state["world_minute"] = max(0, min(59, int(payload["world_minute"])))

        # Emission state
        if "emission_active" in payload:
            state["emission_active"] = bool(payload["emission_active"])
        if "emission_scheduled_turn" in payload and payload["emission_scheduled_turn"] is not None:
            state["emission_scheduled_turn"] = int(payload["emission_scheduled_turn"])
        if "emission_ends_turn" in payload and payload["emission_ends_turn"] is not None:
            state["emission_ends_turn"] = int(payload["emission_ends_turn"])

        events.append({
            "event_type": "debug_full_map_imported",
            "payload": {"location_count": len(new_locations)},
        })
        return state, events

    # ── debug_preview_bot_decision ─────────────────────────────────────────────
    if command_type == "debug_preview_bot_decision":
        import copy
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action, _describe_bot_decision_tree
        agent_id_to_preview = str(payload["agent_id"])
        state_copy = copy.deepcopy(state)
        agent_copy = state_copy["agents"][agent_id_to_preview]
        world_turn = state_copy.get("world_turn", 1)
        preview_evs = _run_bot_action(agent_id_to_preview, agent_copy, state_copy, world_turn)
        decision_tree = _describe_bot_decision_tree(agent_copy, preview_evs, state_copy)
        decision_desc = {"goal": decision_tree["goal"], "action": decision_tree["chosen"]["action"], "reason": decision_tree["chosen"]["reason"]}
        events.append({
            "event_type": "debug_bot_decision_preview",
            "payload": {
                "agent_id": agent_id_to_preview,
                "decision": decision_desc,
                "decision_tree": decision_tree,
            },
        })
        return state, events

    # ── debug_explain_agent_v2 ─────────────────────────────────────────────────
    if command_type == "debug_explain_agent_v2":
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        agent_id_to_explain = str(payload["agent_id"])
        explanation = explain_agent_decision(agent_id_to_explain, state)
        events.append({
            "event_type": "agent_v2_explanation",
            "payload": {
                "agent_id": agent_id_to_explain,
                "explanation": explanation,
            },
        })
        return state, events

    if command_type == "end_turn":
        # Mark this player's agent as having acted
        if agent_id and agent_id in state.get("agents", {}):
            state["agents"][agent_id]["action_used"] = True
        events.append({"event_type": "turn_submitted", "payload": {"participant_id": player_id}})

        # Always advance the world by one tick (bot-only mode — no player waiting).
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
        move_anomaly_activity = new_loc_data.get("anomaly_activity", 0)
        if move_anomaly_activity > 0:
            import random as _move_rng
            _rng = _move_rng.Random(str(state.get("seed", 0)) + str(state.get("world_turn", 0)))
            if _rng.random() < move_anomaly_activity / 20.0:
                dmg = 5 + move_anomaly_activity
                agent["hp"] = max(0, agent["hp"] - dmg)
                anomaly_type = new_loc_data.get("dominant_anomaly_type") or "unknown"
                events.append({
                    "event_type": "anomaly_damage",
                    "payload": {
                        "agent_id": agent_id,
                        "anomaly_type": anomaly_type,
                        "damage": dmg,
                        "hp_remaining": agent["hp"],
                    },
                })
                if agent["hp"] <= 0:
                    _move_loc_name = new_loc_data.get("name", target_loc_id)
                    from app.games.zone_stalkers.rules.agent_lifecycle import kill_agent as _kill_agent
                    _kill_agent(
                        agent_id=agent_id,
                        agent=agent,
                        state=state,
                        world_turn=state.get("world_turn", 1),
                        cause="anomaly",
                        location_id=target_loc_id,
                        memory_title="💀 Смерть",
                        memory_summary=f"Погиб от аномалии при перемещении в «{_move_loc_name}».",
                        memory_effects={"action_kind": "death", "cause": "anomaly",
                                        "location_id": target_loc_id, "anomaly_type": anomaly_type},
                        events=events,
                    )

    elif command_type == "travel":
        target_loc_id = payload["target_location_id"]
        route = _bfs_route(state["locations"], agent["location_id"], target_loc_id)
        if not route:
            events.append({"event_type": "travel_failed", "payload": {"agent_id": agent_id, "reason": "no_route"}})
        else:
            first_hop = route[0]
            conns = state["locations"].get(agent["location_id"], {}).get("connections", [])
            hop_time = next(
                (c.get("travel_time", 12) for c in conns if c["to"] == first_hop),
                12,
            )
            agent["scheduled_action"] = {
                "type": "travel",
                "turns_remaining": hop_time,
                "turns_total": hop_time,
                "target_id": first_hop,
                "final_target_id": target_loc_id,
                "remaining_route": route[1:],
                "started_turn": state.get("world_turn", 1),
                "ends_turn": state.get("world_turn", 1) + hop_time,
                "revision": 1,
                "interruptible": True,
            }
            events.append({
                "event_type": "travel_started",
                "payload": {
                    "agent_id": agent_id,
                    "player_id": player_id,
                    "destination": target_loc_id,
                    "turns_required": hop_time,
                    "route": route,
                },
            })

    elif command_type == "explore_location":
        loc_id = agent["location_id"]
        loc = state["locations"].get(loc_id, {})
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": loc_id,
            "started_turn": state.get("world_turn", 1),
            "ends_turn": state.get("world_turn", 1) + 1,
            "revision": 1,
            "interruptible": True,
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
        turns = hours * 60  # 1 turn = 1 minute
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": turns,
            "turns_total": turns,
            "hours": hours,
            "target_id": agent["location_id"],
            "started_turn": state.get("world_turn", 1),
            "ends_turn": state.get("world_turn", 1) + turns,
            "revision": 1,
            "interruptible": True,
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
            "ends_turn": state.get("world_turn", 1) + 1,
            "revision": 1,
            "interruptible": True,
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

    elif command_type == "buy_from_trader":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        item_type = payload["item_type"]
        item_info = ITEM_TYPES[item_type]
        buy_price = int(item_info.get("value", 0) * 1.5)
        loc_id = agent["location_id"]
        trader = next(
            (t for t in state.get("traders", {}).values()
             if t.get("location_id") == loc_id and t.get("is_alive", True)),
            None,
        )
        if trader:
            import uuid as _uuid
            agent["money"] = agent.get("money", 0) - buy_price
            trader["money"] = trader.get("money", 0) + buy_price
            new_item: Dict[str, Any] = {
                "id": str(_uuid.uuid4()),
                "type": item_type,
                "name": item_info["name"],
                "weight": item_info.get("weight", 0),
                "value": item_info.get("value", 0),
            }
            agent.setdefault("inventory", []).append(new_item)
            agent["action_used"] = True
            events.append({
                "event_type": "item_bought",
                "payload": {
                    "agent_id": agent_id,
                    "player_id": player_id,
                    "trader_id": trader["id"],
                    "item_id": new_item["id"],
                    "item_type": item_type,
                    "price": buy_price,
                },
            })
            # Trader memory: record the sale from the trader's perspective
            from app.games.zone_stalkers.rules.tick_rules import _add_trader_memory
            _wt = state.get("world_turn", 1)
            buyer_name = agent.get("name", agent_id)
            loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
            _add_trader_memory(
                trader, _wt, state, "trade_sale",
                f"Продал «{item_info['name']}» сталкеру {buyer_name}",
                f"Продал «{item_info['name']}» сталкеру {buyer_name} в «{loc_name}» за {buy_price} монет.",
                {
                    "item_type": item_type,
                    "price": buy_price,
                    "buyer_id": agent_id,
                },
            )

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


def _validate_buy_from_trader(payload: Dict[str, Any], state: Dict[str, Any], agent: Dict[str, Any]) -> RuleCheckResult:
    item_type = payload.get("item_type")
    if not item_type:
        return RuleCheckResult(valid=False, error="item_type is required")
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    if item_type not in ITEM_TYPES:
        return RuleCheckResult(valid=False, error=f"Unknown item type: '{item_type}'")
    loc_id = agent.get("location_id")
    traders = state.get("traders", {})
    trader = next(
        (t for t in traders.values()
         if t.get("location_id") == loc_id and t.get("is_alive", True)),
        None,
    )
    if not trader:
        return RuleCheckResult(valid=False, error="No trader at your current location")
    buy_price = int(ITEM_TYPES[item_type].get("value", 0) * 1.5)
    if agent.get("money", 0) < buy_price:
        return RuleCheckResult(valid=False, error=f"Not enough money (need {buy_price} RU)")
    return RuleCheckResult(valid=True)


def _apply_item_effects(agent: Dict[str, Any], effects: Dict[str, Any]) -> None:
    """Apply item effect dict to agent stats (hp, radiation, hunger, thirst, sleepiness)."""
    if "hp" in effects:
        agent["hp"] = max(0, min(agent.get("max_hp", 100), agent.get("hp", 100) + effects["hp"]))
    if "radiation" in effects:
        agent["radiation"] = max(0, agent.get("radiation", 0) + effects["radiation"])
    if "hunger" in effects:
        agent["hunger"] = max(0, agent.get("hunger", 0) + effects["hunger"])
    if "thirst" in effects:
        agent["thirst"] = max(0, agent.get("thirst", 0) + effects["thirst"])
    if "sleepiness" in effects:
        agent["sleepiness"] = max(0, agent.get("sleepiness", 0) + effects["sleepiness"])


def _bfs_route(locations: Dict[str, Any], start: str, goal: str) -> List[str]:
    """Return ordered list of location IDs from start (exclusive) to goal (inclusive), or [] if unreachable.

    Closed connections (conn["closed"] == True) are treated as impassable.
    """
    if start == goal:
        return []
    visited = {start}
    queue: collections.deque = collections.deque([(start, [])])
    while queue:
        current, path = queue.popleft()
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn["to"]
            # Skip closed connections (impassable) and already-visited nodes.
            if conn.get("closed") or nxt in visited:
                continue
            new_path = path + [nxt]
            if nxt == goal:
                return new_path
            visited.add(nxt)
            queue.append((nxt, new_path))
    return []


def _route_travel_turns(
    route: List[str],
    locations: Dict[str, Any],
    start_loc_id: str = None,
) -> int:
    """Sum travel_time (minutes) across all hops. 1 turn = 1 minute.

    *route* is an ordered list of location IDs [waypoint1, ..., destination],
    NOT including the starting location.  *start_loc_id* is the agent's current
    location; when provided the connection weights are used directly.
    Falls back to 12 min/hop if *start_loc_id* is unknown or a connection weight
    is missing.
    """
    if not route:
        return 1
    if start_loc_id is None:
        return max(1, len(route) * 12)
    total_minutes = 0
    current = start_loc_id
    for next_loc in route:
        conns = locations.get(current, {}).get("connections", [])
        travel_time = next(
            (c.get("travel_time", 12) for c in conns if c["to"] == next_loc),
            12,
        )
        total_minutes += travel_time
        current = next_loc
    return max(1, total_minutes)


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
    if controller.get("kind") != "bot":
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


def _validate_debug_delete_location(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
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
    global_goal = payload.get("global_goal")
    if global_goal is not None and global_goal not in _VALID_GLOBAL_GOALS:
        return RuleCheckResult(valid=False, error=f"Invalid global_goal '{global_goal}'; must be one of {sorted(_VALID_GLOBAL_GOALS)}")
    if global_goal == "kill_stalker":
        kill_target_id = str(payload.get("kill_target_id", "")).strip()
        if not kill_target_id:
            return RuleCheckResult(valid=False, error="kill_target_id is required when global_goal='kill_stalker'")
        if kill_target_id not in state.get("agents", {}):
            return RuleCheckResult(valid=False, error=f"kill_target_id '{kill_target_id}' not found in agents")
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


def _validate_debug_spawn_trader(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    return RuleCheckResult(valid=True)


def _validate_debug_spawn_artifact(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    artifact_type = payload.get("artifact_type", "")
    if artifact_type and artifact_type not in ARTIFACT_TYPES:
        return RuleCheckResult(
            valid=False,
            error=f"Invalid artifact_type '{artifact_type}'; must be one of {sorted(ARTIFACT_TYPES.keys())}"
        )
    return RuleCheckResult(valid=True)


def _validate_debug_spawn_item_on_location(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _ITEM_TYPES
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    item_type = payload.get("item_type")
    if not item_type:
        return RuleCheckResult(valid=False, error="item_type is required")
    if item_type not in _ITEM_TYPES:
        return RuleCheckResult(
            valid=False,
            error=f"Unknown item_type '{item_type}'; must be one of {sorted(_ITEM_TYPES.keys())}"
        )
    return RuleCheckResult(valid=True)


def _validate_debug_delete_agent(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    exists = (
        agent_id in state.get("agents", {}) or
        agent_id in state.get("mutants", {}) or
        agent_id in state.get("traders", {})
    )
    if not exists:
        return RuleCheckResult(valid=False, error=f"Agent not found: {agent_id}")
    return RuleCheckResult(valid=True)


def _validate_debug_preview_bot_decision(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    agent = state.get("agents", {}).get(agent_id)
    if not agent:
        return RuleCheckResult(valid=False, error=f"Agent not found: {agent_id}")
    if agent.get("controller", {}).get("kind") != "bot":
        return RuleCheckResult(valid=False, error="Agent is not bot-controlled")
    return RuleCheckResult(valid=True)

def _validate_debug_set_agent_money(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    exists = (
        agent_id in state.get("agents", {}) or
        agent_id in state.get("traders", {})
    )
    if not exists:
        return RuleCheckResult(valid=False, error=f"Agent/trader not found: {agent_id}")
    amount = payload.get("amount")
    if amount is None:
        return RuleCheckResult(valid=False, error="amount is required")
    try:
        int(amount)
    except (TypeError, ValueError):
        return RuleCheckResult(valid=False, error="amount must be an integer")
    return RuleCheckResult(valid=True)


def _validate_debug_add_item(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _ITEM_TYPES
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    if agent_id not in state.get("agents", {}):
        return RuleCheckResult(valid=False, error=f"Agent not found: {agent_id}")
    item_type = payload.get("item_type")
    if not item_type:
        return RuleCheckResult(valid=False, error="item_type is required")
    if item_type not in _ITEM_TYPES:
        return RuleCheckResult(valid=False, error=f"Unknown item_type: {item_type}")
    return RuleCheckResult(valid=True)


def _validate_debug_remove_item(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    if agent_id not in state.get("agents", {}):
        return RuleCheckResult(valid=False, error=f"Agent not found: {agent_id}")
    item_id = payload.get("item_id")
    if not item_id:
        return RuleCheckResult(valid=False, error="item_id is required")
    return RuleCheckResult(valid=True)


def _validate_debug_set_agent_threshold(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return RuleCheckResult(valid=False, error="agent_id is required")
    if agent_id not in state.get("agents", {}):
        return RuleCheckResult(valid=False, error=f"Agent not found: {agent_id}")
    amount = payload.get("amount")
    if amount is None:
        return RuleCheckResult(valid=False, error="amount is required")
    try:
        val = int(amount)
    except (TypeError, ValueError):
        return RuleCheckResult(valid=False, error="amount must be an integer")
    from app.games.zone_stalkers.rules.tick_rules import MATERIAL_THRESHOLD_MIN, MATERIAL_THRESHOLD_MAX
    if not (MATERIAL_THRESHOLD_MIN <= val <= MATERIAL_THRESHOLD_MAX):
        return RuleCheckResult(
            valid=False,
            error=f"amount must be between {MATERIAL_THRESHOLD_MIN} and {MATERIAL_THRESHOLD_MAX}"
        )
    return RuleCheckResult(valid=True)


def _validate_debug_set_location_primary_image(
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleCheckResult:
    loc_id = payload.get("loc_id")
    if not loc_id:
        return RuleCheckResult(valid=False, error="loc_id is required")
    if loc_id not in state.get("locations", {}):
        return RuleCheckResult(valid=False, error=f"Location not found: {loc_id}")
    slot = payload.get("slot")
    if not slot:
        return RuleCheckResult(valid=False, error="slot is required")
    if slot not in _VALID_LOCATION_IMAGE_SLOTS:
        return RuleCheckResult(
            valid=False,
            error=f"Invalid slot '{slot}'. Must be one of: {sorted(_VALID_LOCATION_IMAGE_SLOTS)}",
        )
    return RuleCheckResult(valid=True)
