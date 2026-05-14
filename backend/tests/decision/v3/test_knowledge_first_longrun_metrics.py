from __future__ import annotations

from app.games.zone_stalkers.projections import project_zone_state
from tests.decision.conftest import make_agent, make_minimal_state


def test_observation_memory_metrics_exposed_in_summary() -> None:
    agent = make_agent(agent_id="observer", location_id="loc_a")
    agent["brain_context_metrics"] = {
        "target_belief_memory_fallbacks": 0,
        "context_builder_memory_fallbacks": 0,
    }
    agent["memory_v3"] = {
        "records": {
            "r1": {
                "id": "r1",
                "kind": "target_corpse_seen",
                "layer": "episodic",
                "created_turn": 150,
                "summary": "target_corpse_seen",
                "details": {
                    "action_kind": "target_corpse_seen",
                    "memory_type": "observation",
                    "target_id": "t1",
                },
            }
        },
        "stats": {"memory_evictions": 10, "memory_write_dropped": 5},
    }
    state = make_minimal_state(agent_id="observer", agent=agent)
    state["world_turn"] = 200
    projected = project_zone_state(state=state, mode="full")
    full_agent = projected["agents"]["observer"]
    metrics = full_agent["knowledge_first_metrics"]
    assert "observation_memory_records_written" in metrics
    assert "stalkers_seen_memory_records_written" in metrics
    assert "corpse_seen_memory_records_written" in metrics
    assert "target_belief_memory_fallbacks" in metrics
    assert "context_builder_memory_fallbacks" in metrics
    assert "memory_evictions_per_tick" in metrics
    assert "memory_drops_per_tick" in metrics


def test_knowledge_major_revision_not_bumped_by_minor_refresh() -> None:
    agent = make_agent(agent_id="observer", location_id="loc_a")
    state = make_minimal_state(agent_id="observer", agent=agent)
    projected = project_zone_state(state=state, mode="full")
    metrics = projected["agents"]["observer"]["knowledge_first_metrics"]
    assert metrics["target_belief_memory_fallbacks"] == 0
    assert metrics["context_builder_memory_fallbacks"] == 0
