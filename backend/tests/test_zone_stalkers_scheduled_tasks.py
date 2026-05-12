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


def test_stale_scheduled_task_is_ignored_after_interruption():
    """Setup a travel action with revision=1, interrupt it (bumps to 2),
    then fire a scheduled_action_complete task with revision=1.
    The agent's location should NOT change (task is stale)."""
    from app.games.zone_stalkers.runtime.task_processor import interrupt_action
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

    # Interrupt the action (bumps revision to 2, clears scheduled_action)
    interrupted = interrupt_action(agent_id, agent, state, runtime=None, world_turn=start_turn)
    assert interrupted is True
    # scheduled_action should be cleared
    assert state["agents"][agent_id].get("scheduled_action") is None

    # Now schedule a completion task with the OLD revision=1
    schedule_task(state, runtime=None, turn=start_turn + 1, task={
        "kind": "travel_arrival",
        "agent_id": agent_id,
        "scheduled_action_revision": 1,
    })

    # Advance to the completion turn
    state_2, _ = tick_zone_map(state)
    # Agent should still be at origin since the task is stale (no scheduled_action to complete)
    assert state_2["agents"][agent_id]["location_id"] == origin


def test_emission_interrupts_travel_in_event_driven_mode():
    """Setup agent traveling with emission_active=True, run tick, verify scheduled_action is cleared."""
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    origin = agent["location_id"]
    target = state["locations"][origin]["connections"][0]["to"]
    start_turn = int(state["world_turn"])

    # The interrupt path requires only a long action type and active emission threat.
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 5,
        "turns_total": 5,
        "target_id": target,
        "final_target_id": target,
        "remaining_route": [],
        "started_turn": start_turn,
        "ends_turn": start_turn + 5,
        "revision": 1,
        "interruptible": True,
    }

    # Activate an emission threat
    state["emission_active"] = True
    state["emission_ends_turn"] = start_turn + 20

    state_1, _ = tick_zone_map(state)
    # scheduled_action should be cleared due to emission interrupt
    sched_after = state_1["agents"][agent_id].get("scheduled_action")
    assert sched_after is None, f"Expected None, got {sched_after}"


def test_sleep_tick_applies_recovery_without_per_tick_polling():
    """Setup sleep action with cpu_event_driven_actions_enabled=True.
    Schedule a sleep_tick task at current world_turn.
    Run ONE tick.
    Verify sleepiness decreased without per-tick polling."""
    from app.games.zone_stalkers.runtime.task_processor import process_due_tasks
    from app.games.zone_stalkers.needs.lazy_needs import ensure_needs_state

    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    world_turn = int(state["world_turn"])

    # Set up lazy needs
    ensure_needs_state(agent, world_turn)
    old_revision = int(agent["needs_state"]["revision"])
    agent["needs_state"]["sleepiness"]["base"] = 80.0
    agent["sleepiness"] = 80.0

    # Set up sleep scheduled action
    agent["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 360,
        "turns_total": 360,
        "ends_turn": world_turn + 360,
        "revision": 1,
        "interruptible": True,
        "sleep_intervals_applied": 0,
    }

    # Schedule a sleep_tick task at current world_turn
    schedule_task(state, runtime=None, turn=world_turn, task={
        "kind": "sleep_tick",
        "agent_id": agent_id,
        "scheduled_action_revision": 1,
    })

    task_events, _ = process_due_tasks(state, runtime=None, world_turn=world_turn)
    # Should have a sleep_interval_applied event
    event_types = [e["event_type"] for e in task_events]
    assert "sleep_interval_applied" in event_types, f"Got events: {event_types}"

    # Sleepiness should have decreased
    from app.games.zone_stalkers.needs.lazy_needs import get_need
    new_sleepiness = get_need(agent, "sleepiness", world_turn)
    assert new_sleepiness < 80.0, f"Expected sleepiness < 80, got {new_sleepiness}"
    assert int(agent["needs_state"]["revision"]) == old_revision + 1
    # sleep_tick should re-schedule need threshold tasks for the new revision
    future_tasks = state.get("scheduled_tasks", {})
    assert any(
        task.get("kind") == "need_threshold_crossed" and task.get("agent_id") == agent_id
        for bucket in future_tasks.values()
        for task in bucket
    )


def test_sleep_complete_clears_scheduled_action():
    """Agent with sleep action, ends_turn=world_turn+1. Run 2 ticks.
    After 2nd tick, scheduled_action should be None."""
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    start_turn = int(state["world_turn"])

    agent["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 1,
        "turns_total": 1,
        "ends_turn": start_turn + 1,
        "revision": 1,
        "interruptible": True,
    }

    state_1, _ = tick_zone_map(state)
    assert state_1["agents"][agent_id]["scheduled_action"] is not None

    state_2, _ = tick_zone_map(state_1)
    assert state_2["agents"][agent_id]["scheduled_action"] is None


def test_explore_complete_at_ends_turn():
    """Agent with explore_anomaly_location action, ends_turn=world_turn+1.
    Verify exploration fires at ends_turn."""
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    start_turn = int(state["world_turn"])

    agent["scheduled_action"] = {
        "type": "explore_anomaly_location",
        "turns_remaining": 1,
        "turns_total": 1,
        "ends_turn": start_turn + 1,
        "revision": 1,
        "interruptible": True,
    }

    state_1, _ = tick_zone_map(state)
    # Not yet complete (ends_turn hasn't been reached)
    assert state_1["agents"][agent_id].get("scheduled_action") is not None

    state_2, _ = tick_zone_map(state_1)
    # Now at ends_turn — should be completed (cleared)
    assert state_2["agents"][agent_id].get("scheduled_action") is None


def test_wait_complete_at_ends_turn():
    """Agent with wait action, ends_turn=world_turn+1.
    Verify action clears at ends_turn."""
    state = _make_state()
    agent_id, agent = next(iter(state["agents"].items()))
    start_turn = int(state["world_turn"])

    agent["scheduled_action"] = {
        "type": "wait",
        "turns_remaining": 1,
        "turns_total": 1,
        "ends_turn": start_turn + 1,
        "revision": 1,
        "interruptible": True,
    }

    state_1, _ = tick_zone_map(state)
    assert state_1["agents"][agent_id].get("scheduled_action") is not None

    state_2, _ = tick_zone_map(state_1)
    # Action should be completed/cleared
    assert state_2["agents"][agent_id].get("scheduled_action") is None


def test_tick_flow_schedules_need_threshold_tasks_when_lazy_needs_enabled():
    state = _make_state()
    state["cpu_lazy_needs_enabled"] = True
    agent_id, agent = next(iter(state["agents"].items()))
    world_turn = int(state["world_turn"])
    assert "needs_state" not in agent

    state_1, _ = tick_zone_map(state)
    migrated_agent = state_1["agents"][agent_id]
    assert isinstance(migrated_agent.get("needs_state"), dict)
    scheduled = state_1.get("scheduled_tasks", {})
    assert any(
        task.get("kind") == "need_threshold_crossed" and task.get("agent_id") == agent_id
        for bucket in scheduled.values()
        for task in bucket
    ), f"Expected threshold task scheduling at turn {world_turn}, got keys={list(scheduled.keys())}"
