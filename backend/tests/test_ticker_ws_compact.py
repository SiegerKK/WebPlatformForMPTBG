"""Tests for compact WS tick payload helpers (Change A)."""
from __future__ import annotations

from app.core.ticker.service import (
    WS_TICK_EVENT_PREVIEW_LIMIT,
    _compact_event_payload,
    _compact_tick_event,
)


def test_compact_event_payload_keeps_allowed_fields():
    payload = {
        "agent_id": "a1",
        "location_id": "loc_a",
        "world_turn": 5,
        "objective_key": "TRACK_TARGET",
        "action_kind": "search",
        "summary": "searching near C1",
        # Heavy fields that must be stripped:
        "full_memory": [{"kind": "obs"}] * 100,
        "brain_trace": {"events": [{"k": "v"}]},
        "debug_block": {"size": 99999},
    }
    compact = _compact_event_payload(payload)
    assert compact["agent_id"] == "a1"
    assert compact["location_id"] == "loc_a"
    assert compact["world_turn"] == 5
    assert compact["objective_key"] == "TRACK_TARGET"
    assert compact["action_kind"] == "search"
    assert compact["summary"] == "searching near C1"
    assert "full_memory" not in compact
    assert "brain_trace" not in compact
    assert "debug_block" not in compact


def test_compact_event_payload_missing_fields_skipped():
    payload = {"agent_id": "a1", "summary": "hello"}
    compact = _compact_event_payload(payload)
    assert compact == {"agent_id": "a1", "summary": "hello"}


def test_compact_tick_event_keeps_event_type():
    event = {
        "event_type": "npc_decision",
        "payload": {
            "agent_id": "a1",
            "location_id": "loc_a",
            "world_turn": 5,
            "objective_key": "TRACK_TARGET",
            "action_kind": "search",
            "summary": "searching",
            "full_memory": [{"kind": "obs"}] * 100,
            "brain_trace": {"events": []},
        }
    }
    compact = _compact_tick_event(event)
    assert compact["event_type"] == "npc_decision"
    assert "full_memory" not in compact["payload"]
    assert "brain_trace" not in compact["payload"]
    assert compact["payload"]["agent_id"] == "a1"
    assert compact["payload"]["action_kind"] == "search"


def test_compact_tick_event_handles_missing_payload():
    event = {"event_type": "world_turn_advanced"}
    compact = _compact_tick_event(event)
    assert compact["event_type"] == "world_turn_advanced"
    assert compact["payload"] == {}


def test_ws_tick_event_preview_limit_is_10():
    assert WS_TICK_EVENT_PREVIEW_LIMIT == 10


def test_compact_payload_with_all_allowed_fields():
    payload = {
        "agent_id": "a2",
        "location_id": "G3",
        "world_turn": 42,
        "objective_key": "HUNT_TARGET",
        "action_kind": "move",
        "summary": "moving to G3",
    }
    compact = _compact_event_payload(payload)
    assert compact == payload  # all 6 fields kept, nothing added/removed
