"""PR1 brain_trace helpers for lightweight NPC decision observability."""

from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.need_evaluation import NeedEvaluationResult

from app.games.zone_stalkers.rules.tick_constants import BRAIN_TRACE_MAX_EVENTS


def _time_from_turn(world_turn: int) -> dict[str, int]:
    total_minutes = 6 * 60 + (world_turn - 1)
    return {
        "world_day": 1 + (total_minutes // (24 * 60)),
        "world_hour": (total_minutes // 60) % 24,
        "world_minute": total_minutes % 60,
    }


def _time_payload(world_turn: int, state: dict[str, Any] | None = None) -> dict[str, int]:
    if isinstance(state, dict):
        return {
            "world_day": int(state.get("world_day", 1)),
            "world_hour": int(state.get("world_hour", 6)),
            "world_minute": int(state.get("world_minute", 0)),
        }
    return _time_from_turn(world_turn)


def _new_trace(
    world_turn: int,
    mode: str,
    thought: str,
    event: dict[str, Any] | None = None,
    *,
    world_time: dict[str, int] | None = None,
) -> dict[str, Any]:
    _world_time = world_time or _time_from_turn(world_turn)
    return {
        "schema_version": 1,
        "turn": world_turn,
        "world_time": _world_time,
        "mode": mode,
        "current_thought": thought,
        "events": [event] if event else [],
    }


def _no_op_event(world_turn: int, world_time: dict[str, int]) -> dict[str, Any]:
    return {
        "turn": world_turn,
        "world_time": world_time,
        "mode": "system",
        "decision": "no_op",
        "summary": "В этот тик не было нового решения NPC Brain.",
    }


def append_brain_trace_event(
    agent: dict[str, Any],
    *,
    world_turn: int,
    mode: str,
    decision: str,
    summary: str,
    reason: str | None = None,
    scheduled_action_type: str | None = None,
    intent_kind: str | None = None,
    intent_score: float | None = None,
    dominant_pressure: dict[str, Any] | None = None,
    immediate_needs: list[dict[str, Any]] | None = None,
    item_needs: list[dict[str, Any]] | None = None,
    liquidity: dict[str, Any] | None = None,
    combat_readiness: dict[str, Any] | None = None,
    memory_used: list[dict[str, Any]] | None = None,
    active_objective: dict[str, Any] | None = None,
    objective_scores: list[dict[str, Any]] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    trace = agent.get("brain_trace")
    world_time = _time_payload(world_turn, state)
    event: dict[str, Any] = {
        "turn": world_turn,
        "world_time": world_time,
        "mode": mode,
        "decision": decision,
        "summary": summary,
    }
    if reason is not None:
        event["reason"] = reason
    if scheduled_action_type is not None:
        event["scheduled_action_type"] = scheduled_action_type
    if intent_kind is not None:
        event["intent_kind"] = intent_kind
    if intent_score is not None:
        event["intent_score"] = round(float(intent_score), 3)
    if dominant_pressure is not None:
        event["dominant_pressure"] = dominant_pressure
    if immediate_needs:
        event["immediate_needs"] = immediate_needs[:3]
    if item_needs:
        event["item_needs"] = item_needs[:5]
    if liquidity is not None:
        event["liquidity"] = liquidity
    if combat_readiness is not None:
        event["combat_readiness"] = combat_readiness
    if memory_used:
        # Cap at 5 entries (section 15).
        event["memory_used"] = memory_used[:5]
    if active_objective is not None:
        event["active_objective"] = active_objective
    if objective_scores:
        event["objective_scores"] = objective_scores[:5]
    if alternatives:
        event["alternatives"] = alternatives[:5]

    if not isinstance(trace, dict):
        thought = summary
        agent["brain_trace"] = _new_trace(
            world_turn,
            mode,
            thought,
            event,
            world_time=world_time,
        )
        return

    trace.setdefault("schema_version", 1)
    trace["turn"] = world_turn
    trace["world_time"] = world_time
    trace["mode"] = mode
    trace["current_thought"] = summary
    events = list(trace.get("events", []))
    trace["events"] = (events + [event])[-BRAIN_TRACE_MAX_EVENTS:]


def write_plan_monitor_trace(
    agent: dict[str, Any],
    *,
    world_turn: int,
    decision: str,
    reason: str,
    summary: str,
    scheduled_action_type: str | None,
    dominant_pressure_key: str | None = None,
    dominant_pressure_value: float | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    dominant_pressure = None
    if dominant_pressure_key is not None and dominant_pressure_value is not None:
        dominant_pressure = {
            "key": dominant_pressure_key,
            "value": round(float(dominant_pressure_value), 3),
        }
    append_brain_trace_event(
        agent,
        world_turn=world_turn,
        mode="plan_monitor",
        decision=decision,
        summary=summary,
        reason=reason,
        scheduled_action_type=scheduled_action_type,
        dominant_pressure=dominant_pressure,
        state=state,
    )


def write_decision_brain_trace_from_v2(
    agent: dict[str, Any],
    *,
    world_turn: int,
    intent_kind: str,
    intent_score: float,
    reason: str | None,
    state: dict[str, Any] | None = None,
    need_result: NeedEvaluationResult | None = None,
    memory_used: list[dict[str, Any]] | None = None,
    active_objective: dict[str, Any] | None = None,
    objective_scores: list[dict[str, Any]] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
) -> None:
    if active_objective and isinstance(active_objective, dict) and active_objective.get("key"):
        objective_key = active_objective["key"]
        thought = f"Выбрана цель {objective_key} ({round(intent_score * 100)}%). Адаптер intent: {intent_kind}."
    else:
        thought = f"Выбран intent {intent_kind} ({round(intent_score * 100)}%)."
    if reason:
        thought += f" {reason}"

    immediate_payload = None
    item_payload = None
    liquidity_payload = None
    combat_readiness_payload = None
    if need_result is not None:
        immediate_payload = [
            {
                "key": n.key,
                "urgency": round(float(n.urgency), 3),
                "selected_item_type": n.selected_item_type,
                "reason": n.reason,
            }
            for n in need_result.immediate_needs
            if n.urgency > 0
        ]
        item_payload = [
            {
                "key": n.key,
                "urgency": round(float(n.urgency), 3),
                "missing_count": n.missing_count,
                "reason": n.reason,
            }
            for n in need_result.item_needs
            if n.urgency > 0
        ]
        liquidity_payload = dict(need_result.liquidity_summary or {})
        if need_result.combat_readiness:
            combat_readiness_payload = dict(need_result.combat_readiness)

    append_brain_trace_event(
        agent,
        world_turn=world_turn,
        mode="decision",
        decision="new_intent",
        summary=thought,
        reason=reason,
        intent_kind=intent_kind,
        intent_score=intent_score,
        immediate_needs=immediate_payload,
        item_needs=item_payload,
        liquidity=liquidity_payload,
        combat_readiness=combat_readiness_payload,
        memory_used=memory_used,
        active_objective=active_objective,
        objective_scores=objective_scores,
        alternatives=alternatives,
        state=state,
    )


def ensure_brain_trace_for_tick(
    agent: dict[str, Any],
    *,
    world_turn: int,
    state: dict[str, Any] | None = None,
) -> None:
    trace = agent.get("brain_trace")
    world_time = _time_payload(world_turn, state)
    no_op_event = _no_op_event(world_turn, world_time)
    if not isinstance(trace, dict):
        agent["brain_trace"] = _new_trace(
            world_turn,
            "system",
            "Нет изменений плана в этом тике.",
            no_op_event,
            world_time=world_time,
        )
        return

    trace.setdefault("schema_version", 1)
    trace.setdefault("events", [])
    if trace.get("turn") != world_turn:
        trace["turn"] = world_turn
        trace["world_time"] = world_time
        trace["mode"] = "system"
        trace["current_thought"] = "Нет изменений плана в этом тике."
        trace["events"] = (list(trace.get("events", [])) + [no_op_event])[-BRAIN_TRACE_MAX_EVENTS:]
