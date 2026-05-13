from __future__ import annotations

from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.rules.tick_rules import _write_location_observations


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


def test_new_known_npc_bumps_major_revision() -> None:
    agent = _agent()
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
                "seen_agent_ids": ["npc_a"],
                "names": ["A"],
            },
        },
    )
    knowledge = agent["knowledge_v1"]
    assert int(knowledge.get("major_revision", 0)) == 1


def test_same_known_npc_same_location_does_not_bump_major_revision() -> None:
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
    minor_1 = int(agent["knowledge_v1"].get("minor_revision", 0))
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=110)
    knowledge = agent["knowledge_v1"]
    assert int(knowledge.get("major_revision", 0)) == major_1
    assert int(knowledge.get("minor_revision", 0)) > minor_1


def test_known_npc_location_change_bumps_major_revision() -> None:
    agent = _agent()
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
                "seen_agent_ids": ["npc_a"],
                "names": ["A"],
            },
        },
    )
    major_1 = int(agent["knowledge_v1"].get("major_revision", 0))
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=120,
        legacy_entry={
            "world_turn": 120,
            "type": "observation",
            "title": "stalkers",
            "summary": "stalkers",
            "effects": {
                "observed": "stalkers",
                "location_id": "loc_b",
                "seen_agent_ids": ["npc_a"],
                "names": ["A"],
            },
        },
    )
    knowledge = agent["knowledge_v1"]
    assert int(knowledge.get("major_revision", 0)) > major_1
    assert knowledge["known_npcs"]["npc_a"]["last_seen_location_id"] == "loc_b"


def test_corpse_seen_death_status_change_bumps_major_revision() -> None:
    agent = _agent()
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
                "seen_agent_ids": ["npc_a"],
                "names": ["A"],
            },
        },
    )
    major_1 = int(agent["knowledge_v1"].get("major_revision", 0))
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=130,
        legacy_entry=_event(130, "corpse_seen", dead_agent_id="npc_a", corpse_id="corpse_a", location_id="loc_a"),
    )
    knowledge = agent["knowledge_v1"]
    assert int(knowledge.get("major_revision", 0)) > major_1
    assert knowledge["known_npcs"]["npc_a"]["death_evidence"]["status"] == "corpse_seen"


def test_living_observation_contradicts_stale_death_evidence() -> None:
    agent = _agent()
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=100,
        legacy_entry=_event(100, "corpse_seen", dead_agent_id="npc_a", corpse_id="corpse_a", location_id="loc_a"),
    )
    major_1 = int(agent["knowledge_v1"].get("major_revision", 0))
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=140,
        legacy_entry={
            "world_turn": 140,
            "type": "observation",
            "title": "stalkers",
            "summary": "stalkers",
            "effects": {
                "observed": "stalkers",
                "location_id": "loc_b",
                "seen_agent_ids": ["npc_a"],
                "names": ["A"],
            },
        },
    )
    knowledge = agent["knowledge_v1"]
    npc = knowledge["known_npcs"]["npc_a"]
    assert int(knowledge.get("major_revision", 0)) > major_1
    assert npc["death_evidence"]["status"] == "contradicted"
    assert npc["is_alive"] is True


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


def test_write_location_observations_enriches_equipment_summary() -> None:
    observer = _agent()
    observer["location_id"] = "loc_a"
    state = {
        "locations": {"loc_a": {"name": "A", "items": [], "agents": [], "corpses": []}},
        "agents": {
            "bot1": observer,
            "npc_a": {
                "id": "npc_a",
                "name": "NPC A",
                "location_id": "loc_a",
                "is_alive": True,
                "has_left_zone": False,
                "archetype": "stalker_agent",
                "hp": 80,
                "max_hp": 100,
                "equipment": {
                    "weapon": {"type": "ak74"},
                    "armor": {"type": "stalker_suit"},
                },
                "global_goal": "collect_artifacts",
            },
        },
        "traders": {},
        "mutants": {},
    }
    _write_location_observations("bot1", observer, "loc_a", state, world_turn=100)
    known = observer["knowledge_v1"]["known_npcs"]["npc_a"]
    assert known["equipment_summary"]["weapon_class"] == "rifle"
    assert known["equipment_summary"]["armor_class"] == "medium"
    assert known["detail_level"] == "detailed"
