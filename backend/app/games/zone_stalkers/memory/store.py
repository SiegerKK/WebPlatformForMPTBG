"""memory/store.py — Core MemoryStore v3 API.

Manages ``agent["memory_v3"]`` — a structured, indexed, in-process store on
top of the existing flat ``agent["memory"]`` list.

No external dependencies (Redis / PostgreSQL) are introduced.
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import MemoryRecord, MemoryQuery, VALID_LAYERS

# ── Caps (section 13) ─────────────────────────────────────────────────────────
MEMORY_V3_MAX_RECORDS = 5000
MEMORY_V3_IMPORT_LEGACY_LIMIT = 200
MEMORY_V3_RETRIEVAL_MAX_RESULTS = 50

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
    if len(records) > MEMORY_V3_MAX_RECORDS:
        _evict_over_cap(mem_v3)


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


def _evict_over_cap(mem_v3: dict[str, Any]) -> None:
    """Evict lowest-scoring non-protected records until within cap."""
    records = mem_v3["records"]
    if len(records) <= MEMORY_V3_MAX_RECORDS:
        return

    # Score each record for eviction (lower = evict first).
    def evict_score(d: dict[str, Any]) -> float:
        return (
            float(d.get("importance", 0.5))
            + float(d.get("confidence", 1.0))
            + float(d.get("emotional_weight", 0.0))
        )

    candidates = [
        rid for rid, d in records.items()
        if d.get("layer") not in _PROTECTED_LAYERS
        and d.get("status") != "archived"
    ]
    candidates.sort(key=lambda rid: evict_score(records[rid]))

    to_evict = len(records) - MEMORY_V3_MAX_RECORDS
    for rid in candidates[:to_evict]:
        record = MemoryRecord.from_dict(records.pop(rid))
        _deindex_record(mem_v3, record)

    mem_v3["stats"]["records_count"] = len(records)
