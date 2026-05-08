"""PR1 brain_trace helpers for lightweight NPC decision observability."""

from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.rules.tick_constants import BRAIN_TRACE_MAX_EVENTS


def _new_trace(world_turn: int, mode: str, thought: str, event: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "turn": world_turn,
        "mode": mode,
        "current_thought": thought,
        "events": [event] if event else [],
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
) -> None:
    trace = agent.get("brain_trace")
    event: dict[str, Any] = {
        "turn": world_turn,
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

    if not isinstance(trace, dict):
        thought = summary
        agent["brain_trace"] = _new_trace(world_turn, mode, thought, event)
        return

    trace.setdefault("schema_version", 1)
    trace["turn"] = world_turn
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
    )


def write_decision_brain_trace_from_v2(
    agent: dict[str, Any],
    *,
    world_turn: int,
    intent_kind: str,
    intent_score: float,
    reason: str | None,
) -> None:
    thought = f"Выбран intent {intent_kind} ({round(intent_score * 100)}%)."
    if reason:
        thought += f" {reason}"

    append_brain_trace_event(
        agent,
        world_turn=world_turn,
        mode="decision",
        decision="new_intent",
        summary=thought,
        reason=reason,
        intent_kind=intent_kind,
        intent_score=intent_score,
    )


def ensure_brain_trace_for_tick(agent: dict[str, Any], *, world_turn: int) -> None:
    trace = agent.get("brain_trace")
    if not isinstance(trace, dict):
        agent["brain_trace"] = _new_trace(
            world_turn,
            "system",
            "Нет изменений плана в этом тике.",
        )
        return

    trace.setdefault("schema_version", 1)
    trace.setdefault("events", [])
    if trace.get("turn") != world_turn:
        trace["turn"] = world_turn
        trace["mode"] = "system"
        trace["current_thought"] = "Нет изменений плана в этом тике."
