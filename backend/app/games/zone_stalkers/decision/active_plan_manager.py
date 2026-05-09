"""Active Plan Manager — CRUD and monitoring for ActivePlanV3.

Functions
---------
create_active_plan(objective_decision, world_turn, plan)
    Build an ``ActivePlanV3`` from an ``ObjectiveDecision`` + short ``Plan``.
get_active_plan(agent)
    Deserialise ``agent["active_plan_v3"]`` → ``ActivePlanV3`` (or ``None``).
save_active_plan(agent, active_plan)
    Serialise and persist ``ActivePlanV3`` into the agent dict.
clear_active_plan(agent)
    Remove ``active_plan_v3`` key from the agent dict.
assess_active_plan_v3(agent, state, world_turn)
    Main monitor: return ``(operation, reason)`` tuple.
repair_active_plan(agent, active_plan, repair_reason, world_turn)
    Attempt to repair a plan; returns the (possibly-aborted) plan.
should_replace_active_plan(agent, new_objective_key)
    ``True`` when the new objective differs from the current plan's objective.
"""
from __future__ import annotations

from typing import Any, Optional

from .models.active_plan import (
    ActivePlanStep,
    ActivePlanV3,
    ACTIVE_PLAN_STATUS_ACTIVE,
    ACTIVE_PLAN_STATUS_ABORTED,
    ACTIVE_PLAN_STATUS_COMPLETED,
    ACTIVE_PLAN_STATUS_REPAIRING,
    MAX_REPAIR_COUNT,
    STEP_STATUS_PENDING,
)
from .models.objective import ObjectiveDecision
from .models.plan import Plan
from .plan_monitor import _is_emission_threat_for_monitor

from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HP_THRESHOLD,
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)

_AGENT_KEY = "active_plan_v3"


# ── Factory ───────────────────────────────────────────────────────────────────

def create_active_plan(
    objective_decision: ObjectiveDecision,
    world_turn: int,
    plan: Plan,
) -> ActivePlanV3:
    """Create a new ``ActivePlanV3`` from an ``ObjectiveDecision`` + short ``Plan``.

    Parameters
    ----------
    objective_decision
        The winning objective decision that triggered this plan.
    world_turn
        Current world turn (stored as ``created_turn``).
    plan
        The short ``Plan`` whose steps are promoted to ``ActivePlanStep`` list.
    """
    objective = objective_decision.selected
    steps: list[ActivePlanStep] = [
        ActivePlanStep(
            kind=ps.kind,
            payload=dict(ps.payload),
            status=STEP_STATUS_PENDING,
        )
        for ps in plan.steps
    ]
    source_refs = list(objective.source_refs) if objective.source_refs else []
    return ActivePlanV3(
        objective_key=objective.key,
        status=ACTIVE_PLAN_STATUS_ACTIVE,
        created_turn=world_turn,
        updated_turn=world_turn,
        steps=steps,
        current_step_index=0,
        source_refs=source_refs,
        memory_refs=[],
        repair_count=0,
        abort_reason=None,
    )


# ── Agent dict I/O ────────────────────────────────────────────────────────────

def get_active_plan(agent: dict[str, Any]) -> Optional[ActivePlanV3]:
    """Return ``ActivePlanV3`` from the agent dict, or ``None`` if absent."""
    raw = agent.get(_AGENT_KEY)
    if not isinstance(raw, dict):
        return None
    return ActivePlanV3.from_dict(raw)


def save_active_plan(agent: dict[str, Any], active_plan: ActivePlanV3) -> None:
    """Serialise and store ``ActivePlanV3`` in the agent dict (in-place)."""
    agent[_AGENT_KEY] = active_plan.to_dict()


def clear_active_plan(agent: dict[str, Any]) -> None:
    """Remove ``active_plan_v3`` from the agent dict."""
    agent.pop(_AGENT_KEY, None)


# ── Monitor ───────────────────────────────────────────────────────────────────

def assess_active_plan_v3(
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> tuple[str, Optional[str]]:
    """Assess whether the active plan should continue, be repaired, or aborted.

    Returns
    -------
    tuple of (operation, reason) where operation is one of:
        ``"continue"``  — plan is still valid, keep executing.
        ``"repair"``    — plan assumptions broke; attempt repair.
        ``"abort"``     — unrecoverable; clear the plan.
        ``"complete"``  — plan has finished all steps.
    """
    active_plan = get_active_plan(agent)

    if active_plan is None:
        return ("abort", "no_active_plan")

    # Already in a terminal state.
    if active_plan.status in (
        ACTIVE_PLAN_STATUS_COMPLETED,
        ACTIVE_PLAN_STATUS_ABORTED,
    ):
        return ("complete" if active_plan.status == ACTIVE_PLAN_STATUS_COMPLETED else "abort",
                active_plan.abort_reason)

    if active_plan.is_complete:
        return ("complete", None)

    # Too many repair attempts → abort.
    if active_plan.repair_count >= MAX_REPAIR_COUNT:
        return ("abort", "max_repairs_exceeded")

    # ── Emission threat ───────────────────────────────────────────────────────
    if _is_emission_threat_for_monitor(agent, state):
        return ("repair", "emission_interrupt")

    # ── Critical survival needs ───────────────────────────────────────────────
    hp = float(agent.get("hp", 100))
    thirst = float(agent.get("thirst", 0))
    hunger = float(agent.get("hunger", 0))

    if hp <= CRITICAL_HP_THRESHOLD:
        return ("repair", "critical_hp")
    if thirst >= CRITICAL_THIRST_THRESHOLD:
        return ("repair", "critical_thirst")
    if hunger >= CRITICAL_HUNGER_THRESHOLD:
        return ("repair", "critical_hunger")

    # ── Step-level assumption checks ──────────────────────────────────────────
    step = active_plan.current_step
    if step is not None:
        # Trader unavailable: step payload has trader_id but that trader is not
        # in state["known_traders"].
        trader_id = step.payload.get("trader_id")
        if trader_id is not None:
            known_traders = state.get("known_traders") or []
            if isinstance(known_traders, list):
                trader_ids = {t if isinstance(t, str) else t.get("id") for t in known_traders}
            elif isinstance(known_traders, dict):
                trader_ids = set(known_traders.keys())
            else:
                trader_ids = set()
            if trader_id not in trader_ids:
                return ("repair", "trader_unavailable")

        # Location empty: step payload has location_id and agent memory shows
        # confirmed_empty for that location.
        location_id = step.payload.get("location_id")
        if location_id is not None:
            for mem in agent.get("memory", []):
                mem_loc = mem.get("location_id") or mem.get("effects", {}).get("location_id")
                if mem_loc == location_id and mem.get("confirmed_empty", False):
                    return ("repair", "target_location_empty")

        # Supply depletion mid-plan: step requires a supply item but inventory
        # is missing it.
        required_item = step.payload.get("required_item")
        if required_item is not None:
            inventory: list[dict[str, Any]] = agent.get("inventory") or []
            has_item = any(
                item.get("type") == required_item or item.get("id") == required_item
                for item in inventory
                if isinstance(item, dict)
            )
            if not has_item:
                return ("repair", "supplies_consumed_mid_plan")

    return ("continue", None)


# ── Repair ────────────────────────────────────────────────────────────────────

def repair_active_plan(
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    repair_reason: str,
    world_turn: int,
) -> ActivePlanV3:
    """Attempt to repair the plan.  Aborts if repair_count would exceed limit.

    The repair strategy resets the current step to *pending* (so the executor
    will re-evaluate) and transitions the plan to *active* state.  If
    ``repair_count`` already equals ``MAX_REPAIR_COUNT - 1`` after this
    increment, the plan is aborted instead.

    Parameters
    ----------
    agent
        Agent dict (not mutated here; caller must call ``save_active_plan``).
    active_plan
        The ``ActivePlanV3`` to repair (mutated in-place).
    repair_reason
        Why the repair was triggered.
    world_turn
        Current world turn.

    Returns
    -------
    The modified ``ActivePlanV3`` (same object).
    """
    active_plan.request_repair(repair_reason, world_turn)

    if active_plan.repair_count >= MAX_REPAIR_COUNT:
        active_plan.abort(f"max_repairs_exceeded_after_{repair_reason}", world_turn)
        return active_plan

    # Reset the current step back to pending so the executor will retry.
    step = active_plan.current_step
    if step is not None:
        step.status = STEP_STATUS_PENDING
        step.started_turn = None
        step.failure_reason = None

    # Transition back to active once the repair setup is done.
    active_plan.status = ACTIVE_PLAN_STATUS_ACTIVE
    return active_plan


# ── Replacement check ─────────────────────────────────────────────────────────

def should_replace_active_plan(
    agent: dict[str, Any],
    new_objective_key: str,
) -> bool:
    """Return ``True`` when the new objective differs from the current plan's.

    This allows the caller to decide whether to abandon the current plan in
    favour of a newly selected objective.
    """
    active_plan = get_active_plan(agent)
    if active_plan is None:
        return True
    if active_plan.status in (ACTIVE_PLAN_STATUS_ABORTED, ACTIVE_PLAN_STATUS_COMPLETED):
        return True
    return active_plan.objective_key != new_objective_key
