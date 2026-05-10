"""Tests for brain trace gating (CPU PR1)."""
import pytest
from app.games.zone_stalkers.decision.debug.brain_trace import (
    is_brain_trace_enabled,
    update_agent_decision_summary,
    append_brain_trace_event,
)


def test_brain_trace_disabled_by_default():
    """When debug_brain_trace_enabled=False, trace is disabled."""
    state = {"debug_brain_trace_enabled": False}
    assert is_brain_trace_enabled("agent_1", state) is False


def test_brain_trace_enabled_for_all_agents():
    """When enabled=True and agent_ids=[], all agents get traced."""
    state = {"debug_brain_trace_enabled": True, "debug_brain_trace_agent_ids": []}
    assert is_brain_trace_enabled("agent_1", state) is True
    assert is_brain_trace_enabled("agent_xyz", state) is True


def test_brain_trace_enabled_for_specific_agent():
    """When a specific agent id is listed, only that agent gets traced."""
    state = {"debug_brain_trace_enabled": True, "debug_brain_trace_agent_ids": ["agent_debug"]}
    assert is_brain_trace_enabled("agent_debug", state) is True


def test_brain_trace_enabled_for_wrong_agent_returns_false():
    """An agent not in the allowed list is gated out."""
    state = {"debug_brain_trace_enabled": True, "debug_brain_trace_agent_ids": ["agent_debug"]}
    assert is_brain_trace_enabled("agent_other", state) is False


def test_brain_trace_enabled_without_state_defaults_true():
    """No state means backwards-compat: trace everything."""
    assert is_brain_trace_enabled("any_agent", None) is True


def test_update_agent_decision_summary_writes_compact():
    agent = {}
    update_agent_decision_summary(
        agent,
        world_turn=5,
        objective_key="hunt_target",
        intent_kind="hunt",
        summary="Охочусь на цель",
    )
    ctx = agent.get("brain_v3_context", {})
    summary = ctx.get("latest_decision_summary", {})
    assert summary["turn"] == 5
    assert summary["objective_key"] == "hunt_target"
    assert summary["intent_kind"] == "hunt"
    assert summary["summary"] == "Охочусь на цель"


def test_append_brain_trace_event_skips_when_disabled():
    """When trace is disabled, brain_trace is not written."""
    agent = {"id": "a1", "brain_trace": None}
    state = {"debug_brain_trace_enabled": False}
    append_brain_trace_event(
        agent,
        world_turn=1,
        mode="decision",
        decision="test",
        summary="Test summary",
        agent_id="a1",
        state=state,
    )
    # brain_trace should remain None (not written)
    assert agent.get("brain_trace") is None
    # But summary should be written
    assert agent.get("brain_v3_context", {}).get("latest_decision_summary") is not None


def test_append_brain_trace_event_writes_when_enabled():
    """When trace is enabled, brain_trace is populated."""
    agent = {"id": "a1", "brain_trace": None}
    state = {"debug_brain_trace_enabled": True, "debug_brain_trace_agent_ids": []}
    append_brain_trace_event(
        agent,
        world_turn=2,
        mode="decision",
        decision="test_decision",
        summary="Wrote a trace",
        agent_id="a1",
        state=state,
    )
    assert agent.get("brain_trace") is not None
    trace = agent["brain_trace"]
    assert trace.get("turn") == 2
    assert len(trace.get("events", [])) >= 1


def test_brain_trace_no_state_backwards_compat():
    """Without state arg, trace is written (backwards compat)."""
    agent = {"id": "a1", "brain_trace": None}
    append_brain_trace_event(
        agent,
        world_turn=3,
        mode="system",
        decision="no_op",
        summary="Legacy call without state",
    )
    # Should write trace since no state = always trace
    assert agent.get("brain_trace") is not None
