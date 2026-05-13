from __future__ import annotations

from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.store import ensure_memory_v3


def _agent() -> dict:
    return {"id": "bot1", "name": "bot1", "memory_v3": None, "knowledge_v1": None}


def _event(turn: int, action_kind: str, **effects) -> dict:
    return {
        "world_turn": turn,
        "type": "observation",
        "title": action_kind,
        "summary": action_kind,
        "effects": {"action_kind": action_kind, **effects},
    }


def test_stalkers_seen_updates_known_npcs_without_episodic_memory_spam() -> None:
    agent = _agent()
    for turn in (100, 110, 120):
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
                    "seen_agent_ids": ["npc_a", "npc_b"],
                    "names": ["A", "B"],
                },
            },
        )

    knowledge = agent.get("knowledge_v1", {})
    assert "npc_a" in knowledge.get("known_npcs", {})
    assert "npc_b" in knowledge.get("known_npcs", {})
    records = list(ensure_memory_v3(agent).get("records", {}).values())
    stalkers_seen = [r for r in records if r.get("kind") == "stalkers_seen"]
    assert stalkers_seen == []


def test_repeated_same_stalkers_seen_is_minor_refresh_only() -> None:
    agent = _agent()
    entry = {
        "world_turn": 100,
        "type": "observation",
        "title": "stalkers",
        "summary": "stalkers",
        "effects": {
            "observed": "stalkers",
            "location_id": "loc_a",
            "seen_agent_ids": ["npc_a"],
            "names": ["A"],
        },
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    major_1 = int(agent["knowledge_v1"].get("major_revision", 0))

    entry["world_turn"] = 110
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=110)
    major_2 = int(agent["knowledge_v1"].get("major_revision", 0))

    assert major_2 == major_1
    assert int(agent["knowledge_v1"].get("minor_revision", 0)) >= 1


def test_target_seen_updates_known_npc_and_hunt_evidence() -> None:
    agent = _agent()
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=200,
        legacy_entry=_event(200, "target_seen", target_id="t1", target_name="Target", location_id="loc_b"),
    )
    knowledge = agent.get("knowledge_v1", {})
    known = knowledge.get("known_npcs", {})
    hunt = knowledge.get("hunt_evidence", {})
    assert known["t1"]["last_seen_location_id"] == "loc_b"
    assert hunt["t1"]["last_seen"]["location_id"] == "loc_b"
