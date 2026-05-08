"""memory/models.py — MemoryRecord and MemoryQuery dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryRecord:
    """A single structured memory entry stored in ``agent["memory_v3"]``."""

    id: str
    agent_id: str
    layer: str          # working / episodic / semantic / spatial / social / threat / goal
    kind: str           # free-form kind tag, e.g. "trader_location_known", "item_bought"
    created_turn: int
    last_accessed_turn: int | None

    summary: str
    details: dict = field(default_factory=dict, hash=False, compare=False)

    location_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    item_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    importance: float = 0.5
    confidence: float = 1.0
    emotional_weight: float = 0.0
    decay_rate: float = 0.01

    status: str = "active"   # active, stale, contradicted, archived
    source: str = "observed"  # observed, inferred, heard, legacy_import
    evidence_refs: tuple[str, ...] = ()
    world_time: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this record."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "layer": self.layer,
            "kind": self.kind,
            "created_turn": self.created_turn,
            "last_accessed_turn": self.last_accessed_turn,
            "summary": self.summary,
            "details": self.details,
            "location_id": self.location_id,
            "entity_ids": list(self.entity_ids),
            "item_types": list(self.item_types),
            "tags": list(self.tags),
            "importance": self.importance,
            "confidence": self.confidence,
            "emotional_weight": self.emotional_weight,
            "decay_rate": self.decay_rate,
            "status": self.status,
            "source": self.source,
            "evidence_refs": list(self.evidence_refs),
            "world_time": self.world_time,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MemoryRecord":
        """Reconstruct a MemoryRecord from its serialised dict form."""
        return MemoryRecord(
            id=d["id"],
            agent_id=d["agent_id"],
            layer=d["layer"],
            kind=d["kind"],
            created_turn=d["created_turn"],
            last_accessed_turn=d.get("last_accessed_turn"),
            summary=d.get("summary", ""),
            details=d.get("details", {}),
            location_id=d.get("location_id"),
            entity_ids=tuple(d.get("entity_ids", [])),
            item_types=tuple(d.get("item_types", [])),
            tags=tuple(d.get("tags", [])),
            importance=float(d.get("importance", 0.5)),
            confidence=float(d.get("confidence", 1.0)),
            emotional_weight=float(d.get("emotional_weight", 0.0)),
            decay_rate=float(d.get("decay_rate", 0.01)),
            status=d.get("status", "active"),
            source=d.get("source", "observed"),
            evidence_refs=tuple(d.get("evidence_refs", [])),
            world_time=d.get("world_time"),
        )


# ── Valid layer names ─────────────────────────────────────────────────────────

LAYER_WORKING  = "working"
LAYER_EPISODIC = "episodic"
LAYER_SEMANTIC = "semantic"
LAYER_SPATIAL  = "spatial"
LAYER_SOCIAL   = "social"
LAYER_THREAT   = "threat"
LAYER_GOAL     = "goal"

VALID_LAYERS: frozenset[str] = frozenset({
    LAYER_WORKING,
    LAYER_EPISODIC,
    LAYER_SEMANTIC,
    LAYER_SPATIAL,
    LAYER_SOCIAL,
    LAYER_THREAT,
    LAYER_GOAL,
})


# ── MemoryQuery ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemoryQuery:
    """Parameters for retrieving memory records from ``memory_v3``."""

    purpose: str
    layers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    location_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    item_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    max_results: int = 10
    include_stale: bool = False
