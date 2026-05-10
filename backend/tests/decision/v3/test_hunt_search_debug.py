from __future__ import annotations

import uuid

from app.games.zone_stalkers.debug.hunt_search_debug import (
    build_hunt_debug_payload,
    build_hunt_search_by_agent,
    build_location_hunt_traces,
)
from app.games.zone_stalkers.memory.models import MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from tests.decision.conftest import make_agent, make_minimal_state


def _add_record(
    agent: dict,
    *,
    kind: str,
    created_turn: int,
    location_id: str | None = None,
    confidence: float = 0.7,
    details: dict | None = None,
) -> str:
    ensure_memory_v3(agent)
    record_id = str(uuid.uuid4())
    add_memory_record(
        agent,
        MemoryRecord(
            id=record_id,
            agent_id=str(agent.get("name") or "hunter"),
            layer="spatial",
            kind=kind,
            created_turn=created_turn,
            last_accessed_turn=None,
            summary=kind,
            details=details or {},
            location_id=location_id,
            confidence=confidence,
        ),
    )
    return record_id


def test_build_hunt_search_by_agent_uses_brain_context_belief() -> None:
    hunter = make_agent(agent_id="hunter", global_goal="kill_stalker", kill_target_id="target_1")
    target = make_agent(agent_id="target_1", location_id="loc_b")
    state = make_minimal_state(agent=hunter)
    state["agents"]["target_1"] = target
    state["locations"]["loc_b"]["agents"] = ["target_1"]
    hunter["brain_v3_context"] = {
        "objective_key": "TRACK_TARGET",
        "hunt_target_belief": {
            "target_id": "target_1",
            "best_location_id": "loc_b",
            "best_location_confidence": 0.72,
            "possible_locations": [
                {
                    "location_id": "loc_b",
                    "probability": 0.56,
                    "confidence": 0.72,
                    "freshness": 0.81,
                    "reason": "target_moved",
                    "source_refs": ["memory:abc"],
                }
            ],
            "likely_routes": [],
            "exhausted_locations": ["loc_a"],
            "lead_count": 4,
        },
    }

    payload = build_hunt_search_by_agent(state=state, world_turn=120)
    assert "bot1" in payload
    entry = payload["bot1"]
    assert entry["target_id"] == "target_1"
    assert entry["target_name"] == "target_1"
    assert entry["best_location_id"] == "loc_b"
    assert entry["possible_locations"][0]["location_id"] == "loc_b"
    assert entry["exhausted_locations"] == ["loc_a"]


def test_build_location_hunt_traces_includes_negative_leads_with_cooldown() -> None:
    hunter = make_agent(agent_id="hunter", global_goal="kill_stalker", kill_target_id="target_1")
    state = make_minimal_state(agent=hunter)
    _add_record(
        hunter,
        kind="target_not_found",
        created_turn=100,
        location_id="loc_b",
        confidence=0.8,
        details={
            "target_id": "target_1",
            "location_id": "loc_b",
            "failed_search_count": 3,
            "cooldown_until_turn": 350,
            "source_agent_id": "witness_1",
        },
    )
    _add_record(
        hunter,
        kind="no_tracks_found",
        created_turn=101,
        location_id="loc_b",
        confidence=0.6,
        details={"target_id": "target_1", "location_id": "loc_b"},
    )

    payload = build_location_hunt_traces(state=state, world_turn=120)
    assert "loc_b" in payload
    loc_payload = payload["loc_b"]
    kinds = {item["kind"] for item in loc_payload["negative_leads"]}
    assert "target_not_found" in kinds
    assert "no_tracks_found" in kinds
    exhausted = loc_payload["is_exhausted_for"][0]
    assert exhausted["failed_search_count"] == 3
    assert exhausted["cooldown_until_turn"] == 350
    assert exhausted["source_agent_id"] == "witness_1"


def test_build_location_hunt_traces_extracts_routes() -> None:
    hunter = make_agent(agent_id="hunter", global_goal="kill_stalker", kill_target_id="target_1")
    state = make_minimal_state(agent=hunter)
    _add_record(
        hunter,
        kind="target_moved",
        created_turn=110,
        location_id="loc_b",
        confidence=0.9,
        details={
            "target_id": "target_1",
            "from_location_id": "loc_a",
            "to_location_id": "loc_b",
        },
    )

    payload = build_location_hunt_traces(state=state, world_turn=120)
    assert payload["loc_b"]["routes_in"][0]["from_location_id"] == "loc_a"
    assert payload["loc_a"]["routes_out"][0]["to_location_id"] == "loc_b"


def test_build_location_hunt_traces_includes_witness_source_exhausted() -> None:
    hunter = make_agent(agent_id="hunter", global_goal="kill_stalker", kill_target_id="target_1")
    state = make_minimal_state(agent=hunter)
    _add_record(
        hunter,
        kind="witness_source_exhausted",
        created_turn=111,
        location_id="loc_a",
        confidence=0.9,
        details={
            "target_id": "target_1",
            "location_id": "loc_a",
            "source_kind": "location_witnesses",
            "cooldown_until_turn": 320,
        },
    )

    payload = build_location_hunt_traces(state=state, world_turn=120)
    loc_payload = payload["loc_a"]
    witness_entry = next(item for item in loc_payload["negative_leads"] if item["kind"] == "witness_source_exhausted")
    assert witness_entry["source_kind"] == "location_witnesses"
    assert witness_entry["cooldown_until_turn"] == 320
    exhausted_entry = next(item for item in loc_payload["is_exhausted_for"] if item["source_ref"] == witness_entry["source_ref"])
    assert exhausted_entry["source_kind"] == "location_witnesses"
    assert exhausted_entry["cooldown_until_turn"] == 320


def test_build_hunt_debug_payload_contains_agent_and_location_maps() -> None:
    hunter = make_agent(agent_id="hunter", global_goal="kill_stalker", kill_target_id="target_1")
    state = make_minimal_state(agent=hunter)
    hunter["brain_v3_context"] = {
        "objective_key": "LOCATE_TARGET",
        "hunt_target_belief": {
            "target_id": "target_1",
            "best_location_id": "loc_b",
            "best_location_confidence": 0.62,
            "possible_locations": [],
            "likely_routes": [],
            "exhausted_locations": [],
            "lead_count": 0,
        },
    }
    _add_record(
        hunter,
        kind="target_seen",
        created_turn=118,
        location_id="loc_b",
        confidence=0.95,
        details={"target_id": "target_1", "location_id": "loc_b"},
    )

    payload = build_hunt_debug_payload(state=state, world_turn=120)
    assert "hunt_search_by_agent" in payload
    assert "location_hunt_traces" in payload
    assert "bot1" in payload["hunt_search_by_agent"]
    assert "loc_b" in payload["location_hunt_traces"]
