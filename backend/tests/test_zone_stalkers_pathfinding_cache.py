"""Tests for pathfinding LRU cache (CPU PR1)."""
import pytest
import app.games.zone_stalkers.pathfinding_cache as pc


def setup_function():
    """Clear cache before each test."""
    pc.invalidate_all()


def test_pathfinding_cache_stores_and_retrieves():
    pc.set_cached("rev1", "loc_A", "shortest_path", "loc_B", value=["loc_A", "loc_C", "loc_B"])
    result = pc.get_cached("rev1", "loc_A", "shortest_path", "loc_B")
    assert result == ["loc_A", "loc_C", "loc_B"]


def test_pathfinding_cache_miss_returns_none():
    result = pc.get_cached("rev1", "loc_X", "shortest_path", "loc_Y")
    assert result is None


def test_pathfinding_cache_invalidate_for_revision():
    pc.set_cached("rev_a", "loc_1", "shortest_path", value=["loc_1"])
    pc.set_cached("rev_a", "loc_2", "shortest_path", value=["loc_2"])
    pc.set_cached("rev_b", "loc_1", "shortest_path", value=["loc_1"])

    removed = pc.invalidate_for_revision("rev_a")
    assert removed == 2
    assert pc.get_cached("rev_a", "loc_1", "shortest_path") is None
    assert pc.get_cached("rev_a", "loc_2", "shortest_path") is None
    # rev_b entries survive
    assert pc.get_cached("rev_b", "loc_1", "shortest_path") == ["loc_1"]


def test_pathfinding_cache_bounded_by_max_entries():
    """Cache should not exceed _MAX_PATH_CACHE_ENTRIES."""
    # Override max for test
    original_max = pc._MAX_PATH_CACHE_ENTRIES
    pc._MAX_PATH_CACHE_ENTRIES = 5
    try:
        for i in range(10):
            pc.set_cached("rev", f"loc_{i}", "kind", value=i)
        assert len(pc._cache) <= 5
    finally:
        pc._MAX_PATH_CACHE_ENTRIES = original_max
        pc.invalidate_all()


def test_pathfinding_cache_lru_eviction():
    """LRU: least recently used entries are evicted first."""
    original_max = pc._MAX_PATH_CACHE_ENTRIES
    pc._MAX_PATH_CACHE_ENTRIES = 3
    try:
        pc.set_cached("rev", "loc_0", "kind", value=0)
        pc.set_cached("rev", "loc_1", "kind", value=1)
        pc.set_cached("rev", "loc_2", "kind", value=2)
        # Access loc_0 to make it most-recently-used
        pc.get_cached("rev", "loc_0", "kind")
        # Adding one more should evict the LRU entry (loc_1)
        pc.set_cached("rev", "loc_3", "kind", value=3)
        assert pc.get_cached("rev", "loc_1", "kind") is None  # evicted
        assert pc.get_cached("rev", "loc_0", "kind") == 0   # survived
        assert pc.get_cached("rev", "loc_2", "kind") == 2   # survived
        assert pc.get_cached("rev", "loc_3", "kind") == 3   # survived
    finally:
        pc._MAX_PATH_CACHE_ENTRIES = original_max
        pc.invalidate_all()


def test_pathfinding_cache_invalidate_all():
    pc.set_cached("r1", "l1", "k", value="x")
    pc.set_cached("r2", "l2", "k", value="y")
    pc.invalidate_all()
    assert pc.get_cached("r1", "l1", "k") is None
    assert pc.get_cached("r2", "l2", "k") is None
    assert pc.get_stats()["size"] == 0


def test_pathfinding_cache_get_stats():
    pc.set_cached("r1", "l1", "k", value=1)
    stats = pc.get_stats()
    assert stats["size"] == 1
    assert stats["max_size"] == pc._MAX_PATH_CACHE_ENTRIES
