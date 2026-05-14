from __future__ import annotations

from app.games.zone_stalkers.memory.memory_events import get_memory_metrics, reset_memory_metrics
from app.games.zone_stalkers.rules.agent_lifecycle import cleanup_stale_corpses
from app.games.zone_stalkers.rules.tick_rules import _write_location_observations, tick_zone_map
from tests.decision.conftest import make_agent, make_minimal_state


def test_stale_corpse_for_alive_agent_removed_before_observation() -> None:
    observer = make_agent(agent_id="observer", location_id="loc_a")
    alive_victim = make_agent(agent_id="victim", location_id="loc_a")
    state = make_minimal_state(agent_id="observer", agent=observer)
    state["agents"]["victim"] = alive_victim
    state["locations"]["loc_a"]["corpses"] = [
        {"corpse_id": "corpse_victim", "agent_id": "victim", "visible": True, "location_id": "loc_a"}
    ]
    result = cleanup_stale_corpses(state)
    assert result["stale_corpse_removed"] == 1
    assert state["locations"]["loc_a"]["corpses"] == []


def test_stale_corpse_for_alive_agent_does_not_mark_known_npc_dead() -> None:
    observer = make_agent(agent_id="observer", location_id="loc_a")
    state = make_minimal_state(agent_id="observer", agent=observer)
    state["locations"]["loc_a"]["corpses"] = [
        {"corpse_id": "corpse_generic", "visible": True, "location_id": "loc_a"}
    ]
    _write_location_observations("observer", observer, "loc_a", state, world_turn=101)
    known = (observer.get("knowledge_v1") or {}).get("known_npcs", {})
    assert known == {}


def test_tick_records_stale_cleanup_metrics() -> None:
    reset_memory_metrics()
    observer = make_agent(agent_id="observer", location_id="loc_a")
    alive_victim = make_agent(agent_id="victim", location_id="loc_a")
    state = make_minimal_state(agent_id="observer", agent=observer)
    state["agents"]["victim"] = alive_victim
    state["locations"]["loc_a"]["agents"].append("victim")
    state["locations"]["loc_a"]["corpses"] = [
        {"corpse_id": "corpse_victim", "agent_id": "victim", "visible": True, "location_id": "loc_a"}
    ]
    tick_zone_map(state)
    metrics = get_memory_metrics()
    assert metrics["stale_corpse_removed"] >= 1
