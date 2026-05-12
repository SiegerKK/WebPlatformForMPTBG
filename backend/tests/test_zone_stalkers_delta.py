from __future__ import annotations
import pytest
from app.games.zone_stalkers.delta import (
    build_zone_delta,
    compact_agent_for_delta,
    compact_location_for_delta,
    WS_EVENT_PREVIEW_LIMIT,
)

def _make_agent(location_id="loc_a", hp=100, **kwargs):
    return {
        "id": "a1", "name": "Test", "archetype": "stalker_agent",
        "location_id": location_id, "is_alive": True, "has_left_zone": False,
        "hp": hp, "hunger": 20, "thirst": 30, "sleepiness": 10, "money": 500,
        "current_goal": "survive", "global_goal": "survive", "action_used": False,
        **kwargs,
    }

def _make_location(agents=None, artifacts=None, items=None):
    return {
        "id": "loc_a", "name": "Test Loc", "agents": agents or [],
        "artifacts": artifacts or [], "items": items or [],
        "anomaly_activity": 0.1, "connections": [],
    }

def _base_state(agents=None, locations=None, traders=None, **kwargs):
    return {
        "context_type": "zone_map",
        "world_turn": 10, "world_day": 1, "world_hour": 6, "world_minute": 0,
        "state_revision": 5,
        "agents": agents or {"a1": _make_agent()},
        "locations": locations or {"loc_a": _make_location(agents=["a1"])},
        "traders": traders or {},
        **kwargs,
    }


def test_zone_delta_includes_changed_agent_location():
    old = _base_state()
    new = _base_state()
    new["agents"]["a1"]["location_id"] = "loc_b"
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert "a1" in delta["changes"]["agents"]
    assert delta["changes"]["agents"]["a1"]["location_id"] == "loc_b"


def test_zone_delta_unchanged_agent_not_included():
    old = _base_state()
    new = _base_state()
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert "a1" not in delta["changes"]["agents"]


def test_zone_delta_does_not_include_memory_or_brain_trace():
    old = _base_state()
    new = _base_state()
    new["agents"]["a1"]["location_id"] = "loc_b"
    new["agents"]["a1"]["memory"] = [{"kind": "obs"}] * 100
    new["agents"]["a1"]["brain_trace"] = {"events": [{"k": "v"}] * 50}
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    agent_patch = delta["changes"]["agents"].get("a1", {})
    assert "memory" not in agent_patch
    assert "brain_trace" not in agent_patch
    assert "memory_v3" not in agent_patch


def test_zone_delta_includes_world_time():
    old = _base_state()
    new = _base_state()
    new["world_turn"] = 11
    new["world_minute"] = 1
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert delta["world"]["world_turn"] == 11
    assert delta["world"]["world_minute"] == 1


def test_zone_delta_limits_event_preview():
    old = _base_state()
    new = _base_state()
    new["state_revision"] = 6
    events = [{"event_type": f"ev_{i}", "payload": {}} for i in range(20)]
    delta = build_zone_delta(old_state=old, new_state=new, events=events)
    assert delta["events"]["count"] == 20
    assert len(delta["events"]["preview"]) <= WS_EVENT_PREVIEW_LIMIT


def test_zone_delta_revision_fields():
    old = _base_state()
    new = _base_state()
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert delta["base_revision"] == 5
    assert delta["revision"] == 6


def test_zone_delta_includes_location_change():
    old = _base_state()
    new = _base_state()
    new["locations"]["loc_a"]["agents"] = ["a1", "a2"]
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert "loc_a" in delta["changes"]["locations"]
    assert "a2" in delta["changes"]["locations"]["loc_a"]["agents"]


def test_compact_agent_for_delta_excludes_heavy_fields():
    agent = _make_agent()
    legacy_key = "memory"
    agent[legacy_key] = [{"kind": "obs"}] * 100
    agent["brain_trace"] = {"events": []}
    agent["memory_v3"] = {"records": {}}
    patch = compact_agent_for_delta(agent)
    assert "memory" not in patch
    assert "brain_trace" not in patch
    assert "memory_v3" not in patch
    assert patch["location_id"] == "loc_a"


def test_zone_delta_state_changes_included():
    old = _base_state()
    new = _base_state()
    new["game_over"] = True
    new["state_revision"] = 6
    delta = build_zone_delta(old_state=old, new_state=new, events=[])
    assert delta["changes"]["state"].get("game_over") is True
