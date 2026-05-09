from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetBelief:
    target_id: str
    is_known: bool
    is_alive: bool | None
    last_known_location_id: str | None
    location_confidence: float
    last_seen_turn: int | None
    visible_now: bool
    co_located: bool
    equipment_known: bool
    combat_strength: float | None
    combat_strength_confidence: float
    route_hints: tuple[str, ...]
    source_refs: tuple[str, ...]
