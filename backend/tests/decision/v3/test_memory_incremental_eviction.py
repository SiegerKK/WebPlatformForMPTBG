from __future__ import annotations

import app.games.zone_stalkers.memory.store as memory_store
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_EPISODIC, LAYER_GOAL, LAYER_THREAT
from app.games.zone_stalkers.memory.store import (
    MEMORY_V3_MAX_RECORDS,
    add_memory_record,
    deindex_raw_record,
    ensure_memory_v3,
    index_raw_record,
    normalize_agent_memory_state,
    validate_memory_indexes,
)


def _make_record(
    record_id: str,
    *,
    layer: str = LAYER_EPISODIC,
    kind: str = "routine_event",
    created_turn: int = 100,
    importance: float = 0.2,
    confidence: float = 0.5,
    status: str = "active",
    location_id: str | None = None,
    entity_ids: tuple[str, ...] = (),
    item_types: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    details: dict | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        agent_id="bot1",
        layer=layer,
        kind=kind,
        created_turn=created_turn,
        last_accessed_turn=None,
        summary=f"{kind}:{record_id}",
        details=details or {},
        location_id=location_id,
        entity_ids=entity_ids,
        item_types=item_types,
        tags=tags,
        importance=importance,
        confidence=confidence,
        status=status,
    )


def _fill_memory(
    agent: dict,
    *,
    layer: str,
    kind: str,
    importance: float,
    confidence: float,
    tags: tuple[str, ...] = (),
) -> None:
    for index in range(MEMORY_V3_MAX_RECORDS):
        add_memory_record(
            agent,
            _make_record(
                f"mem_{index:04d}",
                layer=layer,
                kind=kind,
                created_turn=index,
                importance=importance,
                confidence=confidence,
                tags=tags,
                details={"action_kind": kind},
            ),
        )


def test_deindex_raw_record_removes_id_from_all_indexes() -> None:
    agent: dict = {}
    record = _make_record(
        "mem_idx",
        location_id="loc_a",
        entity_ids=("npc_a", "npc_b"),
        item_types=("ammo",),
        tags=("trade", "loot"),
    )
    add_memory_record(agent, record)
    mem_v3 = ensure_memory_v3(agent)

    deindex_raw_record(mem_v3, mem_v3["records"]["mem_idx"])

    indexes = mem_v3["indexes"]
    assert "episodic" not in indexes["by_layer"]
    assert "routine_event" not in indexes["by_kind"]
    assert "loc_a" not in indexes["by_location"]
    assert "npc_a" not in indexes["by_entity"]
    assert "ammo" not in indexes["by_item_type"]
    assert "trade" not in indexes["by_tag"]


def test_index_raw_record_is_idempotent() -> None:
    mem_v3 = ensure_memory_v3({})
    raw = _make_record(
        "mem_dupe",
        location_id="loc_a",
        entity_ids=("npc_a",),
        item_types=("ammo",),
        tags=("trade",),
    ).to_dict()

    index_raw_record(mem_v3, raw)
    index_raw_record(mem_v3, raw)

    assert mem_v3["indexes"]["by_layer"]["episodic"] == ["mem_dupe"]
    assert mem_v3["indexes"]["by_kind"]["routine_event"] == ["mem_dupe"]
    assert mem_v3["indexes"]["by_location"]["loc_a"] == ["mem_dupe"]
    assert mem_v3["indexes"]["by_entity"]["npc_a"] == ["mem_dupe"]
    assert mem_v3["indexes"]["by_item_type"]["ammo"] == ["mem_dupe"]
    assert mem_v3["indexes"]["by_tag"]["trade"] == ["mem_dupe"]


def test_saturated_add_evicts_one_record_without_rebuild() -> None:
    agent: dict = {}
    _fill_memory(agent, layer=LAYER_EPISODIC, kind="objective_decision_summary", importance=0.2, confidence=0.3)
    mem_v3 = ensure_memory_v3(agent)
    stats = mem_v3["stats"]
    before_revision = stats["memory_revision"]
    rebuild_calls = 0
    original_rebuild = memory_store.rebuild_memory_indexes

    def _spy(mem: dict) -> None:
        nonlocal rebuild_calls
        rebuild_calls += 1
        original_rebuild(mem)

    memory_store.rebuild_memory_indexes = _spy
    try:
        stored = add_memory_record(
            agent,
            _make_record(
                "incoming_critical",
                layer=LAYER_THREAT,
                kind="combat_kill",
                created_turn=MEMORY_V3_MAX_RECORDS + 1,
                importance=1.0,
                confidence=1.0,
                tags=("combat", "target"),
            ),
        )
    finally:
        memory_store.rebuild_memory_indexes = original_rebuild

    assert stored is True
    assert len(mem_v3["records"]) == MEMORY_V3_MAX_RECORDS
    assert "incoming_critical" in mem_v3["records"]
    assert "mem_0000" not in mem_v3["records"]
    assert rebuild_calls == 0
    assert stats["memory_index_rebuilds"] == 0
    assert stats["memory_revision"] == before_revision + 1
    assert not validate_memory_indexes(mem_v3)


def test_low_priority_incoming_is_dropped_when_memory_full_of_critical_records() -> None:
    agent: dict = {}
    _fill_memory(
        agent,
        layer=LAYER_THREAT,
        kind="target_death_confirmed",
        importance=1.0,
        confidence=1.0,
        tags=("target",),
    )
    mem_v3 = ensure_memory_v3(agent)
    before_revision = mem_v3["stats"]["memory_revision"]

    stored = add_memory_record(
        agent,
        _make_record(
            "incoming_low",
            layer=LAYER_EPISODIC,
            kind="travel_hop",
            created_turn=MEMORY_V3_MAX_RECORDS + 10,
            importance=0.1,
            confidence=0.2,
            tags=("routine", "travel"),
        ),
    )

    assert stored is False
    assert len(mem_v3["records"]) == MEMORY_V3_MAX_RECORDS
    assert "incoming_low" not in mem_v3["records"]
    assert mem_v3["stats"]["dropped_new_records"] == 1
    assert mem_v3["stats"]["memory_write_dropped"] == 1
    assert mem_v3["stats"]["memory_revision"] == before_revision
    assert not validate_memory_indexes(mem_v3)


def test_high_priority_incoming_evicts_low_priority_record() -> None:
    agent: dict = {}
    _fill_memory(agent, layer=LAYER_GOAL, kind="active_plan_failure_summary", importance=0.15, confidence=0.3)

    stored = add_memory_record(
        agent,
        _make_record(
            "incoming_target",
            layer=LAYER_THREAT,
            kind="target_death_confirmed",
            created_turn=MEMORY_V3_MAX_RECORDS + 5,
            importance=1.0,
            confidence=1.0,
            tags=("target",),
        ),
    )

    mem_v3 = ensure_memory_v3(agent)
    assert stored is True
    assert len(mem_v3["records"]) == MEMORY_V3_MAX_RECORDS
    assert "incoming_target" in mem_v3["records"]
    assert "mem_0000" not in mem_v3["records"]
    assert mem_v3["stats"]["memory_evictions"] >= 1
    assert not validate_memory_indexes(mem_v3)


def test_indexes_remain_consistent_after_many_evictions() -> None:
    agent: dict = {}
    for index in range(MEMORY_V3_MAX_RECORDS):
        add_memory_record(
            agent,
            _make_record(
                f"base_{index:04d}",
                layer=LAYER_EPISODIC,
                kind="routine_event",
                created_turn=index,
                importance=0.2,
                confidence=0.4,
                location_id=f"loc_{index % 7}",
                entity_ids=(f"npc_{index % 11}",),
                item_types=(f"item_{index % 5}",),
                tags=(f"tag_{index % 9}",),
            ),
        )

    for index in range(100):
        stored = add_memory_record(
            agent,
            _make_record(
                f"incoming_{index:04d}",
                layer=LAYER_THREAT if index % 2 == 0 else LAYER_GOAL,
                kind="combat_kill" if index % 2 == 0 else "objective_decision_summary",
                created_turn=MEMORY_V3_MAX_RECORDS + index + 1,
                importance=0.9 if index % 2 == 0 else 0.45,
                confidence=0.95 if index % 2 == 0 else 0.6,
                location_id=f"loc_new_{index % 3}",
                entity_ids=(f"target_{index % 5}",),
                item_types=(f"loot_{index % 4}",),
                tags=("combat",) if index % 2 == 0 else ("decision",),
                details={"action_kind": "combat_kill" if index % 2 == 0 else "objective_decision"},
            ),
        )
        assert stored is True

    mem_v3 = ensure_memory_v3(agent)
    assert len(mem_v3["records"]) == MEMORY_V3_MAX_RECORDS
    assert not validate_memory_indexes(mem_v3)


def test_memory_revision_increments_only_on_actual_change() -> None:
    agent: dict = {}
    base = _make_record("same_id", kind="objective_decision_summary", layer=LAYER_GOAL)
    assert add_memory_record(agent, base) is True

    mem_v3 = ensure_memory_v3(agent)
    assert mem_v3["stats"]["memory_revision"] == 1
    assert add_memory_record(agent, base) is True
    assert mem_v3["stats"]["memory_revision"] == 1

    critical_agent: dict = {}
    _fill_memory(
        critical_agent,
        layer=LAYER_THREAT,
        kind="target_death_confirmed",
        importance=1.0,
        confidence=1.0,
        tags=("target",),
    )
    critical_mem = ensure_memory_v3(critical_agent)
    before_revision = critical_mem["stats"]["memory_revision"]
    assert add_memory_record(
        critical_agent,
        _make_record(
            "drop_me",
            kind="travel_hop",
            created_turn=999,
            importance=0.1,
            confidence=0.1,
            tags=("routine", "travel"),
        ),
    ) is False
    assert critical_mem["stats"]["memory_revision"] == before_revision


def test_records_count_never_exceeds_cap() -> None:
    agent: dict = {}
    for index in range(MEMORY_V3_MAX_RECORDS + 150):
        add_memory_record(
            agent,
            _make_record(
                f"count_{index:04d}",
                layer=LAYER_EPISODIC,
                kind="routine_event",
                created_turn=index,
                importance=0.2,
                confidence=0.2,
            ),
        )
        mem_v3 = ensure_memory_v3(agent)
        assert len(mem_v3["records"]) <= MEMORY_V3_MAX_RECORDS
        assert mem_v3["stats"]["records_count"] == len(mem_v3["records"])


def test_saturated_add_does_not_call_rebuild_indexes(monkeypatch) -> None:
    agent: dict = {}
    _fill_memory(agent, layer=LAYER_EPISODIC, kind="routine_event", importance=0.2, confidence=0.3)

    def _fail(mem: dict) -> None:
        raise AssertionError("rebuild_memory_indexes should not run for normal saturated writes")

    monkeypatch.setattr(memory_store, "rebuild_memory_indexes", _fail)
    monkeypatch.setattr(memory_store, "_rebuild_indexes_from_records", _fail)

    assert add_memory_record(
        agent,
        _make_record(
            "incoming_hot",
            layer=LAYER_THREAT,
            kind="combat_kill",
            created_turn=999,
            importance=1.0,
            confidence=1.0,
            tags=("combat",),
        ),
    ) is True


def test_explicit_repair_rebuild_increments_stat() -> None:
    agent: dict = {}
    add_memory_record(agent, _make_record("repair_me", tags=("trade",), location_id="loc_a"))
    mem_v3 = ensure_memory_v3(agent)
    mem_v3["indexes"] = {"broken": {}}

    counters = normalize_agent_memory_state(agent)

    assert counters["indexes_rebuilt"] == 1
    assert mem_v3["stats"]["memory_index_rebuilds"] == 1
    assert not validate_memory_indexes(mem_v3)


# ---------------------------------------------------------------------------
# A3: trim_memory_v3_to_cap is the repair path, NOT the normal write path
# ---------------------------------------------------------------------------

def test_normal_add_path_does_not_call_trim_memory_v3_to_cap_or_rebuild(monkeypatch) -> None:
    """A3: add_memory_record must NOT call trim_memory_v3_to_cap or
    rebuild_memory_indexes on the normal hot-write path.  Those are
    repair/normalization helpers and would be catastrophically slow if
    called on every single memory write."""
    from app.games.zone_stalkers.memory.store import trim_memory_v3_to_cap as _real_trim
    agent: dict = {}
    _fill_memory(agent, layer=LAYER_EPISODIC, kind="routine_event", importance=0.2, confidence=0.3)

    def _fail_trim(mem: dict) -> None:
        raise AssertionError("trim_memory_v3_to_cap must not be called from normal add path")

    def _fail_rebuild(mem: dict) -> None:
        raise AssertionError("rebuild_memory_indexes must not be called from normal add path")

    monkeypatch.setattr(memory_store, "trim_memory_v3_to_cap", _fail_trim)
    monkeypatch.setattr(memory_store, "rebuild_memory_indexes", _fail_rebuild)
    monkeypatch.setattr(memory_store, "_rebuild_indexes_from_records", _fail_rebuild)

    # Should succeed without calling trim or rebuild
    result = add_memory_record(
        agent,
        _make_record(
            "normal_write",
            layer=LAYER_THREAT,
            kind="combat_kill",
            created_turn=9999,
            importance=1.0,
            confidence=1.0,
        ),
    )
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# A4: fresh (empty) add never triggers eviction
# ---------------------------------------------------------------------------

def test_fresh_add_never_triggers_eviction(monkeypatch) -> None:
    """A4: When memory is below cap, add_memory_record must always return True
    without needing to evict anything."""
    agent: dict = {}

    def _fail_evict(mem: dict, record=None) -> None:
        raise AssertionError("_evict_record must not be called below cap")

    monkeypatch.setattr(memory_store, "_evict_record", _fail_evict, raising=False)

    for i in range(10):
        result = add_memory_record(
            agent,
            _make_record(f"fresh_{i:04d}", created_turn=i, importance=0.5, confidence=0.5),
        )
        assert result is True, f"Expected True at index {i}, got {result}"

    mem_v3 = ensure_memory_v3(agent)
    assert len(mem_v3["records"]) == 10


# ---------------------------------------------------------------------------
# A5: incremental index stays consistent after eviction under cap
# ---------------------------------------------------------------------------

def test_incremental_index_consistent_after_eviction_at_cap() -> None:
    """A5: After eviction at cap, all memory indexes must pass validate_memory_indexes."""
    agent: dict = {}
    _fill_memory(agent, layer=LAYER_EPISODIC, kind="routine_event", importance=0.2, confidence=0.2)

    # Write 50 more records; each write should trigger an eviction
    for i in range(50):
        add_memory_record(
            agent,
            _make_record(
                f"over_cap_{i:04d}",
                layer=LAYER_THREAT,
                kind="combat_kill",
                created_turn=MEMORY_V3_MAX_RECORDS + i,
                importance=0.8,
                confidence=0.8,
            ),
        )

    mem_v3 = ensure_memory_v3(agent)
    assert not validate_memory_indexes(mem_v3), (
        "validate_memory_indexes returned errors after incremental eviction"
    )
    assert len(mem_v3["records"]) <= MEMORY_V3_MAX_RECORDS
    assert mem_v3["stats"]["records_count"] == len(mem_v3["records"])
