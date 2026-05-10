"""Tests for ZoneTickRuntime copy-on-write behavior (CPU PR2)."""
from __future__ import annotations

import copy

from app.games.zone_stalkers.generators.zone_generator import generate_zone
from app.games.zone_stalkers.rules import tick_rules
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


def _make_cow_tick_state(
    *,
    seed: int = 123,
    num_players: int = 1,
    num_ai_stalkers: int = 0,
    num_traders: int = 0,
) -> dict:
    state = generate_zone(
        seed=seed,
        num_players=num_players,
        num_ai_stalkers=num_ai_stalkers,
        num_mutants=0,
        num_traders=num_traders,
    )
    state["cpu_copy_on_write_enabled"] = True
    state["cpu_copy_on_write_legacy_bridge_enabled"] = False
    state["world_turn"] = max(2, int(state.get("world_turn", 2)))
    _mem_v3_template = {
        "records": {},
        "indexes": {
            "by_entity": {},
            "by_item_type": {},
            "by_kind": {},
            "by_layer": {},
            "by_location": {},
            "by_tag": {},
        },
        "stats": {
            "last_consolidation_turn": None,
            "last_decay_turn": None,
            "records_count": 0,
        },
    }
    for agent in state.get("agents", {}).values():
        agent.setdefault("brain_trace", None)
        agent.setdefault("active_plan_v3", None)
        agent["memory_v3"] = copy.deepcopy(_mem_v3_template)
        agent.setdefault("action_queue", [])
        agent.setdefault("scheduled_action", None)
        agent.setdefault("action_used", False)
    return state


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
    old = _make_cow_tick_state(num_players=1, num_ai_stalkers=0, num_traders=0)
    for _agent in old.get("agents", {}).values():
        _agent["has_left_zone"] = True
    old_before = copy.deepcopy(old)

    new_state, _events = tick_zone_map(old)

    assert old == old_before
    assert new_state is not old


def test_tick_zone_map_cow_does_not_copy_all_agents():
    state = _make_cow_tick_state(seed=124, num_players=1, num_ai_stalkers=0, num_traders=0)
    base_loc = next(iter(state["locations"].keys()))
    _complete_mem_v3 = {
        "records": {"seed_rec": {"id": "seed_rec", "kind": "observation"}},
        "indexes": {
            "by_entity": {},
            "by_item_type": {},
            "by_kind": {"observation": ["seed_rec"]},
            "by_layer": {},
            "by_location": {},
            "by_tag": {},
        },
        "stats": {"last_consolidation_turn": None, "last_decay_turn": 1, "records_count": 1},
    }
    for idx in range(9):
        aid = f"cow_agent_{idx}"
        state["agents"][aid] = {
            "id": aid,
            "name": aid,
            "archetype": "stalker_agent",
            "controller": {"kind": "human", "participant_id": None},
            "location_id": base_loc,
            "is_alive": True,
            "has_left_zone": True,
            "brain_trace": None,
            "active_plan_v3": None,
            "memory_v3": copy.deepcopy(_complete_mem_v3),
            "action_queue": [],
            "scheduled_action": None,
            "action_used": False,
            "inventory": [],
            "memory": [],
            "hp": 100,
            "hunger": 0,
            "thirst": 0,
            "sleepiness": 0,
        }
    _new_state, _events = tick_zone_map(state)
    runtime = tick_rules._last_tick_runtime
    assert runtime is not None
    assert runtime.cow_agents_copied < len(state["agents"])


def test_tick_zone_map_cow_does_not_copy_all_locations():
    state = _make_cow_tick_state(seed=125, num_players=1, num_ai_stalkers=0, num_traders=0)
    loc_ids = list(state["locations"].keys())
    assert len(loc_ids) >= 2
    old_loc, new_loc = loc_ids[0], loc_ids[1]
    state["locations"][old_loc]["connections"] = [{"to": new_loc, "travel_time": 1, "closed": False}]
    state["locations"][new_loc]["connections"] = [{"to": old_loc, "travel_time": 1, "closed": False}]
    agent_id = next(iter(state["agents"].keys()))
    for aid, agent in state["agents"].items():
        agent["has_left_zone"] = aid != agent_id
        agent["controller"] = {"kind": "human", "participant_id": None}
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        agent["action_used"] = False
    state["agents"][agent_id]["location_id"] = old_loc
    state["agents"][agent_id]["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": new_loc,
        "final_target_id": new_loc,
        "remaining_route": [],
        "started_turn": state.get("world_turn", 1),
    }
    state["locations"][old_loc]["agents"] = [agent_id]
    state["locations"][new_loc]["agents"] = []

    _new_state, _events = tick_zone_map(state)
    runtime = tick_rules._last_tick_runtime
    assert runtime is not None
    assert runtime.cow_locations_copied < len(state["locations"])
    assert runtime.cow_locations_copied >= 2


def test_tick_zone_map_cow_profiler_deepcopy_ms_low_or_counters_small():
    state = _make_cow_tick_state(seed=126, num_players=1, num_ai_stalkers=0, num_traders=0)
    for agent in state["agents"].values():
        agent["has_left_zone"] = True

    _new_state, _events = tick_zone_map(state)
    runtime = tick_rules._last_tick_runtime
    assert runtime is not None
    profiler_data = runtime.profiler.to_dict() if runtime.profiler is not None else {}
    deepcopy_ms = profiler_data.get("sections_ms", {}).get("deepcopy_ms", 0.0)
    assert deepcopy_ms < 5.0 or runtime.cow_agents_copied < len(state["agents"])


def test_cow_tick_travel_arrival_updates_agent_and_locations():
    state = _make_cow_tick_state(seed=127, num_players=1, num_ai_stalkers=0, num_traders=0)
    loc_ids = list(state["locations"].keys())
    assert len(loc_ids) >= 2
    old_loc, new_loc = loc_ids[0], loc_ids[1]
    state["locations"][old_loc]["connections"] = [{"to": new_loc, "travel_time": 1, "closed": False}]
    state["locations"][new_loc]["connections"] = [{"to": old_loc, "travel_time": 1, "closed": False}]
    agent_id = next(iter(state["agents"].keys()))
    for aid, agent in state["agents"].items():
        agent["has_left_zone"] = aid != agent_id
        agent["controller"] = {"kind": "human", "participant_id": None}
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        agent["action_used"] = False
    state["agents"][agent_id]["location_id"] = old_loc
    state["agents"][agent_id]["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": new_loc,
        "final_target_id": new_loc,
        "remaining_route": [],
        "started_turn": state.get("world_turn", 1),
    }
    state["locations"][old_loc]["agents"] = [agent_id]
    state["locations"][new_loc]["agents"] = []
    new_state, _events = tick_zone_map(state)

    assert new_state["agents"][agent_id]["location_id"] == new_loc
    assert agent_id not in new_state["locations"][old_loc]["agents"]
    assert agent_id in new_state["locations"][new_loc]["agents"]


def test_cow_tick_death_marks_agent_dead_without_mutating_input():
    state = _make_cow_tick_state(seed=128, num_players=1, num_ai_stalkers=0, num_traders=0)
    agent_id = next(iter(state["agents"].keys()))
    for aid, agent in state["agents"].items():
        agent["has_left_zone"] = aid != agent_id
        agent["controller"] = {"kind": "human", "participant_id": None}
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        agent["action_used"] = False
    state["world_minute"] = 59
    state["agents"][agent_id]["hp"] = 1
    state["agents"][agent_id]["hunger"] = 100
    state["agents"][agent_id]["thirst"] = 100
    old_before = copy.deepcopy(state)

    new_state, _events = tick_zone_map(state)

    assert new_state["agents"][agent_id]["is_alive"] is False
    assert old_before["agents"][agent_id]["is_alive"] is True
    assert old_before["agents"][agent_id]["hp"] == 1


def test_cow_tick_emission_still_kills_exposed_agent():
    state = _make_cow_tick_state(seed=129, num_players=1, num_ai_stalkers=0, num_traders=0)
    dangerous_terrains = {"plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge"}
    loc_id = next(iter(state["locations"].keys()))
    for lid, loc in state["locations"].items():
        if loc.get("terrain_type") in dangerous_terrains:
            loc_id = lid
            break
    state["locations"][loc_id]["terrain_type"] = "plain"
    agent_id = next(iter(state["agents"].keys()))
    for aid, agent in state["agents"].items():
        agent["has_left_zone"] = aid != agent_id
        agent["controller"] = {"kind": "human", "participant_id": None}
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        agent["action_used"] = False
    state["agents"][agent_id]["location_id"] = loc_id
    state["locations"][loc_id]["agents"] = [agent_id]
    state["emission_active"] = False
    state["emission_scheduled_turn"] = state.get("world_turn", 1)
    new_state, _events = tick_zone_map(state)

    assert new_state["agents"][agent_id]["is_alive"] is False


def test_cow_tick_does_not_mutate_input_when_agent_missing_optional_fields():
    """Agents lacking optional fields (brain_trace/active_plan_v3/memory_v3/action_queue)
    must not cause tick to mutate the input state dict."""
    state = _make_cow_tick_state(seed=130, num_players=1, num_ai_stalkers=0, num_traders=0)
    for agent in state["agents"].values():
        agent.pop("brain_trace", None)
        agent.pop("active_plan_v3", None)
        agent.pop("memory_v3", None)
        agent.pop("action_queue", None)
        agent["has_left_zone"] = True

    before = copy.deepcopy(state)
    tick_zone_map(state)

    assert state == before


def test_cow_tick_does_not_mutate_input_during_terrain_migration():
    """Terrain migration (unknown → plain) must happen in new_state, not in the input state."""
    state = _make_cow_tick_state(seed=131, num_players=1, num_ai_stalkers=0, num_traders=0)
    state["_terrain_migrated_v3"] = False
    loc_id = next(iter(state["locations"].keys()))
    state["locations"][loc_id]["terrain_type"] = "unknown_legacy_type"
    for agent in state["agents"].values():
        agent["has_left_zone"] = True

    before = copy.deepcopy(state)
    new_state, _ = tick_zone_map(state)

    assert state == before
    assert new_state["locations"][loc_id]["terrain_type"] == "plain"


def test_cow_tick_emission_artifact_spawn_does_not_mutate_input_location():
    """Artifact spawning during emission start must not mutate the input location's artifacts list."""
    state = _make_cow_tick_state(seed=132, num_players=1, num_ai_stalkers=0, num_traders=0)
    anomaly_loc_id = None
    for lid, loc in state["locations"].items():
        if loc.get("anomaly_activity", 0) > 0:
            anomaly_loc_id = lid
            break
    if anomaly_loc_id is None:
        first_lid = next(iter(state["locations"].keys()))
        state["locations"][first_lid]["anomaly_activity"] = 10
        anomaly_loc_id = first_lid
    state["locations"][anomaly_loc_id].setdefault("artifacts", [])
    for agent in state["agents"].values():
        agent["has_left_zone"] = True
    state["emission_active"] = False
    state["emission_scheduled_turn"] = state.get("world_turn", 2)

    before = copy.deepcopy(state)
    new_state, _events = tick_zone_map(state)

    assert state == before
    artifact_events = [e for e in _events if e.get("event_type") == "artifact_spawned"
                       and e.get("payload", {}).get("location_id") == anomaly_loc_id]
    if artifact_events:
        assert len(new_state["locations"][anomaly_loc_id].get("artifacts", [])) > len(
            before["locations"][anomaly_loc_id].get("artifacts", [])
        )
