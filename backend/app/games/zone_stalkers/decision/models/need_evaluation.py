from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .immediate_need import ImmediateNeed
from .item_need import ItemNeed
from .need_scores import NeedScores


@dataclass(frozen=True)
class NeedEvaluationResult:
    """Single evaluated need snapshot reused by intent/planner/trace."""

    scores: NeedScores
    immediate_needs: tuple[ImmediateNeed, ...]
    item_needs: tuple[ItemNeed, ...]
    liquidity_summary: dict[str, Any] | None = None
