from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class HuntLead:
    id: str
    target_id: str
    kind: str
    location_id: str | None
    route_from_id: str | None
    route_to_id: str | None
    created_turn: int
    observed_turn: int | None
    confidence: float
    freshness: float
    source: str
    source_ref: str | None
    source_agent_id: str | None
    expires_turn: int | None
    details: Mapping[str, Any]
