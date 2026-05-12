from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_SOCIAL, LAYER_SPATIAL, LAYER_THREAT
from app.games.zone_stalkers.memory.store import add_memory_record
from tests.decision.conftest import make_agent, make_minimal_state


def test_context_builder_uses_memory_v3_records() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
    state["traders"]["trader_1"] = {
        "name": "Сидорович",
        "location_id": "loc_b",
        "is_alive": True,
        "inventory": [],
    }

    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_target",
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=95,
            last_accessed_turn=None,
            summary="target spotted near trader",
            details={"target_agent_id": "target_1", "location_id": "loc_b"},
            location_id="loc_b",
            entity_ids=("target_1",),
            tags=("target", "intel"),
        ),
    )
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_trader",
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="trader_visited",
            created_turn=96,
            last_accessed_turn=None,
            summary="visited trader",
            details={"trader_id": "trader_1", "trader_name": "Сидорович", "location_id": "loc_b"},
            location_id="loc_b",
            entity_ids=("trader_1",),
            tags=("trade", "trader"),
        ),
    )
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_hazard",
            agent_id="bot1",
            layer=LAYER_THREAT,
            kind="anomaly_detected",
            created_turn=97,
            last_accessed_turn=None,
            summary="dangerous anomaly",
            details={"action_kind": "anomaly_detected", "location_id": "loc_b"},
            location_id="loc_b",
            tags=("anomaly", "danger"),
        ),
    )
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_loc",
            agent_id="bot1",
            layer=LAYER_SPATIAL,
            kind="target_last_known_location",
            created_turn=98,
            last_accessed_turn=None,
            summary="target last known location",
            details={"location_id": "loc_b"},
            location_id="loc_b",
            entity_ids=("target_1",),
            tags=("target", "tracking", "spatial"),
        ),
    )

    ctx = build_agent_context("bot1", agent, state)

    assert any(entity["agent_id"] == "target_1" for entity in ctx.known_entities)
    assert any(location["location_id"] == "loc_b" for location in ctx.known_locations)
    assert any(hazard["kind"] == "anomaly_detected" for hazard in ctx.known_hazards)
    assert any(trader["agent_id"] == "trader_1" for trader in ctx.known_traders)


def test_context_builder_without_memory_v3_returns_empty_known_memory() -> None:
    agent = make_agent()
    agent.pop("memory_v3", None)
    agent["memory"] = [{
        "world_turn": 100,
        "type": "observation",
        "title": "Trader seen",
        "summary": "Saw trader at loc_b",
        "effects": {
            "action_kind": "trader_visit",
            "trader_id": "trader_1",
            "trader_name": "Сидорович",
            "location_id": "loc_b",
        },
    }]
    state = make_minimal_state(agent=agent)
    state["traders"]["trader_1"] = {
        "name": "Сидорович",
        "location_id": "loc_b",
        "is_alive": True,
        "inventory": [],
    }

    ctx = build_agent_context("bot1", agent, state)

    assert ctx.known_entities == []
    assert ctx.known_locations == []
    assert ctx.known_hazards == []
    assert ctx.known_traders == []
