"""knowledge/knowledge_builder.py — Build context lists from knowledge_v1 tables."""
from __future__ import annotations
from typing import Any
from .knowledge_store import effective_known_npc_confidence

_MIN_EFFECTIVE_CONFIDENCE = 0.05


def build_known_entities_from_knowledge(
    agent: dict[str, Any],
    world_turn: int,
    agents: dict[str, Any] | None = None,
    own_id: str = "",
    target_id: str | None = None,
) -> list[dict[str, Any]]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return []
    known_npcs: dict[str, Any] = knowledge.get("known_npcs") or {}
    if not known_npcs:
        return []
    result: list[dict[str, Any]] = []
    for npc_id, entry in known_npcs.items():
        if not isinstance(entry, dict):
            continue
        if npc_id == own_id:
            continue
        eff_conf = effective_known_npc_confidence(entry, world_turn)
        is_target = npc_id == target_id
        if eff_conf < _MIN_EFFECTIVE_CONFIDENCE and not is_target:
            continue
        is_alive = entry.get("is_alive", True)
        last_known_location = entry.get("last_seen_location_id")
        if agents and npc_id in agents:
            live = agents[npc_id]
            # Use live is_alive only for death confirmation (agent can see a corpse disappear).
            # DO NOT override last_known_location with live state — the agent only knows
            # what they have personally observed, stored in knowledge_v1.
            if not live.get("is_alive", True):
                is_alive = False
        result.append({
            "agent_id": npc_id,
            "name": entry.get("name", npc_id),
            "is_alive": is_alive,
            "last_known_location": last_known_location,
            "memory_turn": int(entry.get("last_seen_turn", 0) or 0),
            "confidence": eff_conf,
            "source": "knowledge_v1",
            "relation": entry.get("relation", "neutral"),
            "relation_score": entry.get("relation_score", 0.0),
            "threat_level": entry.get("threat_level", 0.2),
        })
    result.sort(key=lambda e: (
        0 if e["agent_id"] == target_id else 1,
        -int(e.get("memory_turn", 0) or 0),
    ))
    return result


def build_known_locations_from_knowledge(
    agent: dict[str, Any],
    world_turn: int,
    locations: dict[str, Any] | None = None,
    traders: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return []
    known_locations: dict[str, Any] = knowledge.get("known_locations") or {}
    if not known_locations:
        return []
    result: list[dict[str, Any]] = []
    for loc_id, entry in known_locations.items():
        if not isinstance(entry, dict):
            continue
        live_loc: dict[str, Any] = {}
        if locations:
            live_loc = locations.get(loc_id) or {}
        snapshot = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}

        has_trader = bool(snapshot.get("has_trader") or entry.get("has_trader"))
        if traders:
            has_trader = has_trader or any(
                t.get("location_id") == loc_id
                for t in traders.values()
                if isinstance(t, dict)
            )

        result.append({
            "location_id": loc_id,
            "name": snapshot.get("name") or live_loc.get("name") or entry.get("name", loc_id),
            "terrain_type": snapshot.get("terrain_type") or live_loc.get("terrain_type") or entry.get("terrain_type"),
            "anomaly_activity": live_loc.get("anomaly_activity") or entry.get("anomaly_activity", 0),
            "has_trader": has_trader,
            "safe_shelter": bool(snapshot.get("has_shelter") or entry.get("safe_shelter")),
            "knowledge_level": str(entry.get("knowledge_level") or "known_exists"),
            "memory_turn": int(entry.get("last_visited_turn", entry.get("observed_turn", 0)) or 0),
            "confidence": float(entry.get("confidence", 1.0)),
            "source": "knowledge_v1",
        })
    result.sort(key=lambda e: -int(e.get("memory_turn", 0) or 0))
    return result


def build_known_traders_from_knowledge(
    agent: dict[str, Any],
    world_turn: int,
    traders_dict: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return []
    known_traders: dict[str, Any] = knowledge.get("known_traders") or {}
    if not known_traders:
        return []
    result: list[dict[str, Any]] = []
    for trader_id, entry in known_traders.items():
        if not isinstance(entry, dict):
            continue
        live_trader: dict[str, Any] = {}
        if traders_dict:
            live_trader = traders_dict.get(trader_id) or {}
        result.append({
            "agent_id": trader_id,
            "name": live_trader.get("name") or entry.get("name", trader_id),
            "location_id": live_trader.get("location_id") or entry.get("location_id"),
            "source": "knowledge_v1",
            "memory_turn": int(entry.get("last_seen_turn", 0) or 0),
        })
    return result


def build_known_hazards_from_knowledge(
    agent: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return []
    known_hazards: dict[str, Any] = knowledge.get("known_hazards") or {}
    if not known_hazards:
        return []
    result: list[dict[str, Any]] = []
    for entry in known_hazards.values():
        if not isinstance(entry, dict):
            continue
        result.append({
            "kind": entry.get("kind"),
            "world_turn": int(entry.get("last_seen_turn", 0) or 0),
            "effects": {},
            "location_id": entry.get("location_id"),
            "confidence": float(entry.get("confidence", 0.9)),
            "source": "knowledge_v1",
        })
    result.sort(key=lambda e: -int(e.get("world_turn", 0) or 0))
    return result
