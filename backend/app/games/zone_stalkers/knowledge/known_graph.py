from __future__ import annotations

import collections
from typing import Any

from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_ROUTE_ONLY,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_VISITED,
    ensure_location_knowledge_v1,
    get_known_location,
)

MAX_PATH_CACHE_ENTRIES = 96
FRONTIER_MAX_CANDIDATES = 20
TRADER_SEARCH_MAX_CANDIDATES = 10
SHELTER_SEARCH_MAX_CANDIDATES = 10
ARTIFACT_SEARCH_MAX_CANDIDATES = 20

KNOWN_FEATURES = frozenset({
    "has_shelter",
    "has_trader",
    "has_exit",
    "has_anomaly",
    "has_artifacts",
})

_VISITED_LEVELS = frozenset({LOCATION_KNOWLEDGE_VISITED})
_SNAPSHOT_OR_ABOVE = frozenset({LOCATION_KNOWLEDGE_SNAPSHOT, LOCATION_KNOWLEDGE_VISITED})
_ANY_KNOWN = frozenset({
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_ROUTE_ONLY,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_VISITED,
})


class KnownGraphView:
    __slots__ = ("_adj", "_entries", "_total")

    def __init__(self, adj, entries):
        self._adj = adj
        self._entries = entries
        self._total = len(entries)

    @property
    def known_location_count(self):
        return self._total

    def neighbors(self, location_id):
        return list(self._adj.get(location_id, []))

    def has_location(self, location_id):
        return location_id in self._entries

    def entry(self, location_id):
        return self._entries.get(location_id)

    def bfs_path(self, start, target, max_nodes=700):
        if start == target:
            return []
        if target not in self._entries:
            return None
        visited = {start}
        prev = {}
        queue = collections.deque([start])
        nodes_visited = 0
        while queue:
            cur = queue.popleft()
            nodes_visited += 1
            if nodes_visited > max_nodes:
                return None
            for nxt in self._adj.get(cur, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                prev[nxt] = cur
                if nxt == target:
                    path = []
                    node = target
                    while node is not None and node != start:
                        path.append(node)
                        node = prev.get(node)
                    path.reverse()
                    return path
                queue.append(nxt)
        return None


def build_known_graph_view(agent):
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations = knowledge.get("known_locations") or {}
    adj = {}
    for loc_id, entry in known_locations.items():
        if not isinstance(entry, dict):
            continue
        neighbors = []
        edges = entry.get("edges") if isinstance(entry.get("edges"), dict) else {}
        for target_id in (edges or {}):
            if str(target_id or ""):
                neighbors.append(str(target_id))
        snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
        for nbr in (snapshot or {}).get("known_neighbor_ids") or []:
            nbr_s = str(nbr or "")
            if nbr_s and nbr_s not in neighbors:
                neighbors.append(nbr_s)
        adj[loc_id] = neighbors
    return KnownGraphView(adj=adj, entries=dict(known_locations))


def is_location_known(agent, location_id):
    entry = get_known_location(agent, location_id)
    return isinstance(entry, dict) and bool(entry.get("known_exists"))


def is_location_visited(agent, location_id):
    entry = get_known_location(agent, location_id)
    return isinstance(entry, dict) and bool(entry.get("visited"))


def _get_known_locations_revision(agent):
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return 0
    stats = knowledge.get("stats") if isinstance(knowledge.get("stats"), dict) else {}
    return int((stats or {}).get("known_locations_revision", 0) or 0)


def find_known_path(agent, *, start_location_id, target_location_id, max_nodes=None):
    max_n = max_nodes if max_nodes is not None else 700
    cache_key = f"{start_location_id}->{target_location_id}"
    revision = _get_known_locations_revision(agent)
    cache = agent.get("known_graph_path_cache")
    if isinstance(cache, dict) and cache.get("revision") == revision:
        cached_paths = cache.get("paths") if isinstance(cache.get("paths"), dict) else {}
        if cache_key in cached_paths:
            cache["hits"] = int(cache.get("hits", 0)) + 1
            return cached_paths[cache_key]
    view = build_known_graph_view(agent)
    path = view.bfs_path(start_location_id, target_location_id, max_nodes=max_n)
    if not isinstance(cache, dict) or cache.get("revision") != revision:
        cache = {"revision": revision, "paths": {}, "created_turn": None, "hits": 0, "misses": 0}
        agent["known_graph_path_cache"] = cache
    paths = cache.setdefault("paths", {})
    if len(paths) >= MAX_PATH_CACHE_ENTRIES:
        evict_count = MAX_PATH_CACHE_ENTRIES // 2
        for k in list(paths.keys())[:evict_count]:
            paths.pop(k, None)
    paths[cache_key] = path
    cache["misses"] = int(cache.get("misses", 0)) + 1
    return path


def find_frontier_locations(agent, *, from_location_id, limit=10):
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations = knowledge.get("known_locations") or {}
    frontiers = []
    for loc_id, entry in known_locations.items():
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
        if level == LOCATION_KNOWLEDGE_VISITED:
            continue
        frontiers.append(entry)
    if not frontiers:
        return []
    view = build_known_graph_view(agent)
    dist = {from_location_id: 0}
    queue = collections.deque([from_location_id])
    nodes_visited = 0
    while queue and nodes_visited < FRONTIER_MAX_CANDIDATES * 4:
        cur = queue.popleft()
        nodes_visited += 1
        for nxt in view.neighbors(cur):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)
    scored = []
    for entry in frontiers:
        loc_id = str(entry.get("location_id") or "")
        hops = dist.get(loc_id)
        if hops is None:
            continue
        confidence = float(entry.get("confidence", 0.5) or 0.5)
        score = hops * 10 - confidence * 5
        scored.append((score, entry))
    scored.sort(key=lambda x: x[0])
    return [e for _, e in scored[:limit]]


_LEVEL_ORDER = {
    LOCATION_KNOWLEDGE_VISITED: 4,
    LOCATION_KNOWLEDGE_SNAPSHOT: 3,
    LOCATION_KNOWLEDGE_ROUTE_ONLY: 2,
    LOCATION_KNOWLEDGE_EXISTS: 1,
}


def known_locations_with_feature(agent, feature, *, min_confidence=0.4, include_stale=False, world_turn=0):
    if feature not in KNOWN_FEATURES:
        return []
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations = knowledge.get("known_locations") or {}
    result = []
    for loc_id, entry in known_locations.items():
        if not isinstance(entry, dict):
            continue
        confidence = float(entry.get("confidence", 0.0) or 0.0)
        if confidence < min_confidence:
            continue
        if not include_stale and world_turn > 0:
            stale_after = entry.get("stale_after_turn")
            if isinstance(stale_after, (int, float)) and world_turn > int(stale_after):
                continue
        snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
        found = False
        if feature == "has_shelter":
            found = bool((snapshot or {}).get("has_shelter") or entry.get("safe_shelter"))
        elif feature == "has_trader":
            found = bool((snapshot or {}).get("has_trader") or entry.get("has_trader"))
        elif feature == "has_exit":
            found = bool((snapshot or {}).get("has_exit") or entry.get("has_exit"))
        elif feature == "has_anomaly":
            anomaly_risk = (snapshot or {}).get("anomaly_risk_estimate")
            found = bool(
                (anomaly_risk is not None and float(anomaly_risk) > 0.0)
                or entry.get("anomaly_activity", 0)
            )
        elif feature == "has_artifacts":
            artifact_potential = (snapshot or {}).get("artifact_potential_estimate")
            found = bool(artifact_potential is not None and float(artifact_potential) > 0.0)
        if found:
            result.append(entry)
    result.sort(key=lambda e: (
        -float(e.get("confidence", 0.0) or 0.0),
        -_LEVEL_ORDER.get(str(e.get("knowledge_level") or ""), 0),
    ))
    return result


def get_nearest_known_location_with_feature(agent, feature, *, from_location_id, world_turn=0, min_confidence=0.4, max_candidates=10):
    candidates = known_locations_with_feature(agent, feature, min_confidence=min_confidence, world_turn=world_turn)[:max_candidates]
    if not candidates:
        return None
    view = build_known_graph_view(agent)
    best_loc = None
    best_hops = 10 ** 9
    for entry in candidates:
        loc_id = str(entry.get("location_id") or "")
        if not loc_id:
            continue
        if loc_id == from_location_id:
            return loc_id
        path = view.bfs_path(from_location_id, loc_id)
        if path is not None and len(path) < best_hops:
            best_hops = len(path)
            best_loc = loc_id
    return best_loc


def invalidate_known_path_cache(agent):
    cache = agent.get("known_graph_path_cache")
    if isinstance(cache, dict):
        cache["revision"] = -1


def get_known_path_cache_stats(agent):
    cache = agent.get("known_graph_path_cache")
    if not isinstance(cache, dict):
        return {"hits": 0, "misses": 0, "cached_paths": 0, "revision": 0}
    return {
        "hits": int(cache.get("hits", 0)),
        "misses": int(cache.get("misses", 0)),
        "cached_paths": len(cache.get("paths") or {}),
        "revision": cache.get("revision"),
    }


__all__ = [
    "KnownGraphView",
    "KNOWN_FEATURES",
    "FRONTIER_MAX_CANDIDATES",
    "TRADER_SEARCH_MAX_CANDIDATES",
    "SHELTER_SEARCH_MAX_CANDIDATES",
    "ARTIFACT_SEARCH_MAX_CANDIDATES",
    "build_known_graph_view",
    "is_location_known",
    "is_location_visited",
    "find_known_path",
    "find_frontier_locations",
    "known_locations_with_feature",
    "get_nearest_known_location_with_feature",
    "invalidate_known_path_cache",
    "get_known_path_cache_stats",
]
