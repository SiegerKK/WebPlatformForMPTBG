"""Shared fixtures and factories for the decision module test suite."""
from __future__ import annotations

import pytest
from typing import Any, Optional

# Canonical item type constants (match balance/items.py)
_WEAPON_TYPE = "pistol"
_ARMOR_TYPE = "leather_jacket"
_AMMO_TYPE = "ammo_9mm"


def make_agent(
    agent_id: str = "bot1",
    hp: int = 100,
    hunger: int = 0,
    thirst: int = 0,
    sleepiness: int = 0,
    money: int = 500,
    global_goal: str = "get_rich",
    material_threshold: int = 3000,
    location_id: str = "loc_a",
    has_weapon: bool = True,
    has_armor: bool = True,
    has_ammo: bool = True,
    kill_target_id: Optional[str] = None,
    global_goal_achieved: bool = False,
    inventory: Optional[list] = None,
) -> dict[str, Any]:
    """Construct a minimal bot agent dict for testing.

    Defaults to having a weapon, armor, ammo, and sufficient food/drink/medicine
    stock so that ``reload_or_rearm`` is 0.0 and does not mask other needs in
    tests that don't cover equipment or supply logic.

    Supply stock defaults (risk_tolerance=0.5):
      - 3 ammo items (DESIRED_AMMO_COUNT) when has_weapon=True and has_ammo=True
      - 2 food items (desired_food for risk_tolerance=0.5)
      - 2 drink items (desired_drink for risk_tolerance=0.5)
      - 3 medicine items (desired_medicine for risk_tolerance=0.5)

    Pass ``has_weapon=False`` / ``has_armor=False`` / ``has_ammo=False`` when
    specifically testing equipment-related logic.  Override ``inventory``
    directly when testing specific inventory states.
    """
    agent: dict[str, Any] = {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "has_left_zone": False,
        "location_id": location_id,
        "hp": hp,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": sleepiness,
        "money": money,
        "global_goal": global_goal,
        "material_threshold": material_threshold,
        "equipment": {},
        "inventory": list(inventory) if inventory else [],
        "memory": [],
        "name": agent_id,
        "skill_stalker": 1,
        "risk_tolerance": 0.5,
    }
    if has_weapon:
        agent["equipment"]["weapon"] = {"type": _WEAPON_TYPE, "value": 300}
    if has_armor:
        agent["equipment"]["armor"] = {"type": _ARMOR_TYPE, "value": 200}
    if has_ammo and has_weapon:
        # Add DESIRED_AMMO_COUNT (3) ammo items so reload_or_rearm stays 0.0 by default.
        # value=0 to avoid affecting liquid wealth in tests.
        agent["inventory"] += [
            {"id": f"{_AMMO_TYPE}_{i}", "type": _AMMO_TYPE, "value": 0}
            for i in range(3)
        ]
    # Add default food/drink/medicine stock (desired counts for risk_tolerance=0.5)
    # so that reload_or_rearm stays 0.0 by default in tests that don't test supplies.
    # value=0 to avoid affecting liquid wealth calculations.
    if inventory is None:
        agent["inventory"] += [
            {"id": "default_food_0", "type": "bread", "value": 0},
            {"id": "default_food_1", "type": "bread", "value": 0},
            {"id": "default_drink_0", "type": "water", "value": 0},
            {"id": "default_drink_1", "type": "water", "value": 0},
            {"id": "default_med_0", "type": "bandage", "value": 0},
            {"id": "default_med_1", "type": "bandage", "value": 0},
            {"id": "default_med_2", "type": "bandage", "value": 0},
        ]
    if kill_target_id:
        agent["kill_target_id"] = kill_target_id
        agent["global_goal"] = "kill_stalker"
    if global_goal_achieved:
        agent["global_goal_achieved"] = True
    return agent


def make_minimal_state(
    agent_id: str = "bot1",
    agent: Optional[dict] = None,
    loc_terrain: str = "buildings",
) -> dict[str, Any]:
    """Construct a minimal game state with two locations (loc_a → loc_b)."""
    if agent is None:
        agent = make_agent(agent_id=agent_id, location_id="loc_a")
    return {
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {agent_id: agent},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "Локация А",
                "terrain_type": loc_terrain,
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 12}],
                "items": [],
                "agents": [agent_id],
            },
            "loc_b": {
                "name": "Локация Б",
                "terrain_type": "buildings",
                "anomaly_activity": 5,
                "connections": [{"to": "loc_a", "travel_time": 12}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def make_state_with_trader(
    agent_id: str = "bot1",
    agent: Optional[dict] = None,
    trader_at: str = "loc_b",
) -> dict[str, Any]:
    """Construct a game state that includes a trader at the given location."""
    state = make_minimal_state(agent_id=agent_id, agent=agent)
    state["traders"] = {
        "trader_1": {
            "name": "Сидорович",
            "location_id": trader_at,
            "is_alive": True,
            "inventory": [],
        }
    }
    state["locations"][trader_at]["agents"] = ["trader_1"]
    return state
