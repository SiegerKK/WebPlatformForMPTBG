"""bridges — compatibility bridge between legacy scheduled_action and Plan.

During the migration from the monolithic tick-rules cascade to the new
Perceive→Evaluate→Intend→Plan→Act pipeline, both representations coexist.

Rule (from migration spec §7.2):
    ``scheduled_action`` is treated as the *serialised current PlanStep*.
    When a Plan does not yet exist for an agent, one can be reconstructed
    from ``scheduled_action`` via ``plan_from_scheduled_action``.
    Conversely, a ``PlanStep`` can be serialised back into a
    ``scheduled_action`` dict via ``scheduled_action_from_plan_step``.
"""
from __future__ import annotations

from typing import Any, Optional

from .models.plan import Plan, PlanStep, STEP_TRAVEL_TO_LOCATION, STEP_SLEEP_FOR_HOURS, \
    STEP_EXPLORE_LOCATION, STEP_LEGACY_SCHEDULED_ACTION
from .models.intent import (
    INTENT_GET_RICH, INTENT_FLEE_EMISSION, INTENT_WAIT_IN_SHELTER,
    INTENT_HEAL_SELF, INTENT_SEEK_FOOD, INTENT_SEEK_WATER, INTENT_REST,
    INTENT_RESUPPLY, INTENT_HUNT_TARGET, INTENT_SEARCH_INFORMATION,
    INTENT_LEAVE_ZONE, INTENT_SELL_ARTIFACTS, INTENT_UPGRADE_EQUIPMENT,
    INTENT_EXPLORE, INTENT_IDLE,
)

# Mapping from legacy scheduled_action type → best-guess intent kind
_SCHED_TYPE_TO_INTENT: dict[str, str] = {
    "travel": INTENT_GET_RICH,        # overridden by caller if more specific
    "sleep": INTENT_REST,
    "explore_anomaly_location": INTENT_EXPLORE,
    "event": INTENT_IDLE,
}


def plan_from_scheduled_action(
    agent: dict[str, Any],
    world_turn: int = 0,
) -> Optional[Plan]:
    """Reconstruct a single-step ``Plan`` from the agent's ``scheduled_action``.

    This is a best-effort reconstruction — the plan will have a single
    ``PlanStep`` wrapping the raw ``scheduled_action`` dict.  The intent
    is inferred from the action type; the caller may override it.

    Parameters
    ----------
    agent
        The agent dict containing ``scheduled_action``.
    world_turn
        Current world turn (used to populate ``created_turn``).

    Returns
    -------
    Plan or None
        ``None`` when there is no ``scheduled_action`` in the agent.
    """
    sched = agent.get("scheduled_action")
    if not sched:
        return None

    action_type: str = sched.get("type", "")
    intent_kind = _infer_intent_from_scheduled_action(agent, sched)
    step_kind = _sched_type_to_step_kind(action_type)

    step = PlanStep(
        kind=step_kind,
        payload=dict(sched),          # copy to avoid aliasing
        interruptible=True,
        expected_duration_ticks=sched.get("turns_remaining", 1),
    )
    return Plan(
        intent_kind=intent_kind,
        steps=[step],
        current_step_index=0,
        interruptible=True,
        confidence=0.8,
        created_turn=world_turn,
        expires_turn=None,
    )


def scheduled_action_from_plan_step(step: PlanStep) -> dict[str, Any]:
    """Serialise a ``PlanStep`` into a legacy ``scheduled_action`` dict.

    The resulting dict is compatible with the existing tick-rules processor
    (``_process_scheduled_action`` in ``tick_rules.py``).

    Parameters
    ----------
    step
        The PlanStep to serialise.

    Returns
    -------
    dict
        A ``scheduled_action`` dict ready for ``agent["scheduled_action"]``.
    """
    if step.kind == STEP_TRAVEL_TO_LOCATION:
        return {
            "type": "travel",
            "target_id": step.payload.get("target_id"),
            "turns_remaining": step.payload.get("turns_remaining", step.expected_duration_ticks),
            "turns_total": step.payload.get("turns_total", step.expected_duration_ticks),
            **{k: v for k, v in step.payload.items()
               if k not in ("target_id", "turns_remaining", "turns_total")},
        }
    if step.kind == STEP_SLEEP_FOR_HOURS:
        return {
            "type": "sleep",
            "hours": step.payload.get("hours", 6),
            "turns_remaining": step.payload.get("turns_remaining", step.expected_duration_ticks),
            "turns_total": step.payload.get("turns_total", step.expected_duration_ticks),
        }
    if step.kind == STEP_EXPLORE_LOCATION:
        return {
            "type": "explore_anomaly_location",
            "target_id": step.payload.get("target_id"),
            "turns_remaining": step.payload.get("turns_remaining", step.expected_duration_ticks),
            "turns_total": step.payload.get("turns_total", step.expected_duration_ticks),
            "started_turn": step.payload.get("started_turn"),
        }
    if step.kind == STEP_LEGACY_SCHEDULED_ACTION:
        # Pass through the raw payload unchanged
        return dict(step.payload)
    # Fallback: return payload as-is with the step kind as "type"
    return {"type": step.kind, **step.payload}


# ── Private helpers ────────────────────────────────────────────────────────────

def _sched_type_to_step_kind(action_type: str) -> str:
    mapping = {
        "travel": STEP_TRAVEL_TO_LOCATION,
        "sleep": STEP_SLEEP_FOR_HOURS,
        "explore_anomaly_location": STEP_EXPLORE_LOCATION,
    }
    return mapping.get(action_type, STEP_LEGACY_SCHEDULED_ACTION)


def _infer_intent_from_scheduled_action(
    agent: dict[str, Any],
    sched: dict[str, Any],
) -> str:
    """Best-effort intent inference from a legacy scheduled_action."""
    action_type = sched.get("type", "")
    current_goal = agent.get("current_goal", "")
    global_goal = agent.get("global_goal", "")

    if action_type == "sleep":
        return INTENT_REST
    if action_type == "explore_anomaly_location":
        return INTENT_EXPLORE

    # For travel, use current_goal as a hint
    goal_to_intent: dict[str, str] = {
        "flee_emission": INTENT_FLEE_EMISSION,
        "get_weapon": INTENT_RESUPPLY,
        "get_armor": INTENT_RESUPPLY,
        "get_ammo": INTENT_RESUPPLY,
        "get_food": INTENT_SEEK_FOOD,
        "get_water": INTENT_SEEK_WATER,
        "get_heal": INTENT_HEAL_SELF,
        "sell_artifacts": INTENT_SELL_ARTIFACTS,
        "upgrade_equipment": INTENT_UPGRADE_EQUIPMENT,
        "hunt": INTENT_HUNT_TARGET,
        "leave_zone": INTENT_LEAVE_ZONE,
        "unravel_zone_mystery": INTENT_SEARCH_INFORMATION,
    }
    if current_goal in goal_to_intent:
        return goal_to_intent[current_goal]

    # Fall back to global goal
    if global_goal == "get_rich":
        return INTENT_GET_RICH
    if global_goal == "unravel_zone_mystery":
        return INTENT_SEARCH_INFORMATION
    if global_goal == "kill_stalker":
        return INTENT_HUNT_TARGET
    if current_goal == "kill_stalker" or agent.get("kill_target_id"):
        return INTENT_HUNT_TARGET

    return INTENT_GET_RICH
