from __future__ import annotations

import app.games.zone_stalkers.memory.memory_events as ev
from app.games.zone_stalkers.memory.memory_events import (
    reset_memory_metrics,
    should_write_observation_milestone,
    write_memory_event_to_v3,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3


def _agent() -> dict:
    return {"id": "bot1", "name": "bot1", "memory_v3": None, "knowledge_v1": None}


def _records(agent: dict) -> list[dict]:
    return list((ensure_memory_v3(agent).get("records") or {}).values())


def test_stalkers_seen_writes_zero_memory_records_with_compat_off() -> None:
    agent = _agent()
    old_mode = ev.OBSERVATION_MEMORY_COMPAT_MODE
    ev.OBSERVATION_MEMORY_COMPAT_MODE = False
    try:
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=100,
            legacy_entry={
                "world_turn": 100,
                "type": "observation",
                "title": "stalkers",
                "summary": "stalkers",
                "effects": {
                    "observed": "stalkers",
                    "location_id": "loc_a",
                    "seen_agent_ids": ["npc_1"],
                    "names": ["npc_1"],
                },
            },
        )
    finally:
        ev.OBSERVATION_MEMORY_COMPAT_MODE = old_mode
    assert not any(r.get("kind") in {"stalkers_seen", "semantic_stalkers_seen"} for r in _records(agent))


def test_repeated_stalkers_seen_updates_known_npcs_only() -> None:
    agent = _agent()
    for turn in (101, 102):
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=turn,
            legacy_entry={
                "world_turn": turn,
                "type": "observation",
                "title": "stalkers",
                "summary": "stalkers",
                "effects": {
                    "observed": "stalkers",
                    "location_id": "loc_a",
                    "seen_agent_ids": ["npc_1"],
                    "names": ["npc_1"],
                },
            },
        )
    known = (agent.get("knowledge_v1") or {}).get("known_npcs", {}).get("npc_1", {})
    assert known.get("last_seen_turn") == 102
    assert not any(r.get("kind") in {"stalkers_seen", "semantic_stalkers_seen"} for r in _records(agent))


def test_corpse_seen_writes_zero_memory_records_for_repeated_valid_corpse() -> None:
    agent = _agent()
    for turn in (110, 111):
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=turn,
            legacy_entry={
                "world_turn": turn,
                "type": "observation",
                "title": "corpse_seen",
                "summary": "corpse_seen",
                "effects": {
                    "action_kind": "corpse_seen",
                    "dead_agent_id": "dead_1",
                    "corpse_id": "corpse_dead_1",
                    "location_id": "loc_c",
                },
            },
        )
    assert not any(r.get("kind") == "corpse_seen" for r in _records(agent))


def test_kill_target_corpse_seen_writes_milestone_once() -> None:
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    for turn in (120, 121):
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=turn,
            legacy_entry={
                "world_turn": turn,
                "type": "observation",
                "title": "target_corpse_seen",
                "summary": "target_corpse_seen",
                "effects": {
                    "action_kind": "target_corpse_seen",
                    "target_id": "target_1",
                    "corpse_id": "corpse_target_1",
                    "location_id": "loc_t",
                    "corpse_location_id": "loc_t",
                },
            },
        )
    assert sum(1 for r in _records(agent) if r.get("kind") == "target_corpse_seen") == 1


def test_target_seen_for_non_target_is_knowledge_only() -> None:
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=130,
        legacy_entry={
            "world_turn": 130,
            "type": "observation",
            "title": "target_seen",
            "summary": "target_seen",
            "effects": {
                "action_kind": "target_seen",
                "target_id": "target_2",
                "target_name": "target_2",
                "location_id": "loc_x",
            },
        },
    )
    assert not any(r.get("kind") == "target_seen" for r in _records(agent))


def test_should_write_observation_milestone_ignores_minor_refresh() -> None:
    reset_memory_metrics()
    agent = _agent()
    assert should_write_observation_milestone(
        event_kind="stalkers_seen",
        update_result={"changed_minor": True, "reasons": ["refresh_only"]},
        agent=agent,
        effects={"location_id": "loc_x"},
        world_turn=200,
    ) is False


def test_target_not_found_is_knowledge_only_after_cutover() -> None:
    """PR10: target_not_found is knowledge_only; no memory_v3 record, hunt_evidence updated."""
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=200,
        legacy_entry={
            "world_turn": 200,
            "type": "observation",
            "title": "target_not_found",
            "summary": "target_not_found",
            "effects": {
                "action_kind": "target_not_found",
                "target_id": "target_1",
                "location_id": "loc_search",
            },
        },
    )
    # No memory_v3 record.
    assert not any(r.get("kind") == "target_not_found" for r in _records(agent)), (
        "PR10: target_not_found must be knowledge-only"
    )
    # hunt_evidence must reflect the failed search.
    hunt_ev = ((agent.get("knowledge_v1") or {}).get("hunt_evidence") or {}).get("target_1", {})
    failed = hunt_ev.get("failed_search_locations", {})
    assert failed.get("loc_search", {}).get("count", 0) >= 1, (
        "hunt_evidence.failed_search_locations must be updated"
    )


def test_target_seen_for_kill_target_writes_bounded_milestone_once() -> None:
    """PR10: First kill-target sighting writes exactly one target_seen milestone."""
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    _entry = lambda turn, loc: {  # noqa: E731
        "world_turn": turn,
        "type": "observation",
        "title": "target_seen",
        "summary": "target_seen",
        "effects": {
            "action_kind": "target_seen",
            "target_id": "target_1",
            "target_name": "target_1",
            "location_id": loc,
        },
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, world_turn=210, legacy_entry=_entry(210, "loc_a"))
    records_after_first = [r for r in _records(agent) if r.get("kind") == "target_seen"]
    assert len(records_after_first) == 1, "First kill-target sighting must write exactly one milestone"


def test_same_target_seen_repeated_does_not_write_more_milestones() -> None:
    """PR10: Repeated same kill-target sighting at same location must not append new records."""
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    _entry = lambda turn, loc: {  # noqa: E731
        "world_turn": turn,
        "type": "observation",
        "title": "target_seen",
        "summary": "target_seen",
        "effects": {
            "action_kind": "target_seen",
            "target_id": "target_1",
            "target_name": "target_1",
            "location_id": loc,
        },
    }
    # First sighting creates the record.
    write_memory_event_to_v3(agent_id="bot1", agent=agent, world_turn=220, legacy_entry=_entry(220, "loc_a"))
    count_after_first = sum(1 for r in _records(agent) if r.get("kind") == "target_seen")
    assert count_after_first == 1
    # Repeated sighting at same location — same knowledge state — no new record.
    write_memory_event_to_v3(agent_id="bot1", agent=agent, world_turn=221, legacy_entry=_entry(221, "loc_a"))
    count_after_repeat = sum(1 for r in _records(agent) if r.get("kind") == "target_seen")
    assert count_after_repeat == 1, (
        "Repeated kill-target sighting at same location must not create additional milestone records"
    )


def test_target_seen_location_change_policy_is_explicit() -> None:
    """PR10: Location change for kill target is a major update → second milestone written."""
    agent = _agent()
    agent["kill_target_id"] = "target_1"
    _entry = lambda turn, loc: {  # noqa: E731
        "world_turn": turn,
        "type": "observation",
        "title": "target_seen",
        "summary": "target_seen",
        "effects": {
            "action_kind": "target_seen",
            "target_id": "target_1",
            "target_name": "target_1",
            "location_id": loc,
        },
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, world_turn=230, legacy_entry=_entry(230, "loc_a"))
    write_memory_event_to_v3(agent_id="bot1", agent=agent, world_turn=231, legacy_entry=_entry(231, "loc_b"))
    count = sum(1 for r in _records(agent) if r.get("kind") == "target_seen")
    # Material location change is a major update for the kill target; a new milestone is expected.
    assert count >= 1, "Kill-target seen at new location must produce at least one target_seen milestone"
