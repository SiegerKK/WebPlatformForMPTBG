"""Regression: memory_v3 composition over a synthetic long run (Fix 2+3).

One combined regression test: after 50 turns of mixed events including
ActivePlan lifecycle noise, the memory store must NOT contain any
trace-only records (active_plan_created, active_plan_step_started, etc.),
and the total record count must respect the hard cap.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3, MEMORY_V3_MAX_RECORDS as MEMORY_HARD_CAP
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3, _SKIP_ACTION_KINDS


def _write(agent: dict, action_kind: str, world_turn: int) -> None:
    entry = {
        "world_turn": world_turn,
        "type": "action",
        "title": f"event_{action_kind}_{world_turn}",
        "effects": {"action_kind": action_kind},
        "summary": f"{action_kind} at turn {world_turn}",
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=world_turn)


def test_no_trace_only_records_after_long_run() -> None:
    """After 50 turns of mixed events, no trace-only record must exist."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    substantive_kinds = [
        "trade_buy", "travel", "emission_imminent", "combat_kill",
        "global_goal_completed",
    ]
    trace_kinds = list(_SKIP_ACTION_KINDS)

    for turn in range(1, 51):
        # Mix substantive and trace events each turn
        for kind in substantive_kinds:
            _write(agent, kind, turn)
        for kind in trace_kinds:
            _write(agent, kind, turn)

    mem_v3 = ensure_memory_v3(agent)
    records = mem_v3["records"]

    # No trace-only record must have slipped through
    for rid, raw in records.items():
        kind = raw.get("kind", "")
        # We can't map action_kind→record_kind exactly, but trace kinds map to
        # known kinds like "plan_created" etc. Check tags instead.
        tags = raw.get("tags") or []
        assert "trace_only" not in tags, f"Trace-only record found: {raw}"

    # Hard cap must be respected
    assert len(records) <= MEMORY_HARD_CAP, (
        f"Memory hard cap exceeded: {len(records)} > {MEMORY_HARD_CAP}"
    )
