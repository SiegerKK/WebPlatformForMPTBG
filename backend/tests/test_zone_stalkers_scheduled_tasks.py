from __future__ import annotations

from app.games.zone_stalkers.generators.zone_generator import generate_zone
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from app.games.zone_stalkers.runtime.scheduler import pop_due_tasks, schedule_task


def _make_state() -> dict:
    state = generate_zone(
        seed=11,
        num_players=1,
        num_ai_stalkers=0,
        num_mutants=0,
        num_traders=0,
    )
    state["cpu_copy_on_write_enabled"] = True
    state["cpu_copy_on_write_legacy_bridge_enabled"] = False
    state["cpu_event_driven_actions_enabled"] = True
    return state


def test_schedule_and_pop_due_tasks():
    state = {"scheduled_tasks": {}}
    schedule_task(state, runtime=None, turn=10, task={"kind": "k1"})
    schedule_task(state, runtime=None, turn=10, task={"kind": "k2"})
    schedule_task(state, runtime=None, turn=11, task={"kind": "k3"})

    due = pop_due_tasks(state, runtime=None, world_turn=10)
    assert [task["kind"] for task in due] == ["k1", "k2"]
    assert "10" not in state["scheduled_tasks"]
    assert "11" in state["scheduled_tasks"]


def test_legacy_turns_remaining_migrates_to_ends_turn():
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    origin = agent["location_id"]
    target = state["locations"][origin]["connections"][0]["to"]
    start_turn = int(state["world_turn"])
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 2,
        "turns_total": 2,
        "target_id": target,
        "final_target_id": target,
        "remaining_route": [],
        "started_turn": start_turn,
    }

    new_state, _ = tick_zone_map(state)
    migrated = new_state["agents"][agent_id]["scheduled_action"]
    assert migrated is not None
    assert migrated["ends_turn"] == start_turn + 2
    assert migrated["revision"] >= 1
    assert migrated["interruptible"] is True


def test_travel_no_longer_decrements_turns_remaining_each_tick_when_event_driven_enabled():
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    origin = agent["location_id"]
    target = state["locations"][origin]["connections"][0]["to"]
    start_turn = int(state["world_turn"])
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 2,
        "turns_total": 2,
        "target_id": target,
        "final_target_id": target,
        "remaining_route": [],
        "started_turn": start_turn,
        "ends_turn": start_turn + 2,
        "revision": 1,
        "interruptible": True,
    }

    state_1, _ = tick_zone_map(state)
    sched_after_tick = state_1["agents"][agent_id]["scheduled_action"]
    assert sched_after_tick is not None
    assert sched_after_tick["turns_remaining"] == 2


def test_travel_arrival_happens_at_ends_turn():
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    origin = agent["location_id"]
    target = state["locations"][origin]["connections"][0]["to"]
    start_turn = int(state["world_turn"])
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": target,
        "final_target_id": target,
        "remaining_route": [],
        "started_turn": start_turn,
        "ends_turn": start_turn + 1,
        "revision": 1,
        "interruptible": True,
    }

    state_1, _ = tick_zone_map(state)
    assert state_1["agents"][agent_id]["location_id"] == origin
    assert state_1["agents"][agent_id]["scheduled_action"] is not None

    state_2, _ = tick_zone_map(state_1)
    assert state_2["agents"][agent_id]["location_id"] == target
    assert state_2["agents"][agent_id]["scheduled_action"] is None
