from __future__ import annotations

from copy import deepcopy
from time import perf_counter

from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_VISITED,
    MAX_KNOWN_LOCATIONS_PER_AGENT,
    ensure_location_knowledge_v1,
    get_known_location,
    mark_location_visited,
    mark_neighbor_locations_known,
    summarize_location_knowledge,
    upsert_known_location,
)


def _state() -> dict:
    return {
        "world_turn": 100,
        "locations": {
            "loc_a": {
                "id": "loc_a",
                "name": "Loc A",
                "terrain_type": "plain",
                "anomaly_activity": 1,
                "connections": [
                    {"to": "loc_b", "type": "road", "travel_time": 10},
                    {"to": "loc_c", "type": "path", "travel_time": 15},
                ],
                "agents": ["bot_1"],
                "items": [{"id": "item_1"}],
                "artifacts": [{"id": "art_1"}],
            },
            "loc_b": {
                "id": "loc_b",
                "name": "Loc B",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_a", "type": "road", "travel_time": 10}],
                "agents": [],
                "items": [],
                "artifacts": [],
            },
            "loc_c": {
                "id": "loc_c",
                "name": "Loc C",
                "terrain_type": "field_camp",
                "anomaly_activity": 3,
                "connections": [{"to": "loc_a", "type": "path", "travel_time": 15}],
                "agents": [],
                "items": [],
                "artifacts": [],
            },
        },
        "traders": {
            "trader_1": {"id": "trader_1", "location_id": "loc_b"},
        },
    }


def test_spawn_marks_current_location_visited_and_neighbors_known_exists():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)

    loc_a = get_known_location(agent, "loc_a")
    assert loc_a is not None
    assert loc_a["knowledge_level"] == LOCATION_KNOWLEDGE_VISITED
    assert loc_a["visited"] is True

    for neighbor in ("loc_b", "loc_c"):
        entry = get_known_location(agent, neighbor)
        assert entry is not None
        assert entry["knowledge_level"] == LOCATION_KNOWLEDGE_EXISTS
        assert entry.get("snapshot") is None


def test_visit_location_stores_compact_snapshot_not_full_location_dict():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=120)

    entry = get_known_location(agent, "loc_a")
    assert entry is not None
    snapshot = entry.get("snapshot")
    assert isinstance(snapshot, dict)
    assert snapshot["name"] == "Loc A"
    assert "connections" not in snapshot
    assert "items" not in snapshot
    assert "agents" not in snapshot


def test_neighbor_discovery_does_not_reveal_neighbor_snapshot():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=130)

    neighbor = get_known_location(agent, "loc_b")
    assert neighbor is not None
    assert neighbor["knowledge_level"] == LOCATION_KNOWLEDGE_EXISTS
    assert neighbor.get("snapshot") is None


def test_revisit_updates_last_visited_and_visit_count():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=140)
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=150)

    entry = get_known_location(agent, "loc_a")
    assert entry is not None
    assert entry["last_visited_turn"] == 150
    assert entry["visit_count"] == 2


def test_direct_visit_not_overwritten_by_lower_confidence_rumor():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=200)
    before = deepcopy(get_known_location(agent, "loc_a"))

    upsert_known_location(
        agent,
        location_id="loc_a",
        world_turn=210,
        knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="rumor",
        confidence=0.3,
        snapshot=None,
    )

    after = get_known_location(agent, "loc_a")
    assert after is not None and before is not None
    assert after["knowledge_level"] == LOCATION_KNOWLEDGE_VISITED
    assert after["visited"] is True
    assert after["snapshot"] == before["snapshot"]


def test_known_locations_revision_increments_on_update():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    ensure_location_knowledge_v1(agent)
    before = agent["knowledge_v1"]["stats"]["known_locations_revision"]

    upsert_known_location(
        agent,
        location_id="loc_a",
        world_turn=220,
        knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="direct_neighbor_observation",
        confidence=0.8,
    )

    assert agent["knowledge_v1"]["stats"]["known_locations_revision"] == before + 1


def test_known_locations_size_limits_preserve_visited_shelters_traders():
    agent = {"id": "bot_1", "location_id": "loc_anchor"}

    upsert_known_location(
        agent,
        location_id="loc_anchor",
        world_turn=300,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Anchor", "has_shelter": True, "has_trader": True, "has_exit": False},
    )

    for idx in range(MAX_KNOWN_LOCATIONS_PER_AGENT + 50):
        upsert_known_location(
            agent,
            location_id=f"loc_{idx}",
            world_turn=301 + idx,
            knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
            source="rumor",
            confidence=0.1,
        )

    known_locations = agent["knowledge_v1"]["known_locations"]
    assert len(known_locations) <= MAX_KNOWN_LOCATIONS_PER_AGENT
    assert "loc_anchor" in known_locations
    assert known_locations["loc_anchor"]["visited"] is True


def test_snapshot_does_not_include_current_agents_list_or_mutable_location_ref():
    agent = {"id": "bot_1", "location_id": "loc_a"}
    state = _state()

    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=500)
    entry = get_known_location(agent, "loc_a")
    assert entry is not None
    snapshot = entry["snapshot"]

    state["locations"]["loc_a"]["name"] = "Mutated"
    state["locations"]["loc_a"]["agents"].append("another")

    assert snapshot["name"] == "Loc A"
    assert "agents" not in snapshot


def test_mark_location_visited_1000_locations_600_known_fast():
    agent = {"id": "bot_1", "location_id": "loc_0"}
    locations = {}
    for i in range(1000):
        loc_id = f"loc_{i}"
        nxt = f"loc_{(i + 1) % 1000}"
        prv = f"loc_{(i - 1) % 1000}"
        locations[loc_id] = {
            "id": loc_id,
            "name": loc_id,
            "terrain_type": "plain",
            "anomaly_activity": 0,
            "connections": [
                {"to": nxt, "type": "road", "travel_time": 10},
                {"to": prv, "type": "road", "travel_time": 10},
            ],
            "agents": [],
            "items": [],
            "artifacts": [],
        }

    state = {"locations": locations, "traders": {}}

    for i in range(600):
        upsert_known_location(
            agent,
            location_id=f"loc_seed_{i}",
            world_turn=100,
            knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
            source="rumor",
            confidence=0.2,
        )

    start = perf_counter()
    mark_location_visited(agent, state=state, location_id="loc_10", world_turn=600)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_10", world_turn=600)
    elapsed = perf_counter() - start

    summary = summarize_location_knowledge(agent)
    assert summary["known_locations_count"] >= 602
    assert elapsed < 0.25
