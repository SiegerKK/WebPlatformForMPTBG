"""location_knowledge_exchange — budgeted top-K location knowledge sharing between NPCs.

PR3: NPCs can share and trade location knowledge without copying whole maps or
writing high-volume memory records.  Exchange is strictly bounded to at most
MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION entries per interaction.
"""
from __future__ import annotations

import itertools
from typing import Any

from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_EXISTS,
    LOCATION_KNOWLEDGE_ROUTE_ONLY,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_VISITED,
    ensure_location_knowledge_v1,
    upsert_known_location,
    get_location_indexes,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION: int = 5
MAX_LOCATION_EDGES_SHARED_PER_LOCATION: int = 4
LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER: float = 0.75
LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER: float = 0.55

# Multiplier for candidate pool size relative to max_entries.  The pool must
# be large enough to cover all feature indexes plus visited + recently-updated
# candidates, yet small enough to keep scoring O(K) not O(N).
_SHARE_CANDIDATE_POOL_MULTIPLIER: int = 12
# Absolute minimum pool size when max_entries is very small (e.g., 1–2).
_SHARE_CANDIDATE_POOL_MIN: int = 60

_SHAREABLE_LEVELS = frozenset({
    LOCATION_KNOWLEDGE_VISITED,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_ROUTE_ONLY,
    LOCATION_KNOWLEDGE_EXISTS,
})

# Priorities for feature-based selection
_FEATURE_PRIORITY = {
    "has_shelter": 4,
    "has_exit": 4,
    "has_trader": 3,
    "has_anomaly": 2,
    "has_artifacts": 2,
}


# ── Share packet helpers ──────────────────────────────────────────────────────

def build_share_packet(
    entry: dict[str, Any],
    *,
    source_agent_id: str,
    world_turn: int,
) -> dict[str, Any]:
    """Build a compact share packet from a known_locations entry.

    The packet carries provenance and reduced confidence so the receiver
    knows this is hearsay, not a direct observation.
    """
    loc_id = str(entry.get("location_id") or "")
    level = str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
    observed_turn = int(entry.get("observed_turn") or entry.get("last_visited_turn") or 0)
    orig_confidence = float(entry.get("confidence", 0.5) or 0.5)

    if level == LOCATION_KNOWLEDGE_VISITED:
        # Shared direct-visit knowledge → downgraded to hearsay snapshot
        shared_level = LOCATION_KNOWLEDGE_SNAPSHOT
        multiplier = LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER
    elif level == LOCATION_KNOWLEDGE_SNAPSHOT:
        shared_level = LOCATION_KNOWLEDGE_SNAPSHOT
        multiplier = LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER
    elif level == LOCATION_KNOWLEDGE_ROUTE_ONLY:
        shared_level = LOCATION_KNOWLEDGE_ROUTE_ONLY
        multiplier = LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER
    else:
        shared_level = LOCATION_KNOWLEDGE_EXISTS
        multiplier = LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER

    shared_confidence = round(orig_confidence * multiplier, 3)

    # Build compact snapshot (only stable/gameplay-relevant fields)
    orig_snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
    shared_snapshot: dict[str, Any] | None = None
    if orig_snapshot and level in {LOCATION_KNOWLEDGE_VISITED, LOCATION_KNOWLEDGE_SNAPSHOT}:
        shared_snapshot = {
            k: v for k, v in (orig_snapshot or {}).items()
            if k in {
                "name",
                "location_type",
                "danger_level_estimate",
                "has_trader",
                "known_trader_id",
                "has_shelter",
                "has_exit",
                "artifact_potential_estimate",
                "anomaly_risk_estimate",
            }
        }
        # Share only a bounded set of known neighbors (no edge travel cost)
        known_neighbors = list((orig_snapshot or {}).get("known_neighbor_ids") or [])
        if known_neighbors:
            shared_snapshot["known_neighbor_ids"] = known_neighbors[:MAX_LOCATION_EDGES_SHARED_PER_LOCATION]

    # Build compact edges (bounded)
    orig_edges = entry.get("edges") if isinstance(entry.get("edges"), dict) else {}
    shared_edges: dict[str, Any] | None = None
    if orig_edges:
        shared_edges = {}
        for i, (target_id, edge) in enumerate(orig_edges.items()):
            if i >= MAX_LOCATION_EDGES_SHARED_PER_LOCATION:
                break
            if not isinstance(edge, dict):
                continue
            shared_edges[target_id] = {
                "target_location_id": target_id,
                "known_exists": True,
                "confirmed": False,
                "source": "shared_route_fragment",
                "confidence": round(float(edge.get("confidence", 0.5) or 0.5) * multiplier, 3),
                "observed_turn": observed_turn,
            }

    return {
        "location_id": loc_id,
        "knowledge_level": shared_level,
        "source": "shared_by_agent",
        "source_agent_id": source_agent_id,
        "observed_turn": observed_turn,
        "received_turn": world_turn,
        "confidence": shared_confidence,
        "snapshot": shared_snapshot,
        "edges": shared_edges,
    }


def build_trader_intel_packet(
    source_entry: dict[str, Any],
    *,
    trader_id: str,
    intel_type: str,
    world_turn: int,
) -> dict[str, Any]:
    """Build a compact trader intel packet.

    Similar to share_packet but sourced from a trader.
    *intel_type*: shelter | trader | exit | anomaly | route_fragment
    """
    loc_id = str(source_entry.get("location_id") or "")
    observed_turn = int(source_entry.get("observed_turn") or source_entry.get("last_visited_turn") or 0)
    orig_confidence = float(source_entry.get("confidence", 0.7) or 0.7)
    multiplier = LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER

    orig_snapshot = source_entry.get("snapshot") if isinstance(source_entry.get("snapshot"), dict) else {}
    shared_snapshot: dict[str, Any] | None = None
    if orig_snapshot:
        shared_snapshot = {
            k: v for k, v in (orig_snapshot or {}).items()
            if k in {"name", "has_trader", "has_shelter", "has_exit", "anomaly_risk_estimate", "artifact_potential_estimate"}
        }

    return {
        "location_id": loc_id,
        "knowledge_level": LOCATION_KNOWLEDGE_SNAPSHOT,
        "source": "trader_intel",
        "source_agent_id": trader_id,
        "intel_type": intel_type,
        "observed_turn": observed_turn,
        "received_turn": world_turn,
        "confidence": round(orig_confidence * multiplier, 3),
        "snapshot": shared_snapshot,
        "edges": None,
    }


# ── Selection policy ──────────────────────────────────────────────────────────

def select_location_knowledge_to_share(
    source_agent: dict[str, Any],
    *,
    target_needs_shelter: bool = False,
    target_needs_trader: bool = False,
    target_needs_exit: bool = False,
    target_needs_artifacts: bool = False,
    target_is_hunter: bool = False,
    max_entries: int = MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION,
    world_turn: int = 0,
) -> list[dict[str, Any]]:
    """Choose up to *max_entries* location knowledge entries to share.

    Selection priority:
    1. shelters (if target needs shelter or emission risk)
    2. traders (if target has survival/economic needs)
    3. exits (if target wants to leave zone)
    4. anomaly/artifact locations (if target wants money)
    5. recently visited (high confidence)
    6. high confidence and not too stale

    Uses location indexes to build a bounded candidate pool instead of scanning
    the full known_locations table.  Candidate pool size is capped at
    max_entries * _SHARE_CANDIDATE_POOL_MULTIPLIER (min _SHARE_CANDIDATE_POOL_MIN).
    """
    knowledge = ensure_location_knowledge_v1(source_agent)
    known_locations: dict[str, Any] = knowledge.get("known_locations") or {}
    indexes = get_location_indexes(source_agent)

    # ── Build a bounded candidate pool from indexes ────────────────────────────
    # Complexity: O(K) where K = total index entries, not O(N=all known_locations)
    pool_size = max(max_entries * _SHARE_CANDIDATE_POOL_MULTIPLIER, _SHARE_CANDIDATE_POOL_MIN)
    candidate_ids: set[str] = set()

    # Build candidate pool from all feature indexes plus visited and recently-updated.
    # Each index is included so that the receiver always gets feature-rich locations
    # regardless of target_needs flags (the flags only affect scoring).
    # itertools.chain flattens all index lists into a single iteration so the
    # pool_size cap can be applied in one place without nested break logic.
    for loc_id in itertools.chain(
        indexes.get("known_shelter_location_ids") or [],
        indexes.get("known_trader_location_ids") or [],
        indexes.get("known_exit_location_ids") or [],
        indexes.get("known_anomaly_location_ids") or [],
        indexes.get("visited_ids") or [],
        indexes.get("recently_updated_ids") or [],
    ):
        candidate_ids.add(loc_id)
        if len(candidate_ids) >= pool_size:
            break

    # ── Score candidates ───────────────────────────────────────────────────────
    scored: list[tuple[float, dict[str, Any]]] = []
    for loc_id in candidate_ids:
        entry = known_locations.get(loc_id)
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
        if level not in _SHAREABLE_LEVELS:
            continue
        confidence = float(entry.get("confidence", 0.0) or 0.0)
        if confidence < 0.3:
            continue

        snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
        score = confidence * 10.0

        # Boost by feature relevance
        has_shelter = bool((snapshot or {}).get("has_shelter"))
        has_trader = bool((snapshot or {}).get("has_trader"))
        has_exit = bool((snapshot or {}).get("has_exit"))
        has_anomaly = bool((snapshot or {}).get("anomaly_risk_estimate"))
        has_artifacts = bool((snapshot or {}).get("artifact_potential_estimate"))

        if has_shelter and target_needs_shelter:
            score += 40.0
        if has_trader and target_needs_trader:
            score += 30.0
        if has_exit and target_needs_exit:
            score += 40.0
        if (has_anomaly or has_artifacts) and target_needs_artifacts:
            score += 20.0

        # Boost recently visited locations
        last_visited = int(entry.get("last_visited_turn") or 0)
        if last_visited and world_turn > 0:
            turns_ago = world_turn - last_visited
            if turns_ago < 100:
                score += max(0.0, 15.0 - turns_ago * 0.15)

        # Prefer visited over rumor
        if level == LOCATION_KNOWLEDGE_VISITED:
            score += 8.0
        elif level == LOCATION_KNOWLEDGE_SNAPSHOT:
            score += 4.0

        scored.append((-score, entry))

    scored.sort(key=lambda x: x[0])
    return [e for _, e in scored[:max_entries]]


# ── Send / Receive ────────────────────────────────────────────────────────────

def build_location_knowledge_share_packets(
    source_agent: dict[str, Any],
    *,
    world_turn: int,
    target_needs_shelter: bool = False,
    target_needs_trader: bool = False,
    target_needs_exit: bool = False,
    target_needs_artifacts: bool = False,
    target_is_hunter: bool = False,
    max_entries: int = MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION,
) -> list[dict[str, Any]]:
    """Build up to *max_entries* share packets from source_agent\'s known locations.

    Returns list of compact share packets ready for receive_location_knowledge_packets().
    Never copies more than max_entries entries.
    """
    source_id = str(source_agent.get("id") or "unknown")
    entries = select_location_knowledge_to_share(
        source_agent,
        target_needs_shelter=target_needs_shelter,
        target_needs_trader=target_needs_trader,
        target_needs_exit=target_needs_exit,
        target_needs_artifacts=target_needs_artifacts,
        target_is_hunter=target_is_hunter,
        max_entries=max_entries,
        world_turn=world_turn,
    )
    return [build_share_packet(entry, source_agent_id=source_id, world_turn=world_turn)
            for entry in entries]


def receive_location_knowledge_packets(
    receiver: dict[str, Any],
    packets: list[dict[str, Any]],
    *,
    world_turn: int,
) -> int:
    """Merge a list of location knowledge packets into receiver\'s known_locations.

    Returns the count of locations that were updated/inserted.
    Each packet is merged via upsert_known_location with the packet\'s source/confidence.
    Never copies 300-600 locations — only processes what\'s in packets.
    """
    updated = 0
    receiver_knowledge = ensure_location_knowledge_v1(receiver)
    for packet in packets[:MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION]:
        loc_id = str(packet.get("location_id") or "")
        if not loc_id:
            continue
        knowledge_level = str(packet.get("knowledge_level") or LOCATION_KNOWLEDGE_EXISTS)
        source = str(packet.get("source") or "shared_by_agent")
        source_agent_id = packet.get("source_agent_id")
        confidence = float(packet.get("confidence", 0.3) or 0.3)
        observed_turn = packet.get("observed_turn")
        received_turn = packet.get("received_turn") or world_turn
        snapshot = packet.get("snapshot") if isinstance(packet.get("snapshot"), dict) else None
        edges = packet.get("edges") if isinstance(packet.get("edges"), dict) else None

        before_revision = int(
            ((receiver_knowledge.get("stats") or {}).get("known_locations_revision", 0) or 0)
        )
        upsert_known_location(
            receiver,
            location_id=loc_id,
            world_turn=world_turn,
            knowledge_level=knowledge_level,
            source=source,
            source_agent_id=source_agent_id,
            confidence=confidence,
            snapshot=snapshot,
            edges=edges,
            observed_turn=int(observed_turn) if observed_turn is not None else None,
            received_turn=int(received_turn) if received_turn is not None else None,
        )
        receiver_knowledge = ensure_location_knowledge_v1(receiver)
        after_revision = int(
            ((receiver_knowledge.get("stats") or {}).get("known_locations_revision", 0) or 0)
        )
        if after_revision > before_revision:
            updated += 1
    return updated


__all__ = [
    "MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION",
    "MAX_LOCATION_EDGES_SHARED_PER_LOCATION",
    "LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER",
    "LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER",
    "build_share_packet",
    "build_trader_intel_packet",
    "select_location_knowledge_to_share",
    "build_location_knowledge_share_packets",
    "receive_location_knowledge_packets",
]
