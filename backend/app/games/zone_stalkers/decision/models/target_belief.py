from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class LocationHypothesis:
    location_id: str
    probability: float
    confidence: float
    freshness: float
    reason: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class RouteHypothesis:
    from_location_id: str | None
    to_location_id: str | None
    confidence: float
    freshness: float
    reason: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class TargetBelief:
    target_id: str
    is_known: bool
    is_alive: bool | None
    last_known_location_id: str | None
    location_confidence: float
    best_location_id: str | None
    best_location_confidence: float
    last_seen_turn: int | None
    visible_now: bool
    co_located: bool
    equipment_known: bool
    combat_strength: float | None
    combat_strength_confidence: float
    possible_locations: tuple[LocationHypothesis, ...]
    likely_routes: tuple[RouteHypothesis, ...]
    exhausted_locations: tuple[str, ...]
    lead_count: int
    route_hints: tuple[str, ...]
    source_refs: tuple[str, ...]
    # Fix 4 — recently_seen fields (all optional with defaults so existing code works)
    recently_seen: bool = False
    recent_contact_turn: Optional[int] = None
    recent_contact_location_id: Optional[str] = None
    recent_contact_age: Optional[int] = None
