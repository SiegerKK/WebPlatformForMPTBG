from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.rules.tick_rules import _add_memory

from tests.decision.v3.e2e_helpers import any_memory, any_objective_decision, run_until


def _base_world() -> dict[str, Any]:
    return {
        "seed": 7,
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": None,
        "emission_ends_turn": None,
        "agents": {},
        "traders": {},
        "locations": {
            "loc_spawn": {
                "name": "Spawn",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_trader", "travel_time": 2}, {"to": "loc_target", "travel_time": 2}],
                "items": [],
                "agents": [],
            },
            "loc_trader": {
                "name": "Trader",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_spawn", "travel_time": 2}, {"to": "loc_exit", "travel_time": 2}],
                "items": [],
                "agents": [],
            },
            "loc_target": {
                "name": "Target",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_spawn", "travel_time": 2}, {"to": "loc_exit", "travel_time": 2}],
                "items": [],
                "agents": [],
            },
            "loc_exit": {
                "name": "Exit",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "exit_zone": True,
                "connections": [{"to": "loc_trader", "travel_time": 2}, {"to": "loc_target", "travel_time": 2}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _hunter(*, goal: str, kill_target_id: str | None = None) -> dict[str, Any]:
    agent = {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": "hunter",
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": "loc_spawn",
        "hp": 100,
        "max_hp": 100,
        "radiation": 0,
        "hunger": 5,
        "thirst": 5,
        "sleepiness": 5,
        "money": 0,
        "global_goal": goal,
        "material_threshold": 0,
        "wealth_goal_target": 1000,
        "equipment": {
            "weapon": {"type": "pistol", "value": 300},
            "armor": {"type": "leather_jacket", "value": 200},
        },
        "inventory": [
            {"id": "ammo1", "type": "ammo_9mm", "value": 0},
            {"id": "ammo2", "type": "ammo_9mm", "value": 0},
            {"id": "ammo3", "type": "ammo_9mm", "value": 0},
            {"id": "food1", "type": "bread", "value": 0},
            {"id": "food2", "type": "bread", "value": 0},
            {"id": "water1", "type": "water", "value": 0},
            {"id": "water2", "type": "water", "value": 0},
            {"id": "med1", "type": "bandage", "value": 0},
            {"id": "med2", "type": "bandage", "value": 0},
            {"id": "med3", "type": "bandage", "value": 0},
        ],
        "memory": [],
        "action_queue": [],
        "scheduled_action": None,
    }
    if kill_target_id:
        agent["kill_target_id"] = kill_target_id
    return agent


def test_e2e_get_rich_from_spawn_to_leave_zone() -> None:
    state = _base_world()
    state["traders"]["trader_1"] = {
        "id": "trader_1",
        "name": "Trader",
        "location_id": "loc_trader",
        "is_alive": True,
    }
    hunter = _hunter(goal="get_rich")
    # Deterministic wealth source for quick e2e path: sell one artifact.
    hunter["inventory"].append({"id": "artifact_1", "type": "soul", "value": 2000})
    state["agents"]["hunter"] = hunter
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=400,
    )
    hunter = state["agents"]["hunter"]
    assert hunter.get("global_goal_achieved") is True
    assert hunter.get("has_left_zone") is True
    assert any_memory(hunter, "global_goal_completed")
    assert any_memory(hunter, "left_zone")
    assert any_objective_decision(hunter, "LEAVE_ZONE")


def test_e2e_kill_stalker_known_target_to_leave_zone() -> None:
    state = _base_world()
    hunter = _hunter(goal="kill_stalker", kill_target_id="target")
    target = {
        "archetype": "stalker_agent",
        "controller": {"kind": "script"},
        "name": "target",
        "is_alive": False,
        "has_left_zone": False,
        "location_id": "loc_target",
        "hp": 0,
        "max_hp": 100,
        "hunger": 0,
        "thirst": 0,
        "sleepiness": 0,
        "money": 0,
        "global_goal": "get_rich",
        "equipment": {},
        "inventory": [],
        "memory": [],
        "action_queue": [],
        "scheduled_action": None,
    }
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]
    _add_memory(
        hunter,
        state["world_turn"],
        state,
        "observation",
        "✅ Подтверждена ликвидация цели",
        {"action_kind": "target_death_confirmed", "target_id": "target", "location_id": "loc_target"},
        summary="Цель подтверждена как ликвидированная.",
        agent_id="hunter",
    )

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=900,
    )
    hunter = state["agents"]["hunter"]
    target = state["agents"]["target"]
    assert target.get("is_alive") is False
    assert hunter.get("global_goal_achieved") is True
    assert hunter.get("has_left_zone") is True
    assert any_memory(hunter, "target_death_confirmed")
    assert any_memory(hunter, "global_goal_completed")
    assert any_memory(hunter, "left_zone")
    assert any_objective_decision(hunter, "LEAVE_ZONE")
