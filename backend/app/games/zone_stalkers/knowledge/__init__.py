"""knowledge — compact structured world knowledge tables for zone_stalkers agents.

PR3: replaces event-spam representation of world knowledge with compact upsert-based tables.
"""
from .knowledge_store import (
    ensure_knowledge_v1,
    upsert_known_npc,
    upsert_known_location,
    upsert_known_trader,
    upsert_known_hazard,
    effective_known_npc_confidence,
    build_knowledge_summary,
)
from .knowledge_builder import (
    build_known_entities_from_knowledge,
    build_known_locations_from_knowledge,
    build_known_traders_from_knowledge,
    build_known_hazards_from_knowledge,
)

__all__ = [
    "ensure_knowledge_v1",
    "upsert_known_npc",
    "upsert_known_location",
    "upsert_known_trader",
    "upsert_known_hazard",
    "effective_known_npc_confidence",
    "build_knowledge_summary",
    "build_known_entities_from_knowledge",
    "build_known_locations_from_knowledge",
    "build_known_traders_from_knowledge",
    "build_known_hazards_from_knowledge",
]
