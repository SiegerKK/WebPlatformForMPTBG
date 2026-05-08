"""Tests for MemoryStore v3 decay and consolidation (PR 3)."""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_EPISODIC, LAYER_SEMANTIC, LAYER_THREAT
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from app.games.zone_stalkers.memory.decay import decay_memory, DECAY_CADENCE_TURNS, _CONSOLIDATION_MIN_OBSERVATIONS


def _add(agent: dict, record_id: str, **kwargs) -> None:
    defaults = dict(
        agent_id="bot1",
        layer=LAYER_EPISODIC,
        kind="test",
        created_turn=1,
        last_accessed_turn=None,
        summary="s",
        details={},
        importance=0.1,
        confidence=0.1,
        emotional_weight=0.0,
        status="active",
    )
    defaults.update(kwargs)
    rec = MemoryRecord(id=record_id, **defaults)
    add_memory_record(agent, rec)


def test_decay_archives_low_value_old_records() -> None:
    agent: dict = {}
    _add(agent, "old_low", created_turn=1, importance=0.1, confidence=0.1)
    # Run decay at turn 300 (age = 299 >> _ARCHIVE_MIN_AGE_TURNS=200).
    decay_memory(agent, world_turn=300)
    status = agent["memory_v3"]["records"]["old_low"]["status"]
    assert status == "archived"


def test_decay_keeps_semantic_records_active() -> None:
    agent: dict = {}
    _add(agent, "sem", layer=LAYER_SEMANTIC, created_turn=1, importance=0.1, confidence=0.1)
    decay_memory(agent, world_turn=300)
    status = agent["memory_v3"]["records"]["sem"]["status"]
    assert status != "archived"


def test_decay_keeps_threat_records_active() -> None:
    agent: dict = {}
    _add(agent, "threat", layer=LAYER_THREAT, created_turn=1, importance=0.1, confidence=0.1)
    decay_memory(agent, world_turn=300)
    status = agent["memory_v3"]["records"]["threat"]["status"]
    assert status != "archived"


def test_decay_skips_if_cadence_not_met() -> None:
    agent: dict = {}
    _add(agent, "rec", created_turn=1)
    decay_memory(agent, world_turn=300)
    # Second call immediately after should skip.
    agent["memory_v3"]["records"]["rec"]["status"] = "active"  # Reset.
    decay_memory(agent, world_turn=301)
    # Since cadence not met, decay didn't run, status unchanged.
    status = agent["memory_v3"]["records"]["rec"]["status"]
    assert status == "active"


def test_decay_updates_last_decay_turn() -> None:
    agent: dict = {}
    decay_memory(agent, world_turn=200)
    assert agent["memory_v3"]["stats"]["last_decay_turn"] == 200


def test_consolidation_creates_semantic_record() -> None:
    """3+ episodic records of same (kind, location) → semantic record created."""
    agent: dict = {}
    for i in range(_CONSOLIDATION_MIN_OBSERVATIONS):
        _add(
            agent,
            f"trade_{i}",
            layer=LAYER_EPISODIC,
            kind="trader_visited",
            location_id="loc_bunker",
            created_turn=i + 1,
            importance=0.5,
            confidence=0.8,
        )
    decay_memory(agent, world_turn=300)
    records = agent["memory_v3"]["records"]
    semantic_records = [
        d for d in records.values()
        if d.get("layer") == LAYER_SEMANTIC and d.get("kind") == "semantic_trader_visited"
    ]
    assert len(semantic_records) >= 1
    assert semantic_records[0]["location_id"] == "loc_bunker"


def test_consolidation_does_not_trigger_below_threshold() -> None:
    """Less than 3 observations do NOT produce a semantic record."""
    agent: dict = {}
    for i in range(_CONSOLIDATION_MIN_OBSERVATIONS - 1):
        _add(
            agent,
            f"trade_{i}",
            layer=LAYER_EPISODIC,
            kind="trader_visited",
            location_id="loc_bunker",
            created_turn=i + 1,
        )
    decay_memory(agent, world_turn=300)
    records = agent["memory_v3"]["records"]
    semantic_records = [
        d for d in records.values()
        if d.get("layer") == LAYER_SEMANTIC
    ]
    assert len(semantic_records) == 0
