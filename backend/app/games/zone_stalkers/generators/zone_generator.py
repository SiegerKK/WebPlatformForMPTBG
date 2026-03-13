"""
Procedural zone generator for Zone Stalkers.
Given an integer seed produces a deterministic world graph.
"""
import random
import uuid as _uuid
from typing import List, Dict, Any

from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
from app.games.zone_stalkers.balance.mutants import MUTANT_TYPES
from app.games.zone_stalkers.balance.items import ITEM_TYPES

# Valid terrain types for a location
TERRAIN_TYPES = ["plain", "hills", "slag_heaps", "industrial", "urban"]

# Location blueprint templates
_LOCATION_BLUEPRINTS = [
    {"name": "Cordon",          "type": "safe_hub",       "danger_level": 1, "terrain_type": "plain"},
    {"name": "Garbage",         "type": "wild_area",      "danger_level": 2, "terrain_type": "industrial"},
    {"name": "Agroprom",        "type": "ruins",          "danger_level": 3, "terrain_type": "industrial"},
    {"name": "Dark Valley",     "type": "military_zone",  "danger_level": 4, "terrain_type": "hills"},
    {"name": "Bar",             "type": "safe_hub",       "danger_level": 1, "terrain_type": "urban"},
    {"name": "Wild Territory",  "type": "wild_area",      "danger_level": 3, "terrain_type": "plain"},
    {"name": "Yantar",          "type": "anomaly_cluster","danger_level": 4, "terrain_type": "industrial"},
    {"name": "Red Forest",      "type": "anomaly_cluster","danger_level": 5, "terrain_type": "plain"},
    {"name": "Pripyat",         "type": "ruins",          "danger_level": 5, "terrain_type": "urban"},
    {"name": "Swamp",           "type": "wild_area",      "danger_level": 2, "terrain_type": "plain"},
    {"name": "Outskirts",       "type": "ruins",          "danger_level": 3, "terrain_type": "urban"},
    {"name": "Jupiter",         "type": "military_zone",  "danger_level": 4, "terrain_type": "industrial"},
    {"name": "Slag Heaps",      "type": "wild_area",      "danger_level": 3, "terrain_type": "slag_heaps"},
    {"name": "Eastern Hills",   "type": "wild_area",      "danger_level": 2, "terrain_type": "hills"},
]

_MIN_LOCATIONS = 8
_MAX_LOCATIONS = 12


def _make_id(prefix: str, rng: random.Random) -> str:
    # Deterministic short ID using hex
    return f"{prefix}_{rng.randint(0, 0xFFFF):04x}"


def generate_zone(
    seed: int,
    num_players: int = 1,
    num_ai_stalkers: int = 5,
    num_mutants: int = 8,
    num_traders: int = 2,
) -> Dict[str, Any]:
    """
    Generate a full zone_map state blob deterministically from *seed*.

    Returns the state blob dict suitable for use as a context's state_blob.
    """
    rng = random.Random(seed)

    # 1. Choose locations
    num_locs = rng.randint(_MIN_LOCATIONS, min(_MAX_LOCATIONS, len(_LOCATION_BLUEPRINTS)))
    blueprints = rng.sample(_LOCATION_BLUEPRINTS, num_locs)

    locations: Dict[str, Any] = {}
    loc_ids: List[str] = []
    for i, bp in enumerate(blueprints):
        loc_id = f"loc_{i}"
        loc_ids.append(loc_id)
        # anomaly_activity: anomaly_cluster gets 6-10, wild_area 2-5, others 0-3
        if bp["type"] == "anomaly_cluster":
            anom_activity = rng.randint(6, 10)
        elif bp["type"] == "wild_area":
            anom_activity = rng.randint(2, 5)
        else:
            anom_activity = rng.randint(0, 3)
        locations[loc_id] = {
            "id": loc_id,
            "name": bp["name"],
            "type": bp["type"],
            "danger_level": bp["danger_level"],
            "terrain_type": bp.get("terrain_type", "plain"),
            "anomaly_activity": anom_activity,
            "dominant_anomaly_type": None,
            "connections": [],
            "anomalies": [],
            "artifacts": [],
            "agents": [],
            "items": [],
        }

    # 2. Build connections (ensure connectivity: chain + extra edges)
    # First create a spanning chain so every node is reachable
    for i in range(len(loc_ids) - 1):
        a, b = loc_ids[i], loc_ids[i + 1]
        conn_type = "dangerous" if locations[b]["danger_level"] >= 4 else "normal"
        locations[a]["connections"].append({"to": b, "type": conn_type})
        locations[b]["connections"].append({"to": a, "type": conn_type})

    # Add a few extra edges for variety
    extra_edges = rng.randint(2, max(2, num_locs // 3))
    for _ in range(extra_edges):
        a, b = rng.sample(loc_ids, 2)
        # Avoid duplicates
        existing_targets = {c["to"] for c in locations[a]["connections"]}
        if b not in existing_targets:
            conn_type = "dangerous" if locations[b]["danger_level"] >= 4 else "normal"
            locations[a]["connections"].append({"to": b, "type": conn_type})
            locations[b]["connections"].append({"to": a, "type": conn_type})

    # 3. Place anomalies (more in dangerous/cluster locations)
    anomaly_type_keys = list(ANOMALY_TYPES.keys())
    for loc_id, loc in locations.items():
        num_anomalies = 0
        if loc["type"] == "anomaly_cluster":
            num_anomalies = rng.randint(2, 4)
        elif loc["type"] == "wild_area":
            num_anomalies = rng.randint(0, 2)
        elif loc["danger_level"] >= 3:
            num_anomalies = rng.randint(0, 1)
        for _ in range(num_anomalies):
            anom_type = rng.choice(anomaly_type_keys)
            loc["anomalies"].append({
                "id": _make_id("anom", rng),
                "type": anom_type,
                "name": ANOMALY_TYPES[anom_type]["name"],
                "active": True,
            })
        # Set dominant_anomaly_type from the most common anomaly type present
        if loc["anomalies"]:
            counts: Dict[str, int] = {}
            for a in loc["anomalies"]:
                counts[a["type"]] = counts.get(a["type"], 0) + 1
            loc["dominant_anomaly_type"] = max(counts, key=lambda k: counts[k])

    # 4. Place artifacts near anomalies
    artifact_type_keys = list(ARTIFACT_TYPES.keys())
    for loc in locations.values():
        if loc["anomalies"]:
            num_artifacts = rng.randint(0, len(loc["anomalies"]))
            for _ in range(num_artifacts):
                art_type = rng.choice(artifact_type_keys)
                art_info = ARTIFACT_TYPES[art_type]
                loc["artifacts"].append({
                    "id": _make_id("art", rng),
                    "type": art_type,
                    "name": art_info["name"],
                    "value": art_info["value"],
                })

    # 5. Place loose items (containers/stashes)
    item_type_keys = [k for k, v in ITEM_TYPES.items() if v["type"] in ("medical", "consumable", "ammo")]
    for loc in locations.values():
        if rng.random() < 0.4:
            item_type = rng.choice(item_type_keys)
            item_info = ITEM_TYPES[item_type]
            loc["items"].append({
                "id": _make_id("item", rng),
                "type": item_type,
                "name": item_info["name"],
                "weight": item_info["weight"],
                "value": item_info["value"],
            })

    # 6. Create agent states
    agents: Dict[str, Any] = {}

    # Safe hub locations for starting positions
    safe_locs = [lid for lid, l in locations.items() if l["type"] == "safe_hub"]
    if not safe_locs:
        safe_locs = loc_ids[:2]

    # Spawn human player agents (placeholders; actual participant_id filled later)
    player_agents: Dict[str, str] = {}  # participant_id -> agent_id
    for i in range(num_players):
        agent_id = f"agent_p{i}"
        spawn_loc = safe_locs[i % len(safe_locs)]
        agent = _make_stalker_agent(
            agent_id=agent_id,
            name=f"Stalker-{i + 1}",
            location_id=spawn_loc,
            controller_kind="human",
            participant_id=None,  # will be filled on match start
            rng=rng,
        )
        agents[agent_id] = agent
        locations[spawn_loc]["agents"].append(agent_id)

    # Spawn AI stalkers
    for i in range(num_ai_stalkers):
        agent_id = f"agent_ai_{i}"
        spawn_loc = rng.choice(loc_ids)
        agent = _make_stalker_agent(
            agent_id=agent_id,
            name=f"NPC-{i + 1}",
            location_id=spawn_loc,
            controller_kind="bot",
            participant_id=None,
            rng=rng,
        )
        agents[agent_id] = agent
        locations[spawn_loc]["agents"].append(agent_id)

    # Spawn mutants
    mutant_type_keys = list(MUTANT_TYPES.keys())
    mutants: Dict[str, Any] = {}
    for i in range(num_mutants):
        # Prefer dangerous locations
        dangerous_locs = [lid for lid, l in locations.items() if l["danger_level"] >= 2]
        spawn_loc = rng.choice(dangerous_locs if dangerous_locs else loc_ids)
        mutant_id = f"mutant_{i}"
        mutant_type = rng.choice(mutant_type_keys)
        mutant_info = MUTANT_TYPES[mutant_type]
        mutants[mutant_id] = {
            "id": mutant_id,
            "archetype": "mutant_agent",
            "type": mutant_type,
            "name": mutant_info["name"],
            "location_id": spawn_loc,
            "hp": mutant_info["hp"],
            "max_hp": mutant_info["max_hp"],
            "damage": mutant_info["damage"],
            "defense": mutant_info["defense"],
            "aggression": mutant_info["aggression"],
            "is_alive": True,
            "loot_table": mutant_info["loot_table"],
            "money_drop": mutant_info["money_drop"],
        }
        locations[spawn_loc]["agents"].append(mutant_id)

    # Spawn traders
    traders: Dict[str, Any] = {}
    for i in range(num_traders):
        trader_id = f"trader_{i}"
        spawn_loc = safe_locs[i % len(safe_locs)]
        trader_inventory = _generate_trader_inventory(rng)
        traders[trader_id] = {
            "id": trader_id,
            "archetype": "trader_npc",
            "name": ["Sidorovich", "Barkeep", "Nimble", "Sakharov"][i % 4],
            "location_id": spawn_loc,
            "inventory": trader_inventory,
            "money": rng.randint(3000, 8000),
        }
        locations[spawn_loc]["agents"].append(trader_id)

    return {
        "context_type": "zone_map",
        "world_turn": 1,
        "world_hour": 6,    # Game starts at 06:00
        "world_day": 1,
        "max_turns": 50,
        "seed": seed,
        "locations": locations,
        "agents": agents,
        "mutants": mutants,
        "traders": traders,
        "player_agents": player_agents,
        "active_events": [],  # list of zone_event context IDs currently running
        "game_over": False,
        "winner": None,
        "scores": {},
        "global_events": [],
    }


def _make_stalker_agent(
    agent_id: str,
    name: str,
    location_id: str,
    controller_kind: str,
    participant_id,
    rng: random.Random,
) -> Dict[str, Any]:
    faction = rng.choice(["loner", "loner", "loner", "military", "duty", "freedom"])
    weapon = rng.choice([None, "pistol", "pistol", "ak74"])
    armor = rng.choice([None, "leather_jacket", "leather_jacket", "stalker_suit"])

    inventory = []
    # Give everyone at least a bandage
    inventory.append(_make_item_instance("bandage", rng))
    if rng.random() < 0.5:
        inventory.append(_make_item_instance("medkit", rng))
    if rng.random() < 0.6:
        inventory.append(_make_item_instance("bread", rng))

    equipment: Dict[str, Any] = {"weapon": None, "armor": None, "detector": None}
    if weapon:
        equip_item = _make_item_instance(weapon, rng)
        equipment["weapon"] = equip_item
        # Ammo
        ammo_map = {"pistol": "ammo_9mm", "ak74": "ammo_545", "shotgun": "ammo_12gauge"}
        ammo_type = ammo_map.get(weapon)
        if ammo_type:
            inventory.append(_make_item_instance(ammo_type, rng))
    if armor:
        equipment["armor"] = _make_item_instance(armor, rng)

    return {
        "id": agent_id,
        "archetype": "stalker_agent",
        "name": name,
        "location_id": location_id,
        # ─── Health & Status ───
        "hp": 100,
        "max_hp": 100,
        "radiation": 0,
        "stamina": 100,
        # ─── Survival needs (0–100; higher = worse) ───
        "hunger": 20,
        "thirst": 20,
        "sleepiness": 10,
        # ─── Economy ───
        "money": rng.randint(100, 800),
        "carry_capacity": 50.0,
        # ─── Inventory & Equipment ───
        "inventory": inventory,
        "equipment": equipment,
        # ─── Identity & Faction ───
        "faction": faction,
        "controller": {
            "kind": controller_kind,
            "participant_id": participant_id,
        },
        "is_alive": True,
        "action_used": False,
        "reputation": 0,
        # ─── Development ───
        "experience": 0,
        "skill_combat": 1,
        "skill_stalker": 1,
        "skill_trade": 1,
        "skill_medicine": 1,
        "skill_social": 1,
        # ─── Goals & Psychology ───
        "global_goal": "survive",   # "survive" | "get_rich" | "explore" | "serve_faction"
        "current_goal": None,
        "risk_tolerance": 0.5,      # 0.0–1.0
        # ─── Action state ───
        "scheduled_action": None,   # {"type", "turns_remaining", "turns_total", "target_id", "started_turn"}
        "action_queue": [],         # list of scheduled_action dicts to execute after current one
        # ─── Memory ───
        "memory": [],               # list of {world_turn, world_day, type, title, summary, effects}
    }


def _make_item_instance(item_type: str, rng: random.Random) -> Dict[str, Any]:
    info = ITEM_TYPES[item_type]
    return {
        "id": _make_id("item", rng),
        "type": item_type,
        "name": info["name"],
        "weight": info.get("weight", 0),
        "value": info.get("value", 0),
    }


def _generate_trader_inventory(rng: random.Random) -> List[Dict[str, Any]]:
    """Generate a trader's starting inventory."""
    stock: List[Dict[str, Any]] = []
    sell_types = ["medkit", "bandage", "antirad", "ak74", "pistol",
                  "ammo_545", "ammo_9mm", "stalker_suit", "leather_jacket",
                  "energy_drink", "vodka", "echo_detector"]
    for item_type in rng.sample(sell_types, rng.randint(5, len(sell_types))):
        info = ITEM_TYPES[item_type]
        stock.append({
            "id": _make_id("trader_item", rng),
            "type": item_type,
            "name": info["name"],
            "weight": info.get("weight", 0),
            "value": info.get("value", 0),
            "stock": rng.randint(1, 5),
        })
    return stock
