"""
Zone generator for Zone Stalkers.

Builds a deterministic world state from a fixed canonical 32-location graph
defined in ``fixed_zone_map.FIXED_ZONE_LOCATIONS``.

The topology (location names, regions, terrain, anomaly_activity, and
connections with travel_time) is fixed.  Only the runtime contents
(artifacts, items, agents, mutants, traders) are generated
randomly from *seed*.
"""
import copy
import random
from typing import List, Dict, Any

from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
from app.games.zone_stalkers.balance.mutants import MUTANT_TYPES
from app.games.zone_stalkers.balance.items import ITEM_TYPES, SECRET_DOCUMENT_ITEM_TYPES
from app.games.zone_stalkers.generators.fixed_zone_map import FIXED_ZONE_LOCATIONS

# Valid terrain types (kept for reference / external callers)
TERRAIN_TYPES = ["plain", "hills", "slag_heaps", "swamp", "field_camp", "industrial", "bridge"]


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

    The location graph is the fixed 32-location canonical map.
    Anomalies, artifacts, items, agents, mutants and traders are placed
    randomly using *seed*.

    Returns the state blob dict suitable for use as a context's state_blob.
    """
    rng = random.Random(seed)

    # 1. Deep-copy fixed locations and add empty runtime-state lists
    locations: Dict[str, Any] = {}
    for loc_id, blueprint in FIXED_ZONE_LOCATIONS.items():
        loc = copy.deepcopy(blueprint)
        loc["dominant_anomaly_type"] = None
        loc["artifacts"] = []
        loc["agents"] = []
        loc["items"] = []
        locations[loc_id] = loc

    loc_ids: List[str] = list(locations.keys())

    # 2. Set dominant_anomaly_type based on anomaly_activity
    anomaly_type_keys = list(ANOMALY_TYPES.keys())
    for loc_id, loc in locations.items():
        if loc.get("anomaly_activity", 0) > 0:
            loc["dominant_anomaly_type"] = rng.choice(anomaly_type_keys)
        # else dominant_anomaly_type stays None (set above)

    # 3. Place artifacts in locations with anomaly activity
    artifact_type_keys = list(ARTIFACT_TYPES.keys())
    for loc in locations.values():
        anom_act = loc.get("anomaly_activity", 0)
        if anom_act > 0:
            num_artifacts = rng.randint(0, max(1, anom_act // 3))
            for _ in range(num_artifacts):
                art_type = rng.choice(artifact_type_keys)
                art_info = ARTIFACT_TYPES[art_type]
                loc["artifacts"].append({
                    "id": _make_id("art", rng),
                    "type": art_type,
                    "name": art_info["name"],
                    "value": art_info["value"],
                })

    # 4. Place loose items (containers/stashes)
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

    # 4b. Place secret documents in restricted/dangerous Zone locations.
    # Exactly 2 documents are placed across locations whose terrain suggests
    # hidden knowledge (labs, bunkers, dungeons, military sites).
    _SECRET_DOC_TERRAIN = frozenset({"dungeon", "x_lab", "scientific_bunker", "military_buildings"})
    secret_doc_locs = [
        lid for lid, l in locations.items()
        if l.get("terrain_type", "") in _SECRET_DOC_TERRAIN
    ]
    if not secret_doc_locs:
        secret_doc_locs = list(locations.keys())  # fallback: use any location
    secret_doc_types = sorted(SECRET_DOCUMENT_ITEM_TYPES)
    for i in range(min(2, len(secret_doc_locs))):
        doc_type = rng.choice(secret_doc_types)
        doc_info = ITEM_TYPES[doc_type]
        spawn_lid = secret_doc_locs[i % len(secret_doc_locs)]
        locations[spawn_lid]["items"].append({
            "id": _make_id("item", rng),
            "type": doc_type,
            "name": doc_info["name"],
            "weight": doc_info["weight"],
            "value": doc_info["value"],
        })

    # 5. Create agent states
    agents: Dict[str, Any] = {}

    # Safe hub locations for starting positions (low anomaly activity ≤ 3)
    safe_locs = [lid for lid, l in locations.items() if l["anomaly_activity"] <= 3]
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

    # Spawn mutants across all locations
    mutant_type_keys = list(MUTANT_TYPES.keys())
    mutants: Dict[str, Any] = {}
    for i in range(num_mutants):
        spawn_loc = rng.choice(loc_ids)
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
            "memory": [],   # list of {world_turn, world_day, world_hour, type, title, summary, effects}
        }
        locations[spawn_loc]["agents"].append(trader_id)

    return {
        "context_type": "zone_map",
        "world_turn": 1,
        "world_hour": 6,    # Game starts at 06:00
        "world_minute": 0,  # Minute within current hour (0-59)
        "world_day": 1,
        "max_turns": 0,  # 0 = unlimited
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
        # Emission (Выброс) mechanic: first emission 1-2 days after start
        # 1 game day = 1440 turns (1 turn = 1 minute)
        "emission_active": False,
        "emission_scheduled_turn": rng.randint(1440, 2880),
        "emission_ends_turn": 0,
    }


def _make_stalker_agent(
    agent_id: str,
    name: str,
    location_id: str,
    controller_kind: str,
    participant_id,
    rng: random.Random,
    global_goal: str | None = None,
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
    if rng.random() < 0.5:
        inventory.append(_make_item_instance("water", rng))
    if rng.random() < 0.4:
        inventory.append(_make_item_instance("canned_food", rng))

    equipment: Dict[str, Any] = {"weapon": None, "armor": None, "detector": None}
    if weapon:
        equip_item = _make_item_instance(weapon, rng)
        equipment["weapon"] = equip_item
        # Give ammo for the equipped weapon (use AMMO_FOR_WEAPON for single source of truth)
        from app.games.zone_stalkers.balance.items import AMMO_FOR_WEAPON as _AMMO_FOR_WEAPON
        ammo_type = _AMMO_FOR_WEAPON.get(weapon)
        if ammo_type:
            inventory.append(_make_item_instance(ammo_type, rng))
    if armor:
        equipment["armor"] = _make_item_instance(armor, rng)

    # Choose global goal for the agent.
    chosen_global_goal = global_goal if global_goal else rng.choice(
        ["get_rich", "get_rich", "unravel_zone_mystery"]
    )
    # All agents start with the same modest wealth buffer before pursuing their
    # global goal.  material_threshold is strictly in [MATERIAL_THRESHOLD_MIN, MATERIAL_THRESHOLD_MAX].
    from app.games.zone_stalkers.rules.tick_rules import (
        MATERIAL_THRESHOLD_MIN, MATERIAL_THRESHOLD_MAX,
        GET_RICH_COMPLETION_MIN, GET_RICH_COMPLETION_MAX,
    )
    chosen_threshold = rng.randint(MATERIAL_THRESHOLD_MIN, MATERIAL_THRESHOLD_MAX)

    return {
        "id": agent_id,
        "archetype": "stalker_agent",
        "name": name,
        "location_id": location_id,
        # ─── Health & Status ───
        "hp": 100,
        "max_hp": 100,
        "radiation": 0,
        # ─── Survival needs (0–100; higher = worse) ───
        "hunger": 20,
        "thirst": 20,
        "sleepiness": 10,
        # ─── Economy ───
        "money": rng.randint(100, 800),
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
        "skill_survival":    1,
        "skill_survival_xp": 0.0,
        # ─── Goals & Psychology ───
        "global_goal": chosen_global_goal,
        "current_goal": None,
        "risk_tolerance": round(rng.uniform(0.2, 0.9), 2),
        # Wealth buffer (3000–10000) the agent accumulates before pursuing their global goal.
        "material_threshold": chosen_threshold,
        "wealth_goal_target": rng.randint(GET_RICH_COMPLETION_MIN, GET_RICH_COMPLETION_MAX),
        "global_goal_achieved": False,
        "has_left_zone": False,
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
    sell_types = [
        # Medical
        "bandage", "medkit", "army_medkit", "antirad", "rad_cure", "stimpack", "morphine",
        # Weapons
        "pistol", "shotgun", "ak74", "pkm", "svu_svd",
        # Armor
        "leather_jacket", "stalker_suit", "combat_armor", "seva_suit",
        # Ammo
        "ammo_9mm", "ammo_12gauge", "ammo_545", "ammo_762",
        # Consumables
        "bread", "canned_food", "military_ration", "water", "purified_water",
        "energy_drink", "vodka", "glucose",
        # Detectors
        "echo_detector", "bear_detector",
    ]
    for item_type in rng.sample(sell_types, rng.randint(8, min(14, len(sell_types)))):
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
