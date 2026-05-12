"""Lightweight PR1 monitor for active scheduled_action continuity checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HP_THRESHOLD,
    THIRST_INCREASE_PER_HOUR,
    HUNGER_INCREASE_PER_HOUR,
)

_HARD_INTERRUPT_THIRST_THRESHOLD = 90
_HARD_INTERRUPT_HUNGER_THRESHOLD = 90
_SOFT_INTERRUPT_THIRST_THRESHOLD = 70
_SOFT_INTERRUPT_HUNGER_THRESHOLD = 70
_SOFT_INTERRUPT_SLEEPINESS_THRESHOLD = 75
_SOFT_INTERRUPT_MAX_REMAINING_TURNS = 2


@dataclass(slots=True)
class PlanMonitorResult:
    decision: Literal["continue", "pause", "abort", "adapt"]
    reason: str
    dominant_pressure: str | None = None
    dominant_pressure_value: float | None = None
    interruptible: bool = True
    should_run_decision_pipeline: bool = False
    should_clear_action_queue: bool = False
    debug_context: dict[str, Any] | None = None


def _is_emission_threat_for_monitor(
    agent: dict[str, Any], state: dict[str, Any]
) -> bool:
    """Detect emission threat without importing from tick_rules (avoids circular import).

    Returns True when:
    - emission is currently active, OR
    - the agent has an ``emission_imminent`` observation memory that is not
      superseded by a later ``emission_ended`` observation.
    """
    if state.get("emission_active", False):
        return True
    last_ended: int = 0
    last_imminent: int = 0
    from app.games.zone_stalkers.rules.tick_rules import _v3_records_desc, _v3_action_kind, _v3_memory_type, _v3_turn  # noqa: PLC0415
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        mk = _v3_action_kind(rec)
        mt = _v3_turn(rec)
        if mk == "emission_ended" and mt > last_ended:
            last_ended = mt
        elif mk == "emission_imminent" and mt > last_imminent:
            last_imminent = mt
    return last_imminent > last_ended


def is_v3_monitored_bot(agent: dict[str, Any]) -> bool:
    if not agent.get("is_alive", True):
        return False
    if agent.get("has_left_zone"):
        return False
    if agent.get("archetype") != "stalker_agent":
        return False
    if agent.get("controller", {}).get("kind") != "bot":
        return False
    return True


def _projected(value: float, world_minute: int, increase_per_hour: int) -> float:
    if world_minute == 59:
        return min(100.0, value + increase_per_hour)
    return value


def _brain_context(agent: dict[str, Any]) -> dict[str, Any]:
    ctx = agent.get("brain_v3_context")
    return ctx if isinstance(ctx, dict) else {}


def _scheduled_action_remaining_turns(
    *,
    scheduled_action: dict[str, Any],
    world_turn: int,
) -> int:
    turns_remaining = scheduled_action.get("turns_remaining")
    if isinstance(turns_remaining, (int, float)):
        return max(0, int(turns_remaining))
    ends_turn = scheduled_action.get("ends_turn")
    if isinstance(ends_turn, (int, float)):
        return max(0, int(ends_turn) - int(world_turn))
    return 0


def _has_active_support_exhaustion(
    *,
    agent: dict[str, Any],
    objective_key: str,
    location_id: str,
    target_id: str,
    world_turn: int,
) -> bool:
    from app.games.zone_stalkers.rules.tick_rules import _v3_action_kind, _v3_details, _v3_records_desc  # noqa: PLC0415

    objective_key = str(objective_key or "")
    for rec in _v3_records_desc(agent):
        action_kind = _v3_action_kind(rec)
        if action_kind not in {"anomaly_search_exhausted", "support_source_exhausted", "witness_source_exhausted"}:
            continue
        details = _v3_details(rec)
        cooldown_until = details.get("cooldown_until_turn")
        if isinstance(cooldown_until, (int, float)) and int(cooldown_until) <= world_turn:
            continue
        rec_location_id = str(details.get("location_id") or rec.get("location_id") or "")
        if location_id and rec_location_id and rec_location_id != location_id:
            continue
        rec_target_id = str(details.get("target_id") or "")
        if target_id and rec_target_id and rec_target_id != target_id:
            continue
        if action_kind == "anomaly_search_exhausted" and objective_key in {"GET_MONEY_FOR_RESUPPLY", "FIND_ARTIFACTS"}:
            return True
        if action_kind in {"support_source_exhausted", "witness_source_exhausted"} and objective_key in {
            "GATHER_INTEL", "LOCATE_TARGET", "VERIFY_LEAD", "TRACK_TARGET", "GET_MONEY_FOR_RESUPPLY",
        }:
            return True
    return False


def evaluate_scheduled_action_interrupts(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    context: Any,
    scheduled_action: dict[str, Any],
    world_turn: int,
) -> dict[str, Any]:
    ctx = context if isinstance(context, dict) else _brain_context(agent)
    target_belief = ctx.get("hunt_target_belief") if isinstance(ctx.get("hunt_target_belief"), dict) else {}
    objective_key = str(ctx.get("objective_key") or "")
    intent_kind = str(ctx.get("intent_kind") or "")
    global_goal = str(agent.get("global_goal") or "")
    active_plan = agent.get("active_plan_v3")
    active_plan_id = active_plan.get("id") if isinstance(active_plan, dict) else None
    not_attacking_reasons = list(ctx.get("not_attacking_reasons") or [])
    combat_ready = bool(ctx.get("combat_ready")) if ctx.get("combat_ready") is not None else None
    target_visible_now = bool(target_belief.get("visible_now")) if target_belief else False
    target_co_located = bool(target_belief.get("co_located")) if target_belief else False
    location_id = str(agent.get("location_id") or scheduled_action.get("target_id") or "")
    target_id = str(agent.get("kill_target_id") or target_belief.get("target_id") or "")

    interrupts_checked = [
        "critical_hp",
        "critical_thirst",
        "critical_hunger",
        "soft_sleepiness",
        "soft_thirst",
        "soft_hunger",
        "emission",
        "target_visible",
        "support_source_exhausted",
        "global_goal_completed",
    ]
    interrupt_triggered: str | None = None
    if _is_emission_threat_for_monitor(agent, state):
        interrupt_triggered = "emission_danger"
    elif float(agent.get("hp", 100)) <= CRITICAL_HP_THRESHOLD:
        interrupt_triggered = "critical_hp"
    elif _projected(float(agent.get("thirst", 0)), int(state.get("world_minute", 0)), THIRST_INCREASE_PER_HOUR) >= _HARD_INTERRUPT_THIRST_THRESHOLD:
        interrupt_triggered = "critical_thirst"
    elif _projected(float(agent.get("hunger", 0)), int(state.get("world_minute", 0)), HUNGER_INCREASE_PER_HOUR) >= _HARD_INTERRUPT_HUNGER_THRESHOLD:
        interrupt_triggered = "critical_hunger"
    elif bool(agent.get("global_goal_achieved")) and objective_key not in {"LEAVE_ZONE", "CONFIRM_KILL"}:
        interrupt_triggered = "global_goal_completed"
    elif _has_active_support_exhaustion(
        agent=agent,
        objective_key=objective_key,
        location_id=location_id,
        target_id=target_id,
        world_turn=world_turn,
    ):
        interrupt_triggered = "support_source_exhausted"
    elif global_goal == "kill_stalker" and target_visible_now and bool(combat_ready):
        interrupt_triggered = "target_visible_and_combat_ready"

    support_objective_for = ctx.get("support_objective_for")
    if support_objective_for is None and global_goal == "kill_stalker" and objective_key in {
        "GET_MONEY_FOR_RESUPPLY",
        "PREPARE_FOR_HUNT",
        "RESUPPLY_WEAPON",
        "RESUPPLY_AMMO",
        "RESUPPLY_ARMOR",
        "RESUPPLY_FOOD",
        "RESUPPLY_DRINK",
        "RESUPPLY_MEDICINE",
    }:
        support_objective_for = "kill_stalker"

    thirst_projected = _projected(float(agent.get("thirst", 0)), int(state.get("world_minute", 0)), THIRST_INCREASE_PER_HOUR)
    hunger_projected = _projected(float(agent.get("hunger", 0)), int(state.get("world_minute", 0)), HUNGER_INCREASE_PER_HOUR)
    sleepiness = float(agent.get("sleepiness", 0))
    remaining_turns = _scheduled_action_remaining_turns(scheduled_action=scheduled_action, world_turn=world_turn)
    action_type = str(scheduled_action.get("type") or "")
    soft_interruptible_action = action_type in {"explore_anomaly_location", "travel"}

    return {
        "objective_key": objective_key or None,
        "intent_kind": intent_kind or None,
        "global_goal": global_goal or None,
        "support_objective_for": support_objective_for,
        "scheduled_action_type": scheduled_action.get("type"),
        "active_plan_id": active_plan_id,
        "target_visible_now": target_visible_now,
        "target_co_located": target_co_located,
        "combat_ready": combat_ready,
        "not_attacking_reasons": not_attacking_reasons,
        "interrupts_checked": interrupts_checked,
        "interrupt_triggered": interrupt_triggered,
        "soft_interrupt": {
            "thirst_projected": thirst_projected,
            "hunger_projected": hunger_projected,
            "sleepiness": sleepiness,
            "scheduled_action_type": action_type,
            "remaining_turns": remaining_turns,
            "max_remaining_turns": _SOFT_INTERRUPT_MAX_REMAINING_TURNS,
            "should_interrupt": bool(
                sleepiness >= _SOFT_INTERRUPT_SLEEPINESS_THRESHOLD
                or thirst_projected >= _SOFT_INTERRUPT_THIRST_THRESHOLD
                or hunger_projected >= _SOFT_INTERRUPT_HUNGER_THRESHOLD
            ) and remaining_turns > _SOFT_INTERRUPT_MAX_REMAINING_TURNS and soft_interruptible_action,
        },
        "sleep_need": {
            "raw_sleepiness": int(sleepiness),
            "interpreted_fatigue": int(sleepiness),
            "scale": "sleepiness_high_means_tired",
        },
    }


def assess_scheduled_action_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    scheduled_action: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> PlanMonitorResult:
    ctx = _brain_context(agent)
    debug_context = evaluate_scheduled_action_interrupts(
        agent=agent,
        state=state,
        context=ctx,
        scheduled_action=scheduled_action,
        world_turn=world_turn,
    )
    _ = agent_id

    if not is_v3_monitored_bot(agent):
        return PlanMonitorResult(decision="continue", reason="not_monitored", debug_context=debug_context)

    if not scheduled_action:
        return PlanMonitorResult(decision="continue", reason="no_scheduled_action", debug_context=debug_context)

    if scheduled_action.get("emergency_flee"):
        return PlanMonitorResult(
            decision="continue",
            reason="emergency_flee_is_uninterruptible",
            interruptible=False,
            debug_context=debug_context,
        )

    interrupt_triggered = debug_context.get("interrupt_triggered")
    if interrupt_triggered == "emission_danger":
        return PlanMonitorResult(
            decision="abort",
            reason="emission_threat",
            dominant_pressure="emission",
            dominant_pressure_value=1.0,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    world_minute = int(state.get("world_minute", 0))
    hp = float(agent.get("hp", 100))
    thirst = _projected(float(agent.get("thirst", 0)), world_minute, THIRST_INCREASE_PER_HOUR)
    hunger = _projected(float(agent.get("hunger", 0)), world_minute, HUNGER_INCREASE_PER_HOUR)
    sleepiness = float(agent.get("sleepiness", 0))

    if interrupt_triggered == "critical_hp" or hp <= CRITICAL_HP_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_hp",
            dominant_pressure="hp",
            dominant_pressure_value=hp,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    if interrupt_triggered == "critical_thirst" or thirst >= _HARD_INTERRUPT_THIRST_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_thirst",
            dominant_pressure="thirst",
            dominant_pressure_value=thirst,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    if interrupt_triggered == "critical_hunger" or hunger >= _HARD_INTERRUPT_HUNGER_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_hunger",
            dominant_pressure="hunger",
            dominant_pressure_value=hunger,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    if interrupt_triggered in {"support_source_exhausted", "target_visible_and_combat_ready", "global_goal_completed"}:
        return PlanMonitorResult(
            decision="abort",
            reason=str(interrupt_triggered),
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    action_type = str(scheduled_action.get("type") or "")
    soft_interruptible_action = action_type in {"explore_anomaly_location", "travel"}
    soft_needs_triggered = (
        sleepiness >= _SOFT_INTERRUPT_SLEEPINESS_THRESHOLD
        or thirst >= _SOFT_INTERRUPT_THIRST_THRESHOLD
        or hunger >= _SOFT_INTERRUPT_HUNGER_THRESHOLD
    )
    remaining_turns = _scheduled_action_remaining_turns(scheduled_action=scheduled_action, world_turn=world_turn)
    if soft_interruptible_action and soft_needs_triggered and remaining_turns > _SOFT_INTERRUPT_MAX_REMAINING_TURNS:
        return PlanMonitorResult(
            decision="abort",
            reason="soft_restore_needs_interrupt",
            dominant_pressure="restore_needs",
            dominant_pressure_value=max(sleepiness, thirst, hunger),
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
            debug_context=debug_context,
        )

    return PlanMonitorResult(
        decision="continue",
        reason="action_still_valid",
        dominant_pressure=None,
        dominant_pressure_value=None,
        debug_context=debug_context,
    )
