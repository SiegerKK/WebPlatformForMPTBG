"""ActivePlan runtime helpers for Zone Stalkers NPC Brain v3."""
from __future__ import annotations

from typing import Any, Callable

from app.games.zone_stalkers.decision.active_plan_manager import (
    assess_active_plan_v3,
    clear_active_plan,
    get_active_plan,
    repair_active_plan,
    save_active_plan,
)
from app.games.zone_stalkers.decision.debug.brain_trace import (
    write_active_plan_trace,
    write_plan_monitor_trace,
)
from app.games.zone_stalkers.decision.models.active_plan import (
    ActivePlanStep,
    ActivePlanV3,
    ACTIVE_PLAN_STATUS_ACTIVE,
    ACTIVE_PLAN_STATUS_ABORTED,
    ACTIVE_PLAN_STATUS_COMPLETED,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_FAILED,
    STEP_STATUS_PENDING,
    STEP_STATUS_RUNNING,
)
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_SEARCH_TARGET
from app.games.zone_stalkers.decision.plan_monitor import is_v3_monitored_bot

AddMemoryFn = Callable[..., None]
PlanMonitorDedupFn = Callable[..., bool]


def active_plan_step_label(step: ActivePlanStep | None) -> str:
    return step.kind if step is not None else "none"


def active_plan_trace_payload(active_plan: ActivePlanV3) -> dict[str, Any]:
    current_step = active_plan.current_step
    return {
        "active_plan_id": active_plan.id,
        "objective_key": active_plan.objective_key,
        "status": active_plan.status,
        "current_step_index": active_plan.current_step_index,
        "current_step_kind": active_plan_step_label(current_step),
        "steps_count": len(active_plan.steps),
        "repair_count": active_plan.repair_count,
        "source_refs": list(active_plan.source_refs),
        "memory_refs": list(active_plan.memory_refs),
    }


def write_active_plan_memory_event(
    agent: dict[str, Any],
    *,
    world_turn: int,
    state: dict[str, Any],
    action_kind: str,
    active_plan: ActivePlanV3,
    add_memory: AddMemoryFn,
    reason: str | None = None,
    payload_overrides: dict[str, Any] | None = None,
    summary_override: str | None = None,
) -> None:
    payload = {
        "action_kind": action_kind,
        **active_plan_trace_payload(active_plan),
        "step_index": active_plan.current_step_index,
        "step_kind": active_plan_step_label(active_plan.current_step),
    }
    if payload_overrides:
        payload.update(payload_overrides)
    if reason is not None:
        payload["reason"] = reason
    add_memory(
        agent,
        world_turn,
        state,
        "decision",
        f"🧭 {action_kind}",
        payload,
        summary=(
            summary_override
            if summary_override is not None
            else (
                f"ActivePlan {active_plan.objective_key}: "
                f"{action_kind} (шаг {active_plan.current_step_index + 1}/{max(1, len(active_plan.steps))}, "
                f"{active_plan_step_label(active_plan.current_step)})."
                + (f" Причина: {reason}." if reason else "")
            )
        ),
    )


def write_active_plan_trace_event(
    agent: dict[str, Any],
    *,
    world_turn: int,
    state: dict[str, Any],
    event: str,
    active_plan: ActivePlanV3,
    reason: str | None = None,
    summary: str | None = None,
) -> None:
    write_active_plan_trace(
        agent,
        world_turn=world_turn,
        event=event,
        active_plan=active_plan,
        reason=reason,
        summary=summary,
        state=state,
    )


def tag_scheduled_action_with_active_plan(
    scheduled_action: dict[str, Any] | None,
    active_plan: ActivePlanV3,
    step_index: int,
) -> None:
    if not isinstance(scheduled_action, dict):
        return
    scheduled_action["active_plan_id"] = active_plan.id
    scheduled_action["active_plan_step_index"] = step_index
    scheduled_action["active_plan_objective_key"] = active_plan.objective_key


def scheduled_action_matches_active_step(
    scheduled_action: dict[str, Any],
    step: ActivePlanStep | None,
) -> bool:
    if step is None:
        return False
    tagged_step_index = scheduled_action.get("active_plan_step_index")
    if tagged_step_index is not None:
        try:
            return int(tagged_step_index) >= 0
        except (TypeError, ValueError):
            return False
    action_type = str(scheduled_action.get("type") or "")
    step_kind = str(step.kind)
    if action_type == "travel" and step_kind == "travel_to_location":
        target = scheduled_action.get("final_target_id") or scheduled_action.get("target_id")
        step_target = (
            step.payload.get("location_id")
            or step.payload.get("target_id")
            or step.payload.get("final_target_id")
        )
        return step_target is None or target == step_target
    if action_type == "explore_anomaly_location" and step_kind == "explore_location":
        target = scheduled_action.get("target_id")
        step_target = step.payload.get("location_id") or step.payload.get("target_id")
        return step_target is None or target == step_target
    if action_type == "sleep" and step_kind == "sleep_for_hours":
        return True
    return False


def legacy_step_from_scheduled_action(
    scheduled_action: dict[str, Any],
    *,
    default_sleep_hours: int,
) -> ActivePlanStep | None:
    action_type = str(scheduled_action.get("type") or "")
    if action_type == "travel":
        return ActivePlanStep(
            kind="travel_to_location",
            payload={
                "location_id": scheduled_action.get("final_target_id") or scheduled_action.get("target_id"),
                "target_id": scheduled_action.get("target_id"),
                "final_target_id": scheduled_action.get("final_target_id"),
                "reason": "legacy_runtime_action",
            },
            status=STEP_STATUS_RUNNING,
            started_turn=scheduled_action.get("started_turn"),
        )
    if action_type == "explore_anomaly_location":
        return ActivePlanStep(
            kind="explore_location",
            payload={
                "location_id": scheduled_action.get("target_id"),
                "target_id": scheduled_action.get("target_id"),
                "reason": "legacy_runtime_action",
            },
            status=STEP_STATUS_RUNNING,
            started_turn=scheduled_action.get("started_turn"),
        )
    if action_type == "sleep":
        return ActivePlanStep(
            kind="sleep_for_hours",
            payload={"hours": scheduled_action.get("hours", default_sleep_hours)},
            status=STEP_STATUS_RUNNING,
            started_turn=scheduled_action.get("started_turn"),
        )
    if action_type == "event":
        return ActivePlanStep(
            kind="wait",
            payload={"reason": "legacy_runtime_event"},
            status=STEP_STATUS_RUNNING,
            started_turn=scheduled_action.get("started_turn"),
        )
    return None


def migrate_legacy_scheduled_action_to_active_plan(
    agent: dict[str, Any],
    *,
    world_turn: int,
    default_sleep_hours: int,
) -> None:
    if not is_v3_monitored_bot(agent):
        return
    if get_active_plan(agent) is not None:
        return
    scheduled_action = agent.get("scheduled_action")
    if not isinstance(scheduled_action, dict):
        return
    if scheduled_action.get("active_plan_id"):
        return

    legacy_step = legacy_step_from_scheduled_action(
        scheduled_action,
        default_sleep_hours=default_sleep_hours,
    )
    if legacy_step is None:
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        return

    active_plan = ActivePlanV3(
        objective_key="LEGACY_RUNTIME_ACTION",
        status=ACTIVE_PLAN_STATUS_ACTIVE,
        created_turn=world_turn,
        updated_turn=world_turn,
        steps=[legacy_step],
        current_step_index=0,
        source_refs=["legacy:scheduled_action"],
        memory_refs=[],
    )
    save_active_plan(agent, active_plan)
    tag_scheduled_action_with_active_plan(scheduled_action, active_plan, 0)
    agent["action_queue"] = []


def finish_active_plan(
    agent_id: str,
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    state: dict[str, Any],
    world_turn: int,
    *,
    add_memory: AddMemoryFn,
    terminal_event: str,
    reason: str | None = None,
) -> None:
    completed_steps = sum(1 for step in active_plan.steps if step.status == "completed")
    completion_summary = (
        f"ActivePlan {active_plan.objective_key}: completed, "
        f"{completed_steps}/{len(active_plan.steps)} steps completed."
    )
    event_summary = (
        completion_summary
        if terminal_event == "active_plan_completed"
        else (
            f"ActivePlan {active_plan.objective_key}: {terminal_event}."
            + (f" Причина: {reason}." if reason else "")
        )
    )
    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event=terminal_event,
        active_plan=active_plan,
        reason=reason,
        summary=event_summary,
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind=terminal_event,
        active_plan=active_plan,
        add_memory=add_memory,
        reason=reason,
        summary_override=event_summary,
    )
    clear_active_plan(agent)
    sched = agent.get("scheduled_action")
    if isinstance(sched, dict) and sched.get("active_plan_id") == active_plan.id:
        agent["scheduled_action"] = None
    agent["action_queue"] = []


def mark_active_plan_step_failed(
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    *,
    world_turn: int,
    state: dict[str, Any],
    add_memory: AddMemoryFn,
    reason: str,
) -> None:
    current_step = active_plan.current_step
    if current_step is None:
        return
    current_step.status = STEP_STATUS_FAILED
    current_step.failure_reason = reason
    current_step.completed_turn = world_turn
    active_plan.updated_turn = world_turn
    save_active_plan(agent, active_plan)
    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event="active_plan_step_failed",
        active_plan=active_plan,
        reason=reason,
        summary=(
            f"ActivePlan {active_plan.objective_key}: шаг "
            f"{active_plan.current_step_index + 1}/{len(active_plan.steps)} "
            f"{current_step.kind} завершился неудачей ({reason})."
        ),
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind="active_plan_step_failed",
        active_plan=active_plan,
        add_memory=add_memory,
        reason=reason,
    )


def start_or_continue_active_plan_step(
    agent_id: str,
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    state: dict[str, Any],
    world_turn: int,
    *,
    add_memory: AddMemoryFn,
) -> list[dict[str, Any]]:
    if agent.get("scheduled_action"):
        return []

    current_step = active_plan.current_step
    if current_step is None:
        active_plan.status = ACTIVE_PLAN_STATUS_COMPLETED
        save_active_plan(agent, active_plan)
        finish_active_plan(
            agent_id,
            agent,
            active_plan,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_completed",
        )
        return []

    if current_step.status not in (STEP_STATUS_PENDING, STEP_STATUS_RUNNING):
        return []

    current_step.status = STEP_STATUS_RUNNING
    current_step.started_turn = world_turn
    active_plan.updated_turn = world_turn
    save_active_plan(agent, active_plan)
    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event="active_plan_step_started",
        active_plan=active_plan,
        summary=(
            f"ActivePlan {active_plan.objective_key}: старт шага "
            f"{active_plan.current_step_index + 1}/{len(active_plan.steps)} "
            f"{current_step.kind}."
        ),
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind="active_plan_step_started",
        active_plan=active_plan,
        add_memory=add_memory,
    )

    from app.games.zone_stalkers.decision.context_builder import build_agent_context  # noqa: PLC0415
    from app.games.zone_stalkers.decision.executors import execute_plan_step  # noqa: PLC0415

    step_plan = Plan(
        intent_kind="active_plan_step",
        steps=[PlanStep(kind=current_step.kind, payload=dict(current_step.payload))],
        created_turn=world_turn,
    )
    ctx = build_agent_context(agent_id, agent, state)
    events = execute_plan_step(ctx, step_plan, state, world_turn)

    refreshed_plan = get_active_plan(agent) or active_plan
    if agent.get("scheduled_action"):
        tag_scheduled_action_with_active_plan(
            agent["scheduled_action"],
            refreshed_plan,
            refreshed_plan.current_step_index,
        )
        save_active_plan(agent, refreshed_plan)
        return events

    # Fix 2: If search_target found the target, complete the plan early for hunt objectives
    _HUNT_COMPLETE_ON_FIND: frozenset[str] = frozenset({"VERIFY_LEAD", "TRACK_TARGET", "PURSUE_TARGET"})
    _executed_payload = step_plan.steps[0].payload if step_plan.steps else {}
    if (
        current_step.kind == STEP_SEARCH_TARGET
        and _executed_payload.get("_hunt_step_outcome") == "target_found"
        and refreshed_plan.objective_key in _HUNT_COMPLETE_ON_FIND
        and step_plan.current_step_index > 0
    ):
        # Mark all remaining steps as skipped and finish the plan
        for _remaining in refreshed_plan.steps[refreshed_plan.current_step_index + 1:]:
            _remaining.status = STEP_STATUS_COMPLETED
        refreshed_plan.advance_step(world_turn)
        save_active_plan(agent, refreshed_plan)
        finish_active_plan(
            agent_id,
            agent,
            refreshed_plan,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_completed",
            reason="target_found",
        )
        return events

    if step_plan.current_step_index > 0:
        completed_step_index = refreshed_plan.current_step_index
        completed_step = refreshed_plan.current_step
        completed_step_kind = completed_step.kind if completed_step is not None else "unknown"
        steps_count = len(refreshed_plan.steps)

        refreshed_plan.advance_step(world_turn)
        save_active_plan(agent, refreshed_plan)
        next_step = refreshed_plan.current_step
        step_completed_summary = (
            f"ActivePlan {refreshed_plan.objective_key}: шаг "
            f"{completed_step_index + 1}/{steps_count} {completed_step_kind} завершён."
        )
        write_active_plan_trace_event(
            agent,
            world_turn=world_turn,
            state=state,
            event="active_plan_step_completed",
            active_plan=refreshed_plan,
            summary=step_completed_summary,
        )
        write_active_plan_memory_event(
            agent,
            world_turn=world_turn,
            state=state,
            action_kind="active_plan_step_completed",
            active_plan=refreshed_plan,
            add_memory=add_memory,
            reason="completed",
            payload_overrides={
                "step_index": completed_step_index,
                "step_kind": completed_step_kind,
                "completed_step_index": completed_step_index,
                "completed_step_number": completed_step_index + 1,
                "completed_step_kind": completed_step_kind,
                "steps_count": steps_count,
                "next_step_index": (
                    refreshed_plan.current_step_index
                    if next_step is not None
                    else None
                ),
                "next_step_kind": active_plan_step_label(next_step) if next_step is not None else None,
            },
            summary_override=step_completed_summary,
        )
        if refreshed_plan.is_complete:
            finish_active_plan(
                agent_id,
                agent,
                refreshed_plan,
                state,
                world_turn,
                add_memory=add_memory,
                terminal_event="active_plan_completed",
            )
    else:
        save_active_plan(agent, refreshed_plan)

    return events


def process_active_plan_v3(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    *,
    add_memory: AddMemoryFn,
) -> tuple[bool, list[dict[str, Any]]]:
    active_plan = get_active_plan(agent)
    if active_plan is None:
        return False, []

    operation, reason = assess_active_plan_v3(agent, state, world_turn)
    if operation == "continue":
        if not agent.get("scheduled_action"):
            return True, start_or_continue_active_plan_step(
                agent_id,
                agent,
                active_plan,
                state,
                world_turn,
                add_memory=add_memory,
            )
        return True, []

    if operation == "repair":
        write_active_plan_trace_event(
            agent,
            world_turn=world_turn,
            state=state,
            event="active_plan_repair_requested",
            active_plan=active_plan,
            reason=reason,
            summary=f"ActivePlan {active_plan.objective_key}: требуется repair ({reason}).",
        )
        write_active_plan_memory_event(
            agent,
            world_turn=world_turn,
            state=state,
            action_kind="active_plan_repair_requested",
            active_plan=active_plan,
            add_memory=add_memory,
            reason=reason,
        )
        repaired = repair_active_plan(agent, active_plan, reason or "repair", world_turn, state=state)
        if repaired.status == ACTIVE_PLAN_STATUS_ABORTED:
            save_active_plan(agent, repaired)
            finish_active_plan(
                agent_id,
                agent,
                repaired,
                state,
                world_turn,
                add_memory=add_memory,
                terminal_event="active_plan_aborted",
                reason=repaired.abort_reason,
            )
            return False, []
        save_active_plan(agent, repaired)
        write_active_plan_trace_event(
            agent,
            world_turn=world_turn,
            state=state,
            event="active_plan_repaired",
            active_plan=repaired,
            reason=reason,
            summary=f"ActivePlan {repaired.objective_key}: repair {reason}.",
        )
        write_active_plan_memory_event(
            agent,
            world_turn=world_turn,
            state=state,
            action_kind="active_plan_repaired",
            active_plan=repaired,
            add_memory=add_memory,
            reason=reason,
        )
        if not agent.get("scheduled_action"):
            return True, start_or_continue_active_plan_step(
                agent_id,
                agent,
                repaired,
                state,
                world_turn,
                add_memory=add_memory,
            )
        return True, []

    if operation == "complete":
        finish_active_plan(
            agent_id,
            agent,
            active_plan,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_completed",
            reason=reason,
        )
        return False, []

    if operation == "abort":
        active_plan.abort(reason or "abort", world_turn)
        save_active_plan(agent, active_plan)
        finish_active_plan(
            agent_id,
            agent,
            active_plan,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_aborted",
            reason=reason,
        )
        return False, []

    return False, []


def on_active_plan_scheduled_action_completed(
    agent_id: str,
    agent: dict[str, Any],
    scheduled_action: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    *,
    add_memory: AddMemoryFn,
) -> list[dict[str, Any]]:
    active_plan = get_active_plan(agent)
    if active_plan is None:
        return []
    if scheduled_action.get("active_plan_id") != active_plan.id:
        return []
    if active_plan.current_step_index != int(scheduled_action.get("active_plan_step_index", -1)):
        return []
    if not scheduled_action_matches_active_step(scheduled_action, active_plan.current_step):
        return []

    completed_step = active_plan.current_step
    completed_step_index = active_plan.current_step_index
    completed_step_kind = completed_step.kind if completed_step is not None else "unknown"
    steps_count = len(active_plan.steps)
    active_plan.advance_step(world_turn)
    save_active_plan(agent, active_plan)
    next_step = active_plan.current_step
    step_completed_summary = (
        f"ActivePlan {active_plan.objective_key}: шаг "
        f"{completed_step_index + 1}/{steps_count} {completed_step_kind} завершён."
    )
    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event="active_plan_step_completed",
        active_plan=active_plan,
        summary=step_completed_summary,
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind="active_plan_step_completed",
        active_plan=active_plan,
        add_memory=add_memory,
        reason="completed",
        payload_overrides={
            "step_index": completed_step_index,
            "step_kind": completed_step_kind,
            "completed_step_index": completed_step_index,
            "completed_step_number": completed_step_index + 1,
            "completed_step_kind": completed_step_kind,
            "steps_count": steps_count,
            "next_step_index": active_plan.current_step_index if next_step is not None else None,
            "next_step_kind": active_plan_step_label(next_step) if next_step is not None else None,
        },
        summary_override=step_completed_summary,
    )
    if active_plan.is_complete:
        finish_active_plan(
            agent_id,
            agent,
            active_plan,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_completed",
        )
    return []


def handle_v3_monitor_abort(
    agent_id: str,
    agent: dict[str, Any],
    scheduled_action: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    *,
    monitor_result: Any,
    add_memory: AddMemoryFn,
    should_write_plan_monitor_memory_event: PlanMonitorDedupFn,
    sleep_effect_interval_turns: int,
) -> list[dict[str, Any]]:
    normalized_monitor_reason = (
        "emission_interrupt"
        if monitor_result.reason == "emission_threat"
        else monitor_result.reason
    )
    dominant_pressure = None
    if monitor_result.dominant_pressure is not None and monitor_result.dominant_pressure_value is not None:
        dominant_pressure = {
            "key": monitor_result.dominant_pressure,
            "value": round(float(monitor_result.dominant_pressure_value), 3),
        }

    if scheduled_action.get("type") == "sleep":
        sleep_intervals = int(scheduled_action.get("sleep_intervals_applied", 0))
        slept_hours = round(sleep_intervals * sleep_effect_interval_turns / 60, 1)
        summary = (
            f"Прерываю sleep из-за {monitor_result.reason}. "
            f"Успел поспать {slept_hours} ч."
        )
    else:
        summary = f"Прерываю {scheduled_action.get('type')} из-за {monitor_result.reason}."

    signature = {
        "reason": monitor_result.reason,
        "scheduled_action_type": scheduled_action.get("type"),
        "cancelled_final_target": scheduled_action.get("final_target_id", scheduled_action.get("target_id")),
    }
    write_plan_monitor_trace(
        agent,
        world_turn=world_turn,
        decision="abort",
        reason=monitor_result.reason,
        summary=summary,
        scheduled_action_type=scheduled_action.get("type"),
        dominant_pressure_key=monitor_result.dominant_pressure,
        dominant_pressure_value=monitor_result.dominant_pressure_value,
        state=state,
    )
    if should_write_plan_monitor_memory_event(
        agent,
        world_turn,
        action_kind="plan_monitor_abort",
        signature=signature,
    ):
        add_memory(
            agent,
            world_turn,
            state,
            "decision",
            "⚡ PlanMonitor: прерываю активное действие",
            {
                "action_kind": "plan_monitor_abort",
                "reason": monitor_result.reason,
                "scheduled_action_type": scheduled_action.get("type"),
                "dominant_pressure": dominant_pressure,
                "dedup_signature": signature,
            },
            summary=summary,
        )

    events = [{
        "event_type": "plan_monitor_aborted_action",
        "payload": {
            "agent_id": agent_id,
            "scheduled_action_type": scheduled_action.get("type"),
            "reason": monitor_result.reason,
            "dominant_pressure": dominant_pressure or {"key": "unknown", "value": 0.0},
            "cancelled_target": scheduled_action.get("target_id"),
            "cancelled_final_target": scheduled_action.get("final_target_id"),
            "current_location_id": agent.get("location_id"),
            "turns_remaining": scheduled_action.get("turns_remaining"),
            **(
                {
                    "sleep_intervals_applied": scheduled_action.get("sleep_intervals_applied"),
                    "sleep_progress_turns": scheduled_action.get("sleep_progress_turns"),
                }
                if scheduled_action.get("type") == "sleep"
                else {}
            ),
        },
    }]

    active_plan = get_active_plan(agent)
    if not (is_v3_monitored_bot(agent) and active_plan is not None):
        agent["scheduled_action"] = None
        if monitor_result.should_clear_action_queue:
            agent["action_queue"] = []
        return events

    agent["scheduled_action"] = None
    mark_active_plan_step_failed(
        agent,
        active_plan,
        world_turn=world_turn,
        state=state,
        add_memory=add_memory,
        reason=normalized_monitor_reason,
    )
    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event="active_plan_repair_requested",
        active_plan=active_plan,
        reason=normalized_monitor_reason,
        summary=f"ActivePlan {active_plan.objective_key}: требуется repair ({normalized_monitor_reason}).",
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind="active_plan_repair_requested",
        active_plan=active_plan,
        add_memory=add_memory,
        reason=normalized_monitor_reason,
    )
    repaired = repair_active_plan(
        agent,
        active_plan,
        normalized_monitor_reason,
        world_turn,
        state=state,
    )
    save_active_plan(agent, repaired)
    if repaired.status == ACTIVE_PLAN_STATUS_ABORTED:
        finish_active_plan(
            agent_id,
            agent,
            repaired,
            state,
            world_turn,
            add_memory=add_memory,
            terminal_event="active_plan_aborted",
            reason=repaired.abort_reason,
        )
        return events

    write_active_plan_trace_event(
        agent,
        world_turn=world_turn,
        state=state,
        event="active_plan_repaired",
        active_plan=repaired,
        reason=normalized_monitor_reason,
        summary=f"ActivePlan {repaired.objective_key}: repair после monitor abort ({normalized_monitor_reason}).",
    )
    write_active_plan_memory_event(
        agent,
        world_turn=world_turn,
        state=state,
        action_kind="active_plan_repaired",
        active_plan=repaired,
        add_memory=add_memory,
        reason=normalized_monitor_reason,
    )
    if not agent.get("scheduled_action"):
        events.extend(
            start_or_continue_active_plan_step(
                agent_id,
                agent,
                repaired,
                state,
                world_turn,
                add_memory=add_memory,
            )
        )
    return events
