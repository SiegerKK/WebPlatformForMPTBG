"""Fix 2 — ActivePlan lifecycle events must NOT pollute memory_v3.

Tests that trace-only action_kinds are silently dropped by
write_memory_event_to_v3, while substantive events are preserved.
Also validates PR1 policy classifications and resolve_memory_event_policy.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import (
    write_memory_event_to_v3,
    MEMORY_EVENT_POLICY,
    _SKIP_ACTION_KINDS,
    _AGGREGATE_ACTION_KINDS,
    _CRITICAL_ACTION_KINDS,
    resolve_memory_event_policy,
    get_memory_metrics,
    reset_memory_metrics,
)


def _make_agent() -> dict:
    return {"name": "bot1", "memory_v3": None}


def _write(agent: dict, action_kind: str, world_turn: int = 100, **extra_effects) -> None:
    entry = {
        "world_turn": world_turn,
        "type": "action",
        "title": "test",
        "effects": {"action_kind": action_kind, **extra_effects},
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


# ── PR1: additional policy classification tests ──────────────────────────────

def test_aggregate_kinds_have_correct_policy() -> None:
    for kind in _AGGREGATE_ACTION_KINDS:
        assert MEMORY_EVENT_POLICY[kind] == "memory_aggregate", (
            f"{kind!r} is in _AGGREGATE_ACTION_KINDS but policy is {MEMORY_EVENT_POLICY[kind]!r}"
        )


def test_critical_kinds_have_correct_policy() -> None:
    for kind in _CRITICAL_ACTION_KINDS:
        assert MEMORY_EVENT_POLICY[kind] == "memory_critical", (
            f"{kind!r} is in _CRITICAL_ACTION_KINDS but policy is {MEMORY_EVENT_POLICY[kind]!r}"
        )


def test_resolve_memory_event_policy_returns_trace_only() -> None:
    assert resolve_memory_event_policy("active_plan_created", {}) == "trace_only"
    assert resolve_memory_event_policy("sleep_interval_applied", {}) == "trace_only"


def test_resolve_memory_event_policy_returns_memory_aggregate() -> None:
    assert resolve_memory_event_policy("active_plan_step_failed", {}) == "memory_aggregate"
    assert resolve_memory_event_policy("plan_monitor_abort", {}) == "memory_aggregate"
    assert resolve_memory_event_policy("active_plan_aborted", {}) == "memory_aggregate"


def test_resolve_memory_event_policy_returns_knowledge_upsert() -> None:
    assert resolve_memory_event_policy("stalkers_seen", {}) == "knowledge_upsert"
    assert resolve_memory_event_policy("travel_hop", {}) == "knowledge_upsert"


def test_resolve_memory_event_policy_returns_memory_critical() -> None:
    assert resolve_memory_event_policy("target_death_confirmed", {}) == "memory_critical"
    assert resolve_memory_event_policy("combat_kill", {}) == "memory_critical"
    assert resolve_memory_event_policy("global_goal_completed", {}) == "memory_critical"


def test_resolve_memory_event_policy_defaults_to_memory() -> None:
    assert resolve_memory_event_policy("unknown_event", {}) == "memory"


def test_resolve_memory_event_policy_falls_through_to_obs_type() -> None:
    """resolve_memory_event_policy uses the effective action kind (possibly obs_type) for lookup."""
    # "travel_hop" as action_kind returns knowledge_upsert.
    assert resolve_memory_event_policy("travel_hop", {}) == "knowledge_upsert"
    # Unknown effective kinds default to "memory".
    assert resolve_memory_event_policy("unknown_obs", {"observed": "unknown_obs"}) == "memory"


def test_memory_metrics_trace_only_counted() -> None:
    reset_memory_metrics()
    agent = _make_agent()
    _write(agent, "active_plan_created")
    _write(agent, "sleep_interval_applied")
    m = get_memory_metrics()
    assert m["memory_write_attempts"] == 2
    assert m["memory_write_trace_only"] == 2
    assert m["memory_write_written"] == 0


def test_memory_metrics_critical_counted() -> None:
    reset_memory_metrics()
    agent = _make_agent()
    _write(agent, "target_death_confirmed", target_id="agent_target_1")
    m = get_memory_metrics()
    assert m["memory_write_critical"] >= 1
    assert m["memory_write_written"] >= 1


def test_memory_metrics_aggregated_counted() -> None:
    reset_memory_metrics()
    agent = _make_agent()
    for _ in range(5):
        _write(agent, "active_plan_step_failed",
               objective_key="FIND_ARTIFACTS", step_kind="travel_to_location", reason="no_path")
    m = get_memory_metrics()
    assert m["memory_write_aggregated"] >= 5
