from __future__ import annotations

from app.games.zone_stalkers.decision.debug.brain_trace import (
    append_brain_trace_event,
    ensure_brain_trace_for_tick,
    write_decision_brain_trace_from_v2,
)


def test_append_brain_trace_event_keeps_last_five() -> None:
    agent: dict = {"brain_trace": None}

    for i in range(7):
        append_brain_trace_event(
            agent,
            world_turn=100,
            mode="plan_monitor",
            decision="continue",
            summary=f"event-{i}",
        )

    events = agent["brain_trace"]["events"]
    assert len(events) == 5
    assert events[0]["summary"] == "event-2"
    assert events[-1]["summary"] == "event-6"
    assert "world_time" in agent["brain_trace"]
    assert "world_time" in events[-1]


def test_ensure_brain_trace_for_tick_creates_default_trace() -> None:
    agent: dict = {}
    ensure_brain_trace_for_tick(agent, world_turn=123)
    trace = agent["brain_trace"]
    assert trace["schema_version"] == 1
    assert trace["turn"] == 123
    assert trace["mode"] == "system"
    assert "world_time" in trace
    assert trace["events"][-1]["decision"] == "no_op"
    assert trace["events"][-1]["mode"] == "system"


def test_write_decision_brain_trace_from_v2_writes_decision_event() -> None:
    agent: dict = {}
    write_decision_brain_trace_from_v2(
        agent,
        world_turn=77,
        intent_kind="seek_water",
        intent_score=0.91,
        reason="critical_thirst",
    )
    trace = agent["brain_trace"]
    assert trace["mode"] == "decision"
    assert trace["turn"] == 77
    assert trace["world_time"]["world_day"] >= 1
    assert trace["events"][-1]["decision"] == "new_intent"
    assert trace["events"][-1]["intent_kind"] == "seek_water"
    assert "world_time" in trace["events"][-1]
