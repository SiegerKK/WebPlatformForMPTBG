"""
Module-level LRU cache for read-only pathfinding / nearest-object queries.

Cache key: (map_revision, from_loc_id, query_kind, extra_key)
Invalidated when map_revision changes (e.g. after debug_update_map).

This module is intentionally simple: no locks, no threading. Zone Stalkers
ticks run serially in a single thread per match.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

_MAX_PATH_CACHE_ENTRIES: int = 5000

# OrderedDict used as an LRU store: most-recently-used items are at the end.
_cache: OrderedDict[tuple, Any] = OrderedDict()


def get_cached(
    map_revision: Any,
    from_loc_id: str,
    query_kind: str,
    extra_key: str = "",
) -> Any | None:
    """Return cached value for *key*, or None on cache miss.

    Moves the entry to the end of the LRU queue on hit.
    """
    key = (map_revision, from_loc_id, query_kind, extra_key)
    val = _cache.get(key)
    if val is not None:
        _cache.move_to_end(key)
    return val


def set_cached(
    map_revision: Any,
    from_loc_id: str,
    query_kind: str,
    extra_key: str = "",
    value: Any = None,
) -> None:
    """Store *value* in the cache under *key*, evicting LRU entries if needed."""
    key = (map_revision, from_loc_id, query_kind, extra_key)
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _MAX_PATH_CACHE_ENTRIES:
        _cache.popitem(last=False)


def invalidate_for_revision(map_revision: Any) -> int:
    """Remove all entries for *map_revision*.  Returns the count removed."""
    to_delete = [k for k in _cache if k[0] == map_revision]
    for k in to_delete:
        del _cache[k]
    return len(to_delete)


def invalidate_all() -> None:
    """Clear the entire cache."""
    _cache.clear()


def get_stats() -> dict[str, int]:
    """Return current cache statistics."""
    return {"size": len(_cache), "max_size": _MAX_PATH_CACHE_ENTRIES}
