from __future__ import annotations

from app.games.zone_stalkers.needs.lazy_needs import (
    ensure_needs_state,
    get_need,
    get_need_readonly,
    materialize_needs,
    set_need,
    set_needs,
)


def test_lazy_need_value_increases_with_world_turn():
    agent = {"hunger": 10.0, "thirst": 20.0, "sleepiness": 30.0}
    ensure_needs_state(agent, world_turn=100)

    hunger_100 = get_need(agent, "hunger", world_turn=100)
    hunger_160 = get_need(agent, "hunger", world_turn=160)
    assert hunger_160 > hunger_100


def test_materialize_needs_updates_legacy_fields():
    agent = {"hunger": 5.0, "thirst": 10.0, "sleepiness": 15.0}
    ensure_needs_state(agent, world_turn=50)

    values = materialize_needs(agent, world_turn=110)
    assert agent["hunger"] == values["hunger"]
    assert agent["thirst"] == values["thirst"]
    assert agent["sleepiness"] == values["sleepiness"]


def test_set_need_resets_base_and_bumps_revision():
    agent = {"hunger": 40.0, "thirst": 55.0, "sleepiness": 65.0}
    ensure_needs_state(agent, world_turn=10)
    old_revision = agent["needs_state"]["revision"]

    set_need(agent, "thirst", 12.0, world_turn=20)
    assert get_need(agent, "thirst", world_turn=20) == 12.0
    assert agent["thirst"] == 12.0
    assert agent["needs_state"]["revision"] == old_revision + 1


def test_set_needs_batch_bumps_revision_once():
    agent = {"hunger": 40.0, "thirst": 55.0, "sleepiness": 65.0}
    ensure_needs_state(agent, world_turn=10)
    old_revision = agent["needs_state"]["revision"]

    set_needs(
        agent,
        {"hunger": 33.0, "thirst": 22.0, "sleepiness": 11.0},
        world_turn=20,
    )
    assert agent["needs_state"]["revision"] == old_revision + 1
    assert get_need(agent, "hunger", world_turn=20) == 33.0
    assert get_need(agent, "thirst", world_turn=20) == 22.0
    assert get_need(agent, "sleepiness", world_turn=20) == 11.0


def test_get_need_readonly_does_not_create_needs_state_for_legacy_agent():
    agent = {"hunger": 12.0, "thirst": 34.0, "sleepiness": 56.0}

    hunger = get_need_readonly(agent, "hunger", world_turn=100)

    assert hunger == 12.0
    assert "needs_state" not in agent


def test_get_need_readonly_uses_needs_state_projection_without_mutation():
    agent = {"hunger": 10.0, "thirst": 20.0, "sleepiness": 30.0}
    ensure_needs_state(agent, world_turn=100)

    before = {
        "hunger": dict(agent["needs_state"]["hunger"]),
        "thirst": dict(agent["needs_state"]["thirst"]),
        "sleepiness": dict(agent["needs_state"]["sleepiness"]),
        "revision": agent["needs_state"]["revision"],
    }
    hunger = get_need_readonly(agent, "hunger", world_turn=160)

    assert hunger > 10.0
    assert agent["needs_state"] == before


from app.games.zone_stalkers.needs.lazy_needs import project_needs, schedule_need_thresholds
from app.games.zone_stalkers.runtime.task_processor import (
    process_due_tasks,
    interrupt_action,
)
from app.games.zone_stalkers.runtime.scheduler import schedule_task
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER,
)


def _make_agent_with_lazy_needs(hunger=10.0, thirst=10.0, sleepiness=10.0, world_turn=100):
    agent = {
        "id": "agent_test",
        "is_alive": True,
        "hp": 100,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": sleepiness,
    }
    ensure_needs_state(agent, world_turn)
    return agent


def test_need_threshold_crossed_interrupts_action():
    """Setup agent with travel action + needs_state at hunger=85 (above critical 80).
    Schedule a need_threshold_crossed task for hunger/critical.
    Call process_due_tasks - verify interrupt_action was called (scheduled_action cleared)."""
    world_turn = 100
    agent = _make_agent_with_lazy_needs(hunger=85.0, world_turn=world_turn)
    agent["scheduled_action"] = {
        "type": "travel",
        "revision": 1,
        "interruptible": True,
        "ends_turn": world_turn + 10,
    }

    state = {
        "agents": {"agent_test": agent},
        "scheduled_tasks": {},
    }

    revision = agent["needs_state"]["revision"]
    schedule_task(state, runtime=None, turn=world_turn, task={
        "kind": "need_threshold_crossed",
        "agent_id": "agent_test",
        "need": "hunger",
        "threshold": "critical",
        "needs_revision": revision,
    })

    task_events, _ = process_due_tasks(state, runtime=None, world_turn=world_turn)

    # The threshold_crossed event should be there
    event_types = [e["event_type"] for e in task_events]
    assert "need_threshold_crossed" in event_types

    # Travel should have been interrupted
    assert agent.get("scheduled_action") is None


def test_drinking_resets_thirst_and_invalidates_old_threshold_tasks():
    """Agent with thirst at 75, revision=1, has a threshold task at some turn.
    Call set_need(agent, "thirst", 5, world_turn) which bumps revision to 2.
    Fire the old threshold task (still has needs_revision=1).
    Verify task is ignored (revision mismatch)."""
    world_turn = 100
    agent = _make_agent_with_lazy_needs(thirst=75.0, world_turn=world_turn)
    state = {
        "agents": {"agent_test": agent},
        "scheduled_tasks": {},
    }

    old_revision = agent["needs_state"]["revision"]
    # Old threshold task with old revision
    schedule_task(state, runtime=None, turn=world_turn, task={
        "kind": "need_threshold_crossed",
        "agent_id": "agent_test",
        "need": "thirst",
        "threshold": "critical",
        "needs_revision": old_revision,
    })

    # Agent drinks (resets thirst, bumps revision)
    set_need(agent, "thirst", 5.0, world_turn)
    new_revision = agent["needs_state"]["revision"]
    assert new_revision == old_revision + 1

    # Fire the old threshold task
    task_events, _ = process_due_tasks(state, runtime=None, world_turn=world_turn)

    # Task should be ignored (no events emitted for this stale task)
    event_types = [e["event_type"] for e in task_events]
    assert "need_threshold_crossed" not in event_types


def test_critical_need_damage_task_damages_agent():
    """Agent with hunger at 90 (above CRITICAL_HUNGER_THRESHOLD=80), HP=100.
    needs_state with revision=1.
    Schedule a need_damage task for hunger/revision=1.
    Call process_due_tasks.
    Verify HP decreased by HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER.
    Verify a new need_damage task was rescheduled."""
    world_turn = 100
    agent = _make_agent_with_lazy_needs(hunger=90.0, world_turn=world_turn)
    agent["hp"] = 100

    state = {
        "agents": {"agent_test": agent},
        "scheduled_tasks": {},
    }

    revision = agent["needs_state"]["revision"]
    schedule_task(state, runtime=None, turn=world_turn, task={
        "kind": "need_damage",
        "agent_id": "agent_test",
        "need": "hunger",
        "needs_revision": revision,
    })

    task_events, _ = process_due_tasks(state, runtime=None, world_turn=world_turn)

    # HP should have decreased
    assert agent.get("hp") == 100 - HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER

    # A new need_damage task should be rescheduled
    future_tasks = state.get("scheduled_tasks", {})
    next_turn_key = str(world_turn + 60)
    assert next_turn_key in future_tasks, f"Expected reschedule at {world_turn + 60}, got keys: {list(future_tasks.keys())}"
    rescheduled = future_tasks[next_turn_key]
    assert any(t.get("kind") == "need_damage" for t in rescheduled)

    # Damage event should be in events
    event_types = [e["event_type"] for e in task_events]
    assert "critical_need_damage" in event_types


def test_projection_still_exposes_lazy_hunger_thirst_sleepiness():
    """Agent with needs_state, hunger base=5, world_turn=100+60 later.
    project_needs(agent, world_turn) returns a dict with hunger > 5."""
    agent = _make_agent_with_lazy_needs(hunger=5.0, world_turn=100)

    # Project at 60 turns later
    projected = project_needs(agent, world_turn=160)
    assert projected["hunger"] > 5.0, f"Expected projected hunger > 5, got {projected['hunger']}"


def test_projection_does_not_mutate_state_when_projecting_lazy_needs():
    """Agent with needs_state.
    Call project_needs(agent, world_turn).
    Verify agent["hunger"] etc are unchanged (not mutated)."""
    agent = _make_agent_with_lazy_needs(hunger=10.0, thirst=20.0, sleepiness=30.0, world_turn=100)
    original_hunger = agent["hunger"]
    original_thirst = agent["thirst"]
    original_sleepiness = agent["sleepiness"]
    original_revision = agent["needs_state"]["revision"]

    _ = project_needs(agent, world_turn=200)

    # agent fields must not be mutated
    assert agent["hunger"] == original_hunger
    assert agent["thirst"] == original_thirst
    assert agent["sleepiness"] == original_sleepiness
    assert agent["needs_state"]["revision"] == original_revision


def test_delta_includes_lazy_needs_for_dirty_agent():
    """Build old_state with agent having hunger=10, thirst=20.
    Build new_state with agent having needs_state (lazy) but same raw hunger=10.
    new_state agent has a dirty flag or hp change.
    build_zone_delta should include derived hunger/thirst in agent_changes."""
    from app.games.zone_stalkers.delta import build_zone_delta

    world_turn = 200
    agent_id = "agent_test"

    old_agent = {
        "id": agent_id,
        "hunger": 10.0,
        "thirst": 20.0,
        "sleepiness": 5.0,
        "hp": 100,
        "is_alive": True,
    }

    new_agent = {
        "id": agent_id,
        "hunger": 10.0,
        "thirst": 20.0,
        "sleepiness": 5.0,
        "hp": 90,  # Changed — this will trigger the diff
        "is_alive": True,
        "needs_state": {
            "hunger": {"base": 10.0, "updated_turn": 100},
            "thirst": {"base": 20.0, "updated_turn": 100},
            "sleepiness": {"base": 5.0, "updated_turn": 100},
            "revision": 1,
        },
    }

    old_state = {"agents": {agent_id: old_agent}, "locations": {}, "traders": {},
                 "state_revision": 1, "world_turn": world_turn}
    new_state = {"agents": {agent_id: new_agent}, "locations": {}, "traders": {},
                 "state_revision": 2, "world_turn": world_turn}

    delta = build_zone_delta(old_state=old_state, new_state=new_state, events=[])

    agent_delta = delta["changes"]["agents"].get(agent_id)
    assert agent_delta is not None, "Expected agent in delta changes"
    # The derived needs should be projected (at world_turn=200, elapsed=100 turns from base updated_turn=100)
    # hunger rate = 3/60 per turn, elapsed=100 => hunger = 10 + 100*(3/60) = 10 + 5 = 15
    assert agent_delta["hunger"] > 10.0, f"Expected derived hunger > 10, got {agent_delta['hunger']}"


def test_delta_does_not_emit_only_time_based_lazy_growth_without_hot_field_change():
    """Documented PR3 minimal behavior: derived needs are sent only when agent is already dirty."""
    from app.games.zone_stalkers.delta import build_zone_delta

    agent_id = "agent_test"
    old_state = {
        "agents": {
            agent_id: {
                "id": agent_id,
                "hunger": 10.0,
                "thirst": 20.0,
                "sleepiness": 5.0,
                "hp": 100,
                "is_alive": True,
                "needs_state": {
                    "hunger": {"base": 10.0, "updated_turn": 100},
                    "thirst": {"base": 20.0, "updated_turn": 100},
                    "sleepiness": {"base": 5.0, "updated_turn": 100},
                    "revision": 1,
                },
            }
        },
        "locations": {},
        "traders": {},
        "state_revision": 1,
        "world_turn": 100,
    }
    new_state = {
        "agents": {
            agent_id: {
                "id": agent_id,
                "hunger": 10.0,
                "thirst": 20.0,
                "sleepiness": 5.0,
                "hp": 100,
                "is_alive": True,
                "needs_state": {
                    "hunger": {"base": 10.0, "updated_turn": 100},
                    "thirst": {"base": 20.0, "updated_turn": 100},
                    "sleepiness": {"base": 5.0, "updated_turn": 100},
                    "revision": 1,
                },
            }
        },
        "locations": {},
        "traders": {},
        "state_revision": 2,
        "world_turn": 160,
    }
    delta = build_zone_delta(old_state=old_state, new_state=new_state, events=[])
    assert agent_id not in delta["changes"]["agents"]


def test_debug_map_lite_projection_uses_lazy_derived_needs():
    from app.games.zone_stalkers.projections import project_zone_state

    state = {
        "world_turn": 160,
        "agents": {
            "agent_test": {
                "id": "agent_test",
                "hunger": 10.0,
                "thirst": 20.0,
                "sleepiness": 5.0,
                "needs_state": {
                    "hunger": {"base": 10.0, "updated_turn": 100},
                    "thirst": {"base": 20.0, "updated_turn": 100},
                    "sleepiness": {"base": 5.0, "updated_turn": 100},
                    "revision": 1,
                },
            }
        },
        "traders": {},
        "locations": {},
    }
    projected = project_zone_state(state=state, mode="debug-map-lite")
    agent = projected["agents"]["agent_test"]
    assert agent["hunger"] > 10.0


def test_lazy_consume_reschedules_threshold_tasks_immediately():
    from app.games.zone_stalkers.rules.tick_rules import _bot_consume

    world_turn = 100
    agent_id = "agent_test"
    agent = {
        "id": agent_id,
        "is_alive": True,
        "hunger": 80.0,
        "thirst": 10.0,
        "sleepiness": 10.0,
        "inventory": [{"id": "food_1", "type": "bread", "name": "Буханка хлеба"}],
        "memory": [],
    }
    ensure_needs_state(agent, world_turn)
    state = {
        "agents": {agent_id: agent},
        "locations": {},
        "scheduled_tasks": {},
        "cpu_lazy_needs_enabled": True,
    }

    _bot_consume(agent_id, agent, agent["inventory"][0], world_turn, state, action_kind="consume_food")

    assert any(
        task.get("kind") == "need_threshold_crossed" and task.get("agent_id") == agent_id
        for bucket in state["scheduled_tasks"].values()
        for task in bucket
    )
