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
from .location_knowledge import (
    LOCATION_KNOWLEDGE_UNKNOWN,
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_ROUTE_ONLY,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_VISITED,
    ensure_location_knowledge_v1,
    get_known_location,
    build_location_knowledge_snapshot,
    mark_location_visited,
    mark_neighbor_locations_known,
    get_known_neighbor_ids,
    summarize_location_knowledge,
)
from .knowledge_hunt_builder import (
    build_hunt_leads_from_knowledge,
    build_recent_target_contact_from_knowledge,
    build_equipment_belief_from_knowledge,
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
    "LOCATION_KNOWLEDGE_UNKNOWN",
    "LOCATION_KNOWLEDGE_EXISTS",
    "LOCATION_KNOWLEDGE_ROUTE_ONLY",
    "LOCATION_KNOWLEDGE_SNAPSHOT",
    "LOCATION_KNOWLEDGE_VISITED",
    "ensure_location_knowledge_v1",
    "get_known_location",
    "build_location_knowledge_snapshot",
    "mark_location_visited",
    "mark_neighbor_locations_known",
    "get_known_neighbor_ids",
    "summarize_location_knowledge",
    "build_hunt_leads_from_knowledge",
    "build_recent_target_contact_from_knowledge",
    "build_equipment_belief_from_knowledge",
]
