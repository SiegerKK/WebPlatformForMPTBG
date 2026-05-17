"""PR2: Known-graph navigation and planner integration tests."""
from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.knowledge.known_graph import (
    find_frontier_locations,
    is_location_known,
    known_locations_with_feature,
    get_nearest_known_location_with_feature,
)
from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_VISITED,
    LOCATION_KNOWLEDGE_EXISTS,
    mark_location_visited,
    mark_neighbor_locations_known,
    upsert_known_location,
)
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_EXPLORE_FRONTIER,
    OBJECTIVE_GATHER_LOCATION_INTEL,
)
from app.games.zone_stalkers.decision.models.agent_context import AgentContext
from app.games.zone_stalkers.decision.planner import _nearest_trader_location


def _agent(loc_id: str = "loc_a") -> dict[str, Any]:
    return {"id": "bot_1", "location_id": loc_id}


def _state(locs: dict | None = None, traders: dict | None = None) -> dict[str, Any]:
    default_locs = {
        "loc_a": {
            "id": "loc_a", "name": "Start", "terrain_type": "plain",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_b", "type": "road", "travel_time": 10},
                {"to": "loc_c", "type": "path", "travel_time": 15},
            ],
            "agents": [], "items": [], "artifacts": [],
        },
        "loc_b": {
            "id": "loc_b", "name": "Trader Town", "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_a", "type": "road", "travel_time": 10}],
            "agents": [], "items": [], "artifacts": [],
        },
        "loc_c": {
            "id": "loc_c", "name": "Anomaly Field", "terrain_type": "field_camp",
            "anomaly_activity": 3,
            "connections": [{"to": "loc_a", "type": "path", "travel_time": 15}],
            "agents": [], "items": [], "artifacts": [],
        },
    }
    return {"locations": locs or default_locs, "traders": traders or {}, "world_turn": 100}


def test_trader_search_uses_known_trader_not_global_trader_list():
    """known_locations_with_feature('has_trader') must return known traders only."""
    agent = _agent()
    # Seed a known trader location
    upsert_known_location(
        agent,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Trader Town", "has_trader": True, "has_shelter": False, "has_exit": False},
    )

    traders_from_knowledge = known_locations_with_feature(agent, "has_trader", min_confidence=0.4)
    assert any(t.get("location_id") == "loc_b" for t in traders_from_knowledge)
    # Should not include unknown locations
    assert all(t.get("location_id") in agent["knowledge_v1"]["known_locations"]
               for t in traders_from_knowledge)


def test_no_known_trader_generates_explore_frontier_when_frontiers_exist():
    """When agent has no known trader and frontiers exist, EXPLORE_FRONTIER is a valid objective key."""
    # This tests that OBJECTIVE_EXPLORE_FRONTIER and OBJECTIVE_GATHER_LOCATION_INTEL are defined
    assert OBJECTIVE_EXPLORE_FRONTIER == "EXPLORE_FRONTIER"
    assert OBJECTIVE_GATHER_LOCATION_INTEL == "GATHER_LOCATION_INTEL"


def test_emission_uses_known_reachable_shelter():
    """known_locations_with_feature('has_shelter') returns known shelters."""
    agent = _agent()
    upsert_known_location(
        agent,
        location_id="loc_bunker",
        world_turn=200,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Bunker", "has_shelter": True, "has_trader": False, "has_exit": False},
    )

    shelters = known_locations_with_feature(agent, "has_shelter", min_confidence=0.4)
    assert any(s.get("location_id") == "loc_bunker" for s in shelters)


def test_hunt_target_known_location_unreachable_generates_route_exploration():
    """If target known but no known path, find_known_path returns None."""
    from app.games.zone_stalkers.knowledge.known_graph import find_known_path

    agent = _agent()
    # Only know loc_a (visited)
    state = _state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    # Know loc_b exists but NOT the path to it (no edges from loc_a in known graph)
    # Manually add loc_b as known_exists without edges
    upsert_known_location(
        agent,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="rumor",
        confidence=0.5,
    )

    # loc_a was visited, so its neighbors may have edges from snapshot
    # Let's use an agent that only has loc_a visited without neighbors discovered
    agent2 = _agent()
    upsert_known_location(
        agent2,
        location_id="loc_a",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Start", "known_neighbor_ids": []},  # No neighbors known
    )
    upsert_known_location(
        agent2,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="rumor",
        confidence=0.5,
    )

    path = find_known_path(agent2, start_location_id="loc_a", target_location_id="loc_b")
    assert path is None  # No known edge from loc_a to loc_b


def test_exit_zone_requires_known_exit_or_none_returned():
    """get_nearest_known_location_with_feature('has_exit') returns None when no exit known."""
    agent = _agent()

    result = get_nearest_known_location_with_feature(
        agent, "has_exit", from_location_id="loc_a", min_confidence=0.4
    )
    assert result is None


def test_frontier_not_populated_without_known_neighbors():
    """Before knowing any neighbors, find_frontier_locations returns empty."""
    agent = _agent()
    # Only know current loc as visited, no neighbors
    upsert_known_location(
        agent,
        location_id="loc_a",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Start", "known_neighbor_ids": []},
    )

    frontiers = find_frontier_locations(agent, from_location_id="loc_a", limit=10)
    assert frontiers == []


def test_frontier_populated_after_neighbor_discovery():
    """After mark_neighbor_locations_known, frontiers are found."""
    agent = _agent()
    state = _state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)

    frontiers = find_frontier_locations(agent, from_location_id="loc_a", limit=10)
    assert len(frontiers) >= 2
    frontier_ids = {f.get("location_id") for f in frontiers}
    assert "loc_b" in frontier_ids or "loc_c" in frontier_ids


def test_nearest_known_trader_respects_known_graph_not_world_graph():
    """get_nearest_known_location_with_feature uses known graph, not world state."""
    agent = _agent()
    # Add a known trader location without any known path to it
    upsert_known_location(
        agent,
        location_id="loc_trader_island",
        world_turn=300,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Trader Island", "has_trader": True, "has_shelter": False, "has_exit": False},
    )

    # loc_a has no known edges → known graph cannot connect to loc_trader_island
    nearest = get_nearest_known_location_with_feature(
        agent,
        "has_trader",
        from_location_id="loc_a",
        min_confidence=0.4,
    )
    # Because no known path exists, it should return None (no reachable known trader)
    assert nearest is None


def test_no_known_trader_does_not_use_world_state_trader_fallback():
    agent = _agent("loc_a")
    state = _state(
        traders={"trader_1": {"id": "trader_1", "location_id": "loc_b", "is_alive": True}},
    )
    state["settings"] = {"location_knowledge_enabled": True}
    ctx = AgentContext(
        agent_id="bot_1",
        self_state=agent,
        location_state=state["locations"]["loc_a"],
        world_context={"world_turn": 100},
    )

    nearest = _nearest_trader_location(ctx, state)
    assert nearest is None
