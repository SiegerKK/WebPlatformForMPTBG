"""memory/retrieval.py — MemoryStore v3 retrieval with deterministic scoring.

Scoring formula (section 9 of PR3 contract):

  score =
      tag_match     * 0.25
    + kind_match    * 0.20
    + location_match* 0.20
    + confidence    * 0.15
    + importance    * 0.10
    + recency       * 0.10
    - stale_penalty

Tie-breaker: (-score, -created_turn, record.id)   — fully deterministic, no random.
"""
from __future__ import annotations

from typing import Any

from .models import MemoryRecord, MemoryQuery
from .store import ensure_memory_v3, MEMORY_V3_RETRIEVAL_MAX_RESULTS

# Penalty applied to stale records when include_stale is True.
_STALE_PENALTY = 0.30
# Turns window for full recency bonus.
_RECENCY_FULL_WINDOW = 50


def retrieve_memory(
    agent: dict[str, Any],
    query: MemoryQuery,
    world_turn: int,
) -> list[MemoryRecord]:
    """Return top-N MemoryRecords matching *query*, scored and sorted.

    Result count is capped at ``min(query.max_results, MEMORY_V3_RETRIEVAL_MAX_RESULTS)``.
    Results are deterministic — no randomness.
    """
    mem_v3 = ensure_memory_v3(agent)
    records_raw: dict[str, Any] = mem_v3.get("records", {})
    if not records_raw:
        return []

    cap = min(query.max_results, MEMORY_V3_RETRIEVAL_MAX_RESULTS)
    indexes = mem_v3.get("indexes", {})

    # Build candidate set from indexes (intersection of all non-empty filters).
    candidate_ids: set[str] | None = None

    def _intersect(ids: list[str]) -> None:
        nonlocal candidate_ids
        s = set(ids)
        candidate_ids = s if candidate_ids is None else candidate_ids & s

    if query.layers:
        merged: set[str] = set()
        for layer in query.layers:
            for rid in indexes.get("by_layer", {}).get(layer, []):
                merged.add(rid)
        _intersect(list(merged))

    if query.kinds:
        merged = set()
        for kind in query.kinds:
            for rid in indexes.get("by_kind", {}).get(kind, []):
                merged.add(rid)
        _intersect(list(merged))

    if query.location_id:
        _intersect(indexes.get("by_location", {}).get(query.location_id, []))

    if query.entity_ids:
        merged = set()
        for eid in query.entity_ids:
            for rid in indexes.get("by_entity", {}).get(eid, []):
                merged.add(rid)
        _intersect(list(merged))

    if query.item_types:
        merged = set()
        for itype in query.item_types:
            for rid in indexes.get("by_item_type", {}).get(itype, []):
                merged.add(rid)
        _intersect(list(merged))

    if query.tags:
        # Tags: use union (any matching tag qualifies), then score by how many match.
        merged = set()
        for tag in query.tags:
            for rid in indexes.get("by_tag", {}).get(tag, []):
                merged.add(rid)
        _intersect(list(merged))

    # If no filter was applied, consider all records.
    if candidate_ids is None:
        candidate_ids = set(records_raw.keys())

    # Deserialise and score.
    scored: list[tuple[float, int, str, MemoryRecord]] = []
    query_tags_set = set(query.tags)
    query_kinds_set = set(query.kinds)

    for rid in candidate_ids:
        raw = records_raw.get(rid)
        if raw is None:
            continue
        record = MemoryRecord.from_dict(raw)

        # Skip stale/archived unless requested.
        if not query.include_stale and record.status in ("stale", "archived", "contradicted"):
            continue

        score = _score_record(record, query, world_turn, query_tags_set, query_kinds_set)
        # Tie-breaker: (neg_score, neg_created_turn, id) → ascending sort → best first.
        scored.append((-score, -record.created_turn, record.id, record))

    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    selected = scored[:cap]

    # Update last_accessed_turn for selected records.
    for _, _, rid, _ in selected:
        raw = records_raw.get(rid)
        if isinstance(raw, dict):
            raw["last_accessed_turn"] = world_turn

    return [MemoryRecord.from_dict(records_raw[rid]) for _, _, rid, _ in selected if rid in records_raw]


def _score_record(
    record: MemoryRecord,
    query: MemoryQuery,
    world_turn: int,
    query_tags_set: set[str],
    query_kinds_set: set[str],
) -> float:
    # Tag match: fraction of query tags matched.
    if query_tags_set:
        matched_tags = len(query_tags_set & set(record.tags))
        tag_match = matched_tags / len(query_tags_set)
    else:
        tag_match = 0.0

    # Kind match.
    kind_match = 1.0 if (query_kinds_set and record.kind in query_kinds_set) else 0.0

    # Location match.
    location_match = 1.0 if (query.location_id and record.location_id == query.location_id) else 0.0

    # Recency: 1.0 for brand-new, decays to 0.0 at _RECENCY_FULL_WINDOW turns.
    age = world_turn - record.created_turn
    recency = max(0.0, 1.0 - age / _RECENCY_FULL_WINDOW)

    # Stale penalty.
    stale_penalty = _STALE_PENALTY if record.status in ("stale", "contradicted") else 0.0

    return (
        tag_match     * 0.25
        + kind_match    * 0.20
        + location_match * 0.20
        + record.confidence * 0.15
        + record.importance * 0.10
        + recency       * 0.10
        - stale_penalty
    )
