"""Tests for build_zone_delta_from_dirty (CPU PR1)."""
import pytest
from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime
from app.games.zone_stalkers.delta_dirty import (
    build_zone_delta_from_dirty,
    should_use_dirty_delta,
)


def _make_state(**kwargs):
    base = {
        "state_revision": 5,
        "world_turn": 10,
        "world_day": 1,
        "world_hour": 8,
        "world_minute": 0,
        "agents": {},
        "locations": {},
        "traders": {},
    }
    base.update(kwargs)
    return base


def _make_agent(agent_id, **kwargs):
    defaults = {
        "id": agent_id,
        "location_id": "loc_A",
        "is_alive": True,
        "has_left_zone": False,
        "hp": 100,
        "hunger": 0,
        "thirst": 0,
        "sleepiness": 0,
        "money": 0,
        "current_goal": None,
        "global_goal": None,
        "action_used": False,
        "scheduled_action": None,
        "active_plan_v3": None,
        "equipment": None,
        "inventory": [],
    }
    defaults.update(kwargs)
    return defaults


def test_dirty_delta_contains_only_dirty_agent():
    agent = _make_agent("a1", hp=80)
    state = _make_state(agents={"a1": agent, "a2": _make_agent("a2", hp=100)})
    rt = TickRuntime()
    rt.dirty_agents.add("a1")

    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=[])
    changes = delta["changes"]
    assert "a1" in changes["agents"]
    assert "a2" not in changes["agents"]


def test_dirty_delta_falls_back_when_runtime_empty():
    """When runtime has no dirty sets and old_state differs, falls back to full diff."""
    old_state = _make_state(agents={"a1": _make_agent("a1", hp=100)})
    new_state = _make_state(agents={"a1": _make_agent("a1", hp=80)})
    rt = TickRuntime()  # empty dirty sets

    delta = build_zone_delta_from_dirty(
        state=new_state,
        runtime=rt,
        events=[],
        old_state=old_state,
    )
    # Falls back to full diff which should catch the change
    assert "a1" in delta["changes"]["agents"]


def test_dirty_delta_world_time_included():
    state = _make_state(world_turn=42, world_day=2, world_hour=14, world_minute=30)
    rt = TickRuntime()
    rt.dirty_agents.add("x")  # need at least one dirty thing
    state["agents"]["x"] = _make_agent("x")

    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=[])
    assert delta["world"]["world_turn"] == 42
    assert delta["world"]["world_day"] == 2
    assert delta["world"]["world_hour"] == 14
    assert delta["world"]["world_minute"] == 30


def test_dirty_delta_trader_changes():
    trader = {"location_id": "loc_B", "is_alive": True, "money": 1000, "inventory": [], "prices": {}}
    state = _make_state(traders={"t1": trader})
    rt = TickRuntime()
    rt.dirty_traders.add("t1")

    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=[])
    assert "t1" in delta["changes"]["traders"]
    assert delta["changes"]["traders"]["t1"]["money"] == 1000


def test_dirty_delta_location_changes():
    loc = {"agents": ["a1"], "artifacts": [], "items": [], "anomaly_activity": 5, "dominant_anomaly_type": "fire"}
    state = _make_state(locations={"L1": loc})
    rt = TickRuntime()
    rt.dirty_locations.add("L1")

    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=[])
    assert "L1" in delta["changes"]["locations"]


def test_dirty_delta_revision_fields():
    state = _make_state(state_revision=7)
    rt = TickRuntime()
    rt.dirty_agents.add("a1")
    state["agents"]["a1"] = _make_agent("a1")

    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=[])
    assert delta["revision"] == 7


def test_dirty_delta_base_revision_uses_old_state_revision():
    old_state = _make_state(state_revision=41, agents={"a1": _make_agent("a1", hp=100)})
    new_state = _make_state(state_revision=42, agents={"a1": _make_agent("a1", hp=90)})
    rt = TickRuntime()
    rt.dirty_agents.add("a1")

    delta = build_zone_delta_from_dirty(
        state=new_state,
        runtime=rt,
        events=[],
        old_state=old_state,
    )
    assert delta["base_revision"] == 41
    assert delta["revision"] == 42


def test_dirty_delta_events_preview():
    state = _make_state()
    rt = TickRuntime()
    rt.dirty_agents.add("a1")
    state["agents"]["a1"] = _make_agent("a1")
    events = [
        {"event_type": "world_turn_advanced", "payload": {"agent_id": None, "summary": "tick"}},
    ]
    delta = build_zone_delta_from_dirty(state=state, runtime=rt, events=events)
    assert delta["events"]["count"] == 1
    assert len(delta["events"]["preview"]) == 1


def test_should_use_dirty_delta_true():
    rt = TickRuntime()
    rt.dirty_agents.add("a1")
    assert should_use_dirty_delta(rt) is True


def test_should_use_dirty_delta_false_when_empty():
    rt = TickRuntime()
    assert should_use_dirty_delta(rt) is False


def test_should_use_dirty_delta_false_when_none():
    assert should_use_dirty_delta(None) is False
