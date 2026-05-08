from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ItemNeed:
    """Stock/equipment need describing missing future supplies."""

    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    priority: int = 100
    source_factors: tuple[dict[str, Any], ...] = ()
    expected_min_price: int | None = None
    affordability_hint: str | None = None
