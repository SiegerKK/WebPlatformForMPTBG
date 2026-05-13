"""memory/store.py — Core MemoryStore v3 API.

Manages ``agent["memory_v3"]`` — a structured, indexed, in-process store.

No external dependencies (Redis / PostgreSQL) are introduced.
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import MemoryRecord

# ── Caps (section 13 / PR5) ───────────────────────────────────────────────────
MEMORY_V3_MAX_RECORDS = 500
MEMORY_V3_IMPORT_LEGACY_LIMIT = 200
MEMORY_V3_RETRIEVAL_MAX_RESULTS = 50
MEMORY_V3_RETRIEVAL_MAX_CANDIDATES = 200
MEMORY_V3_MAX_STALKERS_SEEN_RECORDS = 75

# ── by_tag denylist and bucket cap (PR1) ─────────────────────────────────────
# Tags in this set are too noisy/broad to be useful in the index.
DO_NOT_INDEX_TAGS: frozenset[str] = frozenset({
    "active_plan",
    "repair",
    "step",
    "objective",
    "decision",
    "routine",
})

# Tag prefixes whose values are too specific/noisy (e.g. "objective:FIND_ARTIFACTS").
DO_NOT_INDEX_TAG_PREFIXES: tuple[str, ...] = (
    "objective:",
    "step:",
    "repair:",
)

# Maximum number of record IDs stored in any single by_tag bucket.
MAX_TAG_BUCKET_SIZE = 64

# Metrics counters for by_tag indexing (readable externally).
_TAG_METRICS: dict[str, int] = {
    "memory_by_tag_refs": 0,
    "memory_by_tag_skipped_refs": 0,
}

_INDEX_KEYS: tuple[str, ...] = (
    "by_layer",
    "by_kind",
    "by_location",
    "by_entity",
    "by_item_type",
    "by_tag",
)
_STALE_STATUSES = frozenset({"stale", "contradicted"})
_CRITICAL_KINDS = frozenset({
    "combat_killed",
    "combat_kill",
    "corpse_seen",
    "death",
    "emission_started",
    "emission_warning",
    "global_goal_completed",
    "target_corpse_reported",
    "target_corpse_seen",
    "target_death_confirmed",
    "target_intel",
    "target_seen",
})
_RECENT_PROTECTED_KINDS = frozenset({"emission_started", "emission_warning"})
_RECENT_PROTECTED_WINDOW_TURNS = 100
_LOW_RETENTION_KINDS = frozenset({
    "active_plan_failure_summary",
    "objective_decision",
    "objective_decision_summary",
    "stalkers_seen",
    "travel_hop",
})


def get_tag_metrics() -> dict[str, int]:
    """Return a snapshot of by_tag indexing metrics."""
    return dict(_TAG_METRICS)


def reset_tag_metrics() -> None:
    """Reset tag metrics to zero."""
    for key in _TAG_METRICS:
        _TAG_METRICS[key] = 0


def _empty_indexes() -> dict[str, dict[str, list[str]]]:
    return {key: {} for key in _INDEX_KEYS}


def _default_memory_stats() -> dict[str, Any]:
    return {
        "records_count": 0,
        "last_decay_turn": None,
        "last_consolidation_turn": None,
        "memory_revision": 0,
        "memory_evictions": 0,
        "dropped_new_records": 0,
        "memory_write_attempts": 0,
        "memory_write_dropped": 0,
        "memory_index_rebuilds": 0,
    }


def _ensure_memory_indexes(mem_v3: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    indexes = mem_v3.setdefault("indexes", {})
    if not isinstance(indexes, dict):
        indexes = {}
        mem_v3["indexes"] = indexes
    for key in _INDEX_KEYS:
        bucket = indexes.get(key)
        if not isinstance(bucket, dict):
            indexes[key] = {}
    return indexes


def _ensure_memory_stats(mem_v3: dict[str, Any]) -> dict[str, Any]:
    stats = mem_v3.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        mem_v3["stats"] = stats
    for key, default in _default_memory_stats().items():
        stats.setdefault(key, default)
    return stats


def _empty_memory_v3() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "records": {},
        "indexes": _empty_indexes(),
        "stats": _default_memory_stats(),
    }


def ensure_memory_v3(agent: dict[str, Any]) -> dict[str, Any]:
    """Return ``agent["memory_v3"]``, creating it if absent.

    This is idempotent and safe to call on every tick.
    """
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        mem_v3 = _empty_memory_v3()
        agent["memory_v3"] = mem_v3
    _ensure_memory_indexes(mem_v3)
    stats = mem_v3.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        mem_v3["stats"] = stats
    stats.setdefault("records_count", 0)
    stats.setdefault("last_decay_turn", None)
    stats.setdefault("last_consolidation_turn", None)
    return mem_v3


def _generate_record_id() -> str:
    return "mem_" + uuid.uuid4().hex[:12]


def add_memory_record(agent: dict[str, Any], record: MemoryRecord) -> bool:
    """Add a MemoryRecord to ``memory_v3`` using incremental index maintenance."""
    mem_v3 = ensure_memory_v3(agent)
    stats = _ensure_memory_stats(mem_v3)
    records = mem_v3.setdefault("records", {})
    if not isinstance(records, dict):
        records = {}
        mem_v3["records"] = records

    raw = record.to_dict()
    stats["memory_write_attempts"] = int(stats.get("memory_write_attempts", 0)) + 1

    had_existing = record.id in records
    existing_raw = records.get(record.id)
    if had_existing and isinstance(existing_raw, dict) and existing_raw == raw:
        stats["records_count"] = len(records)
        return True

    changed = False
    if had_existing:
        previous_raw = records.pop(record.id, None)
        if isinstance(previous_raw, dict):
            deindex_raw_record(mem_v3, previous_raw)
        changed = True
    elif len(records) >= MEMORY_V3_MAX_RECORDS:
        victim_id = choose_eviction_candidate(mem_v3, incoming_raw=raw)
        if victim_id is None:
            stats["dropped_new_records"] = int(stats.get("dropped_new_records", 0)) + 1
            stats["memory_write_dropped"] = int(stats.get("memory_write_dropped", 0)) + 1
            stats["records_count"] = len(records)
            return False
        victim_raw = records.pop(victim_id, None)
        if isinstance(victim_raw, dict):
            deindex_raw_record(mem_v3, victim_raw)
        stats["memory_evictions"] = int(stats.get("memory_evictions", 0)) + 1
        changed = True

    records[record.id] = raw
    index_raw_record(mem_v3, raw)
    changed = True

    stalkers_seen_evictions = _enforce_stalkers_seen_budget(mem_v3)
    if stalkers_seen_evictions > 0:
        stats["memory_evictions"] = int(stats.get("memory_evictions", 0)) + stalkers_seen_evictions

    stats["records_count"] = len(records)
    if changed or stalkers_seen_evictions > 0:
        stats["memory_revision"] = int(stats.get("memory_revision", 0)) + 1
    return True


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

def _append_unique(bucket: list[str], record_id: str) -> None:
    if record_id not in bucket:
        bucket.append(record_id)


def _should_index_tag(tag: str) -> bool:
    if tag in DO_NOT_INDEX_TAGS:
        return False
    return not tag.startswith(DO_NOT_INDEX_TAG_PREFIXES)


def _record_index_entries(raw: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {
        "by_layer": [],
        "by_kind": [],
        "by_location": [],
        "by_entity": [],
        "by_item_type": [],
        "by_tag": [],
    }
    layer = str(raw.get("layer") or "")
    kind = str(raw.get("kind") or "")
    if layer:
        values["by_layer"].append(layer)
    if kind:
        values["by_kind"].append(kind)

    location_id = str(raw.get("location_id") or "")
    if location_id:
        values["by_location"].append(location_id)

    for entity_id in raw.get("entity_ids", []) or ():
        entity_key = str(entity_id or "")
        if entity_key and entity_key not in values["by_entity"]:
            values["by_entity"].append(entity_key)

    for item_type in raw.get("item_types", []) or ():
        item_key = str(item_type or "")
        if item_key and item_key not in values["by_item_type"]:
            values["by_item_type"].append(item_key)

    for tag in raw.get("tags", []) or ():
        tag_key = str(tag or "")
        if tag_key and tag_key not in values["by_tag"]:
            values["by_tag"].append(tag_key)
    return values


def index_raw_record(mem_v3: dict[str, Any], raw: dict[str, Any]) -> None:
    record_id = str(raw.get("id") or "")
    if not record_id:
        return

    indexes = _ensure_memory_indexes(mem_v3)
    entries = _record_index_entries(raw)
    for index_name in ("by_layer", "by_kind", "by_location", "by_entity", "by_item_type"):
        bucket_map = indexes[index_name]
        for key in entries[index_name]:
            bucket = bucket_map.setdefault(key, [])
            _append_unique(bucket, record_id)

    by_tag = indexes["by_tag"]
    for tag in entries["by_tag"]:
        _TAG_METRICS["memory_by_tag_refs"] += 1
        if not _should_index_tag(tag):
            _TAG_METRICS["memory_by_tag_skipped_refs"] += 1
            continue
        bucket = by_tag.setdefault(tag, [])
        if record_id in bucket:
            continue
        if len(bucket) >= MAX_TAG_BUCKET_SIZE:
            bucket.pop(0)
        bucket.append(record_id)


def _index_record(mem_v3: dict[str, Any], record: MemoryRecord) -> None:
    index_raw_record(mem_v3, record.to_dict())


def deindex_raw_record(mem_v3: dict[str, Any], raw: dict[str, Any]) -> None:
    record_id = str(raw.get("id") or "")
    if not record_id:
        return

    indexes = mem_v3.get("indexes")
    if not isinstance(indexes, dict):
        return

    for bucket_map in indexes.values():
        if not isinstance(bucket_map, dict):
            continue
        empty_keys: list[str] = []
        for key, bucket in list(bucket_map.items()):
            if not isinstance(bucket, list):
                continue
            filtered = [rid for rid in bucket if rid != record_id]
            if filtered:
                bucket_map[key] = filtered
            else:
                empty_keys.append(key)
        for key in empty_keys:
            bucket_map.pop(key, None)


def _deindex_record(mem_v3: dict[str, Any], record: MemoryRecord) -> None:
    deindex_raw_record(mem_v3, record.to_dict())


def _status_evict_rank(status: Any) -> int:
    s = str(status or "active")
    if s == "archived":
        return 0
    if s in _STALE_STATUSES:
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
    tags = {str(tag or "") for tag in raw.get("tags", []) or () if tag}

    if kind in _CRITICAL_KINDS:
        return 100
    if kind.startswith("semantic_"):
        return 85
    if layer == "threat":
        return 90
    if layer == "semantic":
        return 80
    if layer == "goal":
        return 55
    if kind in _LOW_RETENTION_KINDS:
        return 20
    if action_kind.startswith("active_plan_"):
        return 15
    if {"routine", "travel", "crowd"} & tags:
        return 20
    return 40


def _record_reference_turn(mem_v3: dict[str, Any], incoming_raw: dict[str, Any] | None = None) -> int:
    max_turn = _coerce_int((incoming_raw or {}).get("created_turn"), 0)
    records = mem_v3.get("records", {})
    if isinstance(records, dict):
        for raw in records.values():
            if not isinstance(raw, dict):
                continue
            max_turn = max(max_turn, _coerce_int(raw.get("created_turn"), 0))
    return max_turn


def _is_target_related_record(raw: dict[str, Any]) -> bool:
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    if str(raw.get("kind") or "") in {"target_corpse_seen", "target_corpse_reported"}:
        return True
    if str(raw.get("kind") or "") != "corpse_seen":
        return False
    if details.get("target_id") or details.get("corpse_target_id"):
        return True
    tags = {str(tag or "") for tag in raw.get("tags", []) or () if tag}
    return "target" in tags


def _is_protected_record(raw: dict[str, Any], reference_turn: int) -> bool:
    kind = str(raw.get("kind") or "")
    if kind in _CRITICAL_KINDS:
        return True
    if _is_target_related_record(raw):
        return True
    if kind in _RECENT_PROTECTED_KINDS:
        age = max(0, reference_turn - _coerce_int(raw.get("created_turn"), reference_turn))
        return age <= _RECENT_PROTECTED_WINDOW_TURNS
    return False


def _keep_priority(raw: dict[str, Any], reference_turn: int) -> tuple[Any, ...]:
    return (
        _status_evict_rank(raw.get("status")),
        1 if _is_protected_record(raw, reference_turn) else 0,
        _retention_priority(raw),
        _coerce_float(raw.get("importance"), 0.5),
        _coerce_float(raw.get("confidence"), 1.0),
        _coerce_int(raw.get("created_turn"), 0),
        str(raw.get("id") or ""),
    )


def _lowest_priority_record_id(mem_v3: dict[str, Any], *, reference_turn: int) -> str | None:
    records = mem_v3.get("records", {})
    if not isinstance(records, dict) or not records:
        return None

    selected_id: str | None = None
    selected_priority: tuple[Any, ...] | None = None
    for record_id, raw in records.items():
        candidate_raw = raw if isinstance(raw, dict) else {"id": record_id}
        candidate_raw.setdefault("id", record_id)
        priority = _keep_priority(candidate_raw, reference_turn)
        if selected_priority is None or priority < selected_priority:
            selected_id = record_id
            selected_priority = priority
    return selected_id


def choose_eviction_candidate(
    mem_v3: dict[str, Any],
    *,
    incoming_raw: dict[str, Any],
) -> str | None:
    reference_turn = _record_reference_turn(mem_v3, incoming_raw)
    victim_id = _lowest_priority_record_id(mem_v3, reference_turn=reference_turn)
    if victim_id is None:
        return None

    records = mem_v3.get("records", {})
    victim_raw = records.get(victim_id, {}) if isinstance(records, dict) else {}
    incoming_priority = _keep_priority(incoming_raw, reference_turn)
    victim_priority = _keep_priority(victim_raw if isinstance(victim_raw, dict) else {"id": victim_id}, reference_turn)
    if incoming_priority <= victim_priority:
        return None
    return victim_id


def rebuild_memory_indexes(mem_v3: dict[str, Any]) -> None:
    records = mem_v3.get("records", {})
    if not isinstance(records, dict):
        records = {}
        mem_v3["records"] = records

    mem_v3["indexes"] = _empty_indexes()
    for record_id in sorted(records.keys()):
        raw = records.get(record_id)
        if isinstance(raw, dict):
            index_raw_record(mem_v3, raw)

    stats = _ensure_memory_stats(mem_v3)
    stats["records_count"] = len(records)
    stats["memory_index_rebuilds"] = int(stats.get("memory_index_rebuilds", 0)) + 1
    stats["memory_revision"] = int(stats.get("memory_revision", 0)) + 1


def _rebuild_indexes_from_records(mem_v3: dict[str, Any]) -> None:
    rebuild_memory_indexes(mem_v3)


def _enforce_stalkers_seen_budget(mem_v3: dict[str, Any]) -> int:
    records = mem_v3.get("records", {})
    if not isinstance(records, dict):
        return 0

    stalkers_seen_ids: list[tuple[int, str]] = []
    for record_id, raw in records.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "") != "stalkers_seen":
            continue
        stalkers_seen_ids.append((_coerce_int(raw.get("created_turn"), 0), record_id))

    if len(stalkers_seen_ids) <= MEMORY_V3_MAX_STALKERS_SEEN_RECORDS:
        return 0

    stalkers_seen_ids.sort(key=lambda item: (item[0], item[1]))
    evicted_total = 0
    overflow = len(stalkers_seen_ids) - MEMORY_V3_MAX_STALKERS_SEEN_RECORDS
    for _, record_id in stalkers_seen_ids[:overflow]:
        victim_raw = records.pop(record_id, None)
        if isinstance(victim_raw, dict):
            deindex_raw_record(mem_v3, victim_raw)
        evicted_total += 1
    return evicted_total


def trim_memory_v3_to_cap(
    agent: dict[str, Any],
    *,
    max_records: int = MEMORY_V3_MAX_RECORDS,
) -> int:
    """Evict records until hard cap is respected, then rebuild indexes."""
    mem_v3 = ensure_memory_v3(agent)
    stats = _ensure_memory_stats(mem_v3)
    records = mem_v3.get("records", {})
    if not isinstance(records, dict):
        records = {}
        mem_v3["records"] = records

    evicted_total = _enforce_stalkers_seen_budget(mem_v3)
    reference_turn = _record_reference_turn(mem_v3)
    while len(records) > max_records:
        victim_id = _lowest_priority_record_id(mem_v3, reference_turn=reference_turn)
        if victim_id is None:
            victim_id = sorted(records.keys())[0]
        victim_raw = records.pop(victim_id, None)
        if isinstance(victim_raw, dict):
            deindex_raw_record(mem_v3, victim_raw)
        evicted_total += 1

    if evicted_total > 0:
        stats["memory_evictions"] = int(stats.get("memory_evictions", 0)) + evicted_total
        rebuild_memory_indexes(mem_v3)
    else:
        stats["records_count"] = len(records)
    return evicted_total


def validate_memory_indexes(mem_v3: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    records = mem_v3.get("records", {})
    if not isinstance(records, dict):
        return ["records is not a dict"]

    indexes = mem_v3.get("indexes", {})
    if not isinstance(indexes, dict):
        return ["indexes is not a dict"]

    for index_name in _INDEX_KEYS:
        bucket_map = indexes.get(index_name)
        if not isinstance(bucket_map, dict):
            errors.append(f"{index_name} is not a dict")
            continue
        for key, bucket in bucket_map.items():
            if not isinstance(bucket, list):
                errors.append(f"{index_name}[{key!r}] is not a list")
                continue
            if not bucket:
                errors.append(f"{index_name}[{key!r}] is empty")
                continue
            if len(bucket) != len(set(bucket)):
                errors.append(f"{index_name}[{key!r}] contains duplicate ids")
            if index_name == "by_tag" and len(bucket) > MAX_TAG_BUCKET_SIZE:
                errors.append(f"{index_name}[{key!r}] exceeds cap")
            for record_id in bucket:
                raw = records.get(record_id)
                if not isinstance(raw, dict):
                    errors.append(f"{index_name}[{key!r}] references missing record {record_id!r}")
                    continue
                if index_name == "by_layer" and str(raw.get("layer") or "") != key:
                    errors.append(f"{record_id!r} is indexed under wrong layer {key!r}")
                elif index_name == "by_kind" and str(raw.get("kind") or "") != key:
                    errors.append(f"{record_id!r} is indexed under wrong kind {key!r}")
                elif index_name == "by_location" and str(raw.get("location_id") or "") != key:
                    errors.append(f"{record_id!r} is indexed under wrong location {key!r}")
                elif index_name == "by_entity" and key not in {str(value) for value in raw.get("entity_ids", []) or ()}:
                    errors.append(f"{record_id!r} is indexed under wrong entity {key!r}")
                elif (
                    index_name == "by_item_type"
                    and key not in {str(value) for value in raw.get("item_types", []) or ()}
                ):
                    errors.append(f"{record_id!r} is indexed under wrong item type {key!r}")
                elif index_name == "by_tag":
                    tag_values = {str(value) for value in raw.get("tags", []) or ()}
                    if key not in tag_values:
                        errors.append(f"{record_id!r} is indexed under wrong tag {key!r}")
                    if not _should_index_tag(key):
                        errors.append(f"{record_id!r} is indexed under denied tag {key!r}")

    for record_id, raw in records.items():
        if not isinstance(raw, dict):
            errors.append(f"record {record_id!r} is not a dict")
            continue
        for index_name, keys in _record_index_entries(raw).items():
            if index_name == "by_tag":
                continue
            bucket_map = indexes.get(index_name, {})
            if not isinstance(bucket_map, dict):
                continue
            for key in keys:
                bucket = bucket_map.get(key, [])
                if record_id not in bucket:
                    errors.append(f"{record_id!r} missing from {index_name}[{key!r}]")

    stats = _ensure_memory_stats(mem_v3)
    if int(stats.get("records_count", -1)) != len(records):
        errors.append("stats.records_count does not match records size")
    return errors


def normalize_agent_memory_state(agent: dict[str, Any]) -> dict[str, int]:
    """Normalize memory_v3 structure for oversized/old states."""
    legacy_trimmed = 0

    mem_v3 = ensure_memory_v3(agent)
    records = mem_v3.get("records")
    if not isinstance(records, dict):
        mem_v3["records"] = {}
        records = mem_v3["records"]

    indexes = mem_v3.get("indexes")
    repair_needed = not isinstance(indexes, dict)
    if repair_needed:
        mem_v3["indexes"] = _empty_indexes()
    else:
        for key in _INDEX_KEYS:
            if not isinstance(indexes.get(key), dict):
                repair_needed = True
                break

    memory_v3_evicted = trim_memory_v3_to_cap(agent)
    if validate_memory_indexes(mem_v3):
        repair_needed = True

    indexes_rebuilt = 0
    if repair_needed:
        rebuild_memory_indexes(mem_v3)
        indexes_rebuilt = 1

    _ensure_memory_stats(mem_v3)["records_count"] = len(records)
    return {
        "legacy_trimmed": legacy_trimmed,
        "memory_v3_evicted": memory_v3_evicted,
        "indexes_rebuilt": indexes_rebuilt,
    }
