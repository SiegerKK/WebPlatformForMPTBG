"""PR5 fast-path tests for MemoryStore v3 retrieval."""
from __future__ import annotations

from app.games.zone_stalkers.memory.models import MemoryRecord, MemoryQuery
from app.games.zone_stalkers.memory.retrieval import _score_record_raw, retrieve_memory
from app.games.zone_stalkers.memory.store import add_memory_record


def _add(agent: dict, record_id: str, **kwargs) -> None:
    defaults = dict(
        agent_id="bot1",
        layer="episodic",
        kind="test",
        created_turn=100,
        last_accessed_turn=None,
        summary="summary",
        details={},
        importance=0.5,
        confidence=1.0,
        status="active",
        tags=(),
    )
    defaults.update(kwargs)
    add_memory_record(agent, MemoryRecord(id=record_id, **defaults))


def test_score_record_raw_matches_record_based_semantics() -> None:
    raw = {
        "id": "m1",
        "kind": "target_seen",
        "tags": ["target", "intel"],
        "location_id": "loc_a",
        "created_turn": 90,
        "status": "active",
        "confidence": 0.8,
        "importance": 0.6,
    }
    query = MemoryQuery(
        purpose="hunt",
        kinds=("target_seen",),
        tags=("target", "intel"),
        location_id="loc_a",
    )
    score = _score_record_raw(raw, query, 100, set(query.tags), set(query.kinds))
    assert score > 0.85


def test_retrieve_memory_limits_deserialization_to_top_k_records(monkeypatch) -> None:
    agent: dict = {}
    for i in range(500):
        _add(agent, f"m{i}", tags=("item",), created_turn=i)

    calls = {"count": 0}
    original = MemoryRecord.from_dict

    def _counted(raw):
        calls["count"] += 1
        return original(raw)

    monkeypatch.setattr(MemoryRecord, "from_dict", staticmethod(_counted))
    result = retrieve_memory(
        agent,
        MemoryQuery(purpose="items", tags=("item",), max_results=10),
        world_turn=600,
    )
    assert len(result) == 10
    assert calls["count"] == 10


def test_retrieve_memory_applies_candidate_limit() -> None:
    agent: dict = {}
    for i in range(500):
        _add(agent, f"m{i}", tags=("item",), created_turn=i, importance=0.5, confidence=0.5)

    _ = retrieve_memory(
        agent,
        MemoryQuery(purpose="items", tags=("item",), max_results=10, max_candidates=50),
        world_turn=600,
        record_metrics=True,
    )
    metrics = agent["memory_v3"]["stats"]["retrieval_metrics"]
    assert metrics["memory_retrieval_candidates_max"] <= 50
    assert metrics["memory_retrieval_scored_max"] <= 50
