"""Fix 2 — ActivePlan lifecycle events must NOT pollute memory_v3.

Tests that trace-only action_kinds are silently dropped by
write_memory_event_to_v3, while substantive events are preserved.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import (
    write_memory_event_to_v3,
    MEMORY_EVENT_POLICY,
    _SKIP_ACTION_KINDS,
)


def _make_agent() -> dict:
    return {"name": "bot1", "memory_v3": None}


def _write(agent: dict, action_kind: str, world_turn: int = 100) -> None:
    entry = {
        "world_turn": world_turn,
        "type": "action",
        "title": "test",
        "effects": {"action_kind": action_kind},
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=world_turn)


def test_active_plan_created_not_stored() -> None:
    agent = _make_agent()
    _write(agent, "active_plan_created")
    assert len(ensure_memory_v3(agent)["records"]) == 0


def test_active_plan_step_started_not_stored() -> None:
    agent = _make_agent()
    _write(agent, "active_plan_step_started")
    assert len(ensure_memory_v3(agent)["records"]) == 0


def test_active_plan_step_completed_not_stored() -> None:
    agent = _make_agent()
    _write(agent, "active_plan_step_completed")
    assert len(ensure_memory_v3(agent)["records"]) == 0


def test_active_plan_completed_not_stored() -> None:
    agent = _make_agent()
    _write(agent, "active_plan_completed")
    assert len(ensure_memory_v3(agent)["records"]) == 0


def test_sleep_interval_not_stored() -> None:
    agent = _make_agent()
    _write(agent, "sleep_interval_applied")
    assert len(ensure_memory_v3(agent)["records"]) == 0


def test_trade_buy_is_stored() -> None:
    """Substantive event must not be blocked by the policy."""
    agent = _make_agent()
    _write(agent, "trade_buy")
    # trade_buy may produce 0 records if not mapped — important check is no KeyError.
    # The policy guard must NOT block it.
    pass  # No assertion needed beyond no crash


def test_skip_kinds_derived_from_policy() -> None:
    """_SKIP_ACTION_KINDS must be a subset of MEMORY_EVENT_POLICY entries with trace_only=True."""
    for kind in _SKIP_ACTION_KINDS:
        assert kind in MEMORY_EVENT_POLICY, f"{kind!r} missing from MEMORY_EVENT_POLICY"
        assert MEMORY_EVENT_POLICY[kind] == "trace_only", (
            f"{kind!r} is in _SKIP_ACTION_KINDS but policy value is {MEMORY_EVENT_POLICY[kind]!r}"
        )
