"""memory/store.py — Core MemoryStore v3 API.

Manages ``agent["memory_v3"]`` — a structured, indexed, in-process store.

No external dependencies (Redis / PostgreSQL) are introduced.
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import MemoryRecord, MemoryQuery, VALID_LAYERS

# ── Caps (section 13 / PR5) ───────────────────────────────────────────────────
MEMORY_V3_MAX_RECORDS = 500
MEMORY_V3_IMPORT_LEGACY_LIMIT = 200
MEMORY_V3_RETRIEVAL_MAX_RESULTS = 50
MEMORY_V3_RETRIEVAL_MAX_CANDIDATES = 200

# Layers whose records must not be deleted during cap-trimming.
_PROTECTED_LAYERS = frozenset({"threat", "goal", "semantic"})


def _empty_memory_v3() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "records": {},
        "indexes": {
            "by_layer": {},
            "by_kind": {},
            "by_location": {},
            "by_entity": {},
            "by_item_type": {},
            "by_tag": {},
        },
        "stats": {
            "records_count": 0,
            "last_decay_turn": None,
            "last_consolidation_turn": None,
        },
    }


def ensure_memory_v3(agent: dict[str, Any]) -> dict[str, Any]:
    """Return ``agent["memory_v3"]``, creating it if absent.

    This is idempotent and safe to call on every tick.
    """
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        mem_v3 = _empty_memory_v3()
        agent["memory_v3"] = mem_v3
    # Ensure all index keys exist (forward-compat for older schema versions).
    indexes = mem_v3.setdefault("indexes", {})
    for key in ("by_layer", "by_kind", "by_location", "by_entity", "by_item_type", "by_tag"):
        indexes.setdefault(key, {})
    mem_v3.setdefault("stats", {
        "records_count": 0,
        "last_decay_turn": None,
        "last_consolidation_turn": None,
    })
    return mem_v3


def _generate_record_id() -> str:
    return "mem_" + uuid.uuid4().hex[:12]


def add_memory_record(agent: dict[str, Any], record: MemoryRecord) -> None:
    """Add a MemoryRecord to ``memory_v3``, updating all indexes.

    If a record with the same ``id`` already exists it will be overwritten.
    This allows callers to update/replace records by recycling the same id.

    Enforces the cap: when over ``MEMORY_V3_MAX_RECORDS``, the lowest-scoring
    non-protected records are evicted.
    """
    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3["records"]

    # Remove old index entries for this id if it was already stored.
    if record.id in records:
        _deindex_record(mem_v3, MemoryRecord.from_dict(records[record.id]))

    records[record.id] = record.to_dict()
    _index_record(mem_v3, record)
    mem_v3["stats"]["records_count"] = len(records)

    # Enforce cap.
    trim_memory_v3_to_cap(agent)


def mark_memory_stale(agent: dict[str, Any], memory_id: str, reason: str = "") -> None:
    """Set a record's status to 'stale'."""
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        return
    record_dict = mem_v3.get("records", {}).get(memory_id)
    if record_dict is None:
        return
    record_dict["status"] = "stale"
    if reason:
        details = record_dict.setdefault("details", {})
        details["stale_reason"] = reason


def get_memory_record(agent: dict[str, Any], memory_id: str) -> MemoryRecord | None:
    """Return a MemoryRecord by id, or None."""
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        return None
    d = mem_v3.get("records", {}).get(memory_id)
    if d is None:
        return None
    return MemoryRecord.from_dict(d)


# ── Internal indexing ─────────────────────────────────────────────────────────

def _index_record(mem_v3: dict[str, Any], record: MemoryRecord) -> None:
    idx = mem_v3["indexes"]

    def _add(bucket: dict, key: str, rid: str) -> None:
        bucket.setdefault(key, [])
        if rid not in bucket[key]:
            bucket[key].append(rid)

    _add(idx["by_layer"], record.layer, record.id)
    _add(idx["by_kind"],  record.kind,  record.id)
    if record.location_id:
        _add(idx["by_location"], record.location_id, record.id)
    for eid in record.entity_ids:
        _add(idx["by_entity"], eid, record.id)
    for itype in record.item_types:
        _add(idx["by_item_type"], itype, record.id)
    for tag in record.tags:
        _add(idx["by_tag"], tag, record.id)


def _deindex_record(mem_v3: dict[str, Any], record: MemoryRecord) -> None:
    idx = mem_v3["indexes"]

    def _remove(bucket: dict, key: str, rid: str) -> None:
        lst = bucket.get(key)
        if lst and rid in lst:
            lst.remove(rid)

    _remove(idx["by_layer"], record.layer, record.id)
    _remove(idx["by_kind"],  record.kind,  record.id)
    if record.location_id:
        _remove(idx["by_location"], record.location_id, record.id)
    for eid in record.entity_ids:
        _remove(idx["by_entity"], eid, record.id)
    for itype in record.item_types:
        _remove(idx["by_item_type"], itype, record.id)
    for tag in record.tags:
        _remove(idx["by_tag"], tag, record.id)


def _status_evict_rank(status: Any) -> int:
    s = str(status or "active")
    if s == "archived":
        return 0
    if s in {"stale", "contradicted"}:
        return 1
    return 2


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _retention_priority(raw: dict[str, Any]) -> int:
    """Return a retention priority score (higher = keep longer)."""
    kind = str(raw.get("kind") or "")
    layer = str(raw.get("layer") or "")
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    action_kind = str(details.get("action_kind") or kind)

    if kind in {
        "combat_killed",
        "combat_kill",
        "target_death_confirmed",
        "target_intel",
        "emission_warning",
        "emission_started",
        "anomaly_detected",
        "global_goal_completed",
        "death",
    }:
        return 100

    if layer == "semantic":
        return 80

    if kind == "objective_decision":
        return 60

    if action_kind.startswith("active_plan_"):
        return 10

    return 40


def _eviction_sort_key(record_id: str, raw: dict[str, Any], *, protected_penalty: int) -> tuple:
    # Use semantic retention priority instead of broad layer protection.
    retention = _retention_priority(raw)
    return (
        retention * protected_penalty // 100,
        _status_evict_rank(raw.get("status")),
        _coerce_float(raw.get("importance"), 0.5),
        _coerce_float(raw.get("confidence"), 1.0),
        _coerce_int(raw.get("created_turn"), 0),
        record_id,
    )


def _rebuild_indexes_from_records(mem_v3: dict[str, Any]) -> None:
    records: dict[str, dict[str, Any]] = mem_v3.get("records", {})
    indexes = {
        "by_layer": {},
        "by_kind": {},
        "by_location": {},
        "by_entity": {},
        "by_item_type": {},
        "by_tag": {},
    }
    mem_v3["indexes"] = indexes
    for rid in sorted(records.keys()):
        _index_record(mem_v3, MemoryRecord.from_dict(records[rid]))


def trim_memory_v3_to_cap(
    agent: dict[str, Any],
    *,
    max_records: int = MEMORY_V3_MAX_RECORDS,
) -> int:
    """Evict records until hard cap is respected and indexes are rebuilt."""
    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3.get("records", {})
    over = len(records) - max_records
    if over <= 0:
        mem_v3["stats"]["records_count"] = len(records)
        return 0

    ranked = sorted(
        records.keys(),
        key=lambda rid: _eviction_sort_key(rid, records[rid], protected_penalty=10_000_000),
    )
    evicted_ids = ranked[:over]
    for rid in evicted_ids:
        records.pop(rid, None)

    _rebuild_indexes_from_records(mem_v3)
    mem_v3["stats"]["records_count"] = len(records)
    return len(evicted_ids)


def normalize_agent_memory_state(agent: dict[str, Any]) -> dict[str, int]:
    """Normalize memory_v3 structure for oversized/old states."""
    legacy_trimmed = 0

    mem_v3 = ensure_memory_v3(agent)
    memory_v3_evicted = trim_memory_v3_to_cap(agent)

    indexes_rebuilt = 0
    records = mem_v3.get("records")
    indexes = mem_v3.get("indexes")
    if not isinstance(records, dict) or not isinstance(indexes, dict):
        mem_v3["records"] = records if isinstance(records, dict) else {}
        _rebuild_indexes_from_records(mem_v3)
        indexes_rebuilt = 1
    else:
        required_keys = {"by_layer", "by_kind", "by_location", "by_entity", "by_item_type", "by_tag"}
        if not required_keys.issubset(set(indexes.keys())):
            _rebuild_indexes_from_records(mem_v3)
            indexes_rebuilt = 1
        elif records:
            index_incomplete = False
            by_layer = indexes.get("by_layer", {})
            by_kind = indexes.get("by_kind", {})
            for rid, raw in records.items():
                layer = str(raw.get("layer", ""))
                kind = str(raw.get("kind", ""))
                by_layer_set = set(by_layer.get(layer, []))
                by_kind_set = set(by_kind.get(kind, []))
                if rid not in by_layer_set or rid not in by_kind_set:
                    index_incomplete = True
                    break
            if index_incomplete:
                _rebuild_indexes_from_records(mem_v3)
                indexes_rebuilt = 1
    mem_v3["stats"]["records_count"] = len(mem_v3.get("records", {}))
    return {
        "legacy_trimmed": legacy_trimmed,
        "memory_v3_evicted": memory_v3_evicted,
        "indexes_rebuilt": indexes_rebuilt,
    }
