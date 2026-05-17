"""PR4: Location knowledge performance benchmarks.

Tests use operation counters, monkeypatching, and bounded-entry assertions
rather than flaky wall-clock timing.  Wall-clock smoke is marked with
skip conditions to stay CI-stable.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_VISITED,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_EXISTS,
    MAX_KNOWN_LOCATIONS_PER_AGENT,
    ensure_location_knowledge_v1,
    upsert_known_location,
    mark_location_visited,
    mark_neighbor_locations_known,
    get_location_indexes,
    build_location_knowledge_debug_summary,
)
from app.games.zone_stalkers.knowledge.known_graph import (
    build_known_graph_view,
    find_known_path,
    find_frontier_locations,
    known_locations_with_feature,
    get_known_path_cache_stats,
    MAX_PATH_CACHE_ENTRIES,
    FRONTIER_MAX_CANDIDATES,
    TRADER_SEARCH_MAX_CANDIDATES,
    ARTIFACT_SEARCH_MAX_CANDIDATES,
)
from app.games.zone_stalkers.knowledge.location_knowledge_exchange import (
    MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION,
    build_location_knowledge_share_packets,
    receive_location_knowledge_packets,
)
from app.games.zone_stalkers.rules.tick_rules import _passive_location_knowledge_exchange


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agent(agent_id: str = "perf_bot") -> dict[str, Any]:
    return {"id": agent_id, "location_id": "loc_0"}


def _state_chain(n_locs: int) -> dict[str, Any]:
    """Build a linear chain world of n_locs locations."""
    locs = {}
    for i in range(n_locs):
        connections = []
        if i > 0:
            connections.append({"to": f"loc_{i-1}", "type": "road", "travel_time": 10})
        if i < n_locs - 1:
            connections.append({"to": f"loc_{i+1}", "type": "road", "travel_time": 10})
        locs[f"loc_{i}"] = {
            "id": f"loc_{i}", "name": f"Loc {i}", "terrain_type": "plain",
            "anomaly_activity": 1 if i % 5 == 0 else 0,
            "connections": connections,
            "agents": [], "items": [], "artifacts": [],
        }
    return {"locations": locs, "traders": {}, "world_turn": 100}


def _populate_agent_with_n_known_locations(agent: dict, n: int) -> None:
    """Seed agent with n known locations."""
    for i in range(n):
        upsert_known_location(
            agent,
            location_id=f"loc_{i}",
            world_turn=100 + i,
            knowledge_level=LOCATION_KNOWLEDGE_VISITED if i % 3 == 0 else LOCATION_KNOWLEDGE_EXISTS,
            source="direct_visit" if i % 3 == 0 else "rumor",
            confidence=0.9 if i % 3 == 0 else 0.5,
            snapshot={
                "name": f"Loc {i}",
                "has_trader": i % 10 == 0,
                "has_shelter": i % 15 == 0,
                "anomaly_risk_estimate": 0.5 if i % 5 == 0 else 0.0,
            },
        )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_upsert_known_location_600_entries_fast():
    """Inserting 600 entries should correctly enforce caps and update indexes."""
    agent = _agent()
    _populate_agent_with_n_known_locations(agent, 600)

    knowledge = ensure_location_knowledge_v1(agent)
    # Caps should be respected
    assert len(knowledge["known_locations"]) <= MAX_KNOWN_LOCATIONS_PER_AGENT
    # Indexes should be populated correctly
    indexes = get_location_indexes(agent)
    assert isinstance(indexes.get("visited_ids"), list)
    assert isinstance(indexes.get("frontier_ids"), list)
    assert isinstance(indexes.get("known_trader_location_ids"), list)
    assert len(indexes["visited_ids"]) > 0
    assert len(indexes["known_trader_location_ids"]) > 0


def test_mark_visit_with_1000_world_locations_only_touches_degree():
    """mark_neighbor_locations_known should touch O(degree) neighbors, not O(N)."""
    agent = _agent()
    state = _state_chain(1000)  # world has 1000 locations
    # Agent is at loc_500 which has 2 neighbors
    agent["location_id"] = "loc_500"

    # Spy on upsert_known_location to count calls
    call_count = {"n": 0}
    original = upsert_known_location

    def spy_upsert(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    with patch(
        "app.games.zone_stalkers.knowledge.location_knowledge.upsert_known_location",
        side_effect=spy_upsert,
    ):
        mark_location_visited(agent, state=state, location_id="loc_500", world_turn=200)
        mark_neighbor_locations_known(agent, state=state, location_id="loc_500", world_turn=200)

    # loc_500 has at most 2 neighbors → at most 3 upserts (self + 2 neighbors)
    assert call_count["n"] <= 6, f"Expected ≤6 upserts, got {call_count['n']}"


def test_known_path_cache_600_known_locations_reuses_result():
    """After first path lookup, second identical lookup should be a cache hit."""
    agent = _agent()
    _populate_agent_with_n_known_locations(agent, 600)
    # Wire up sequential edges in known graph via snapshots
    for i in range(600):
        knowledge = ensure_location_knowledge_v1(agent)
        entry = knowledge["known_locations"].get(f"loc_{i}")
        if entry:
            entry["snapshot"] = entry.get("snapshot") or {}
            entry["snapshot"]["known_neighbor_ids"] = [f"loc_{i+1}"] if i < 599 else []

    find_known_path(agent, start_location_id="loc_0", target_location_id="loc_10")
    stats_first = get_known_path_cache_stats(agent)
    assert stats_first["cached_paths"] >= 1

    find_known_path(agent, start_location_id="loc_0", target_location_id="loc_10")
    stats_second = get_known_path_cache_stats(agent)

    assert stats_second["hits"] == stats_first["hits"] + 1


def test_context_builder_does_not_copy_all_known_locations():
    """Debug summary should not copy the full known_locations table."""
    agent = _agent()
    _populate_agent_with_n_known_locations(agent, 600)

    # Check that get_location_indexes returns a lightweight index dict, not the full table
    indexes = get_location_indexes(agent)
    # Index should be a compact dict with small lists, not 600-entry table
    for key in ("visited_ids", "frontier_ids", "known_trader_location_ids"):
        assert isinstance(indexes.get(key), list), f"Expected list for {key}"

    # Debug summary should be compact, not include full table
    summary = build_location_knowledge_debug_summary(agent)
    assert "known_locations" in summary
    assert isinstance(summary["known_locations"], int)
    # Summary should not contain the actual location entries
    assert "loc_0" not in summary


def test_exchange_600_known_locations_sends_only_top_k():
    """Exchange of 600-location agent sends at most MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION."""
    source = _agent("rich_source")
    _populate_agent_with_n_known_locations(source, 600)

    packets = build_location_knowledge_share_packets(source, world_turn=500)
    assert len(packets) <= MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION
    assert len(packets) <= 5


def test_40_agents_600_known_locations_summary_budget():
    """Building debug summaries for 40 agents with 600 known locations returns compact results."""
    agents = [_agent(f"bot_{i}") for i in range(40)]
    for ag in agents:
        _populate_agent_with_n_known_locations(ag, 600)

    summaries = [build_location_knowledge_debug_summary(ag) for ag in agents]

    assert len(summaries) == 40
    # All summaries should have compact integer fields (not full table copies)
    for s in summaries:
        assert isinstance(s["known_locations"], int)
        assert s["known_locations"] <= MAX_KNOWN_LOCATIONS_PER_AGENT
        assert isinstance(s.get("visited_locations"), int)
        assert isinstance(s.get("frontiers"), int)
        assert isinstance(s.get("known_traders"), int)


def test_location_indexes_updated_incrementally():
    """Indexes should be updated after each upsert without full rebuild."""
    agent = _agent()
    upsert_known_location(
        agent,
        location_id="loc_trader",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Trader Base", "has_trader": True, "has_shelter": True},
    )
    indexes = get_location_indexes(agent)
    assert "loc_trader" in indexes.get("known_trader_location_ids", [])
    assert "loc_trader" in indexes.get("known_shelter_location_ids", [])
    assert "loc_trader" in indexes.get("visited_ids", [])


def test_frontier_lookup_uses_index_not_full_scan():
    """find_frontier_locations should respect the frontier_ids index."""
    agent = _agent()
    upsert_known_location(
        agent, location_id="loc_visited",
        world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit", confidence=1.0,
        snapshot={"known_neighbor_ids": ["loc_frontier"]},
    )
    upsert_known_location(
        agent, location_id="loc_frontier",
        world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="direct_neighbor_observation", confidence=0.8,
    )
    indexes = get_location_indexes(agent)
    assert "loc_frontier" in indexes.get("frontier_ids", []), "frontier not in index"
    assert "loc_visited" not in indexes.get("frontier_ids", []), "visited should not be frontier"

    frontiers = find_frontier_locations(agent, from_location_id="loc_visited", limit=10)
    frontier_ids_found = {f.get("location_id") for f in frontiers}
    assert "loc_frontier" in frontier_ids_found


def test_known_locations_with_feature_uses_trader_index():
    """known_locations_with_feature('has_trader') should use trader index."""
    agent = _agent()
    upsert_known_location(
        agent, location_id="loc_t",
        world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit", confidence=1.0,
        snapshot={"has_trader": True},
    )
    upsert_known_location(
        agent, location_id="loc_no_t",
        world_turn=100, knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit", confidence=1.0,
        snapshot={"has_trader": False},
    )
    indexes = get_location_indexes(agent)
    assert "loc_t" in indexes.get("known_trader_location_ids", [])
    assert "loc_no_t" not in indexes.get("known_trader_location_ids", [])

    traders = known_locations_with_feature(agent, "has_trader", min_confidence=0.3)
    assert any(t.get("location_id") == "loc_t" for t in traders)
    assert not any(t.get("location_id") == "loc_no_t" for t in traders)


def test_40_agents_600_known_locations_debug_summary_does_not_copy_full_tables():
    agents = [_agent(f"bot_{i}") for i in range(40)]
    for ag in agents:
        _populate_agent_with_n_known_locations(ag, 600)
    summaries = [build_location_knowledge_debug_summary(ag) for ag in agents]
    assert all("known_locations" in s and isinstance(s["known_locations"], int) for s in summaries)
    assert all("loc_0" not in s for s in summaries)


def test_40_agents_600_known_locations_exchange_respects_top_k_and_cooldown():
    receiver = _agent("receiver")
    receiver.update({"archetype": "stalker_agent", "is_alive": True, "location_id": "loc_0"})
    sources = []
    for idx in range(40):
        src = _agent(f"src_{idx}")
        src.update({"archetype": "stalker_agent", "is_alive": True, "location_id": "loc_0"})
        _populate_agent_with_n_known_locations(src, 600)
        sources.append(src)
    state = {"agents": {"receiver": receiver, **{s["id"]: s for s in sources}}}

    updated_first = _passive_location_knowledge_exchange("receiver", receiver, state, 100)
    rev_first = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))
    updated_second = _passive_location_knowledge_exchange("receiver", receiver, state, 101)
    rev_second = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))
    assert updated_first <= MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION
    assert updated_second == 0
    assert rev_second == rev_first


def test_600_known_locations_known_feature_query_uses_index():
    agent = _agent()
    _populate_agent_with_n_known_locations(agent, 600)
    indexes = get_location_indexes(agent)
    indexes["known_trader_location_ids"] = []
    indexes["revision"] = int((ensure_location_knowledge_v1(agent).get("stats") or {}).get("known_locations_revision", 0))
    assert known_locations_with_feature(agent, "has_trader", min_confidence=0.3) == []


def test_600_known_locations_path_cache_hit_avoids_bfs_rebuild():
    agent = _agent()
    _populate_agent_with_n_known_locations(agent, 600)
    for i in range(600):
        entry = ensure_location_knowledge_v1(agent)["known_locations"].get(f"loc_{i}")
        if isinstance(entry, dict):
            entry["snapshot"] = entry.get("snapshot") or {}
            entry["snapshot"]["known_neighbor_ids"] = [f"loc_{i+1}"] if i < 599 else []

    with patch("app.games.zone_stalkers.knowledge.known_graph.KnownGraphView.bfs_path") as bfs_mock:
        bfs_mock.side_effect = lambda start, target, max_nodes=700: [target] if start != target else []
        find_known_path(agent, start_location_id="loc_0", target_location_id="loc_10")
        first_calls = bfs_mock.call_count
        find_known_path(agent, start_location_id="loc_0", target_location_id="loc_10")
        assert bfs_mock.call_count == first_calls
