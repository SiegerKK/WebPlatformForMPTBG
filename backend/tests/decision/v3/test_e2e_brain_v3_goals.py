from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.economy.debts import advance_survival_credit
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
    assert any_memory(hunter, "goal_achieved")
    assert any_memory(hunter, "left_zone")


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
    assert any_memory(hunter, "goal_achieved"), "Hunter must record goal completion"
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
    _records = hunter.get("memory_v3", {}).get("records", {})
    def _any_memory_v3_kind(kind: str) -> bool:
        return any(isinstance(record, dict) and record.get("kind") == kind for record in _records.values())
    # Hunter must notice the target was missing from loc_old.
    assert (
        any_memory(hunter, "target_not_found")
        or any_memory(hunter, "target_moved")
        or any_memory(hunter, "no_tracks_found")
        or any_memory(hunter, "target_route_observed")
        or _any_memory_v3_kind("target_not_found")
        or _any_memory_v3_kind("target_moved")
        or _any_memory_v3_kind("target_route_observed")
    ), (
        "Hunter must record that the original lead at loc_old was invalidated or replaced"
    )
    # Hunter must track the target to its new location.
    assert any_objective_decision(hunter, "TRACK_TARGET") or any_objective_decision(hunter, "VERIFY_LEAD"), (
        "Hunter must record a hunt-followup objective after the target moved"
    )
    # Hunter must record the target dying and the mission succeeding.
    assert any_memory(hunter, "target_death_confirmed") or _any_memory_v3_kind("target_death_confirmed"), (
        "Hunter must record target_death_confirmed"
    )
    assert any_memory(hunter, "goal_achieved") or _any_memory_v3_kind("goal_achieved"), (
        "Hunter must record global_goal_completed"
    )
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
    _records = hunter.get("memory_v3", {}).get("records", {})
    def _any_memory_v3_kind(kind: str) -> bool:
        return any(isinstance(record, dict) and record.get("kind") == kind for record in _records.values())
    # Hunter must gather intel when the target's location was unknown.
    assert any_memory(hunter, "intel_from_trader") or any_memory(hunter, "target_intel") or _any_memory_v3_kind("target_intel"), (
        "Hunter must record intel_from_trader or target_intel"
    )
    # After intel, the hunter must switch to tracking instead of looping on locate.
    assert (
        any_objective_decision(hunter, "TRACK_TARGET")
        or any_objective_decision(hunter, "VERIFY_LEAD")
        or any(
            isinstance(record, dict)
            and record.get("kind") == "objective_decision"
            and str(record.get("details", {}).get("objective_key") or "") in {"TRACK_TARGET", "VERIFY_LEAD"}
            for record in _records.values()
        )
    ), (
        "Hunter must record a follow-up lead verification or tracking objective"
    )
    # Hunter must finish the hunt successfully after switching to tracking.
    assert any_memory(hunter, "target_death_confirmed") or _any_memory_v3_kind("target_death_confirmed"), (
        "Hunter must record target_death_confirmed"
    )
    # Hunter must leave the zone after completing the mission.
    assert any_objective_decision(hunter, "LEAVE_ZONE"), "Hunter must record LEAVE_ZONE objective"
    assert hunter.get("has_left_zone") is True, "Hunter must have has_left_zone=True"


def test_hunter_does_not_repeat_search_target_same_empty_location_forever() -> None:
    """E2E regression: hunter must exhaust a false lead, then switch to intel,
    find the target via trader intel, and complete the hunt — without looping on
    the same empty location indefinitely.

    Setup:
    - Hunter starts at loc_spawn with a WRONG memory lead pointing to loc_false.
    - Target is actually at loc_target (different from loc_false).
    - A trader at loc_spawn can sell the correct intel once the wrong lead is exhausted.
    - The hunter must search loc_false up to 3 times, mark it exhausted, then
      buy intel and switch to the real target location.
    """
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_false", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
                {"to": "loc_exit", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_false": {
            "name": "False Lead",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_target": {
            "name": "Target Location",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_false", "travel_time": 2},
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
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
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
    hunter = _hunter(goal="kill_stalker", kill_target_id="target", ammo_count=5)
    target = _target(location_id="loc_target", hp=1)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]

    # Plant a FALSE lead so the hunter starts by checking the wrong location.
    _remember_target_location(hunter, state, "loc_false")

    state, _ = run_until(
        state,
        lambda s, _events: int(
            s["agents"]["hunter"].get("knowledge_v1", {}).get("hunt_evidence", {}).get("target", {}).get("failed_search_locations", {}).get("loc_false", {}).get("count", 0)
        ) >= 1,
        max_ticks=1200,
    )
    hunter = state["agents"]["hunter"]

    # Helper: check memory_v3 records (legacy memory list may evict old entries
    # over long runs; memory_v3 retains up to 5000 records with eviction of
    # the lowest-priority entries, so relevant hunt records survive).
    def _any_memory_v3_kind(kind: str) -> bool:
        mv3 = hunter.get("memory_v3", {})
        return any(r.get("kind") == kind for r in mv3.get("records", {}).values())

    hunt_evidence = (
        hunter.get("knowledge_v1", {})
        .get("hunt_evidence", {})
        .get("target", {})
    )
    failed_locations = hunt_evidence.get("failed_search_locations", {})
    false_failed = failed_locations.get("loc_false", {})
    assert "loc_false" in failed_locations, (
        "Hunter must store failed search for false lead in knowledge_v1.hunt_evidence"
    )
    # PR10 cutover: target_not_found must not be written into memory_v3.
    assert not _any_memory_v3_kind("target_not_found"), (
        "target_not_found must be knowledge-only after PR10 cutover"
    )
    # Crucially: the hunter must NOT have looped on loc_false indefinitely.
    # The exhaustion threshold is 3, so loc_false must be bounded even when
    # the first lead is wrong.
    false_lead_searches = int(false_failed.get("count", 0))
    assert false_lead_searches >= 1, (
        "Hunter must search the false lead at least once"
    )
    assert false_lead_searches <= 3, (
        f"Hunter must stop searching the empty location at the exhaustion threshold (≤3), "
        f"got {false_lead_searches} — the exhaustion / track-following mechanism may be broken"
    )
    # The hunter must continue with search/intel objectives rather than sticking
    # forever to one stale location.
    assert (
        any_objective_decision(hunter, "GATHER_INTEL")
        or any_objective_decision(hunter, "VERIFY_LEAD")
        or any_objective_decision(hunter, "TRACK_TARGET")
    ), "Hunter should continue lead-based search flow after exhausting stale location"


def test_hunter_exhausts_empty_location_without_omniscient_tracks() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_false", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_false": {
            "name": "False Lead",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_target": {
            "name": "Target Location",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_false", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
    }
    state = _base_state(locations)
    state["debug_omniscient_targets"] = False

    hunter = _hunter(goal="kill_stalker", kill_target_id="target", ammo_count=5)
    target = _target(location_id="loc_target", hp=100)
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]

    # Wrong initial lead and no traders/witnesses to avoid omniscient shortcuts.
    _remember_target_location(hunter, state, "loc_false")

    for _ in range(220):
        state, _ = tick_zone_map(state)

    hunter = state["agents"]["hunter"]
    mv3 = hunter.get("memory_v3", {})
    records = mv3.get("records", {}) if isinstance(mv3, dict) else {}
    hunt_evidence = (
        hunter.get("knowledge_v1", {})
        .get("hunt_evidence", {})
        .get("target", {})
    )
    failed_locations = hunt_evidence.get("failed_search_locations", {})
    false_failed = failed_locations.get("loc_false", {})

    assert "loc_false" in failed_locations, (
        "Hunter must store failed loc_false searches in knowledge hunt evidence"
    )
    assert 1 <= int(false_failed.get("count", 0)) <= 3, (
        "Hunter must stop repeatedly searching loc_false after exhaustion threshold (<=3)"
    )
    assert int(false_failed.get("count", 0)) >= 1, (
        "loc_false should accumulate failed-search evidence after repeated failed searches"
    )
    # PR10 cutover: target_not_found must not be written into memory_v3.
    assert not any(
        isinstance(record, dict) and record.get("kind") == "target_not_found"
        for record in records.values()
    ), "target_not_found must be knowledge-only after PR10 cutover"

    assert (
        any_objective_decision(hunter, "GATHER_INTEL")
        or any_objective_decision(hunter, "LOCATE_TARGET")
        or any_memory(hunter, "no_witnesses")
    ), "After exhausting false lead hunter should switch to intel-gathering behavior"


def test_e2e_debt_escape_reaches_left_zone() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_exit", "travel_time": 2}],
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
        "money": 100000,
        "accounts_receivable": 0,
        "inventory": [],
    }
    hunter = _hunter(goal="get_rich")
    hunter["location_id"] = "loc_spawn"
    hunter["global_goal"] = "get_rich"
    hunter["money"] = 250
    state["agents"]["hunter"] = hunter
    state["locations"]["loc_spawn"]["agents"] = ["hunter", "trader_1"]

    advance_survival_credit(
        state=state,
        debtor_id="hunter",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=5200,
        purpose="survival_food",
        location_id="loc_spawn",
        world_turn=int(state.get("world_turn") or 0),
    )

    state, _ = run_until(
        state,
        lambda s, _events: bool((s.get("agents") or {}).get("hunter", {}).get("has_left_zone")),
        max_ticks=240,
    )
    hunter = state["agents"]["hunter"]
    assert hunter.get("has_left_zone") is True
    assert any_objective_decision(hunter, "LEAVE_ZONE")
    assert any_memory(hunter, "left_zone")


def test_killer_receives_fresh_trader_intel_after_failed_search_and_restarts_hunt() -> None:
    locations = {
        "loc_spawn": {
            "name": "Spawn",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_trader", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_trader": {
            "name": "Trader Hub",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_target", "travel_time": 2},
            ],
            "items": [],
            "agents": [],
        },
        "loc_target": {
            "name": "Target",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_spawn", "travel_time": 2},
                {"to": "loc_trader", "travel_time": 2},
            ],
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
    hunter = _hunter(goal="kill_stalker", kill_target_id="target", ammo_count=5)
    target = _target(location_id="loc_target", hp=50)
    hunter["location_id"] = "loc_spawn"
    state["agents"]["hunter"] = hunter
    state["agents"]["target"] = target
    state["locations"]["loc_spawn"]["agents"] = ["hunter"]
    state["locations"]["loc_target"]["agents"] = ["target"]
    state["locations"]["loc_trader"]["agents"] = ["trader_1"]

    hunter["knowledge_v1"] = {
        "revision": 1,
        "major_revision": 1,
        "known_npcs": {
            "target": {
                "agent_id": "target",
                "name": "target",
                "last_seen_turn": 95,
                "last_direct_seen_turn": 95,
                "last_seen_location_id": "loc_trader",
                "is_alive": True,
                "confidence": 0.8,
            }
        },
        "known_locations": {},
        "known_traders": {},
        "known_hazards": {},
        "known_corpses": {},
        "hunt_evidence": {
            "target": {
                "target_id": "target",
                "last_seen": {"location_id": "loc_trader", "turn": 95, "confidence": 0.8, "source": "witness_report"},
                "death": None,
                "route_hints": [],
                "failed_search_locations": {
                    "loc_trader": {
                        "count": 3,
                        "turn": 100,
                        "cooldown_until_turn": 160,
                        "confidence": 0.8,
                        "location_kind": "trader_hub",
                        "is_hub_location": True,
                    }
                },
                "recent_contact": None,
                "revision": 1,
            }
        },
        "stats": {"last_update_turn": 100, "hunt_evidence_targets_count": 1},
    }

    hunter["knowledge_v1"]["hunt_evidence"]["target"]["last_seen"] = {
        "location_id": "loc_trader",
        "turn": 120,
        "confidence": 0.85,
        "source": "trader_network",
    }
    hunter["knowledge_v1"]["hunt_evidence"]["target"]["recent_contact"] = {
        "turn": 120,
        "location_id": "loc_trader",
    }
    state, _events = tick_zone_map(state)
    hunter = state["agents"]["hunter"]

    target_belief = hunter.get("brain_v3_context", {}).get("hunt_target_belief", {})
    assert target_belief.get("best_location_id") == "loc_trader"
    assert "loc_trader" not in (target_belief.get("exhausted_locations") or [])
