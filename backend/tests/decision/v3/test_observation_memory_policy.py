from __future__ import annotations

import app.games.zone_stalkers.memory.memory_events as ev
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.store import ensure_memory_v3


def _agent() -> dict:
    return {"id": "bot1", "name": "bot1", "memory_v3": None, "knowledge_v1": None}


def test_observation_memory_compat_mode_preserves_legacy_target_lead_records() -> None:
    agent = _agent()
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=100,
        legacy_entry={
            "world_turn": 100,
            "type": "observation",
            "title": "target_last_known_location",
            "summary": "target_last_known_location",
            "effects": {
                "action_kind": "target_last_known_location",
                "target_id": "target_1",
                "location_id": "loc_a",
            },
        },
    )
    records = list(ensure_memory_v3(agent).get("records", {}).values())
    assert any(r.get("kind") == "target_last_known_location" for r in records)


def test_observation_memory_compat_off_writes_no_routine_corpse_or_stalker_records() -> None:
    agent = _agent()
    old_mode = ev.OBSERVATION_MEMORY_COMPAT_MODE
    ev.OBSERVATION_MEMORY_COMPAT_MODE = False
    try:
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=200,
            legacy_entry={
                "world_turn": 200,
                "type": "observation",
                "title": "stalkers",
                "summary": "stalkers",
                "effects": {
                    "observed": "stalkers",
                    "location_id": "loc_x",
                    "seen_agent_ids": ["npc_x"],
                    "names": ["X"],
                },
            },
        )
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            world_turn=201,
            legacy_entry={
                "world_turn": 201,
                "type": "observation",
                "title": "corpse",
                "summary": "corpse",
                "effects": {
                    "action_kind": "corpse_seen",
                    "dead_agent_id": "npc_dead",
                    "corpse_id": "corpse_dead",
                    "location_id": "loc_x",
                },
            },
        )
    finally:
        ev.OBSERVATION_MEMORY_COMPAT_MODE = old_mode

    records = list(ensure_memory_v3(agent).get("records", {}).values())
    assert not any(r.get("kind") == "stalkers_seen" for r in records)
    assert any(r.get("kind") == "semantic_stalkers_seen" for r in records)
    assert any(r.get("kind") == "corpse_seen" for r in records)
