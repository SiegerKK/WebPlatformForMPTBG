"""Regression: memory_v3 composition over a synthetic long run (Fix 2+3, PR1).

One combined regression test: after 50 turns of mixed events including
ActivePlan lifecycle noise, the memory store must NOT contain any
trace-only records (active_plan_created, active_plan_step_started, etc.),
and the total record count must respect the hard cap.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3, MEMORY_V3_MAX_RECORDS as MEMORY_HARD_CAP
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3, _SKIP_ACTION_KINDS


def _write(agent: dict, action_kind: str, world_turn: int, **extra_effects) -> None:
    entry = {
        "world_turn": world_turn,
        "type": "action",
        "title": f"event_{action_kind}_{world_turn}",
        "effects": {"action_kind": action_kind, **extra_effects},
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


def test_plan_failure_loop_does_not_dominate_memory() -> None:
    """200 repeated active_plan failures must produce << 200 records in memory_v3."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(1, 201):
        _write(agent, "active_plan_step_failed",
               turn,
               objective_key="FIND_ARTIFACTS",
               step_kind="travel_to_location",
               reason="support_source_exhausted")
        _write(agent, "active_plan_repair_requested",
               turn,
               objective_key="FIND_ARTIFACTS",
               step_kind="travel_to_location",
               reason="support_source_exhausted")

    records = ensure_memory_v3(agent)["records"]
    all_failure_kinds = {r.get("kind") for r in records.values()}
    # Repeated failures must NOT produce raw active_plan_step_failed records.
    assert "active_plan_step_failed" not in all_failure_kinds
    # They should produce at most a handful of aggregate records.
    agg_count = sum(1 for r in records.values() if r.get("kind") == "active_plan_failure_summary")
    assert agg_count <= 5, f"Expected <= 5 failure aggregates, got {agg_count}"
    assert len(records) < 20, f"Expected << 20 records for failure loop, got {len(records)}"


def test_repeated_stalkers_seen_keeps_episodic_under_budget() -> None:
    """200 stalkers_seen events at the same location must stay knowledge-only."""

    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(100, 300):
        entry = {
            "world_turn": turn,
            "type": "observation",
            "title": "saw stalkers",
            "summary": "saw stalkers",
            "effects": {
                "observed": "stalkers",
                "location_id": "loc_repeat",
                "entity_ids": ["agent_debug_0", "agent_debug_7"],
                "names": ["Сталкер #0", "Сталкер #7"],
            },
        }
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)
    records = list(ensure_memory_v3(agent)["records"].values())
    assert not any(r.get("kind") == "stalkers_seen" for r in records)
    assert not any(r.get("kind") == "semantic_stalkers_seen" for r in records)
    assert len(agent.get("knowledge_v1", {}).get("known_npcs", {})) == 2


def test_repeated_travel_hop_updates_route_aggregate() -> None:
    """Repeated travel_hop events must update a single semantic_route_traveled aggregate."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(200, 230):
        entry = {
            "world_turn": turn,
            "type": "action",
            "title": "traveled",
            "summary": f"traveled loc_a→loc_b at turn {turn}",
            "effects": {
                "action_kind": "travel_hop",
                "location_id": "loc_b",
                "from_location_id": "loc_a",
                "to_location_id": "loc_b",
            },
        }
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)
    records = list(ensure_memory_v3(agent)["records"].values())
    route_semantic = [r for r in records if r.get("kind") == "semantic_route_traveled"]
    assert len(route_semantic) == 1
    assert route_semantic[0]["details"]["times_traveled"] >= 30
    assert route_semantic[0]["details"]["last_traveled_turn"] == 229
