"""ActivePlan v3 — persistent long-running NPC execution context.

An ``ActivePlanV3`` wraps a multi-step operation that persists across multiple
ticks.  It is the *source of truth* for long-running execution, tracking which
step is being executed, how many repair attempts have been made, and which
memories justify the plan.

Hierarchy:
    Objective (why) → Intent (what) → Plan (how) → ActivePlanV3 (long execution)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Plan-level status constants ───────────────────────────────────────────────
ACTIVE_PLAN_STATUS_ACTIVE = "active"
ACTIVE_PLAN_STATUS_PAUSED = "paused"
ACTIVE_PLAN_STATUS_REPAIRING = "repairing"
ACTIVE_PLAN_STATUS_COMPLETED = "completed"
ACTIVE_PLAN_STATUS_FAILED = "failed"
ACTIVE_PLAN_STATUS_ABORTED = "aborted"

# ── Step-level status constants ───────────────────────────────────────────────
STEP_STATUS_PENDING = "pending"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_SKIPPED = "skipped"

# Maximum repair attempts before forcing an abort.
MAX_REPAIR_COUNT = 3


@dataclass
class ActivePlanStep:
    """One step in a long-running ActivePlanV3.

    Parameters
    ----------
    kind
        Step kind (reuses ``STEP_*`` constants from ``models/plan.py``).
    payload
        Arbitrary kwargs consumed by the executor for this step.
    status
        One of ``STEP_STATUS_*`` constants.
    started_turn
        World turn when this step started executing.
    completed_turn
        World turn when this step finished (completed, failed, or skipped).
    failure_reason
        Human-readable reason if this step failed.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = STEP_STATUS_PENDING
    started_turn: Optional[int] = None
    completed_turn: Optional[int] = None
    failure_reason: Optional[str] = None

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": dict(self.payload),
            "status": self.status,
            "started_turn": self.started_turn,
            "completed_turn": self.completed_turn,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActivePlanStep":
        return cls(
            kind=d["kind"],
            payload=dict(d.get("payload") or {}),
            status=d.get("status", STEP_STATUS_PENDING),
            started_turn=d.get("started_turn"),
            completed_turn=d.get("completed_turn"),
            failure_reason=d.get("failure_reason"),
        )


@dataclass
class ActivePlanV3:
    """Long-running execution context for a multi-step NPC operation.

    Parameters
    ----------
    id
        Unique plan identifier (UUID string).
    objective_key
        The Objective key this plan serves (e.g. ``"FIND_ARTIFACTS"``).
    status
        One of ``ACTIVE_PLAN_STATUS_*`` constants.
    created_turn
        World turn when this plan was created.
    updated_turn
        World turn of the last status change.
    steps
        Ordered list of ``ActivePlanStep`` objects.
    current_step_index
        Index of the step currently being executed.
    source_refs
        Objective source_refs carried forward from ``ObjectiveDecision``.
    memory_refs
        Memory record IDs that justify this plan (evidence chain).
    repair_count
        Number of repair attempts made so far.
    abort_reason
        Human-readable reason if the plan was aborted.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    objective_key: str = ""
    status: str = ACTIVE_PLAN_STATUS_ACTIVE
    created_turn: int = 0
    updated_turn: int = 0
    steps: list[ActivePlanStep] = field(default_factory=list)
    current_step_index: int = 0
    source_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    repair_count: int = 0
    abort_reason: Optional[str] = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def current_step(self) -> Optional[ActivePlanStep]:
        """Return the active step, or ``None`` if the plan is complete."""
        if self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        """True when all steps have been executed (index past end of list)."""
        return self.current_step_index >= len(self.steps)

    def advance_step(self, world_turn: int) -> None:
        """Mark the current step completed and advance to the next."""
        step = self.current_step
        if step is not None:
            step.status = STEP_STATUS_COMPLETED
            step.completed_turn = world_turn
        self.current_step_index += 1
        self.updated_turn = world_turn
        if self.is_complete:
            self.status = ACTIVE_PLAN_STATUS_COMPLETED

    def mark_failed(self, reason: str, world_turn: int) -> None:
        """Mark the current step and the overall plan as failed."""
        step = self.current_step
        if step is not None:
            step.status = STEP_STATUS_FAILED
            step.failure_reason = reason
            step.completed_turn = world_turn
        self.status = ACTIVE_PLAN_STATUS_FAILED
        self.abort_reason = reason
        self.updated_turn = world_turn

    def request_repair(self, reason: str, world_turn: int) -> None:
        """Transition the plan to *repairing* state and increment repair count."""
        self.status = ACTIVE_PLAN_STATUS_REPAIRING
        self.abort_reason = reason
        self.repair_count += 1
        self.updated_turn = world_turn

    def abort(self, reason: str, world_turn: int) -> None:
        """Permanently abort the plan."""
        self.status = ACTIVE_PLAN_STATUS_ABORTED
        self.abort_reason = reason
        self.updated_turn = world_turn

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_key": self.objective_key,
            "status": self.status,
            "created_turn": self.created_turn,
            "updated_turn": self.updated_turn,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "source_refs": list(self.source_refs),
            "memory_refs": list(self.memory_refs),
            "repair_count": self.repair_count,
            "abort_reason": self.abort_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActivePlanV3":
        steps = [ActivePlanStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            objective_key=d.get("objective_key", ""),
            status=d.get("status", ACTIVE_PLAN_STATUS_ACTIVE),
            created_turn=int(d.get("created_turn", 0)),
            updated_turn=int(d.get("updated_turn", 0)),
            steps=steps,
            current_step_index=int(d.get("current_step_index", 0)),
            source_refs=list(d.get("source_refs") or []),
            memory_refs=list(d.get("memory_refs") or []),
            repair_count=int(d.get("repair_count", 0)),
            abort_reason=d.get("abort_reason"),
        )
