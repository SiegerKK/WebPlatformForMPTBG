"""knowledge/knowledge_store.py — Compact structured world knowledge tables.

PR3 implementation: agents accumulate world knowledge in agent[knowledge_v1]
instead of creating repeated memory records for every stalker/target/corpse observation.

Public API
----------
ensure_knowledge_v1(agent) -> dict
    Ensure agent has a valid knowledge_v1 structure; migrate if needed.

upsert_known_npc(agent, *, other_agent_id, name, location_id, world_turn, source, confidence, ...)
    Update or create a known_npc entry.  Enforces caps and detail demotion.

upsert_known_location(agent, *, location_id, name, world_turn, ...)
    Update or create a known_location entry.

upsert_known_trader(agent, *, agent_id, location_id, world_turn, ...)
    Update or create a known_trader entry.

upsert_known_hazard(agent, *, location_id, kind, world_turn, confidence)
    Update or create a known_hazard entry.

effective_known_npc_confidence(entry, world_turn) -> float
    Compute time-decayed confidence WITHOUT writing back to state.

Constants
---------
MAX_KNOWN_NPCS_PER_AGENT = 100
MAX_DETAILED_KNOWN_NPCS_PER_AGENT = 30
KNOWN_NPC_DIRECT_HALF_LIFE_TURNS = 1440
KNOWN_NPC_RUMOR_HALF_LIFE_TURNS = 720
KNOWN_NPC_THREAT_HALF_LIFE_TURNS = 2880
"""
from __future__ import annotations

from typing import Any

# Constants
MAX_KNOWN_NPCS_PER_AGENT = 100
MAX_DETAILED_KNOWN_NPCS_PER_AGENT = 30

KNOWN_NPC_DIRECT_HALF_LIFE_TURNS = 1440
KNOWN_NPC_RUMOR_HALF_LIFE_TURNS = 720
KNOWN_NPC_THREAT_HALF_LIFE_TURNS = 2880

_SOURCE_PRIORITY: dict[str, int] = {
    "direct_observation": 100,
    "target_intel": 90,
    "corpse_seen": 85,
    "combat": 80,
    "trade_interaction": 70,
    "witness_report": 50,
    "rumor": 20,
}

_RUMOR_SOURCES: frozenset[str] = frozenset({"witness_report", "rumor"})
_THREAT_SOURCES: frozenset[str] = frozenset({"combat", "target_intel"})
_PROTECTED_RELATIONS: frozenset[str] = frozenset({"kill_target", "enemy"})


def ensure_knowledge_v1(agent: dict[str, Any]) -> dict[str, Any]:
    existing = agent.get("knowledge_v1")
    if isinstance(existing, dict):
        if not isinstance(existing.get("known_npcs"), dict):
            existing["known_npcs"] = {}
        if not isinstance(existing.get("known_locations"), dict):
            existing["known_locations"] = {}
        if not isinstance(existing.get("known_traders"), dict):
            existing["known_traders"] = {}
        if not isinstance(existing.get("known_hazards"), dict):
            existing["known_hazards"] = {}
        if not isinstance(existing.get("stats"), dict):
            existing["stats"] = {
                "known_npcs_count": 0,
                "detailed_known_npcs_count": 0,
                "last_update_turn": 0,
            }
        return existing

    knowledge: dict[str, Any] = {
        "revision": 0,
        "known_npcs": {},
        "known_locations": {},
        "known_traders": {},
        "known_hazards": {},
        "stats": {
            "known_npcs_count": 0,
            "detailed_known_npcs_count": 0,
            "last_update_turn": 0,
        },
    }
    agent["knowledge_v1"] = knowledge
    return knowledge


def effective_known_npc_confidence(entry: dict[str, Any], world_turn: int) -> float:
    base_confidence = float(entry.get("confidence", 0.5))
    last_seen_turn = int(entry.get("last_seen_turn", 0) or 0)
    source = str(entry.get("source", "direct_observation"))

    age = max(0, world_turn - last_seen_turn)
    if age == 0:
        return base_confidence

    if source in _RUMOR_SOURCES:
        half_life = KNOWN_NPC_RUMOR_HALF_LIFE_TURNS
    elif source in _THREAT_SOURCES:
        half_life = KNOWN_NPC_THREAT_HALF_LIFE_TURNS
    else:
        half_life = KNOWN_NPC_DIRECT_HALF_LIFE_TURNS

    decayed = base_confidence * (0.5 ** (age / half_life))
    return max(0.0, min(1.0, decayed))


def upsert_known_npc(
    agent: dict[str, Any],
    *,
    other_agent_id: str,
    name: str | None,
    location_id: str | None,
    world_turn: int,
    source: str,
    confidence: float,
    observed_agent: dict[str, Any] | None = None,
    relation_delta: float | None = None,
    threat_level: float | None = None,
    death_status: dict[str, Any] | None = None,
) -> None:
    knowledge = ensure_knowledge_v1(agent)
    known_npcs: dict[str, Any] = knowledge["known_npcs"]

    existing: dict[str, Any] | None = known_npcs.get(other_agent_id)
    incoming_priority = _SOURCE_PRIORITY.get(source, 50)

    if existing is None:
        entry: dict[str, Any] = {
            "agent_id": other_agent_id,
            "name": name or other_agent_id,
            "last_seen_location_id": location_id,
            "last_seen_turn": world_turn,
            "last_seen_distance": 0,
            "is_alive": True,
            "alive_confidence": confidence,
            "relation": "neutral",
            "relation_score": 0.0,
            "relation_updated_turn": world_turn,
            "threat_level": threat_level if threat_level is not None else 0.2,
            "source": source,
            "confidence": confidence,
            "detail_level": "compact",
        }
        if death_status is not None:
            _apply_death_status(entry, death_status, confidence)
        if observed_agent is not None:
            _apply_observed_agent_detail(entry, observed_agent)
            entry["detail_level"] = "detailed"
        known_npcs[other_agent_id] = entry
    else:
        stored_priority = _SOURCE_PRIORITY.get(str(existing.get("source", "")), 0)

        if name and not existing.get("name"):
            existing["name"] = name
        elif name and incoming_priority >= stored_priority:
            existing["name"] = name

        # Death observations always update location regardless of source priority —
        # a corpse at loc_B is definitive, even if direct_observation was at loc_A before.
        _death_forces_location = (death_status is not None and not death_status.get("is_alive", True))

        if incoming_priority >= stored_priority or _death_forces_location:
            if location_id:
                existing["last_seen_location_id"] = location_id
            existing["last_seen_turn"] = max(
                int(existing.get("last_seen_turn", 0) or 0), world_turn
            )
            existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
            existing["source"] = source
        else:
            if world_turn > int(existing.get("last_seen_turn", 0) or 0):
                existing["last_seen_turn"] = world_turn

        if relation_delta is not None:
            old_score = float(existing.get("relation_score", 0.0))
            new_score = max(-1.0, min(1.0, old_score + relation_delta))
            existing["relation_score"] = new_score
            existing["relation_updated_turn"] = world_turn
            existing["relation"] = _score_to_relation(new_score)

        if threat_level is not None:
            existing["threat_level"] = threat_level

        if death_status is not None:
            _apply_death_status(existing, death_status, confidence)

        if observed_agent is not None:
            _apply_observed_agent_detail(existing, observed_agent)
            existing["detail_level"] = "detailed"

    _update_knowledge_stats(knowledge, world_turn)
    _enforce_npc_caps(agent, knowledge)


def _apply_death_status(
    entry: dict[str, Any],
    death_status: dict[str, Any],
    confidence: float,
) -> None:
    is_alive = bool(death_status.get("is_alive", True))
    entry["is_alive"] = is_alive
    if not is_alive:
        existing_conf = float(entry.get("alive_confidence", 1.0))
        new_alive_conf = max(0.0, 1.0 - confidence)
        entry["alive_confidence"] = min(new_alive_conf, existing_conf)
        if "death_cause" in death_status:
            entry["death_cause"] = death_status["death_cause"]
        if "killer_id" in death_status:
            entry["killer_id"] = death_status["killer_id"]
    else:
        entry["alive_confidence"] = max(float(entry.get("alive_confidence", 0.5)), confidence)


def _apply_observed_agent_detail(entry: dict[str, Any], observed_agent: dict[str, Any]) -> None:
    equipment = observed_agent.get("equipment") or {}
    weapon = equipment.get("weapon") or {}
    armor = equipment.get("armor") or {}
    weapon_type = str(weapon.get("type", "unknown")) if weapon else "unknown"
    armor_type = str(armor.get("type", "unknown")) if armor else "unknown"
    weapon_class = _classify_weapon(weapon_type)
    armor_class = _classify_armor(armor_type)
    entry["equipment_summary"] = {
        "weapon_class": weapon_class,
        "armor_class": armor_class,
        "detector_tier": 0,
    }
    global_goal = observed_agent.get("global_goal") or ""
    role_hints: list[str] = []
    if "artifact" in str(global_goal).lower():
        role_hints.append("artifact_hunter")
    archetype = str(observed_agent.get("archetype") or "")
    if archetype == "trader_agent":
        role_hints.append("trader")
    if role_hints:
        entry["role_hints"] = role_hints


def _classify_weapon(weapon_type: str) -> str:
    wt = weapon_type.lower()
    if any(x in wt for x in ("sniper", "rifle", "ak", "svu", "svd")):
        return "rifle"
    if any(x in wt for x in ("shotgun", "spas")):
        return "shotgun"
    if any(x in wt for x in ("pistol", "pm", "glock")):
        return "pistol"
    if "knife" in wt or "melee" in wt:
        return "melee"
    return "unknown"


def _classify_armor(armor_type: str) -> str:
    at = armor_type.lower()
    if any(x in at for x in ("exo", "heavy", "military")):
        return "heavy"
    if any(x in at for x in ("suit", "medium", "stalker")):
        return "medium"
    if any(x in at for x in ("leather", "light", "jacket")):
        return "light"
    return "unknown"


def _score_to_relation(score: float) -> str:
    if score >= 0.5:
        return "ally"
    if score >= 0.1:
        return "friendly"
    if score <= -0.5:
        return "enemy"
    if score <= -0.1:
        return "hostile"
    return "neutral"


def _enforce_npc_caps(agent: dict[str, Any], knowledge: dict[str, Any]) -> None:
    known_npcs: dict[str, Any] = knowledge.get("known_npcs", {})
    kill_target_id = str(agent.get("kill_target_id") or "")

    def _is_protected(entry: dict[str, Any]) -> bool:
        aid = str(entry.get("agent_id", ""))
        if aid == kill_target_id:
            return True
        relation = str(entry.get("relation", ""))
        if relation in _PROTECTED_RELATIONS:
            return True
        if float(entry.get("threat_level", 0.0)) >= 0.6:
            return True
        if not entry.get("is_alive", True) and aid == kill_target_id:
            return True
        return False

    detailed_entries = [
        (aid, e) for aid, e in known_npcs.items()
        if str(e.get("detail_level", "compact")) == "detailed"
    ]
    if len(detailed_entries) > MAX_DETAILED_KNOWN_NPCS_PER_AGENT:
        candidates = [
            (aid, e) for aid, e in detailed_entries
            if not _is_protected(e)
            and str(e.get("relation", "neutral")) == "neutral"
        ]
        candidates.sort(key=lambda x: int(x[1].get("last_seen_turn", 0) or 0))
        demote_count = len(detailed_entries) - MAX_DETAILED_KNOWN_NPCS_PER_AGENT
        for aid, entry in candidates[:demote_count]:
            for field in ("equipment_summary", "role_hints", "combat_strength_estimate",
                          "last_interaction_turn", "stale_after_turn"):
                entry.pop(field, None)
            entry["detail_level"] = "compact"

    if len(known_npcs) > MAX_KNOWN_NPCS_PER_AGENT:
        candidates = [
            (aid, e) for aid, e in known_npcs.items()
            if not _is_protected(e)
            and str(e.get("detail_level", "compact")) == "compact"
        ]
        candidates.sort(key=lambda x: (
            float(x[1].get("confidence", 0.5)),
            int(x[1].get("last_seen_turn", 0) or 0),
        ))
        drop_count = len(known_npcs) - MAX_KNOWN_NPCS_PER_AGENT
        for aid, _ in candidates[:drop_count]:
            del known_npcs[aid]


def _update_knowledge_stats(knowledge: dict[str, Any], world_turn: int) -> None:
    known_npcs = knowledge.get("known_npcs", {})
    stats = knowledge.setdefault("stats", {})
    stats["known_npcs_count"] = len(known_npcs)
    stats["detailed_known_npcs_count"] = sum(
        1 for e in known_npcs.values()
        if isinstance(e, dict) and str(e.get("detail_level", "compact")) == "detailed"
    )
    stats["last_update_turn"] = world_turn
    knowledge["revision"] = int(knowledge.get("revision", 0)) + 1


def upsert_known_location(
    agent: dict[str, Any],
    *,
    location_id: str,
    name: str | None,
    world_turn: int,
    safe_shelter: bool = False,
    confidence: float = 1.0,
    extra: dict[str, Any] | None = None,
) -> None:
    knowledge = ensure_knowledge_v1(agent)
    known_locations: dict[str, Any] = knowledge["known_locations"]

    existing = known_locations.get(location_id)
    if existing is None:
        entry: dict[str, Any] = {
            "location_id": location_id,
            "name": name or location_id,
            "last_visited_turn": world_turn,
            "safe_shelter": safe_shelter,
            "confidence": confidence,
        }
        if extra:
            entry.update(extra)
        known_locations[location_id] = entry
    else:
        if name:
            existing["name"] = name
        if world_turn > int(existing.get("last_visited_turn", 0) or 0):
            existing["last_visited_turn"] = world_turn
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
        if safe_shelter:
            existing["safe_shelter"] = True
        if extra:
            existing.update(extra)

    knowledge["revision"] = int(knowledge.get("revision", 0)) + 1


def upsert_known_trader(
    agent: dict[str, Any],
    *,
    trader_id: str,
    location_id: str | None,
    world_turn: int,
    name: str | None = None,
    buys_artifacts: bool = False,
    sells_food: bool = False,
    sells_drink: bool = False,
    confidence: float = 1.0,
) -> None:
    knowledge = ensure_knowledge_v1(agent)
    known_traders: dict[str, Any] = knowledge["known_traders"]

    existing = known_traders.get(trader_id)
    if existing is None:
        entry: dict[str, Any] = {
            "agent_id": trader_id,
            "name": name or trader_id,
            "location_id": location_id,
            "last_seen_turn": world_turn,
            "buys_artifacts": buys_artifacts,
            "sells_food": sells_food,
            "sells_drink": sells_drink,
            "confidence": confidence,
        }
        known_traders[trader_id] = entry
    else:
        if name:
            existing["name"] = name
        if location_id:
            existing["location_id"] = location_id
        if world_turn > int(existing.get("last_seen_turn", 0) or 0):
            existing["last_seen_turn"] = world_turn
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
        if buys_artifacts:
            existing["buys_artifacts"] = True
        if sells_food:
            existing["sells_food"] = True
        if sells_drink:
            existing["sells_drink"] = True

    knowledge["revision"] = int(knowledge.get("revision", 0)) + 1


def upsert_known_hazard(
    agent: dict[str, Any],
    *,
    location_id: str,
    kind: str,
    world_turn: int,
    confidence: float = 0.9,
) -> None:
    knowledge = ensure_knowledge_v1(agent)
    known_hazards: dict[str, Any] = knowledge["known_hazards"]

    hazard_key = f"{location_id}:{kind}"
    existing = known_hazards.get(hazard_key)
    if existing is None:
        known_hazards[hazard_key] = {
            "location_id": location_id,
            "kind": kind,
            "last_seen_turn": world_turn,
            "confidence": confidence,
        }
    else:
        if world_turn > int(existing.get("last_seen_turn", 0) or 0):
            existing["last_seen_turn"] = world_turn
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)

    knowledge["revision"] = int(knowledge.get("revision", 0)) + 1


def build_knowledge_summary(agent: dict[str, Any], world_turn: int) -> dict[str, Any]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return {"revision": 0, "known_npcs_count": 0, "detailed_known_npcs_count": 0,
                "known_traders_count": 0, "known_hazards_count": 0, "top_recent_known_npcs": []}

    known_npcs: dict[str, Any] = knowledge.get("known_npcs") or {}
    known_traders: dict[str, Any] = knowledge.get("known_traders") or {}
    known_hazards: dict[str, Any] = knowledge.get("known_hazards") or {}
    stats: dict[str, Any] = knowledge.get("stats") or {}

    sorted_npcs = sorted(
        known_npcs.values(),
        key=lambda e: int(e.get("last_seen_turn", 0) or 0),
        reverse=True,
    )
    top_recent: list[dict[str, Any]] = []
    for entry in sorted_npcs[:5]:
        eff_conf = effective_known_npc_confidence(entry, world_turn)
        top_recent.append({
            "agent_id": entry.get("agent_id"),
            "name": entry.get("name"),
            "last_seen_location_id": entry.get("last_seen_location_id"),
            "last_seen_turn": entry.get("last_seen_turn"),
            "is_alive": entry.get("is_alive"),
            "effective_confidence": round(eff_conf, 3),
            "detail_level": entry.get("detail_level"),
        })

    return {
        "revision": knowledge.get("revision", 0),
        "known_npcs_count": stats.get("known_npcs_count", len(known_npcs)),
        "detailed_known_npcs_count": stats.get("detailed_known_npcs_count", 0),
        "known_traders_count": len(known_traders),
        "known_hazards_count": len(known_hazards),
        "top_recent_known_npcs": top_recent,
    }
