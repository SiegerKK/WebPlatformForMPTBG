from __future__ import annotations

from app.games.zone_stalkers.memory.memory_events import (
    get_memory_metrics,
    reset_memory_metrics,
    write_memory_event_to_v3,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3


def _agent() -> dict:
    return {"id": "bot1", "name": "bot1", "memory_v3": None, "knowledge_v1": None}


def _corpse_entry(turn: int, **effects) -> dict:
    return {
        "world_turn": turn,
        "type": "observation",
        "title": "corpse_seen",
        "summary": "corpse_seen",
        "effects": {"action_kind": "corpse_seen", **effects},
    }


def test_corpse_seen_updates_known_corpse_and_known_npc_death_evidence() -> None:
    agent = _agent()
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=300,
        legacy_entry=_corpse_entry(
            300,
            dead_agent_id="dead_1",
            dead_agent_name="Dead One",
            corpse_id="corpse_dead_1",
            location_id="loc_c",
            confidence=0.95,
            directly_observed=True,
        ),
    )
    knowledge = agent.get("knowledge_v1", {})
    corpses = knowledge.get("known_corpses", {})
    npc = knowledge.get("known_npcs", {}).get("dead_1", {})
    assert "corpse_dead_1" in corpses
    assert npc.get("is_alive") is False
    assert npc.get("death_evidence", {}).get("status") in {"corpse_seen", "confirmed_dead"}


def test_repeated_same_corpse_seen_does_not_write_memory_records() -> None:
    agent = _agent()
    entry = _corpse_entry(
        400,
        dead_agent_id="dead_2",
        dead_agent_name="Dead Two",
        corpse_id="corpse_dead_2",
        location_id="loc_d",
        confidence=0.95,
        directly_observed=True,
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=400)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=401)
    records = list(ensure_memory_v3(agent).get("records", {}).values())
    corpse_seen = [r for r in records if r.get("kind") == "corpse_seen"]
    assert len(corpse_seen) <= 1


def test_corpse_seen_for_alive_agent_is_ignored_and_records_metric() -> None:
    agent = _agent()
    reset_memory_metrics()
    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        world_turn=500,
        legacy_entry=_corpse_entry(
            500,
            dead_agent_id="alive_1",
            dead_agent_name="Alive One",
            corpse_id="corpse_alive_1",
            location_id="loc_e",
            dead_agent_is_alive=True,
        ),
    )
    m = get_memory_metrics()
    assert m["stale_corpse_seen_ignored"] >= 1
    assert m["corpse_seen_alive_agent_ignored"] >= 1
