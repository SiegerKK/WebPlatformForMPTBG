from __future__ import annotations

from copy import deepcopy

from app.games.zone_stalkers.knowledge.known_graph import (
    MAX_PATH_CACHE_ENTRIES,
    build_known_graph_view,
    find_frontier_locations,
    find_known_path,
    get_known_path_cache_stats,
    is_location_known,
    is_location_visited,
    known_locations_with_feature,
)
from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_VISITED,
    mark_location_visited,
    mark_neighbor_locations_known,
    upsert_known_location,
)


def _make_agent(agent_id: str = "bot_1", loc_id: str = "loc_a") -> dict:
    return {"id": agent_id, "location_id": loc_id}


def _make_state(*,
    locs: dict | None = None,
    traders: dict | None = None,
) -> dict:
    if locs is None:
        locs = {
            "loc_a": {
                "id": "loc_a", "name": "Loc A", "terrain_type": "plain",
                "anomaly_activity": 0,
                "connections": [
                    {"to": "loc_b", "type": "road", "travel_time": 10},
                    {"to": "loc_c", "type": "path", "travel_time": 15},
                ],
                "agents": [], "items": [], "artifacts": [],
            },
            "loc_b": {
                "id": "loc_b", "name": "Loc B", "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [
                    {"to": "loc_a", "type": "road", "travel_time": 10},
                    {"to": "loc_d", "type": "road", "travel_time": 20},
                ],
                "agents": [], "items": [], "artifacts": [],
            },
            "loc_c": {
                "id": "loc_c", "name": "Loc C", "terrain_type": "field_camp",
                "anomaly_activity": 3,
                "connections": [
                    {"to": "loc_a", "type": "path", "travel_time": 15},
                ],
                "agents": [], "items": [], "artifacts": [],
            },
            "loc_d": {
                "id": "loc_d", "name": "Loc D", "terrain_type": "industrial",
                "anomaly_activity": 0,
                "connections": [
                    {"to": "loc_b", "type": "road", "travel_time": 20},
                ],
                "agents": [], "items": [], "artifacts": [],
            },
        }
    return {"locations": locs, "traders": traders or {}, "world_turn": 100}


def test_known_path_uses_only_known_edges():
    """Path must only travel through edges present in the known graph."""
    agent = _make_agent()
    state = _make_state()
    # Build known path: a -> b -> d
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)
    mark_location_visited(agent, state=state, location_id="loc_b", world_turn=110)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_b", world_turn=110)

    path = find_known_path(agent, start_location_id="loc_a", target_location_id="loc_d")
    assert path is not None
    assert "loc_d" in path
    for hop in path:
        assert is_location_known(agent, hop), f"hop {hop} not in known graph"


def test_unknown_world_shortcut_not_used_by_npc_planner():
    """If target location is completely unknown, find_known_path returns None."""
    agent = _make_agent()
    state = _make_state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)

    # loc_d is not known to the agent at all
    path = find_known_path(agent, start_location_id="loc_a", target_location_id="loc_d")
    assert path is None


def test_known_exists_neighbor_can_be_selected_as_explore_frontier():
    """find_frontier_locations should return known_exists (not yet visited) locations."""
    agent = _make_agent()
    state = _make_state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)

    frontiers = find_frontier_locations(agent, from_location_id="loc_a", limit=10)
    frontier_ids = [f.get("location_id") for f in frontiers]
    # loc_b and loc_c should appear as frontiers (known_exists from loc_a visit)
    assert any(fid in {"loc_b", "loc_c"} for fid in frontier_ids)
    # loc_a itself (visited) must NOT be a frontier
    assert "loc_a" not in frontier_ids


def test_is_location_known_and_visited():
    agent = _make_agent()
    state = _make_state()

    assert not is_location_known(agent, "loc_a")
    assert not is_location_visited(agent, "loc_a")

    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=200)
    assert is_location_known(agent, "loc_b")
    assert not is_location_visited(agent, "loc_b")

    mark_location_visited(agent, state=state, location_id="loc_b", world_turn=210)
    assert is_location_visited(agent, "loc_b")


def test_known_locations_with_feature_has_trader():
    agent = _make_agent()
    upsert_known_location(
        agent,
        location_id="loc_trader",
        world_turn=300,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Trader Base", "has_trader": True, "has_shelter": False, "has_exit": False},
    )
    upsert_known_location(
        agent,
        location_id="loc_plain",
        world_turn=300,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Plain", "has_trader": False, "has_shelter": False, "has_exit": False},
    )

    traders = known_locations_with_feature(agent, "has_trader", min_confidence=0.4)
    trader_ids = [t.get("location_id") for t in traders]
    assert "loc_trader" in trader_ids
    assert "loc_plain" not in trader_ids


def test_known_locations_with_feature_has_shelter():
    agent = _make_agent()
    upsert_known_location(
        agent,
        location_id="loc_shelter",
        world_turn=400,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Bunker", "has_shelter": True, "has_trader": False, "has_exit": False},
    )

    shelters = known_locations_with_feature(agent, "has_shelter", min_confidence=0.4)
    assert any(s.get("location_id") == "loc_shelter" for s in shelters)


def test_path_cache_returns_same_result_on_repeated_call():
    agent = _make_agent()
    state = _make_state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)
    mark_location_visited(agent, state=state, location_id="loc_b", world_turn=110)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_b", world_turn=110)

    path1 = find_known_path(agent, start_location_id="loc_a", target_location_id="loc_d")
    stats_after_first = get_known_path_cache_stats(agent)

    path2 = find_known_path(agent, start_location_id="loc_a", target_location_id="loc_d")
    stats_after_second = get_known_path_cache_stats(agent)

    assert path1 == path2
    # Second call should hit the cache
    assert stats_after_second["hits"] == stats_after_first["hits"] + 1


def test_path_cache_invalidates_on_revision_change():
    """After a knowledge update, the next find_known_path call rebuilds the cache."""
    from app.games.zone_stalkers.knowledge.location_knowledge import ensure_location_knowledge_v1

    agent = _make_agent()
    state = _make_state()
    mark_location_visited(agent, state=state, location_id="loc_a", world_turn=100)
    mark_neighbor_locations_known(agent, state=state, location_id="loc_a", world_turn=100)

    find_known_path(agent, start_location_id="loc_a", target_location_id="loc_b")
    cache_revision_before = get_known_path_cache_stats(agent)["revision"]

    # Learn a new location (bumps known_locations_revision)
    mark_location_visited(agent, state=state, location_id="loc_b", world_turn=150)
    actual_revision = int(
        (ensure_location_knowledge_v1(agent).get("stats") or {}).get("known_locations_revision", 0)
    )
    assert actual_revision != cache_revision_before, "revision must have incremented"

    # Second find_known_path should detect stale cache and rebuild with new revision
    find_known_path(agent, start_location_id="loc_a", target_location_id="loc_b")
    stats_after = get_known_path_cache_stats(agent)
    assert stats_after["revision"] == actual_revision


def test_artifact_search_uses_known_anomaly_locations_only():
    agent = _make_agent()
    upsert_known_location(
        agent,
        location_id="loc_anomaly",
        world_turn=500,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={
            "name": "Anomaly Zone",
            "has_shelter": False, "has_trader": False, "has_exit": False,
            "anomaly_risk_estimate": 0.6,
            "artifact_potential_estimate": 0.7,
        },
    )
    upsert_known_location(
        agent,
        location_id="loc_normal",
        world_turn=500,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Normal", "has_shelter": False, "anomaly_risk_estimate": 0.0},
    )

    anomaly_locs = known_locations_with_feature(agent, "has_anomaly", min_confidence=0.3)
    assert any(a.get("location_id") == "loc_anomaly" for a in anomaly_locs)
    assert not any(a.get("location_id") == "loc_normal" for a in anomaly_locs)


def test_known_graph_view_bfs_path_does_not_exceed_max_nodes():
    """When the graph is too large, BFS should return None instead of running forever."""
    agent = _make_agent()
    # Build a chain of 200 locations: known graph is large but we cap at 5 nodes
    prev = "loc_0"
    upsert_known_location(agent, location_id=prev, world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_VISITED,
                           source="direct_visit", confidence=1.0,
                           snapshot={"known_neighbor_ids": ["loc_1"]})
    for i in range(1, 200):
        curr = f"loc_{i}"
        nxt = f"loc_{i+1}"
        upsert_known_location(agent, location_id=curr, world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
                               source="rumor", confidence=0.5)
        # Add snapshot with neighbor for next hop
        entry = agent["knowledge_v1"]["known_locations"].get(curr) or {}
        entry["snapshot"] = {"known_neighbor_ids": [nxt]}
        agent["knowledge_v1"]["known_locations"][curr] = entry

    view = build_known_graph_view(agent)
    path_short = view.bfs_path("loc_0", "loc_3", max_nodes=100)
    assert path_short is not None

    # Limit to 2 nodes → cannot reach loc_100
    path_too_short = view.bfs_path("loc_0", "loc_100", max_nodes=2)
    assert path_too_short is None
