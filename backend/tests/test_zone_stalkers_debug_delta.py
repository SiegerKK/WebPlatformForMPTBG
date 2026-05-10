"""Tests for the zone_debug_delta builder."""
import pytest
from app.games.zone_stalkers.debug_delta import build_zone_debug_delta


def _make_debug_state(hunt_search=None, lht=None):
    return {
        "world_turn": 10,
        "state_revision": 5,
        "agents": {},
        "debug": {
            "hunt_search_by_agent": hunt_search or {},
            "location_hunt_traces": lht or {},
        },
    }


def test_debug_delta_filters_by_selected_hunter():
    old = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_id": "loc_A", "best_location_confidence": 0.8, "lead_count": 5}}
    )
    new = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_id": "loc_B", "best_location_confidence": 0.9, "lead_count": 6}}
    )
    old["state_revision"] = 5
    new["state_revision"] = 6
    sub = {"mode": "debug-map", "hunter_id": "a1", "target_id": "t1"}
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=1)
    assert delta is not None
    assert "a1" in delta["changes"]["hunt_search_by_agent"]
    assert delta["changes"]["hunt_search_by_agent"]["a1"]["best_location_id"] == "loc_B"
    assert delta["revision"] == 6
    assert delta["base_revision"] == 5


def test_debug_delta_returns_none_when_nothing_changed():
    state = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.8, "lead_count": 5}}
    )
    state["state_revision"] = 5
    sub = {"mode": "debug-map", "hunter_id": "a1", "target_id": "t1"}
    delta = build_zone_debug_delta(old_state=state, new_state=state, subscription=sub, debug_revision=1)
    assert delta is None


def test_debug_delta_does_not_include_full_memory():
    old = _make_debug_state()
    new = _make_debug_state()
    new["agents"]["a1"] = {
        "id": "a1",
        "memory": [{"kind": "obs"}] * 100,
        "brain_trace": {"events": []},
        "is_alive": True,
    }
    new["state_revision"] = 6
    old["state_revision"] = 5
    sub = {"mode": "agent-profile", "selected_agent_id": "a1"}
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=1)
    if delta and "selected_agent_profile_summary" in (delta.get("changes") or {}):
        assert "memory" not in delta["changes"]["selected_agent_profile_summary"]
        assert "brain_trace" not in delta["changes"]["selected_agent_profile_summary"]


def test_debug_delta_filters_location_traces_by_visible():
    old = _make_debug_state(
        lht={"loc_A": {"records": [{"hunter_id": "a1", "kind": "target_seen"}]}}
    )
    new = _make_debug_state(
        lht={"loc_A": {"records": [{"hunter_id": "a1", "kind": "target_seen"}, {"hunter_id": "a1", "kind": "target_moved"}]}}
    )
    old["state_revision"] = 5
    new["state_revision"] = 6
    sub = {"mode": "debug-map", "visible_location_ids": ["loc_A"], "hunter_id": "a1"}
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=1)
    assert delta is not None
    assert "loc_A" in delta["changes"]["location_hunt_traces"]


def test_debug_delta_filters_wrong_target():
    """If target_id filter doesn't match, compact entry should be excluded."""
    old = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.5, "lead_count": 1}}
    )
    new = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.9, "lead_count": 2}}
    )
    old["state_revision"] = 5
    new["state_revision"] = 6
    sub = {"mode": "debug-map", "hunter_id": "a1", "target_id": "t2"}  # different target
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=1)
    assert delta is None


def test_debug_delta_min_confidence_filter():
    old = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.1, "lead_count": 1}}
    )
    new = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.2, "lead_count": 2}}
    )
    old["state_revision"] = 5
    new["state_revision"] = 6
    sub = {"mode": "debug-map", "hunter_id": "a1", "min_confidence": 0.5}
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=1)
    assert delta is None  # Below min_confidence threshold


def test_debug_delta_scope_fields():
    old = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.8, "lead_count": 1}}
    )
    new = _make_debug_state(
        hunt_search={"a1": {"target_id": "t1", "best_location_confidence": 0.9, "lead_count": 2}}
    )
    old["state_revision"] = 3
    new["state_revision"] = 4
    sub = {"mode": "debug-map", "hunter_id": "a1", "target_id": "t1"}
    delta = build_zone_debug_delta(old_state=old, new_state=new, subscription=sub, debug_revision=7)
    assert delta is not None
    assert delta["debug_revision"] == 7
    assert delta["scope"]["mode"] == "debug-map"
    assert delta["scope"]["hunter_id"] == "a1"
    assert delta["scope"]["target_id"] == "t1"
