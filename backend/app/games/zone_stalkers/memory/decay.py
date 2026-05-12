"""memory/decay.py — Simple decay and minimal consolidation for MemoryStore v3.

Decay cadence: every 100 turns per agent (section 13).
Minimal consolidation: same kind + same subject/location >= 3 observations
→ create/update a semantic record (section 14).
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import MemoryRecord, LAYER_SEMANTIC, LAYER_THREAT, LAYER_GOAL
from .store import (
    ensure_memory_v3,
    add_memory_record,
    _deindex_record,
    MEMORY_V3_MAX_RECORDS,
)

# How often (in world turns) to run decay per agent.
DECAY_CADENCE_TURNS = 100

# Thresholds for effective_score below which records become archived.
_ARCHIVE_THRESHOLD = 0.3
_ARCHIVE_MIN_AGE_TURNS = 200

# Layers/statuses that are protected from archiving.
_PROTECTED_LAYERS = frozenset({LAYER_SEMANTIC, LAYER_THREAT, LAYER_GOAL})

# Minimum observations before episodic → semantic consolidation.
_CONSOLIDATION_MIN_OBSERVATIONS = 3


def decay_memory(agent: dict[str, Any], world_turn: int) -> None:
    """Run decay pass on ``memory_v3`` if enough turns have elapsed.

    Safe to call every tick — it skips work when cadence not met.
    """
    if not agent.get("is_alive", True):
        # Dead agents: skip consolidation — no new semantic records after death.
        return

    mem_v3 = ensure_memory_v3(agent)
    stats = mem_v3["stats"]
    last_decay = stats.get("last_decay_turn")

    if last_decay is not None and (world_turn - last_decay) < DECAY_CADENCE_TURNS:
        return  # Not time yet.

    stats["last_decay_turn"] = world_turn
    _run_decay_pass(mem_v3, world_turn)
    _run_consolidation(mem_v3, agent, world_turn)
    mem_v3["stats"]["records_count"] = len(mem_v3["records"])


def _run_decay_pass(mem_v3: dict[str, Any], world_turn: int) -> None:
    """Archive low-value old records."""
    records: dict[str, Any] = mem_v3["records"]
    to_archive: list[str] = []

    for rid, d in records.items():
        layer = d.get("layer", "")
        status = d.get("status", "active")
        if status == "archived":
            continue
        if layer in _PROTECTED_LAYERS:
            continue

        age = world_turn - int(d.get("created_turn", world_turn))
        if age < _ARCHIVE_MIN_AGE_TURNS:
            continue

        importance = float(d.get("importance", 0.5))
        confidence = float(d.get("confidence", 1.0))
        emotional_weight = float(d.get("emotional_weight", 0.0))
        # Simple recency bonus: 0 for old records (age >= _ARCHIVE_MIN_AGE_TURNS).
        effective_score = importance + confidence + emotional_weight

        if effective_score < _ARCHIVE_THRESHOLD:
            to_archive.append(rid)

    for rid in to_archive:
        records[rid]["status"] = "archived"


def _run_consolidation(mem_v3: dict[str, Any], agent: dict[str, Any], world_turn: int) -> None:
    """Consolidate repeated episodic observations into semantic records.

    Rule: if the same (kind, location_id) pair is observed >= 3 times in
    episodic layer, create or update a semantic record for it.
    """
    records: dict[str, Any] = mem_v3["records"]

    # Gather episodic records grouped by (kind, location_id).
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for d in records.values():
        if d.get("layer") != "episodic":
            continue
        key = (d.get("kind", ""), d.get("location_id"))
        groups.setdefault(key, []).append(d)

    for (kind, loc_id), group_records in groups.items():
        if not kind:
            continue
        if len(group_records) < _CONSOLIDATION_MIN_OBSERVATIONS:
            continue

        # Check whether a semantic record for this key already exists.
        semantic_kind = f"semantic_{kind}"
        existing_semantic_id: str | None = None
        for d in records.values():
            if d.get("layer") == LAYER_SEMANTIC and d.get("kind") == semantic_kind:
                if d.get("location_id") == loc_id:
                    existing_semantic_id = d.get("id")
                    break

        avg_confidence = sum(float(d.get("confidence", 0.7)) for d in group_records) / len(group_records)
        avg_importance = sum(float(d.get("importance", 0.5)) for d in group_records) / len(group_records)
        summary = group_records[-1].get("summary", f"{kind} at {loc_id}")

        # Collect all tags from the group.
        all_tags: set[str] = {"semantic", kind}
        for d in group_records:
            all_tags.update(d.get("tags", []))

        if existing_semantic_id:
            # Update existing semantic record in-place.
            existing = records[existing_semantic_id]
            existing["confidence"] = min(1.0, avg_confidence + 0.05)
            existing["importance"] = min(1.0, avg_importance + 0.05)
            existing["summary"] = summary
            existing["tags"] = list(all_tags)
            existing["details"]["observation_count"] = len(group_records)
            existing["details"]["last_updated_turn"] = world_turn
        else:
            # Create a new semantic record.
            new_id = "mem_sem_" + uuid.uuid4().hex[:10]
            semantic_record = MemoryRecord(
                id=new_id,
                agent_id=group_records[0].get("agent_id", ""),
                layer=LAYER_SEMANTIC,
                kind=semantic_kind,
                created_turn=world_turn,
                last_accessed_turn=world_turn,
                summary=summary,
                details={
                    "observation_count": len(group_records),
                    "last_updated_turn": world_turn,
                },
                location_id=loc_id,
                tags=tuple(all_tags),
                importance=min(1.0, avg_importance + 0.05),
                confidence=min(1.0, avg_confidence + 0.05),
                source="inferred",
            )
            add_memory_record(agent, semantic_record)
