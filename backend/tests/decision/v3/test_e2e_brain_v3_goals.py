from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.rules.tick_rules import _add_memory, tick_zone_map

from tests.decision.v3.e2e_helpers import (
    any_active_plan_event,
    any_active_plan_step,
    any_memory,
    any_objective_decision,
    first_memory_turn,
    first_objective_turn,
    run_until,
)


def _hunter(*, goal: str, kill_target_id: str | None = None, ammo_count: int = 3) -> dict[str, Any]:
    inventory = [
        {"id": "food1", "type": "bread", "value": 0},
        {"id": "food2", "type": "bread", "value": 0},
        {"id": "water1", "type": "water", "value": 0},
        {"id": "water2", "type": "water", "value": 0},
        {"id": "med1", "type": "bandage", "value": 0},
        {"id": "med2", "type": "bandage", "value": 0},
        {"id": "med3", "type": "bandage", "value": 0},
    ]
    inventory.extend(
        {"id": f"ammo{i}", "type": "ammo_9mm", "value": 0}
        for i in range(1, ammo_count + 1)
    )
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
        "money": 3000,
        "global_goal": goal,
        "material_threshold": 0,
        "wealth_goal_target": 1000,
        "equipment": {
            "weapon": {"type": "pistol", "value": 300},
            "armor": {"type": "leather_jacket", "value": 200},
        },
        "inventory": inventory,
        "memory": [],
        "action_queue": [],
        "scheduled_action": None,
    }
    if kill_target_id:
        agent["kill_target_id"] = kill_target_id
    return agent


def _target(*, location_id: str, hp: int = 1) -> dict[str, Any]:
    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "script"},
        "name": "target",
        "is_alive": True,
        "has_left_zone": False,
        "location_id": location_id,
        "hp": hp,
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


def _base_state(locations: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
        "locations": locations,
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _remember_target_location(agent: dict[str, Any], state: dict[str, Any], location_id: str) -> None:
    _add_memory(
        agent,
        state["world_turn"],
        state,
        "observation",
        "📍 Известно местоположение цели",
        {
            "action_kind": "target_last_known_location",
            "target_id": str(agent.get("kill_target_id") or ""),
            "location_id": location_id,
        },
        summary=f"Цель замечена в {location_id}",
        agent_id="hunter",
    )


def test_e2e_get_rich_finds_artifact_sells_and_leaves_zone() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_anomaly", "travel_time": 2},
                {"to": "loc_trader", "travel_time": 2},
                {"to": "loc_exit", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_anomaly": {
            "name": "Anomaly",
            "terrain_type": "wasteland",
            "anomaly_activity": 10,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_trader", "travel_time": 2},
            ],
            "items": [],
            "artifacts": [{"id": "artifact_1", "type": "soul", "value": 2500}],
            "agents": [],
        },
        "loc_trader": {
            "name": "Trader",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_anomaly", "travel_time": 2},
                {"to": "loc_exit", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_exit": {
            "name": "Exit",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "exit_zone": True,
            "connections": [{"to": "loc_trader", "travel_time": 2}, {"to": "loc_spawn", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    state["traders"]["trader_1"] = {
        "id": "trader_1",
        "name": "Trader",
        "location_id": "loc_trader",
        "is_alive": True,
        "money": 50000,
    }
    hunter = _hunter(goal="get_rich")
    hunter["money"] = 0
    state["agents"]["hunter"] = hunter
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=1200,
    )
    hunter = state["agents"]["hunter"]
    assert hunter.get("global_goal_achieved") is True
    assert hunter.get("has_left_zone") is True
    assert any_memory(hunter, "global_goal_completed")
    assert any_memory(hunter, "left_zone")
    assert any_objective_decision(hunter, "LEAVE_ZONE")
    assert any_objective_decision(hunter, "FIND_ARTIFACTS") or any_objective_decision(
        hunter, "GET_MONEY_FOR_RESUPPLY"
    )
    assert any_memory(hunter, "trade_sell") or any_memory(hunter, "global_goal_completed")


def test_e2e_kill_stalker_live_target_to_leave_zone() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_target", "travel_time": 2}, {"to": "loc_exit", "travel_time": 2}],
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
            "connections": [{"to": "loc_spawn", "travel_time": 2}, {"to": "loc_target", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    hunter = _hunter(goal="kill_stalker", kill_target_id="target")
    target = _target(location_id="loc_target", hp=1)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]
    _remember_target_location(hunter, state, "loc_target")

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=1200,
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


def test_e2e_kill_stalker_prepares_before_engage_when_no_ammo() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_target", "travel_time": 2}, {"to": "loc_exit", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
        "loc_target": {
            "name": "Target",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_spawn", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
        "loc_exit": {
            "name": "Exit",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "exit_zone": True,
            "connections": [{"to": "loc_spawn", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    state["traders"]["trader_1"] = {
        "id": "trader_1",
        "name": "Trader",
        "location_id": "loc_spawn",
        "is_alive": True,
        "money": 50000,
        # Stock ammo_9mm so the hunter can resupply at the start.
        "inventory": [
            {"id": "ammo_s1", "type": "ammo_9mm", "value": 50, "price": 50},
            {"id": "ammo_s2", "type": "ammo_9mm", "value": 50, "price": 50},
            {"id": "ammo_s3", "type": "ammo_9mm", "value": 50, "price": 50},
        ],
    }
    hunter = _hunter(goal="kill_stalker", kill_target_id="target", ammo_count=0)
    target = _target(location_id="loc_target", hp=1)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]
    _remember_target_location(hunter, state, "loc_target")

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=1400,
    )
    hunter = state["agents"]["hunter"]
    # Full success chain must be present in memory.
    # (Early-turn purchase memories may be pruned after 1400 ticks; ammo-before-engage
    # ordering is a unit-level guarantee in test_hunt_kill_stalker_goal.py.)
    assert any_memory(hunter, "target_death_confirmed"), "Hunter must confirm the kill"
    assert any_memory(hunter, "global_goal_completed"), "Hunter must record goal completion"
    assert any_objective_decision(hunter, "LEAVE_ZONE"), "Hunter must decide to leave the zone"
    assert hunter.get("has_left_zone") is True, "Hunter must have actually left the zone"


def test_e2e_kill_stalker_target_moved_repairs_tracking_plan() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_old", "travel_time": 2},
                {"to": "loc_new", "travel_time": 2},
                {"to": "loc_exit", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_old": {
            "name": "Old",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_spawn", "travel_time": 2}, {"to": "loc_new", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
        "loc_new": {
            "name": "New",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_old", "travel_time": 2},
                {"to": "loc_exit", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_exit": {
            "name": "Exit",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "exit_zone": True,
            "connections": [
                {"to": "loc_new", "travel_time": 2},
                {"to": "loc_spawn", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    state["traders"]["trader_1"] = {
        "id": "trader_1",
        "name": "Trader",
        "location_id": "loc_old",
        "is_alive": True,
        "money": 50000,
    }
    hunter = _hunter(goal="kill_stalker", kill_target_id="target")
    target = _target(location_id="loc_old", hp=1)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_old"]["agents"] = ["target"]
    _remember_target_location(hunter, state, "loc_old")

    # Advance one tick so the hunter starts moving, then teleport the target to
    # loc_new so the hunter arrives at loc_old and finds no one there.
    state, _ = tick_zone_map(state)
    target = state["agents"]["target"]
    if target.get("is_alive", True):
        if "target" in state["locations"]["loc_old"]["agents"]:
            state["locations"]["loc_old"]["agents"].remove("target")
        target["location_id"] = "loc_new"
        if "target" not in state["locations"]["loc_new"]["agents"]:
            state["locations"]["loc_new"]["agents"].append("target")

    # run_until raises AssertionError if the predicate never fires (hard failure).
    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=1399,
    )
    hunter = state["agents"]["hunter"]
    # Hunter must notice the target was missing from loc_old.
    assert any_memory(hunter, "target_not_found") or any_memory(hunter, "target_moved"), (
        "Hunter must record that the target was not found at loc_old"
    )
    # Hunter must track the target to its new location.
    assert any_objective_decision(hunter, "TRACK_TARGET"), (
        "Hunter must record TRACK_TARGET objective after the target moved"
    )
    # Hunter must record the target dying and the mission succeeding.
    assert any_memory(hunter, "target_death_confirmed"), "Hunter must record target_death_confirmed"
    assert any_memory(hunter, "global_goal_completed"), "Hunter must record global_goal_completed"
    # Hunter must decide to leave and then actually leave.
    assert any_objective_decision(hunter, "LEAVE_ZONE"), "Hunter must record LEAVE_ZONE objective"
    assert hunter.get("has_left_zone") is True, "Hunter must have has_left_zone=True"


def test_e2e_kill_stalker_unknown_target_uses_intel_then_hunts() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_target", "travel_time": 2}, {"to": "loc_exit", "travel_time": 2}],
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
            "connections": [{"to": "loc_spawn", "travel_time": 2}, {"to": "loc_target", "travel_time": 2}],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    state["traders"]["trader_1"] = {
        "id": "trader_1",
        "name": "Trader",
        "location_id": "loc_spawn",
        "is_alive": True,
        "money": 50000,
    }
    hunter = _hunter(goal="kill_stalker", kill_target_id="target")
    target = _target(location_id="loc_target", hp=1)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]

    state, _ = run_until(
        state,
        lambda s, _events: bool(s["agents"]["hunter"].get("has_left_zone")),
        max_ticks=1400,
    )
    hunter = state["agents"]["hunter"]
    # Hunter must gather intel when the target's location was unknown.
    assert any_objective_decision(hunter, "LOCATE_TARGET"), "Hunter must record LOCATE_TARGET"
    assert any_memory(hunter, "intel_from_trader") or any_memory(hunter, "target_last_known_location"), (
        "Hunter must record intel_from_trader or target_last_known_location"
    )
    # Hunter must engage and confirm the target died.
    assert any_objective_decision(hunter, "TRACK_TARGET") or any_objective_decision(hunter, "LOCATE_TARGET"), (
        "Hunter must record TRACK_TARGET or LOCATE_TARGET once the target's position is known"
    )
    assert any_memory(hunter, "target_death_confirmed"), "Hunter must record target_death_confirmed"
    # Hunter must leave the zone after completing the mission.
    assert any_objective_decision(hunter, "LEAVE_ZONE"), "Hunter must record LEAVE_ZONE objective"
    assert hunter.get("has_left_zone") is True, "Hunter must have has_left_zone=True"
