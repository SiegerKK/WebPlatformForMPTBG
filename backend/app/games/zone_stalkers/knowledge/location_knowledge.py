from __future__ import annotations

from copy import deepcopy
from typing import Any

LOCATION_KNOWLEDGE_UNKNOWN = "unknown"
LOCATION_KNOWLEDGE_EXISTS = "known_exists"
LOCATION_KNOWLEDGE_ROUTE_ONLY = "known_route_only"
LOCATION_KNOWLEDGE_SNAPSHOT = "known_snapshot"
LOCATION_KNOWLEDGE_VISITED = "visited"

MAX_KNOWN_LOCATIONS_PER_AGENT = 700
MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT = 350
MAX_KNOWN_LOCATION_EDGES_PER_AGENT = 1800

SOURCE_PRIORITY: dict[str, int] = {
    "direct_visit": 100,
    "direct_neighbor_observation": 80,
    "trader_intel": 70,
    "shared_by_agent": 60,
    "witness_report": 50,
    "rumor": 30,
}

_DEFAULT_STALE_TURNS = 1440
_LEVEL_RANK: dict[str, int] = {
    LOCATION_KNOWLEDGE_UNKNOWN: 0,
    LOCATION_KNOWLEDGE_EXISTS: 1,
    LOCATION_KNOWLEDGE_ROUTE_ONLY: 2,
    LOCATION_KNOWLEDGE_SNAPSHOT: 3,
    LOCATION_KNOWLEDGE_VISITED: 4,
}


def ensure_location_knowledge_v1(agent: dict[str, Any]) -> dict[str, Any]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        knowledge = {
            "revision": 0,
            "major_revision": 0,
            "minor_revision": 0,
            "known_npcs": {},
            "known_corpses": {},
            "known_locations": {},
            "known_traders": {},
            "known_hazards": {},
            "hunt_evidence": {},
            "stats": {},
        }
        agent["knowledge_v1"] = knowledge

    for key in ("known_npcs", "known_corpses", "known_locations", "known_traders", "known_hazards", "hunt_evidence"):
        if not isinstance(knowledge.get(key), dict):
            knowledge[key] = {}

    stats = knowledge.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        knowledge["stats"] = stats

    stats.setdefault("known_npcs_count", len(knowledge.get("known_npcs") or {}))
    stats.setdefault("known_corpses_count", len(knowledge.get("known_corpses") or {}))
    stats.setdefault("hunt_evidence_targets_count", len(knowledge.get("hunt_evidence") or {}))
    stats.setdefault("known_locations_count", len(knowledge.get("known_locations") or {}))
    if "detailed_known_locations_count" not in stats:
        stats["detailed_known_locations_count"] = sum(
            1
            for entry in (knowledge.get("known_locations") or {}).values()
            if isinstance(entry, dict) and isinstance(entry.get("snapshot"), dict)
        )
    if "known_location_edges_count" not in stats:
        stats["known_location_edges_count"] = _edges_count(knowledge.get("known_locations") or {})
    stats.setdefault("known_locations_revision", 0)
    stats.setdefault("last_location_knowledge_update_turn", 0)
    stats.setdefault("last_update_turn", 0)
    stats.setdefault("last_major_update_turn", 0)
    stats.setdefault("last_minor_update_turn", 0)

    knowledge.setdefault("revision", 0)
    knowledge.setdefault("major_revision", int(knowledge.get("revision", 0) or 0))
    knowledge.setdefault("minor_revision", 0)
    return knowledge


def get_known_location(
    agent: dict[str, Any],
    location_id: str,
) -> dict[str, Any] | None:
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations = knowledge.get("known_locations") or {}
    entry = known_locations.get(location_id)
    return entry if isinstance(entry, dict) else None


def _normalize_level(level: str | None) -> str:
    normalized = str(level or LOCATION_KNOWLEDGE_EXISTS)
    return normalized if normalized in _LEVEL_RANK else LOCATION_KNOWLEDGE_EXISTS


def _level_rank(level: str | None) -> int:
    return _LEVEL_RANK.get(_normalize_level(level), 0)


def _source_priority(source: str | None) -> int:
    return SOURCE_PRIORITY.get(str(source or "rumor"), 0)


def _entry_has_priority_features(entry: dict[str, Any]) -> bool:
    snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
    return bool(
        snapshot.get("has_shelter")
        or snapshot.get("has_trader")
        or snapshot.get("has_exit")
        or entry.get("visited")
    )


def _edges_count(known_locations: dict[str, Any]) -> int:
    total = 0
    for entry in known_locations.values():
        if isinstance(entry, dict):
            edges = entry.get("edges")
            if isinstance(edges, dict):
                total += len(edges)
    return total


def _location_eviction_score(entry: dict[str, Any]) -> tuple[float, int, int, int]:
    confidence = float(entry.get("confidence", 0.0) or 0.0)
    observed_turn = int(entry.get("observed_turn", 0) or 0)
    level_rank = _level_rank(str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_UNKNOWN))
    source_pri = _source_priority(str(entry.get("source") or "rumor"))
    return confidence, observed_turn, level_rank, source_pri


def _enforce_location_knowledge_caps(knowledge: dict[str, Any], world_turn: int) -> None:
    known_locations: dict[str, Any] = knowledge.get("known_locations") or {}
    stats = knowledge.setdefault("stats", {})
    detailed_count = int(stats.get("detailed_known_locations_count", 0) or 0)
    edges_count = int(stats.get("known_location_edges_count", 0) or 0)

    if detailed_count > MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT:
        detailed = [
            (loc_id, entry)
            for loc_id, entry in known_locations.items()
            if isinstance(entry, dict) and isinstance(entry.get("snapshot"), dict)
        ]
        if len(detailed) > MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT:
            drop_snapshot_candidates = [
                (loc_id, entry)
                for loc_id, entry in detailed
                if not entry.get("visited") and not _entry_has_priority_features(entry)
            ]
            drop_snapshot_candidates.sort(key=lambda row: _location_eviction_score(row[1]))
            to_compact = len(detailed) - MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT
            for loc_id, _ in drop_snapshot_candidates[:to_compact]:
                cur = known_locations.get(loc_id)
                if isinstance(cur, dict):
                    cur.pop("snapshot", None)
                    if _normalize_level(str(cur.get("knowledge_level") or "")) == LOCATION_KNOWLEDGE_SNAPSHOT:
                        cur["knowledge_level"] = LOCATION_KNOWLEDGE_EXISTS

    if len(known_locations) > MAX_KNOWN_LOCATIONS_PER_AGENT:
        removable = [
            (loc_id, entry)
            for loc_id, entry in known_locations.items()
            if isinstance(entry, dict)
            and not entry.get("visited")
            and not _entry_has_priority_features(entry)
        ]
        removable.sort(key=lambda row: _location_eviction_score(row[1]))
        to_remove = len(known_locations) - MAX_KNOWN_LOCATIONS_PER_AGENT
        for loc_id, _ in removable[:to_remove]:
            known_locations.pop(loc_id, None)

    if edges_count > MAX_KNOWN_LOCATION_EDGES_PER_AGENT:
        edge_candidates: list[tuple[str, str, tuple[float, int, int, int]]] = []
        for loc_id, entry in known_locations.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("visited"):
                continue
            edges = entry.get("edges")
            if not isinstance(edges, dict):
                continue
            for target_id, edge in edges.items():
                if not isinstance(edge, dict):
                    continue
                edge_candidates.append(
                    (
                        loc_id,
                        target_id,
                        (
                            float(edge.get("confidence", 0.0) or 0.0),
                            int(edge.get("observed_turn", 0) or 0),
                            0 if bool(edge.get("confirmed")) else 1,
                            _source_priority(str(edge.get("source") or "rumor")),
                        ),
                    )
                )

        edge_candidates.sort(key=lambda row: row[2])
        overflow = edges_count - MAX_KNOWN_LOCATION_EDGES_PER_AGENT
        for loc_id, target_id, _ in edge_candidates[:overflow]:
            entry = known_locations.get(loc_id)
            if not isinstance(entry, dict):
                continue
            edges = entry.get("edges")
            if isinstance(edges, dict):
                edges.pop(target_id, None)

    stats["known_locations_count"] = len(known_locations)
    stats["detailed_known_locations_count"] = sum(
        1
        for entry in known_locations.values()
        if isinstance(entry, dict) and isinstance(entry.get("snapshot"), dict)
    )
    stats["known_location_edges_count"] = _edges_count(known_locations)
    stats["last_location_knowledge_update_turn"] = world_turn


def _touch_location_revision(knowledge: dict[str, Any], world_turn: int) -> None:
    stats = knowledge.setdefault("stats", {})
    knowledge["major_revision"] = int(knowledge.get("major_revision", 0) or 0)
    knowledge["minor_revision"] = int(knowledge.get("minor_revision", 0) or 0) + 1
    knowledge["revision"] = knowledge["major_revision"] + knowledge["minor_revision"]

    stats["known_locations_revision"] = int(stats.get("known_locations_revision", 0) or 0) + 1
    stats["last_location_knowledge_update_turn"] = world_turn
    stats["last_update_turn"] = world_turn
    stats["last_minor_update_turn"] = world_turn


def _should_accept_meta_update(existing: dict[str, Any], *, world_turn: int, source: str, confidence: float) -> bool:
    existing_priority = _source_priority(str(existing.get("source") or "rumor"))
    incoming_priority = _source_priority(source)
    if incoming_priority > existing_priority:
        return True

    existing_conf = float(existing.get("confidence", 0.0) or 0.0)
    existing_observed = int(existing.get("observed_turn", 0) or 0)
    if world_turn > existing_observed and confidence >= max(0.0, existing_conf - 0.15):
        return True
    if confidence > existing_conf:
        return True
    return False


def upsert_known_location(
    agent: dict[str, Any],
    *,
    location_id: str,
    world_turn: int,
    knowledge_level: str,
    source: str,
    confidence: float,
    source_agent_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
    edges: dict[str, Any] | None = None,
    observed_turn: int | None = None,
    received_turn: int | None = None,
) -> dict[str, Any]:
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations: dict[str, Any] = knowledge["known_locations"]

    normalized_level = _normalize_level(knowledge_level)
    obs_turn = int(world_turn if observed_turn is None else observed_turn)
    recv_turn = int(world_turn if received_turn is None else received_turn)

    existing = known_locations.get(location_id)
    created = not isinstance(existing, dict)
    prev_edges_count = 0
    prev_is_detailed = 0
    if isinstance(existing, dict):
        prev_edges_count = len(existing.get("edges")) if isinstance(existing.get("edges"), dict) else 0
        prev_is_detailed = 1 if isinstance(existing.get("snapshot"), dict) else 0
    changed = False

    if created:
        existing = {
            "location_id": location_id,
            "knowledge_level": normalized_level,
            "known_exists": True,
            "visited": normalized_level == LOCATION_KNOWLEDGE_VISITED,
            "visit_count": 1 if normalized_level == LOCATION_KNOWLEDGE_VISITED else 0,
            "first_known_turn": obs_turn,
            "last_confirmed_turn": obs_turn,
            "last_visited_turn": obs_turn if normalized_level == LOCATION_KNOWLEDGE_VISITED else None,
            "observed_turn": obs_turn,
            "received_turn": recv_turn,
            "source": source,
            "source_agent_id": source_agent_id,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "stale_after_turn": obs_turn + _DEFAULT_STALE_TURNS,
            "snapshot": deepcopy(snapshot) if isinstance(snapshot, dict) else None,
            "edges": deepcopy(edges) if isinstance(edges, dict) else {},
            "stats": {
                "times_shared_out": 0,
                "times_received": 0,
                "last_used_for_path_turn": None,
            },
        }
        if existing.get("snapshot") is None:
            existing.pop("snapshot", None)
        known_locations[location_id] = existing
        changed = True
    else:
        assert isinstance(existing, dict)
        old_level = _normalize_level(str(existing.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS))
        old_rank = _level_rank(old_level)
        new_rank = _level_rank(normalized_level)

        existing["known_exists"] = True
        if existing.get("first_known_turn") is None:
            existing["first_known_turn"] = obs_turn
            changed = True

        if new_rank > old_rank:
            existing["knowledge_level"] = normalized_level
            changed = True

        if normalized_level == LOCATION_KNOWLEDGE_VISITED or source == "direct_visit":
            existing["knowledge_level"] = LOCATION_KNOWLEDGE_VISITED
            existing["visited"] = True
            existing["visit_count"] = int(existing.get("visit_count", 0) or 0) + 1
            existing["last_visited_turn"] = obs_turn
            existing["last_confirmed_turn"] = obs_turn
            changed = True
        elif existing.get("visited"):
            existing["knowledge_level"] = LOCATION_KNOWLEDGE_VISITED

        if _should_accept_meta_update(existing, world_turn=obs_turn, source=source, confidence=float(confidence)):
            existing["source"] = source
            existing["source_agent_id"] = source_agent_id
            existing["observed_turn"] = max(int(existing.get("observed_turn", 0) or 0), obs_turn)
            existing["received_turn"] = max(int(existing.get("received_turn", 0) or 0), recv_turn)
            existing["confidence"] = max(float(existing.get("confidence", 0.0) or 0.0), float(confidence))
            existing["stale_after_turn"] = max(
                int(existing.get("stale_after_turn", 0) or 0),
                int(obs_turn + _DEFAULT_STALE_TURNS),
            )
            changed = True

        if isinstance(snapshot, dict):
            if source == "direct_visit" or _level_rank(str(existing.get("knowledge_level") or "")) >= _level_rank(LOCATION_KNOWLEDGE_SNAPSHOT):
                existing["snapshot"] = deepcopy(snapshot)
                if existing.get("knowledge_level") != LOCATION_KNOWLEDGE_VISITED:
                    existing["knowledge_level"] = max(
                        (existing.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS),
                        LOCATION_KNOWLEDGE_SNAPSHOT,
                        key=lambda lvl: _level_rank(str(lvl)),
                    )
                changed = True

        if isinstance(edges, dict):
            existing_edges = existing.get("edges")
            if not isinstance(existing_edges, dict):
                existing_edges = {}
                existing["edges"] = existing_edges
            for target_id, incoming in edges.items():
                if not isinstance(incoming, dict):
                    continue
                stored = existing_edges.get(target_id)
                if not isinstance(stored, dict):
                    existing_edges[target_id] = deepcopy(incoming)
                    changed = True
                    continue
                incoming_priority = _source_priority(str(incoming.get("source") or source))
                stored_priority = _source_priority(str(stored.get("source") or "rumor"))
                incoming_turn = int(incoming.get("observed_turn", obs_turn) or obs_turn)
                stored_turn = int(stored.get("observed_turn", 0) or 0)
                incoming_conf = float(incoming.get("confidence", confidence) or confidence)
                stored_conf = float(stored.get("confidence", 0.0) or 0.0)
                if incoming_priority > stored_priority or (
                    incoming_turn > stored_turn and incoming_conf >= max(0.0, stored_conf - 0.15)
                ):
                    existing_edges[target_id] = deepcopy(incoming)
                    changed = True

    assert isinstance(existing, dict)
    stats = existing.get("stats")
    if not isinstance(stats, dict):
        existing["stats"] = {
            "times_shared_out": 0,
            "times_received": 0,
            "last_used_for_path_turn": None,
        }
        changed = True

    knowledge_stats = knowledge.setdefault("stats", {})
    if created:
        knowledge_stats["known_locations_count"] = int(knowledge_stats.get("known_locations_count", 0) or 0) + 1
    new_edges_count = len(existing.get("edges")) if isinstance(existing.get("edges"), dict) else 0
    new_is_detailed = 1 if isinstance(existing.get("snapshot"), dict) else 0
    knowledge_stats["known_location_edges_count"] = int(knowledge_stats.get("known_location_edges_count", 0) or 0) + (
        new_edges_count - prev_edges_count
    )
    knowledge_stats["detailed_known_locations_count"] = int(
        knowledge_stats.get("detailed_known_locations_count", 0) or 0
    ) + (new_is_detailed - prev_is_detailed)
    if (
        len(known_locations) > MAX_KNOWN_LOCATIONS_PER_AGENT
        or int(knowledge_stats.get("detailed_known_locations_count", 0) or 0) > MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT
        or int(knowledge_stats.get("known_location_edges_count", 0) or 0) > MAX_KNOWN_LOCATION_EDGES_PER_AGENT
    ):
        _enforce_location_knowledge_caps(knowledge, world_turn)
    if changed:
        _touch_location_revision(knowledge, world_turn)
        _update_location_indexes_incremental(knowledge, location_id, existing)
    return existing


def build_location_knowledge_snapshot(
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    location = locations.get(location_id) if isinstance(locations, dict) else None
    if not isinstance(location, dict):
        return {
            "name": location_id,
            "location_type": None,
            "terrain_type": None,
            "danger_level_estimate": 0.0,
            "has_trader": False,
            "known_trader_id": None,
            "has_shelter": False,
            "has_exit": False,
            "artifact_potential_estimate": 0.0,
            "anomaly_risk_estimate": 0.0,
            "last_artifact_seen_turn": None,
            "last_searched_turn": None,
            "search_exhausted_until": None,
            "known_neighbor_ids": [],
        }

    anomaly_activity = int(location.get("anomaly_activity", 0) or 0)
    connections = location.get("connections") if isinstance(location.get("connections"), list) else []
    known_neighbor_ids = [
        str(conn.get("to") or "")
        for conn in connections
        if isinstance(conn, dict) and str(conn.get("to") or "")
    ]

    traders = state.get("traders") if isinstance(state.get("traders"), dict) else {}
    known_trader_id = None
    for trader_id, trader in (traders or {}).items():
        if isinstance(trader, dict) and str(trader.get("location_id") or "") == location_id:
            known_trader_id = str(trader.get("id") or trader.get("agent_id") or trader_id)
            break

    terrain_type = location.get("terrain_type")
    indoor_terrain = {
        "buildings",
        "military_buildings",
        "hamlet",
        "field_camp",
        "dungeon",
        "x_lab",
        "scientific_bunker",
        "tunnel",
    }
    has_shelter = bool(location.get("safe_shelter") or (terrain_type in indoor_terrain))
    has_exit = bool(location.get("has_exit") or location.get("is_exit") or str(location.get("kind") or "") == "exit")

    artifacts = location.get("artifacts") if isinstance(location.get("artifacts"), list) else []
    artifact_count = len(artifacts)
    artifact_potential = max(0.0, min(1.0, (artifact_count / 5.0) + (anomaly_activity / 20.0)))
    anomaly_risk = max(0.0, min(1.0, anomaly_activity / 10.0))

    return {
        "name": str(location.get("name") or location_id),
        "location_type": terrain_type,
        "terrain_type": terrain_type,
        "danger_level_estimate": round(anomaly_risk, 3),
        "has_trader": bool(known_trader_id),
        "known_trader_id": known_trader_id,
        "has_shelter": has_shelter,
        "has_exit": has_exit,
        "artifact_potential_estimate": round(artifact_potential, 3),
        "anomaly_risk_estimate": round(anomaly_risk, 3),
        "last_artifact_seen_turn": world_turn if artifact_count > 0 else None,
        "last_searched_turn": location.get("last_searched_turn"),
        "search_exhausted_until": location.get("search_exhausted_until"),
        "known_neighbor_ids": known_neighbor_ids,
    }


def _direct_neighbor_edges_for_location(
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    location = locations.get(location_id) if isinstance(locations, dict) else None
    if not isinstance(location, dict):
        return {}

    connections = location.get("connections") if isinstance(location.get("connections"), list) else []
    edges: dict[str, Any] = {}
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        target_id = str(conn.get("to") or "")
        if not target_id:
            continue
        travel_cost = conn.get("travel_time")
        if travel_cost is None:
            travel_cost = 1
        edges[target_id] = {
            "target_location_id": target_id,
            "known_exists": True,
            "confirmed": True,
            "source": "direct_visit_neighbor",
            "observed_turn": world_turn,
            "confidence": 0.95,
            "travel_cost_estimate": travel_cost,
        }
    return edges


def mark_location_visited(
    agent: dict[str, Any],
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    snapshot = build_location_knowledge_snapshot(state=state, location_id=location_id, world_turn=world_turn)
    edges = _direct_neighbor_edges_for_location(state=state, location_id=location_id, world_turn=world_turn)
    return upsert_known_location(
        agent,
        location_id=location_id,
        world_turn=world_turn,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        source_agent_id=None,
        snapshot=snapshot,
        edges=edges,
        observed_turn=world_turn,
        received_turn=world_turn,
    )


def mark_neighbor_locations_known(
    agent: dict[str, Any],
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    location = locations.get(location_id) if isinstance(locations, dict) else None
    if not isinstance(location, dict):
        return result

    connections = location.get("connections") if isinstance(location.get("connections"), list) else []
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        target_id = str(conn.get("to") or "")
        if not target_id:
            continue
        entry = upsert_known_location(
            agent,
            location_id=target_id,
            world_turn=world_turn,
            knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
            source="direct_neighbor_observation",
            confidence=0.85,
            source_agent_id=None,
            snapshot=None,
            edges=None,
            observed_turn=world_turn,
            received_turn=world_turn,
        )
        result.append(entry)

    return result


def get_known_neighbor_ids(
    agent: dict[str, Any],
    location_id: str,
) -> tuple[str, ...]:
    entry = get_known_location(agent, location_id)
    if not isinstance(entry, dict):
        return ()

    ids: set[str] = set()
    snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
    neighbors = snapshot.get("known_neighbor_ids") if isinstance(snapshot, dict) else None
    if isinstance(neighbors, list):
        for loc_id in neighbors:
            loc = str(loc_id or "")
            if loc:
                ids.add(loc)

    edges = entry.get("edges") if isinstance(entry.get("edges"), dict) else {}
    for target_id in edges:
        loc = str(target_id or "")
        if loc:
            ids.add(loc)

    return tuple(sorted(ids))


def summarize_location_knowledge(agent: dict[str, Any]) -> dict[str, Any]:
    knowledge = ensure_location_knowledge_v1(agent)
    known_locations: dict[str, Any] = knowledge.get("known_locations") or {}
    stats = knowledge.get("stats") if isinstance(knowledge.get("stats"), dict) else {}

    visited_count = 0
    snapshot_count = 0
    exists_count = 0
    route_only_count = 0
    stale_count = 0

    now_turn = int(stats.get("last_update_turn", 0) or 0)

    for entry in known_locations.values():
        if not isinstance(entry, dict):
            continue
        level = _normalize_level(str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS))
        if level == LOCATION_KNOWLEDGE_VISITED:
            visited_count += 1
        elif level == LOCATION_KNOWLEDGE_SNAPSHOT:
            snapshot_count += 1
        elif level == LOCATION_KNOWLEDGE_ROUTE_ONLY:
            route_only_count += 1
        else:
            exists_count += 1

        stale_after_turn = entry.get("stale_after_turn")
        if stale_after_turn is not None and now_turn >= int(stale_after_turn or 0):
            stale_count += 1

    return {
        "known_locations_count": len(known_locations),
        "visited_locations_count": visited_count,
        "known_snapshot_count": snapshot_count,
        "known_exists_count": exists_count,
        "known_route_only_count": route_only_count,
        "stale_locations_count": stale_count,
        "known_locations_revision": int(stats.get("known_locations_revision", 0) or 0),
        "last_location_knowledge_update_turn": int(stats.get("last_location_knowledge_update_turn", 0) or 0),
    }



# ── Indexes ───────────────────────────────────────────────────────────────────

def ensure_location_indexes(knowledge: dict) -> dict:
    """Return (and initialise if missing) the location_indexes sub-dict."""
    indexes = knowledge.get("location_indexes")
    if not isinstance(indexes, dict):
        indexes = {
            "revision": -1,
            "visited_ids": [],
            "frontier_ids": [],
            "known_trader_location_ids": [],
            "known_shelter_location_ids": [],
            "known_exit_location_ids": [],
            "known_anomaly_location_ids": [],
            "recently_updated_ids": [],
        }
        knowledge["location_indexes"] = indexes
    return indexes


def _update_location_indexes_incremental(
    knowledge: dict,
    location_id: str,
    entry: dict,
) -> None:
    """Update location_indexes incrementally for a single upserted entry.

    Called after every upsert_known_location so indexes stay current without
    scanning the full known_locations table.

    Uses a per-call transient set cache for O(1) membership checks on each
    index list.  The sets are built once from the current list contents and
    kept in sync with list mutations for the duration of this call.  They are
    NOT stored in the knowledge dict (not persisted to JSON).
    """
    indexes = ensure_location_indexes(knowledge)
    level = str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
    visited = bool(entry.get("visited") or level == LOCATION_KNOWLEDGE_VISITED)
    snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}

    # Build a transient set cache so membership tests below are O(1) instead
    # of O(N).  Removal from the *list* is still O(N) (unavoidable for plain
    # lists), but eliminating the redundant membership pre-check halves the
    # list scan work for the "remove" path.
    #
    # We determine which index lists will be touched based on entry properties:
    # - visited_ids, frontier_ids, recently_updated_ids are always touched.
    # - Feature index sets are built only when the feature flag is True (add path).
    #   For the remove path (feature=False), we call list.remove() directly —
    #   these lists are small (typically ≤100 entries) so O(K) is negligible,
    #   and most entries will not be in the list at all (ValueError → no-op).
    has_trader = bool((snapshot or {}).get("has_trader") or entry.get("has_trader"))
    has_shelter = bool((snapshot or {}).get("has_shelter") or entry.get("safe_shelter"))
    has_exit = bool((snapshot or {}).get("has_exit") or entry.get("has_exit"))
    anomaly_risk = (snapshot or {}).get("anomaly_risk_estimate")
    has_anomaly = bool(
        (anomaly_risk is not None and float(anomaly_risk) > 0.0)
        or int(entry.get("anomaly_activity", 0) or 0) > 0
    )

    # Always-touched indexes (build sets unconditionally)
    _needed_always = ("visited_ids", "frontier_ids", "recently_updated_ids")
    _sets: dict[str, set[str]] = {
        name: set(indexes[name]) if isinstance(indexes.get(name), list) else set()
        for name in _needed_always
    }

    # Feature index sets: build only when the feature is True so that most
    # common-case upserts (no features) avoid building 4 extra sets.
    _feature_flags = {
        "known_trader_location_ids": has_trader,
        "known_shelter_location_ids": has_shelter,
        "known_exit_location_ids": has_exit,
        "known_anomaly_location_ids": has_anomaly,
    }
    for _fname, _flag in _feature_flags.items():
        if _flag:
            lst = indexes.get(_fname)
            _sets[_fname] = set(lst) if isinstance(lst, list) else set()

    def _idx_add(name: str, lid: str) -> None:
        if lid not in _sets[name]:
            _sets[name].add(lid)
            indexes[name].append(lid)

    def _idx_remove_via_set(name: str, lid: str) -> None:
        """Remove using set cache — used for always-built indexes."""
        if lid in _sets[name]:
            _sets[name].discard(lid)
            try:
                indexes[name].remove(lid)
            except ValueError:
                # Defensive: set and list fell out of sync (e.g., after JSON
                # round-trip that lost the transient cache).  Log-less no-op.
                pass

    def _idx_remove_direct(name: str, lid: str) -> None:
        """Remove by direct list.remove() — used when set cache was not built."""
        try:
            indexes[name].remove(lid)
        except ValueError:
            pass  # Entry not in index — expected for most feature=False upserts.

    # visited_ids
    if visited:
        _idx_add("visited_ids", location_id)
    else:
        _idx_remove_via_set("visited_ids", location_id)

    # frontier_ids: known but not visited
    if not visited and entry.get("known_exists"):
        _idx_add("frontier_ids", location_id)
    else:
        _idx_remove_via_set("frontier_ids", location_id)

    # known_trader_location_ids
    if has_trader:
        _idx_add("known_trader_location_ids", location_id)
    else:
        _idx_remove_direct("known_trader_location_ids", location_id)

    # known_shelter_location_ids
    if has_shelter:
        _idx_add("known_shelter_location_ids", location_id)
    else:
        _idx_remove_direct("known_shelter_location_ids", location_id)

    # known_exit_location_ids
    if has_exit:
        _idx_add("known_exit_location_ids", location_id)
    else:
        _idx_remove_direct("known_exit_location_ids", location_id)

    # known_anomaly_location_ids
    if has_anomaly:
        _idx_add("known_anomaly_location_ids", location_id)
    else:
        _idx_remove_direct("known_anomaly_location_ids", location_id)

    # recently_updated_ids: keep last 50 (bounded to 50).  Use set cache for
    # O(1) membership check; list.remove() on a 50-entry list is O(50) ≈ O(1).
    ru = indexes["recently_updated_ids"]
    ru_set = _sets["recently_updated_ids"]
    if location_id in ru_set:
        ru_set.discard(location_id)
        try:
            ru.remove(location_id)
        except ValueError:
            pass
    ru_set.add(location_id)
    ru.insert(0, location_id)
    if len(ru) > 50:
        # Trim excess entries; also drop them from the set to keep in sync
        for _evicted in ru[50:]:
            ru_set.discard(_evicted)
        indexes["recently_updated_ids"] = ru[:50]

    indexes["revision"] = int(knowledge.get("stats", {}).get("known_locations_revision", 0) or 0)


def get_location_indexes(agent: dict) -> dict:
    """Return the location_indexes dict for *agent* (read-only view).

    If indexes are stale (revision mismatch), rebuilds them from scratch.
    """
    knowledge = ensure_location_knowledge_v1(agent)
    indexes = ensure_location_indexes(knowledge)
    stats = knowledge.get("stats") if isinstance(knowledge.get("stats"), dict) else {}
    current_rev = int((stats or {}).get("known_locations_revision", 0) or 0)
    if indexes.get("revision") == current_rev:
        return indexes
    # Rebuild from scratch (only happens once per revision change)
    _rebuild_location_indexes(knowledge)
    return knowledge["location_indexes"]


def _rebuild_location_indexes(knowledge: dict) -> None:
    """Full rebuild of location_indexes from known_locations.  O(N) but rare."""
    known_locations = knowledge.get("known_locations") or {}
    indexes: dict = {
        "revision": -1,
        "visited_ids": [],
        "frontier_ids": [],
        "known_trader_location_ids": [],
        "known_shelter_location_ids": [],
        "known_exit_location_ids": [],
        "known_anomaly_location_ids": [],
        "recently_updated_ids": [],
    }
    for loc_id, entry in known_locations.items():
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
        visited = bool(entry.get("visited") or level == LOCATION_KNOWLEDGE_VISITED)
        snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
        if visited:
            indexes["visited_ids"].append(loc_id)
        elif entry.get("known_exists"):
            indexes["frontier_ids"].append(loc_id)
        if (snapshot or {}).get("has_trader") or entry.get("has_trader"):
            indexes["known_trader_location_ids"].append(loc_id)
        if (snapshot or {}).get("has_shelter") or entry.get("safe_shelter"):
            indexes["known_shelter_location_ids"].append(loc_id)
        if (snapshot or {}).get("has_exit") or entry.get("has_exit"):
            indexes["known_exit_location_ids"].append(loc_id)
        anomaly_risk = (snapshot or {}).get("anomaly_risk_estimate")
        if (anomaly_risk is not None and float(anomaly_risk) > 0.0) or int(entry.get("anomaly_activity", 0) or 0) > 0:
            indexes["known_anomaly_location_ids"].append(loc_id)

    stats = knowledge.get("stats") if isinstance(knowledge.get("stats"), dict) else {}
    indexes["revision"] = int((stats or {}).get("known_locations_revision", 0) or 0)
    knowledge["location_indexes"] = indexes


def build_location_knowledge_debug_summary(agent: dict) -> dict:
    """Return a compact debug projection of agent location knowledge.

    Safe to send to frontend / debug endpoints: does NOT include full table.
    """
    knowledge = ensure_location_knowledge_v1(agent)
    indexes = get_location_indexes(agent)
    stats = knowledge.get("stats") if isinstance(knowledge.get("stats"), dict) else {}
    path_cache = agent.get("known_graph_path_cache") if isinstance(agent.get("known_graph_path_cache"), dict) else {}
    exchange_stats = (stats or {}).get("exchange") if isinstance((stats or {}).get("exchange"), dict) else {}

    return {
        "known_locations": int((stats or {}).get("known_locations_count", 0) or len(knowledge.get("known_locations") or {})),
        "visited_locations": len(indexes.get("visited_ids") or []),
        "frontiers": len(indexes.get("frontier_ids") or []),
        "known_traders": len(indexes.get("known_trader_location_ids") or []),
        "known_shelters": len(indexes.get("known_shelter_location_ids") or []),
        "known_exits": len(indexes.get("known_exit_location_ids") or []),
        "known_anomalies": len(indexes.get("known_anomaly_location_ids") or []),
        "stale_locations": _count_stale_locations(knowledge, stats),
        "last_update_turn": int((stats or {}).get("last_location_knowledge_update_turn", 0) or 0),
        "revision": int((stats or {}).get("known_locations_revision", 0) or 0),
        "path_cache_hits": int((path_cache or {}).get("hits", 0)),
        "path_cache_misses": int((path_cache or {}).get("misses", 0)),
        "path_cache_entries": len((path_cache or {}).get("paths") or {}),
        "exchange_received_count": int((exchange_stats or {}).get("received_count", 0)),
        "exchange_sent_count": int((exchange_stats or {}).get("sent_count", 0)),
    }


def _count_stale_locations(knowledge: dict, stats: dict) -> int:
    now_turn = int((stats or {}).get("last_update_turn", 0) or 0)
    if now_turn == 0:
        return 0
    known_locations = knowledge.get("known_locations") or {}
    count = 0
    for entry in known_locations.values():
        if not isinstance(entry, dict):
            continue
        stale_after = entry.get("stale_after_turn")
        if isinstance(stale_after, (int, float)) and now_turn >= int(stale_after):
            count += 1
    return count

__all__ = [
    "LOCATION_KNOWLEDGE_UNKNOWN",
    "LOCATION_KNOWLEDGE_EXISTS",
    "LOCATION_KNOWLEDGE_ROUTE_ONLY",
    "LOCATION_KNOWLEDGE_SNAPSHOT",
    "LOCATION_KNOWLEDGE_VISITED",
    "MAX_KNOWN_LOCATIONS_PER_AGENT",
    "MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT",
    "MAX_KNOWN_LOCATION_EDGES_PER_AGENT",
    "SOURCE_PRIORITY",
    "ensure_location_knowledge_v1",
    "get_known_location",
    "upsert_known_location",
    "build_location_knowledge_snapshot",
    "mark_location_visited",
    "mark_neighbor_locations_known",
    "get_known_neighbor_ids",
    "summarize_location_knowledge",
    "ensure_location_indexes",
    "get_location_indexes",
    "build_location_knowledge_debug_summary",
]
