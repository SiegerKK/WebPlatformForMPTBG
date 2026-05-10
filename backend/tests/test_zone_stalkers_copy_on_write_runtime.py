"""Tests for ZoneTickRuntime copy-on-write behavior (CPU PR2)."""
from __future__ import annotations

import copy

from app.games.zone_stalkers.generators.zone_generator import generate_zone
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from app.games.zone_stalkers.runtime.zone_tick_runtime import ZoneTickRuntime


def _make_min_state() -> dict:
    return {
        "agents": {
            "a1": {
                "id": "a1",
                "hp": 100,
                "inventory": [{"id": "i1"}],
                "scheduled_action": {"type": "sleep", "turns_remaining": 2},
                "active_plan_v3": {"objective_key": "IDLE"},
                "memory_v3": {"records": {"x": {"world_turn": 1}}},
            }
        },
        "locations": {
            "L1": {"id": "L1", "agents": ["a1"], "connections": []},
            "L2": {"id": "L2", "agents": [], "connections": []},
        },
        "traders": {"t1": {"id": "t1", "money": 1000, "inventory": [{"id": "ti1"}]}},
    }


def test_copy_on_write_agent_mutation_does_not_mutate_original_agent():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    runtime.set_agent_field("a1", "hp", 40)

    assert source["agents"]["a1"]["hp"] == 100
    assert runtime.state["agents"]["a1"]["hp"] == 40
    assert "a1" in runtime.dirty_agents


def test_copy_on_write_location_mutation_does_not_mutate_original_location():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    runtime.set_location_field("L1", "name", "New name")

    assert source["locations"]["L1"].get("name") is None
    assert runtime.state["locations"]["L1"]["name"] == "New name"
    assert "L1" in runtime.dirty_locations


def test_inventory_mutation_copies_inventory_list():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    inventory = runtime.mutable_agent_list("a1", "inventory")
    inventory.append({"id": "i2"})

    assert len(source["agents"]["a1"]["inventory"]) == 1
    assert len(runtime.state["agents"]["a1"]["inventory"]) == 2


def test_scheduled_action_mutation_copies_nested_dict():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    scheduled_action = runtime.mutable_agent_dict("a1", "scheduled_action")
    scheduled_action["turns_remaining"] = 1

    assert source["agents"]["a1"]["scheduled_action"]["turns_remaining"] == 2
    assert runtime.state["agents"]["a1"]["scheduled_action"]["turns_remaining"] == 1


def test_active_plan_mutation_copies_nested_dict():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    active_plan = runtime.mutable_agent_dict("a1", "active_plan_v3")
    active_plan["objective_key"] = "REST"

    assert source["agents"]["a1"]["active_plan_v3"]["objective_key"] == "IDLE"
    assert runtime.state["agents"]["a1"]["active_plan_v3"]["objective_key"] == "REST"


def test_memory_v3_mutation_copies_records_container():
    source = _make_min_state()
    runtime = ZoneTickRuntime(source_state=source)

    mem_v3 = runtime.mutable_agent_dict("a1", "memory_v3")
    mem_v3["records"] = dict(mem_v3.get("records") or {})
    mem_v3["records"]["y"] = {"world_turn": 2}

    assert "y" not in source["agents"]["a1"]["memory_v3"]["records"]
    assert "y" in runtime.state["agents"]["a1"]["memory_v3"]["records"]


def test_tick_zone_map_does_not_mutate_input_state():
    old = generate_zone(
        seed=123,
        num_players=1,
        num_ai_stalkers=1,
        num_mutants=0,
        num_traders=1,
    )
    old["cpu_copy_on_write_enabled"] = True
    old_before = copy.deepcopy(old)

    new_state, _events = tick_zone_map(old)

    assert old == old_before
    assert new_state is not old
