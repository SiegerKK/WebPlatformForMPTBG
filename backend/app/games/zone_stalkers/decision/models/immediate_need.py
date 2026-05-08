from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImmediateNeed:
    """Urgent consume/heal need affecting current tick decisions."""

    key: str
    urgency: float
    current_value: float
    threshold: float
    trigger_context: str = "survival"
    blocks_intents: frozenset[str] = field(default_factory=frozenset)
    available_inventory_item_types: frozenset[str] = field(default_factory=frozenset)
    selected_item_id: str | None = None
    selected_item_type: str | None = None
    reason: str = ""
    source_factors: tuple[dict[str, Any], ...] = ()
