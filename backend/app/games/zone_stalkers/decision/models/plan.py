"""Plan and PlanStep — short executable plans built from an Intent.

A Plan is a short ordered list of PlanSteps that realise a selected Intent.
``scheduled_action`` in the agent state maps to the *current* executing PlanStep
during the migration period (see ``bridges.py``).

Plan invariant (11.2 from spec):
    Every ``scheduled_action`` should ultimately be part of a Plan rather
    than appearing out of thin air.  During Phase 1–4 the bridge maintains
    this mapping for backwards-compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Valid plan step kinds ─────────────────────────────────────────────────────
STEP_TRAVEL_TO_LOCATION = "travel_to_location"
STEP_SLEEP_FOR_HOURS = "sleep_for_hours"
STEP_EXPLORE_LOCATION = "explore_location"
STEP_TRADE_BUY_ITEM = "trade_buy_item"
STEP_TRADE_SELL_ITEM = "trade_sell_item"
STEP_CONSUME_ITEM = "consume_item"
STEP_EQUIP_ITEM = "equip_item"
STEP_PICKUP_ITEM = "pickup_item"
STEP_HEAL_SELF = "heal_self"
STEP_HEAL_ALLY = "heal_ally"
STEP_ASK_FOR_INTEL = "ask_for_intel"
STEP_START_DIALOGUE = "start_dialogue"
STEP_JOIN_COMBAT = "join_combat"
STEP_RETREAT_FROM_COMBAT = "retreat_from_combat"
STEP_FOLLOW_LEADER = "follow_leader"
STEP_SHARE_SUPPLIES = "share_supplies"
STEP_WAIT = "wait"

# ── Legacy bridge kinds ────────────────────────────────────────────────────────
# These wrap existing tick_rules scheduled_action types.
STEP_LEGACY_SCHEDULED_ACTION = "legacy_scheduled_action"


@dataclass
class PlanStep:
    """A single executable step in a Plan.

    Parameters
    ----------
    kind
        One of the ``STEP_*`` constants above.
    payload
        Arbitrary kwargs consumed by the executor for this step.
        Mirrors ``scheduled_action`` dict layout where applicable.
    interruptible
        If ``False``, a hard interrupt cannot displace this step mid-execution
        (use only for instantaneous actions).
    expected_duration_ticks
        How many ticks this step is expected to occupy.  Used for planning
        and display only; actual duration comes from ``scheduled_action``.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    interruptible: bool = True
    expected_duration_ticks: int = 1


@dataclass
class Plan:
    """A short ordered sequence of PlanSteps that realise one Intent.

    Parameters
    ----------
    intent_kind
        The Intent kind this plan was built for.
    steps
        Ordered list of PlanSteps.  ``current_step_index`` points to the
        step currently being executed.
    current_step_index
        Index into ``steps`` for the active step.
    interruptible
        Whether the whole plan can be replaced by a higher-priority intent.
    confidence
        0.0–1.0 estimate of how likely this plan will succeed.
    created_turn
        World turn when this plan was built.
    expires_turn
        World turn after which the plan should be rebuilt.
        ``None`` means it persists until completion or interrupt.
    """

    intent_kind: str
    steps: list[PlanStep] = field(default_factory=list)
    current_step_index: int = 0
    interruptible: bool = True
    confidence: float = 0.5
    created_turn: Optional[int] = None
    expires_turn: Optional[int] = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def current_step(self) -> Optional[PlanStep]:
        """Return the active step, or ``None`` if the plan is complete."""
        if self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        """True when all steps have been executed."""
        return self.current_step_index >= len(self.steps)

    def advance(self) -> None:
        """Mark current step done and move to the next."""
        self.current_step_index += 1
