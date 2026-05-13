"""Tests for MemoryStore v3 core API (PR 3)."""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_EPISODIC, LAYER_THREAT
from app.games.zone_stalkers.memory.store import (
    ensure_memory_v3,
    add_memory_record,
    mark_memory_stale,
    get_memory_record,
    MEMORY_V3_MAX_RECORDS,
    trim_memory_v3_to_cap,
    normalize_agent_memory_state,
    MEMORY_V3_MAX_STALKERS_SEEN_RECORDS,
)


def _make_record(
    record_id: str = "mem_001",
    layer: str = LAYER_EPISODIC,
    kind: str = "test_event",
    location_id: str | None = None,
    tags: tuple[str, ...] = (),
    item_types: tuple[str, ...] = (),
    importance: float = 0.5,
    confidence: float = 1.0,
    status: str = "active",
    created_turn: int = 100,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        agent_id="bot1",
        layer=layer,
        kind=kind,
        created_turn=created_turn,
        last_accessed_turn=None,
        summary="test summary",
        details={"test": True},
        location_id=location_id,
        tags=tags,
        item_types=item_types,
        importance=importance,
        confidence=confidence,
        status=status,
    )


def test_ensure_memory_v3_creates_empty_structure() -> None:
    agent: dict = {}
    mem_v3 = ensure_memory_v3(agent)
    assert mem_v3["schema_version"] == 1
    assert mem_v3["records"] == {}
    assert "by_layer" in mem_v3["indexes"]
    assert "by_kind" in mem_v3["indexes"]
    assert "by_location" in mem_v3["indexes"]
    assert "by_entity" in mem_v3["indexes"]
    assert "by_item_type" in mem_v3["indexes"]
    assert "by_tag" in mem_v3["indexes"]
    assert mem_v3["stats"]["records_count"] == 0
    assert mem_v3["stats"]["last_decay_turn"] is None


def test_ensure_memory_v3_is_idempotent() -> None:
    agent: dict = {}
    m1 = ensure_memory_v3(agent)
    m2 = ensure_memory_v3(agent)
    assert m1 is m2  # Same object.


def test_add_memory_record_stores_in_records() -> None:
    agent: dict = {}
    rec = _make_record("mem_001")
    add_memory_record(agent, rec)
    mem_v3 = agent["memory_v3"]
    assert "mem_001" in mem_v3["records"]
    assert mem_v3["stats"]["records_count"] == 1


def test_add_memory_record_updates_layer_index() -> None:
    agent: dict = {}
    rec = _make_record("mem_001", layer=LAYER_EPISODIC)
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_layer"]
    assert "mem_001" in idx.get(LAYER_EPISODIC, [])


def test_add_memory_record_updates_tag_index() -> None:
    agent: dict = {}
    rec = _make_record("mem_001", tags=("trade", "item"))
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_tag"]
    assert "mem_001" in idx.get("trade", [])
    assert "mem_001" in idx.get("item", [])


def test_add_memory_record_updates_location_index() -> None:
    agent: dict = {}
    rec = _make_record("mem_001", location_id="loc_a")
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_location"]
    assert "mem_001" in idx.get("loc_a", [])


def test_add_memory_record_updates_item_type_index() -> None:
    agent: dict = {}
    rec = _make_record("mem_001", item_types=("bread", "water"))
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_item_type"]
    assert "mem_001" in idx.get("bread", [])
    assert "mem_001" in idx.get("water", [])


def test_add_memory_record_overwrites_same_id() -> None:
    agent: dict = {}
    rec1 = _make_record("mem_001", tags=("trade",))
    add_memory_record(agent, rec1)
    rec2 = _make_record("mem_001", tags=("sleep",))
    add_memory_record(agent, rec2)
    mem_v3 = agent["memory_v3"]
    # Old tag removed, new tag present.
    assert "mem_001" not in mem_v3["indexes"]["by_tag"].get("trade", [])
    assert "mem_001" in mem_v3["indexes"]["by_tag"].get("sleep", [])
    assert mem_v3["stats"]["records_count"] == 1


def test_mark_memory_stale() -> None:
    agent: dict = {}
    rec = _make_record("mem_001")
    add_memory_record(agent, rec)
    mark_memory_stale(agent, "mem_001", reason="test_stale")
    raw = agent["memory_v3"]["records"]["mem_001"]
    assert raw["status"] == "stale"
    assert raw["details"]["stale_reason"] == "test_stale"


def test_mark_memory_stale_missing_id_noop() -> None:
    agent: dict = {}
    ensure_memory_v3(agent)
    mark_memory_stale(agent, "nonexistent", reason="x")  # Should not raise.


def test_get_memory_record_returns_record() -> None:
    agent: dict = {}
    rec = _make_record("mem_001")
    add_memory_record(agent, rec)
    result = get_memory_record(agent, "mem_001")
    assert result is not None
    assert result.id == "mem_001"


def test_get_memory_record_returns_none_for_missing() -> None:
    agent: dict = {}
    ensure_memory_v3(agent)
    assert get_memory_record(agent, "nonexistent") is None


def test_cap_evicts_non_protected_records() -> None:
    """When over cap, low-importance non-protected records are evicted."""
    agent: dict = {}
    ensure_memory_v3(agent)
    # Add MAX_RECORDS + 5 low-importance episodic records.
    for i in range(MEMORY_V3_MAX_RECORDS + 5):
        rec = _make_record(
            record_id=f"mem_{i:06d}",
            layer=LAYER_EPISODIC,
            importance=0.1,
            confidence=0.1,
        )
        add_memory_record(agent, rec)
    assert len(agent["memory_v3"]["records"]) == MEMORY_V3_MAX_RECORDS


def test_trim_memory_v3_enforces_hard_cap_even_with_protected_records() -> None:
    agent: dict = {}
    for i in range(MEMORY_V3_MAX_RECORDS + 25):
        rec = _make_record(
            record_id=f"p_{i:06d}",
            layer=LAYER_THREAT,
            importance=1.0,
            confidence=1.0,
        )
        add_memory_record(agent, rec)
    assert len(agent["memory_v3"]["records"]) == MEMORY_V3_MAX_RECORDS


def test_trim_memory_v3_rebuilds_indexes() -> None:
    agent: dict = {}
    rec = _make_record("mem_001", location_id="loc_a", tags=("item",))
    add_memory_record(agent, rec)
    agent["memory_v3"]["indexes"] = {"broken": {}}
    counters = normalize_agent_memory_state(agent)
    assert counters["indexes_rebuilt"] == 1
    idx = agent["memory_v3"]["indexes"]
    assert "by_location" in idx and "mem_001" in idx["by_location"].get("loc_a", [])


def test_trim_memory_v3_to_cap_returns_evicted_count() -> None:
    agent: dict = {}
    for i in range(MEMORY_V3_MAX_RECORDS + 3):
        add_memory_record(agent, _make_record(record_id=f"m_{i:06d}"))
    # add_memory_record already trimmed; force overflow manually then normalize.
    agent["memory_v3"]["records"]["manual_overflow"] = _make_record("manual_overflow").to_dict()
    evicted = trim_memory_v3_to_cap(agent)
    assert evicted == 1
    assert len(agent["memory_v3"]["records"]) == MEMORY_V3_MAX_RECORDS


def test_trim_memory_v3_enforces_stalkers_seen_budget() -> None:
    agent: dict = {}
    for i in range(MEMORY_V3_MAX_STALKERS_SEEN_RECORDS + 10):
        add_memory_record(
            agent,
            _make_record(
                record_id=f"st_{i:04d}",
                kind="stalkers_seen",
                created_turn=100 + i,
            ),
        )
    records = agent["memory_v3"]["records"]
    stalkers_seen_count = sum(1 for raw in records.values() if raw.get("kind") == "stalkers_seen")
    assert stalkers_seen_count == MEMORY_V3_MAX_STALKERS_SEEN_RECORDS


# ── PR1: by_tag denylist and bucket cap tests ────────────────────────────────

def test_by_tag_denylist_skips_noisy_tags() -> None:
    """Tags in DO_NOT_INDEX_TAGS must not appear in the by_tag index."""
    from app.games.zone_stalkers.memory.store import DO_NOT_INDEX_TAGS, reset_tag_metrics

    reset_tag_metrics()
    agent: dict = {}
    # Use every denied tag plus a valid one.
    all_tags = tuple(DO_NOT_INDEX_TAGS) + ("valid_tag",)
    rec = _make_record("mem_denylist", tags=all_tags)
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_tag"]

    for denied_tag in DO_NOT_INDEX_TAGS:
        assert "mem_denylist" not in idx.get(denied_tag, []), (
            f"Denied tag {denied_tag!r} should not be in by_tag index"
        )
    assert "mem_denylist" in idx.get("valid_tag", [])


def test_by_tag_prefix_denylist_skips_noisy_prefixes() -> None:
    """Tags with denied prefixes (objective:, step:, repair:) must not appear in by_tag."""
    agent: dict = {}
    noisy_tags = ("objective:FIND_ARTIFACTS", "step:travel_to_location", "repair:reason_x", "safe_tag")
    rec = _make_record("mem_prefix", tags=noisy_tags)
    add_memory_record(agent, rec)
    idx = agent["memory_v3"]["indexes"]["by_tag"]

    for noisy in ("objective:FIND_ARTIFACTS", "step:travel_to_location", "repair:reason_x"):
        assert "mem_prefix" not in idx.get(noisy, [])
    assert "mem_prefix" in idx.get("safe_tag", [])


def test_by_tag_bucket_size_is_capped() -> None:
    """Adding more records than MAX_TAG_BUCKET_SIZE must not grow the bucket beyond the cap."""
    from app.games.zone_stalkers.memory.store import MAX_TAG_BUCKET_SIZE

    agent: dict = {}
    num_records = MAX_TAG_BUCKET_SIZE + 20
    for i in range(num_records):
        add_memory_record(agent, _make_record(record_id=f"m_cap_{i:04d}", tags=("shared_tag",)))
    idx = agent["memory_v3"]["indexes"]["by_tag"]
    bucket = idx.get("shared_tag", [])
    assert len(bucket) <= MAX_TAG_BUCKET_SIZE


def test_by_tag_metrics_track_skipped_refs() -> None:
    """Skipped tag refs must be counted in tag metrics."""
    from app.games.zone_stalkers.memory.store import reset_tag_metrics, get_tag_metrics, DO_NOT_INDEX_TAGS

    reset_tag_metrics()
    agent: dict = {}
    denied_tags = tuple(list(DO_NOT_INDEX_TAGS)[:3])
    rec = _make_record("mem_metrics", tags=denied_tags + ("ok_tag",))
    add_memory_record(agent, rec)
    m = get_tag_metrics()
    assert m["memory_by_tag_refs"] >= len(denied_tags) + 1
    assert m["memory_by_tag_skipped_refs"] >= len(denied_tags)

