from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.games.zone_stalkers.decision.beliefs import BeliefState
    from app.games.zone_stalkers.decision.models.target_belief import TargetBelief
    from .need_evaluation import NeedEvaluationResult


@dataclass(frozen=True)
class Objective:
    key: str
    source: str
    urgency: float
    expected_value: float
    risk: float
    time_cost: float
    resource_cost: float
    confidence: float
    goal_alignment: float
    memory_confidence: float
    target: dict[str, Any] | None = None
    required_capabilities: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectiveScore:
    objective_key: str
    raw_score: float
    final_score: float
    factors: tuple[dict[str, Any], ...]
    penalties: tuple[dict[str, Any], ...]
    decision: str | None = None


@dataclass(frozen=True)
class ObjectiveDecision:
    selected: Objective
    selected_score: ObjectiveScore
    alternatives: tuple[tuple[Objective, ObjectiveScore], ...]
    continue_current_score: ObjectiveScore | None = None
    switch_decision: str = "new_objective"
    reason: str = ""


@dataclass(frozen=True)
class ObjectiveGenerationContext:
    agent_id: str
    world_turn: int
    belief_state: "BeliefState"
    need_result: "NeedEvaluationResult"
    active_plan_summary: dict[str, Any] | None
    personality: dict[str, Any]
    target_belief: "TargetBelief | None" = None
