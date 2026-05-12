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

import heapq
import time
from typing import Any

from .models import MemoryRecord, MemoryQuery
from .store import (
    ensure_memory_v3,
    MEMORY_V3_RETRIEVAL_MAX_RESULTS,
    MEMORY_V3_RETRIEVAL_MAX_CANDIDATES,
)

# Penalty applied to stale records when include_stale is True.
_STALE_PENALTY = 0.30
# Turns window for full recency bonus.
_RECENCY_FULL_WINDOW = 50


def retrieve_memory(
    agent: dict[str, Any],
    query: MemoryQuery,
    world_turn: int,
    *,
    track_access: bool = False,
    record_metrics: bool = False,
) -> list[MemoryRecord]:
    """Return top-N MemoryRecords matching *query*, scored and sorted.

    Result count is capped at ``min(query.max_results, MEMORY_V3_RETRIEVAL_MAX_RESULTS)``.
    Results are deterministic — no randomness.
    """
    mem_v3 = ensure_memory_v3(agent)
    records_raw: dict[str, Any] = mem_v3.get("records", {})
    if not records_raw:
        return []

    started = time.perf_counter() if record_metrics else 0.0
    cap = min(query.max_results, MEMORY_V3_RETRIEVAL_MAX_RESULTS)
    indexes = mem_v3.get("indexes", {})

    # Build candidate set from indexes (OR within category, AND across categories)
    # while intersecting from the smallest category first.
    candidate_buckets: list[set[str]] = []

    if query.layers:
        merged: set[str] = set()
        for layer in query.layers:
            merged.update(indexes.get("by_layer", {}).get(layer, []))
        candidate_buckets.append(merged)

    if query.kinds:
        merged = set()
        for kind in query.kinds:
            merged.update(indexes.get("by_kind", {}).get(kind, []))
        candidate_buckets.append(merged)

    if query.location_id:
        candidate_buckets.append(set(indexes.get("by_location", {}).get(query.location_id, [])))

    if query.entity_ids:
        merged = set()
        for eid in query.entity_ids:
            merged.update(indexes.get("by_entity", {}).get(eid, []))
        candidate_buckets.append(merged)

    if query.item_types:
        merged = set()
        for itype in query.item_types:
            merged.update(indexes.get("by_item_type", {}).get(itype, []))
        candidate_buckets.append(merged)

    if query.tags:
        merged = set()
        for tag in query.tags:
            merged.update(indexes.get("by_tag", {}).get(tag, []))
        candidate_buckets.append(merged)

    candidate_ids: set[str]
    if not candidate_buckets:
        candidate_ids = set(records_raw.keys())
    else:
        ordered = sorted(candidate_buckets, key=len)
        candidate_ids = set(ordered[0])
        for bucket in ordered[1:]:
            candidate_ids &= bucket

    # Deterministic candidate limiting.
    max_candidates = min(
        query.max_candidates or MEMORY_V3_RETRIEVAL_MAX_CANDIDATES,
        len(candidate_ids),
    )
    candidate_ids = _limit_candidate_ids(records_raw, candidate_ids, max_candidates)
    candidate_count = len(candidate_ids)

    # Score raw dicts first; deserialize only selected.
    scored: list[tuple[float, int, str]] = []
    query_tags_set = set(query.tags)
    query_kinds_set = set(query.kinds)

    for rid in candidate_ids:
        raw = records_raw.get(rid)
        if raw is None:
            continue
        status = str(raw.get("status", "active"))
        if not query.include_stale and status in ("stale", "archived", "contradicted"):
            continue

        score = _score_record_raw(raw, query, world_turn, query_tags_set, query_kinds_set)
        # Tie-breaker: (neg_score, neg_created_turn, id) → ascending sort → best first.
        scored.append((-score, -_coerce_int(raw.get("created_turn"), 0), rid))

    selected = heapq.nsmallest(cap, scored, key=lambda t: (t[0], t[1], t[2]))

    if track_access:
        for _, _, rid in selected:
            raw = records_raw.get(rid)
            if isinstance(raw, dict):
                raw["last_accessed_turn"] = world_turn

    result = [
        MemoryRecord.from_dict(records_raw[rid])
        for _, _, rid in selected
        if rid in records_raw
    ]
    if record_metrics:
        _update_retrieval_metrics(
            mem_v3,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            candidate_count=candidate_count,
            scored_count=len(scored),
            selected_count=len(result),
            from_dict_count=len(result),
        )
    return result


def _score_record_raw(
    raw: dict[str, Any],
    query: MemoryQuery,
    world_turn: int,
    query_tags_set: set[str],
    query_kinds_set: set[str],
) -> float:
    # Tag match: fraction of query tags matched.
    if query_tags_set:
        matched_tags = len(query_tags_set & set(raw.get("tags", [])))
        tag_match = matched_tags / len(query_tags_set)
    else:
        tag_match = 0.0

    # Kind match.
    kind_match = 1.0 if (query_kinds_set and str(raw.get("kind", "")) in query_kinds_set) else 0.0

    # Location match.
    location_match = 1.0 if (query.location_id and str(raw.get("location_id")) == query.location_id) else 0.0

    # Recency: 1.0 for brand-new, decays to 0.0 at _RECENCY_FULL_WINDOW turns.
    age = world_turn - _coerce_int(raw.get("created_turn"), 0)
    recency = max(0.0, 1.0 - age / _RECENCY_FULL_WINDOW)

    # Stale penalty.
    stale_penalty = _STALE_PENALTY if str(raw.get("status", "active")) in ("stale", "contradicted") else 0.0

    return (
        tag_match     * 0.25
        + kind_match    * 0.20
        + location_match * 0.20
        + _coerce_float(raw.get("confidence"), 1.0) * 0.15
        + _coerce_float(raw.get("importance"), 0.5) * 0.10
        + recency       * 0.10
        - stale_penalty
    )


def _status_rank(raw: dict[str, Any]) -> int:
    status = str(raw.get("status", "active"))
    if status == "active":
        return 0
    if status in {"stale", "contradicted"}:
        return 1
    return 2


def _limit_candidate_ids(
    records_raw: dict[str, Any],
    candidate_ids: set[str],
    max_candidates: int,
) -> set[str]:
    if max_candidates >= len(candidate_ids):
        return candidate_ids
    ranked = sorted(
        candidate_ids,
        key=lambda rid: (
            _status_rank(records_raw.get(rid, {})),
            -_coerce_float(records_raw.get(rid, {}).get("importance"), 0.5),
            -_coerce_float(records_raw.get(rid, {}).get("confidence"), 1.0),
            -_coerce_int(records_raw.get(rid, {}).get("created_turn"), 0),
            rid,
        ),
    )
    return set(ranked[:max_candidates])


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


def _update_retrieval_metrics(
    mem_v3: dict[str, Any],
    *,
    elapsed_ms: float,
    candidate_count: int,
    scored_count: int,
    selected_count: int,
    from_dict_count: int,
) -> None:
    stats = mem_v3.setdefault("stats", {})
    metrics = stats.setdefault("retrieval_metrics", {})
    metrics["memory_retrieval_calls"] = int(metrics.get("memory_retrieval_calls", 0)) + 1
    metrics["memory_retrieval_ms_total"] = float(metrics.get("memory_retrieval_ms_total", 0.0)) + elapsed_ms
    metrics["memory_retrieval_ms_max"] = max(float(metrics.get("memory_retrieval_ms_max", 0.0)), elapsed_ms)
    metrics["memory_retrieval_candidates_total"] = int(metrics.get("memory_retrieval_candidates_total", 0)) + candidate_count
    metrics["memory_retrieval_candidates_max"] = max(int(metrics.get("memory_retrieval_candidates_max", 0)), candidate_count)
    metrics["memory_retrieval_scored_total"] = int(metrics.get("memory_retrieval_scored_total", 0)) + scored_count
    metrics["memory_retrieval_scored_max"] = max(int(metrics.get("memory_retrieval_scored_max", 0)), scored_count)
    metrics["memory_retrieval_selected_total"] = int(metrics.get("memory_retrieval_selected_total", 0)) + selected_count
    metrics["memory_retrieval_from_dict_count"] = int(metrics.get("memory_retrieval_from_dict_count", 0)) + from_dict_count
