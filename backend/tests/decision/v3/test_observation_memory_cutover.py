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
