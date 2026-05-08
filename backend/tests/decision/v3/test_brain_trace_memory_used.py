"""Tests for brain_trace memory_used field (PR 3)."""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.decision.debug.brain_trace import (
    append_brain_trace_event,
    write_decision_brain_trace_from_v2,
)


def _make_memory_used(n: int) -> list[dict]:
    return [
        {
            "id": f"mem_{i}",
            "kind": "trader_location_known",
            "summary": f"Trader at loc_{i}",
            "confidence": 0.9,
            "used_for": "find_trader",
        }
        for i in range(n)
    ]


def test_memory_used_stored_in_brain_trace_event() -> None:
    agent: dict = {}
    mem_used = _make_memory_used(3)
    append_brain_trace_event(
        agent,
        world_turn=100,
        mode="decision",
        decision="new_intent",
        summary="intent decided",
        memory_used=mem_used,
    )
    events = agent["brain_trace"]["events"]
    assert len(events) == 1
    assert "memory_used" in events[0]
    assert len(events[0]["memory_used"]) == 3
    assert events[0]["memory_used"][0]["used_for"] == "find_trader"


def test_memory_used_capped_at_five() -> None:
    agent: dict = {}
    mem_used = _make_memory_used(8)
    append_brain_trace_event(
        agent,
        world_turn=100,
        mode="decision",
        decision="new_intent",
        summary="intent",
        memory_used=mem_used,
    )
    events = agent["brain_trace"]["events"]
    assert len(events[0]["memory_used"]) <= 5


def test_memory_used_not_added_when_empty() -> None:
    agent: dict = {}
    append_brain_trace_event(
        agent,
        world_turn=100,
        mode="decision",
        decision="new_intent",
        summary="intent",
        memory_used=None,
    )
    events = agent["brain_trace"]["events"]
    assert "memory_used" not in events[0]


def test_write_decision_brain_trace_passes_memory_used() -> None:
    agent: dict = {}
    mem_used = _make_memory_used(2)
    write_decision_brain_trace_from_v2(
        agent,
        world_turn=50,
        intent_kind="seek_water",
        intent_score=0.85,
        reason="critical_thirst",
        memory_used=mem_used,
    )
    events = agent["brain_trace"]["events"]
    assert "memory_used" in events[-1]
    assert len(events[-1]["memory_used"]) == 2


def test_memory_used_contains_required_fields() -> None:
    agent: dict = {}
    mem_used = [
        {
            "id": "mem_001",
            "kind": "emission_warning",
            "summary": "Опасность выброса",
            "confidence": 0.95,
            "used_for": "avoid_threat",
        }
    ]
    append_brain_trace_event(
        agent,
        world_turn=100,
        mode="decision",
        decision="new_intent",
        summary="flee",
        memory_used=mem_used,
    )
    mu = agent["brain_trace"]["events"][0]["memory_used"][0]
    assert mu["id"] == "mem_001"
    assert mu["kind"] == "emission_warning"
    assert mu["summary"] == "Опасность выброса"
    assert mu["confidence"] == 0.95
    assert mu["used_for"] == "avoid_threat"


def test_brain_trace_memory_used_accepts_target_used_for_values() -> None:
    agent: dict = {}
    target_values = [
        "locate_target",
        "track_target",
        "prepare_for_hunt",
        "engage_target",
        "confirm_kill",
    ]
    mem_used = [
        {
            "id": f"mem_t_{i}",
            "kind": "target_seen",
            "summary": "Цель замечена",
            "confidence": 0.8,
            "used_for": used_for,
        }
        for i, used_for in enumerate(target_values)
    ]

    append_brain_trace_event(
        agent,
        world_turn=200,
        mode="decision",
        decision="new_intent",
        summary="hunt",
        memory_used=mem_used,
    )

    stored = agent["brain_trace"]["events"][-1]["memory_used"]
    assert [m["used_for"] for m in stored] == target_values
