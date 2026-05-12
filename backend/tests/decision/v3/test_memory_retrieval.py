"""Tests for MemoryStore v3 retrieval scoring (PR 3)."""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.memory.models import MemoryRecord, MemoryQuery, LAYER_EPISODIC, LAYER_THREAT, LAYER_SEMANTIC
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from app.games.zone_stalkers.memory.retrieval import retrieve_memory


def _add(agent: dict, record_id: str, **kwargs) -> MemoryRecord:
    defaults = dict(
        agent_id="bot1",
        layer=LAYER_EPISODIC,
        kind="test",
        created_turn=100,
        last_accessed_turn=None,
        summary="summary",
        details={},
        importance=0.5,
        confidence=1.0,
        status="active",
    )
    defaults.update(kwargs)
    rec = MemoryRecord(id=record_id, **defaults)
    add_memory_record(agent, rec)
    return rec


def test_retrieve_by_tag_returns_expected() -> None:
    agent: dict = {}
    _add(agent, "m1", tags=("trader", "trade"))
    _add(agent, "m2", tags=("sleep", "rest"))
    results = retrieve_memory(agent, MemoryQuery(purpose="p", tags=("trader",)), world_turn=100)
    ids = [r.id for r in results]
    assert "m1" in ids
    assert "m2" not in ids


def test_retrieve_by_location_returns_expected() -> None:
    agent: dict = {}
    _add(agent, "m1", location_id="loc_a")
    _add(agent, "m2", location_id="loc_b")
    results = retrieve_memory(agent, MemoryQuery(purpose="p", location_id="loc_a"), world_turn=100)
    ids = [r.id for r in results]
    assert "m1" in ids
    assert "m2" not in ids


def test_retrieve_by_item_type() -> None:
    agent: dict = {}
    _add(agent, "m1", item_types=("bread",))
    _add(agent, "m2", item_types=("pistol",))
    results = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", item_types=("bread",)),
        world_turn=100,
    )
    ids = [r.id for r in results]
    assert "m1" in ids
    assert "m2" not in ids


def test_fresh_high_confidence_ranks_above_stale() -> None:
    agent: dict = {}
    _add(agent, "stale", created_turn=1, confidence=0.9, status="stale", tags=("trader",))
    _add(agent, "fresh", created_turn=100, confidence=0.9, status="active", tags=("trader",))
    results = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", tags=("trader",), include_stale=True),
        world_turn=100,
    )
    assert results[0].id == "fresh"


def test_stale_excluded_by_default() -> None:
    agent: dict = {}
    _add(agent, "stale", status="stale", tags=("trader",))
    results = retrieve_memory(agent, MemoryQuery(purpose="p", tags=("trader",)), world_turn=100)
    assert all(r.id != "stale" for r in results)


def test_max_results_cap_enforced() -> None:
    agent: dict = {}
    for i in range(20):
        _add(agent, f"m{i}", tags=("item",), created_turn=100 + i)
    results = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", tags=("item",), max_results=5),
        world_turn=120,
    )
    assert len(results) <= 5


def test_results_are_deterministic() -> None:
    """Same query on same state must return identical ordered results."""
    agent: dict = {}
    for i in range(10):
        _add(agent, f"m{i}", tags=("combat",), created_turn=100 + i, confidence=0.8)
    q = MemoryQuery(purpose="p", tags=("combat",), max_results=5)
    r1 = [r.id for r in retrieve_memory(agent, q, world_turn=110)]
    r2 = [r.id for r in retrieve_memory(agent, q, world_turn=110)]
    assert r1 == r2


def test_retrieve_by_layer() -> None:
    agent: dict = {}
    _add(agent, "threat_m", layer=LAYER_THREAT, tags=("danger",))
    _add(agent, "epis_m", layer=LAYER_EPISODIC, tags=("danger",))
    results = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", layers=(LAYER_THREAT,), tags=("danger",)),
        world_turn=100,
    )
    ids = [r.id for r in results]
    assert "threat_m" in ids
    assert "epis_m" not in ids


def test_retrieval_max_cap_global_hard_limit() -> None:
    """max_results > global cap is clamped to global cap."""
    from app.games.zone_stalkers.memory.store import MEMORY_V3_RETRIEVAL_MAX_RESULTS
    agent: dict = {}
    for i in range(60):
        _add(agent, f"m{i}", tags=("item",), created_turn=i)
    results = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", tags=("item",), max_results=100),
        world_turn=60,
    )
    assert len(results) <= MEMORY_V3_RETRIEVAL_MAX_RESULTS


def test_retrieve_memory_updates_last_accessed_turn() -> None:
    agent: dict = {}
    _add(agent, "m1", tags=("trader",), last_accessed_turn=None)
    _ = retrieve_memory(agent, MemoryQuery(purpose="p", tags=("trader",)), world_turn=123)
    assert agent["memory_v3"]["records"]["m1"]["last_accessed_turn"] is None


def test_retrieve_memory_updates_last_accessed_turn_when_track_access_enabled() -> None:
    agent: dict = {}
    _add(agent, "m1", tags=("trader",), last_accessed_turn=None)
    _ = retrieve_memory(
        agent,
        MemoryQuery(purpose="p", tags=("trader",)),
        world_turn=123,
        track_access=True,
    )
    assert agent["memory_v3"]["records"]["m1"]["last_accessed_turn"] == 123
