"""Tests for TickRuntime and dirty helpers (CPU PR1)."""
import pytest
from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime
from app.games.zone_stalkers.runtime.dirty import (
    mark_agent_dirty,
    mark_location_dirty,
    mark_trader_dirty,
    mark_state_dirty,
    set_agent_field,
    set_location_field,
    set_trader_field,
    set_state_field,
)


def test_dirty_runtime_starts_empty():
    rt = TickRuntime()
    assert rt.dirty_agents == set()
    assert rt.dirty_locations == set()
    assert rt.dirty_traders == set()
    assert rt.dirty_state_fields == set()
    assert rt.events == []
    assert rt.profiler is None


def test_mark_agent_dirty():
    rt = TickRuntime()
    mark_agent_dirty(rt, "agent_1")
    assert "agent_1" in rt.dirty_agents
    mark_agent_dirty(rt, "agent_2")
    assert len(rt.dirty_agents) == 2


def test_mark_location_dirty():
    rt = TickRuntime()
    mark_location_dirty(rt, "loc_A")
    assert "loc_A" in rt.dirty_locations


def test_mark_trader_dirty():
    rt = TickRuntime()
    mark_trader_dirty(rt, "trader_x")
    assert "trader_x" in rt.dirty_traders


def test_mark_state_dirty():
    rt = TickRuntime()
    mark_state_dirty(rt, "world_turn")
    assert "world_turn" in rt.dirty_state_fields


def test_mark_dirty_none_runtime_is_noop():
    # Should not raise when runtime is None
    mark_agent_dirty(None, "agent_1")
    mark_location_dirty(None, "loc_A")
    mark_trader_dirty(None, "t1")
    mark_state_dirty(None, "field")


def test_mark_dirty_none_id_is_noop():
    rt = TickRuntime()
    mark_agent_dirty(rt, None)
    assert rt.dirty_agents == set()


def test_set_agent_field_marks_dirty_on_change():
    state = {"agents": {"a1": {"hp": 100, "hunger": 0}}}
    rt = TickRuntime()
    changed = set_agent_field(state, rt, "a1", "hp", 50)
    assert changed is True
    assert state["agents"]["a1"]["hp"] == 50
    assert "a1" in rt.dirty_agents


def test_set_agent_field_no_change_no_dirty():
    state = {"agents": {"a1": {"hp": 100}}}
    rt = TickRuntime()
    changed = set_agent_field(state, rt, "a1", "hp", 100)
    assert changed is False
    assert rt.dirty_agents == set()


def test_set_agent_field_missing_agent_returns_false():
    state = {"agents": {}}
    rt = TickRuntime()
    changed = set_agent_field(state, rt, "missing", "hp", 50)
    assert changed is False


def test_set_location_field():
    state = {"locations": {"L1": {"anomaly_activity": 3}}}
    rt = TickRuntime()
    changed = set_location_field(state, rt, "L1", "anomaly_activity", 7)
    assert changed is True
    assert "L1" in rt.dirty_locations
    # No change
    changed2 = set_location_field(state, rt, "L1", "anomaly_activity", 7)
    assert changed2 is False


def test_set_trader_field():
    state = {"traders": {"t1": {"money": 500}}}
    rt = TickRuntime()
    changed = set_trader_field(state, rt, "t1", "money", 600)
    assert changed is True
    assert "t1" in rt.dirty_traders


def test_set_state_field():
    state = {"world_turn": 10}
    rt = TickRuntime()
    changed = set_state_field(state, rt, "world_turn", 11)
    assert changed is True
    assert state["world_turn"] == 11
    assert "world_turn" in rt.dirty_state_fields


def test_dirty_runtime_to_debug_counters():
    rt = TickRuntime()
    rt.dirty_agents = {"a1", "a2"}
    rt.dirty_locations = {"L1"}
    counters = rt.to_debug_counters()
    assert counters["dirty_agents_count"] == 2
    assert counters["dirty_locations_count"] == 1
    assert counters["dirty_traders_count"] == 0
    assert counters["dirty_state_fields_count"] == 0


def test_dirty_runtime_not_persisted_in_state():
    """TickRuntime must not have any db/state save method."""
    rt = TickRuntime()
    assert not hasattr(rt, "save")
    assert not hasattr(rt, "persist")
    assert not hasattr(rt, "to_state_blob")
