"""knowledge/knowledge_store.py — Compact structured world knowledge tables.

PR8 extends PR3 knowledge schema with:
- major/minor revisions;
- known_corpses table;
- hunt_evidence table;
- knowledge-first write helpers returning update result metadata.
"""
from __future__ import annotations

from typing import Any

# Constants
MAX_KNOWN_NPCS_PER_AGENT = 100
MAX_DETAILED_KNOWN_NPCS_PER_AGENT = 30
MAX_KNOWN_CORPSES_PER_AGENT = 80
MAX_DETAILED_KNOWN_CORPSES_PER_AGENT = 20

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
_DEAD_STATUSES: frozenset[str] = frozenset({"reported_dead", "corpse_seen", "confirmed_dead"})


def _default_death_evidence() -> dict[str, Any]:
    return {
        "status": "alive",
        "corpse_id": None,
        "corpse_location_id": None,
        "observed_turn": None,
        "reported_turn": None,
        "death_cause": None,
        "killer_id": None,
        "source_agent_id": None,
        "confidence": 0.0,
        "directly_observed": False,
    }


def _new_update_result(*, created: bool = False, reason: str = "minor_refresh") -> dict[str, Any]:
    return {
        "changed_major": created,
        "changed_minor": not created,
        "created": created,
        "reason": reason,
    }


def ensure_knowledge_v1(agent: dict[str, Any]) -> dict[str, Any]:
    existing = agent.get("knowledge_v1")
    if isinstance(existing, dict):
        existing.setdefault("revision", 0)
        existing.setdefault("major_revision", int(existing.get("revision", 0) or 0))
        existing.setdefault("minor_revision", 0)
        if not isinstance(existing.get("known_npcs"), dict):
            existing["known_npcs"] = {}
        if not isinstance(existing.get("known_corpses"), dict):
            existing["known_corpses"] = {}
        if not isinstance(existing.get("known_locations"), dict):
            existing["known_locations"] = {}
        if not isinstance(existing.get("known_traders"), dict):
            existing["known_traders"] = {}
        if not isinstance(existing.get("known_hazards"), dict):
            existing["known_hazards"] = {}
        if not isinstance(existing.get("hunt_evidence"), dict):
            existing["hunt_evidence"] = {}

        stats = existing.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            existing["stats"] = stats
        stats.setdefault("known_npcs_count", len(existing["known_npcs"]))
        stats.setdefault("detailed_known_npcs_count", 0)
        stats.setdefault("known_corpses_count", len(existing["known_corpses"]))
        stats.setdefault("hunt_evidence_targets_count", len(existing["hunt_evidence"]))
        stats.setdefault("last_update_turn", 0)
        stats.setdefault("last_major_update_turn", 0)
        stats.setdefault("last_minor_update_turn", 0)
        return existing

    knowledge: dict[str, Any] = {
        "revision": 0,
        "major_revision": 0,
        "minor_revision": 0,
        "known_npcs": {},
        "known_corpses": {},
        "known_locations": {},
        "known_traders": {},
        "known_hazards": {},
        "hunt_evidence": {},
        "stats": {
            "known_npcs_count": 0,
            "detailed_known_npcs_count": 0,
            "known_corpses_count": 0,
            "hunt_evidence_targets_count": 0,
            "last_update_turn": 0,
            "last_major_update_turn": 0,
            "last_minor_update_turn": 0,
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


def _major_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    death = entry.get("death_evidence") if isinstance(entry.get("death_evidence"), dict) else {}
    equip = entry.get("equipment_summary") if isinstance(entry.get("equipment_summary"), dict) else {}
    return (
        entry.get("last_seen_location_id"),
        bool(entry.get("is_alive", True)),
        str(death.get("status") or "unknown"),
        death.get("corpse_id"),
        death.get("corpse_location_id"),
        str(equip.get("weapon_class") or ""),
        str(equip.get("armor_class") or ""),
        entry.get("relation"),
        round(float(entry.get("relation_score", 0.0)), 2),
        round(float(entry.get("threat_level", 0.0)), 2),
    )


def _apply_death_status(
    entry: dict[str, Any],
    *,
    death_status: dict[str, Any],
    confidence: float,
    world_turn: int,
    source: str,
) -> None:
    death_evidence = entry.get("death_evidence")
    if not isinstance(death_evidence, dict):
        death_evidence = _default_death_evidence()
        entry["death_evidence"] = death_evidence

    is_alive = bool(death_status.get("is_alive", True))
    entry["is_alive"] = is_alive
    if not is_alive:
        existing_conf = float(entry.get("alive_confidence", 1.0))
        new_alive_conf = max(0.0, 1.0 - confidence)
        entry["alive_confidence"] = min(new_alive_conf, existing_conf)

        direct = bool(death_status.get("death_directly_confirmed", False) or source == "corpse_seen")
        status = "corpse_seen" if direct else "reported_dead"
        if bool(death_status.get("confirmed_dead", False)):
            status = "confirmed_dead"

        corpse_location = death_status.get("reported_corpse_location_id")
        corpse_id = death_status.get("corpse_id")
        death_evidence.update(
            {
                "status": status,
                "corpse_id": corpse_id,
                "corpse_location_id": corpse_location,
                "observed_turn": world_turn if direct else death_evidence.get("observed_turn"),
                "reported_turn": world_turn,
                "death_cause": death_status.get("death_cause"),
                "killer_id": death_status.get("killer_id"),
                "source_agent_id": death_status.get("source_agent_id"),
                "confidence": max(float(death_evidence.get("confidence", 0.0)), confidence),
                "directly_observed": direct,
            }
        )

        # Legacy fields used by old readers/tests.
        entry["death_reported"] = True
        entry["death_directly_confirmed"] = direct
        if corpse_location is not None:
            entry["reported_corpse_location_id"] = corpse_location
        if death_status.get("death_cause") is not None:
            entry["death_cause"] = death_status.get("death_cause")
        if death_status.get("killer_id") is not None:
            entry["killer_id"] = death_status.get("killer_id")
    else:
        entry["alive_confidence"] = max(float(entry.get("alive_confidence", 0.5)), confidence)
        prev_status = str(death_evidence.get("status") or "alive")
        if prev_status in _DEAD_STATUSES:
            death_evidence["status"] = "contradicted"
            death_evidence["reported_turn"] = world_turn
            death_evidence["confidence"] = min(
                1.0,
                max(float(death_evidence.get("confidence", 0.0)), confidence),
            )
        else:
            death_evidence["status"] = "alive"


def _apply_observed_agent_detail(entry: dict[str, Any], observed_agent: dict[str, Any], *, world_turn: int) -> None:
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
        "combat_strength_estimate": float(observed_agent.get("combat_strength_estimate", 0.0) or 0.0),
        "last_observed_turn": world_turn,
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
            for field in (
                "equipment_summary",
                "role_hints",
                "combat_strength_estimate",
                "last_interaction_turn",
                "stale_after_turn",
            ):
                entry.pop(field, None)
            entry["detail_level"] = "compact"

    if len(known_npcs) > MAX_KNOWN_NPCS_PER_AGENT:
        candidates = [
            (aid, e) for aid, e in known_npcs.items()
            if not _is_protected(e)
            and str(e.get("detail_level", "compact")) == "compact"
        ]
        candidates.sort(
            key=lambda x: (
                float(x[1].get("confidence", 0.5)),
                int(x[1].get("last_seen_turn", 0) or 0),
            )
        )
        drop_count = len(known_npcs) - MAX_KNOWN_NPCS_PER_AGENT
        for aid, _ in candidates[:drop_count]:
            del known_npcs[aid]


def _enforce_corpse_caps(agent: dict[str, Any], knowledge: dict[str, Any]) -> None:
    known_corpses: dict[str, Any] = knowledge.get("known_corpses", {})
    if len(known_corpses) <= MAX_KNOWN_CORPSES_PER_AGENT:
        return

    kill_target_id = str(agent.get("kill_target_id") or "")

    def _is_protected(entry: dict[str, Any]) -> bool:
        dead_agent_id = str(entry.get("dead_agent_id") or "")
        return bool(kill_target_id and dead_agent_id == kill_target_id)

    priority = []
    for corpse_id, entry in known_corpses.items():
        if not isinstance(entry, dict):
            continue
        if _is_protected(entry):
            continue
        stale = bool(entry.get("is_stale", False))
        conf = float(entry.get("confidence", 0.5))
        last_seen = int(entry.get("last_seen_turn", 0) or 0)
        seen_count = int(entry.get("seen_count", 1) or 1)
        priority.append((corpse_id, stale, conf, last_seen, seen_count))

    # stale first, then low confidence, then old & low frequency.
    priority.sort(key=lambda row: (0 if row[1] else 1, row[2], row[3], row[4]))

    drop_count = len(known_corpses) - MAX_KNOWN_CORPSES_PER_AGENT
    for corpse_id, *_ in priority[:drop_count]:
        known_corpses.pop(corpse_id, None)


def _refresh_stats(knowledge: dict[str, Any], world_turn: int) -> None:
    known_npcs = knowledge.get("known_npcs", {})
    known_corpses = knowledge.get("known_corpses", {})
    hunt_evidence = knowledge.get("hunt_evidence", {})
    stats = knowledge.setdefault("stats", {})
    stats["known_npcs_count"] = len(known_npcs)
    stats["detailed_known_npcs_count"] = sum(
        1
        for e in known_npcs.values()
        if isinstance(e, dict) and str(e.get("detail_level", "compact")) == "detailed"
    )
    stats["known_corpses_count"] = len(known_corpses)
    stats["hunt_evidence_targets_count"] = len(hunt_evidence)
    stats["last_update_turn"] = world_turn


def _apply_revision_update(
    knowledge: dict[str, Any],
    world_turn: int,
    *,
    changed_major: bool,
    changed_minor: bool,
) -> None:
    if not changed_major and not changed_minor:
        return

    knowledge["major_revision"] = int(knowledge.get("major_revision", 0) or 0)
    knowledge["minor_revision"] = int(knowledge.get("minor_revision", 0) or 0)
    stats = knowledge.setdefault("stats", {})
    stats["last_update_turn"] = world_turn

    if changed_major:
        knowledge["major_revision"] += 1
        stats["last_major_update_turn"] = world_turn
    if changed_minor and not changed_major:
        knowledge["minor_revision"] += 1
        stats["last_minor_update_turn"] = world_turn

    knowledge["revision"] = knowledge["major_revision"] + knowledge["minor_revision"]


def upsert_known_npc_observation(
    agent: dict[str, Any],
    *,
    other_agent_id: str,
    name: str | None,
    location_id: str | None,
    world_turn: int,
    observed_agent: dict[str, Any] | None = None,
    confidence: float = 0.95,
    source: str = "direct_observation",
    relation_delta: float | None = None,
    threat_level: float | None = None,
    death_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
            "last_direct_seen_turn": world_turn if source == "direct_observation" else None,
            "last_reported_seen_turn": world_turn if source in _RUMOR_SOURCES else None,
            "last_seen_distance": 0,
            "is_alive": True,
            "alive_confidence": confidence,
            "death_evidence": _default_death_evidence(),
            "relation": "neutral",
            "relation_score": 0.0,
            "relation_updated_turn": world_turn,
            "threat_level": threat_level if threat_level is not None else 0.2,
            "source": source,
            "confidence": confidence,
            "detail_level": "compact",
        }
        if death_status is not None:
            _apply_death_status(
                entry,
                death_status=death_status,
                confidence=confidence,
                world_turn=world_turn,
                source=source,
            )
        elif source == "direct_observation":
            entry["death_evidence"]["status"] = "alive"

        if observed_agent is not None:
            _apply_observed_agent_detail(entry, observed_agent, world_turn=world_turn)
            entry["detail_level"] = "detailed"
        known_npcs[other_agent_id] = entry

        _enforce_npc_caps(agent, knowledge)
        _refresh_stats(knowledge, world_turn)
        _apply_revision_update(knowledge, world_turn, changed_major=True, changed_minor=False)
        return _new_update_result(created=True, reason="new_entry")

    before_sig = _major_signature(existing)
    changed_minor = False

    stored_priority = _SOURCE_PRIORITY.get(str(existing.get("source", "")), 0)

    if name and (not existing.get("name") or incoming_priority >= stored_priority):
        existing["name"] = name

    death_forces_location = death_status is not None and not bool(death_status.get("is_alive", True))
    if incoming_priority >= stored_priority or death_forces_location:
        if location_id and location_id != existing.get("last_seen_location_id"):
            existing["last_seen_location_id"] = location_id
        existing["last_seen_turn"] = max(int(existing.get("last_seen_turn", 0) or 0), world_turn)
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
        existing["source"] = source
        changed_minor = True
    elif world_turn > int(existing.get("last_seen_turn", 0) or 0):
        existing["last_seen_turn"] = world_turn
        changed_minor = True

    if source == "direct_observation":
        existing["last_direct_seen_turn"] = max(int(existing.get("last_direct_seen_turn", 0) or 0), world_turn)
    elif source in _RUMOR_SOURCES:
        existing["last_reported_seen_turn"] = max(int(existing.get("last_reported_seen_turn", 0) or 0), world_turn)

    if relation_delta is not None:
        old_score = float(existing.get("relation_score", 0.0))
        new_score = max(-1.0, min(1.0, old_score + relation_delta))
        existing["relation_score"] = new_score
        existing["relation_updated_turn"] = world_turn
        existing["relation"] = _score_to_relation(new_score)
        changed_minor = True

    if threat_level is not None:
        existing["threat_level"] = threat_level
        changed_minor = True

    if death_status is not None:
        _apply_death_status(
            existing,
            death_status=death_status,
            confidence=confidence,
            world_turn=world_turn,
            source=source,
        )
        changed_minor = True
    elif source == "direct_observation" and bool(existing.get("is_alive", True)):
        death_evidence = existing.get("death_evidence")
        if isinstance(death_evidence, dict) and str(death_evidence.get("status") or "") in _DEAD_STATUSES:
            death_evidence["status"] = "contradicted"
            death_evidence["reported_turn"] = world_turn
            death_evidence["confidence"] = min(
                1.0,
                max(float(death_evidence.get("confidence", 0.0)), confidence),
            )
            changed_minor = True

    if observed_agent is not None:
        _apply_observed_agent_detail(existing, observed_agent, world_turn=world_turn)
        existing["detail_level"] = "detailed"
        changed_minor = True

    after_sig = _major_signature(existing)
    changed_major = before_sig != after_sig

    reason = "minor_refresh"
    if changed_major:
        if before_sig[0] != after_sig[0]:
            reason = "location_changed"
        elif before_sig[1] != after_sig[1] or before_sig[2] != after_sig[2]:
            reason = "death_status_changed"
        else:
            reason = "major_update"

    _enforce_npc_caps(agent, knowledge)
    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )
    return {
        "changed_major": changed_major,
        "changed_minor": bool(changed_minor or changed_major),
        "created": False,
        "reason": reason,
    }


def upsert_known_corpse(
    agent: dict[str, Any],
    *,
    corpse_id: str,
    dead_agent_id: str | None,
    dead_agent_name: str | None,
    location_id: str | None,
    world_turn: int,
    death_cause: str | None = None,
    killer_id: str | None = None,
    source_agent_id: str | None = None,
    confidence: float = 0.95,
    directly_observed: bool = True,
    is_stale: bool = False,
    stale_reason: str | None = None,
) -> dict[str, Any]:
    knowledge = ensure_knowledge_v1(agent)
    known_corpses: dict[str, Any] = knowledge["known_corpses"]

    existing = known_corpses.get(corpse_id)
    if existing is None:
        known_corpses[corpse_id] = {
            "corpse_id": corpse_id,
            "dead_agent_id": dead_agent_id,
            "dead_agent_name": dead_agent_name or dead_agent_id,
            "location_id": location_id,
            "first_seen_turn": world_turn,
            "last_seen_turn": world_turn,
            "seen_count": 1,
            "death_cause": death_cause,
            "killer_id": killer_id,
            "source": "direct_observation" if directly_observed else "witness_report",
            "source_agent_id": source_agent_id,
            "confidence": confidence,
            "is_stale": is_stale,
            "stale_reason": stale_reason,
        }
        _enforce_corpse_caps(agent, knowledge)
        _refresh_stats(knowledge, world_turn)
        _apply_revision_update(knowledge, world_turn, changed_major=True, changed_minor=False)
        return _new_update_result(created=True, reason="new_entry")

    changed_major = False
    changed_minor = False

    old_sig = (
        existing.get("location_id"),
        existing.get("dead_agent_id"),
        existing.get("death_cause"),
        existing.get("killer_id"),
        bool(existing.get("is_stale", False)),
    )

    existing["last_seen_turn"] = max(int(existing.get("last_seen_turn", 0) or 0), world_turn)
    existing["seen_count"] = int(existing.get("seen_count", 0) or 0) + 1
    changed_minor = True

    for key, value in (
        ("location_id", location_id),
        ("dead_agent_id", dead_agent_id),
        ("dead_agent_name", dead_agent_name),
        ("death_cause", death_cause),
        ("killer_id", killer_id),
        ("source_agent_id", source_agent_id),
    ):
        if value is not None and value != existing.get(key):
            existing[key] = value
            changed_major = True

    existing["confidence"] = max(float(existing.get("confidence", 0.0)), float(confidence))
    existing["is_stale"] = bool(is_stale)
    if stale_reason is not None:
        existing["stale_reason"] = stale_reason

    new_sig = (
        existing.get("location_id"),
        existing.get("dead_agent_id"),
        existing.get("death_cause"),
        existing.get("killer_id"),
        bool(existing.get("is_stale", False)),
    )
    if old_sig != new_sig:
        changed_major = True

    _enforce_corpse_caps(agent, knowledge)
    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )
    return {
        "changed_major": changed_major,
        "changed_minor": bool(changed_minor or changed_major),
        "created": False,
        "reason": "corpse_updated" if changed_major else "minor_refresh",
    }


def upsert_hunt_evidence_from_observation(
    agent: dict[str, Any],
    *,
    target_id: str,
    kind: str,
    location_id: str | None,
    world_turn: int,
    confidence: float,
    source: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    knowledge = ensure_knowledge_v1(agent)
    hunt_evidence: dict[str, Any] = knowledge["hunt_evidence"]

    entry = hunt_evidence.get(target_id)
    created = entry is None
    if created:
        entry = {
            "target_id": target_id,
            "last_seen": None,
            "death": None,
            "route_hints": [],
            "failed_search_locations": {},
            "recent_contact": None,
            "revision": 0,
        }
        hunt_evidence[target_id] = entry

    changed_major = created
    changed_minor = False

    details = details or {}

    if kind in {"target_seen", "target_last_known_location"}:
        last_seen = entry.get("last_seen") if isinstance(entry.get("last_seen"), dict) else {}
        old_sig = (
            last_seen.get("location_id"),
            last_seen.get("source"),
        )
        new_last_seen = {
            "location_id": location_id,
            "turn": world_turn,
            "confidence": max(float(last_seen.get("confidence", 0.0) or 0.0), confidence),
            "source": source,
        }
        entry["last_seen"] = new_last_seen
        entry["recent_contact"] = {"turn": world_turn, "location_id": location_id}
        new_sig = (
            new_last_seen.get("location_id"),
            new_last_seen.get("source"),
        )
        changed_major = changed_major or (old_sig != new_sig)
        changed_minor = True

    elif kind in {"corpse_seen", "target_corpse_seen", "target_corpse_reported"}:
        death = entry.get("death") if isinstance(entry.get("death"), dict) else {}
        status = "corpse_seen" if kind in {"corpse_seen", "target_corpse_seen"} else "reported_dead"
        old_sig = (
            death.get("status"),
            death.get("corpse_id"),
            death.get("location_id"),
        )
        new_death = {
            "status": status,
            "corpse_id": details.get("corpse_id"),
            "location_id": location_id,
            "turn": world_turn,
            "confidence": confidence,
            "source": source,
        }
        entry["death"] = new_death
        new_sig = (
            new_death.get("status"),
            new_death.get("corpse_id"),
            new_death.get("location_id"),
        )
        changed_major = changed_major or (old_sig != new_sig)
        changed_minor = True

    entry["revision"] = int(entry.get("revision", 0) or 0) + 1

    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )

    return {
        "changed_major": changed_major,
        "changed_minor": bool(changed_minor or changed_major),
        "created": created,
        "reason": "new_entry" if created else ("major_update" if changed_major else "minor_refresh"),
    }


# Backward-compatible API names from PR3.
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
) -> dict[str, Any]:
    return upsert_known_npc_observation(
        agent,
        other_agent_id=other_agent_id,
        name=name,
        location_id=location_id,
        world_turn=world_turn,
        source=source,
        confidence=confidence,
        observed_agent=observed_agent,
        relation_delta=relation_delta,
        threat_level=threat_level,
        death_status=death_status,
    )


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
    changed_major = False
    changed_minor = False

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
        changed_major = True
    else:
        old_loc = (existing.get("name"), bool(existing.get("safe_shelter", False)))
        if name:
            existing["name"] = name
        if world_turn > int(existing.get("last_visited_turn", 0) or 0):
            existing["last_visited_turn"] = world_turn
            changed_minor = True
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
        if safe_shelter:
            existing["safe_shelter"] = True
        if extra:
            existing.update(extra)
        new_loc = (existing.get("name"), bool(existing.get("safe_shelter", False)))
        changed_major = old_loc != new_loc

    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )


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
) -> dict[str, Any]:
    knowledge = ensure_knowledge_v1(agent)
    known_traders: dict[str, Any] = knowledge["known_traders"]

    existing = known_traders.get(trader_id)
    changed_major = False
    changed_minor = False
    created = existing is None

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
        changed_major = True
    else:
        old_sig = (
            existing.get("location_id"),
            bool(existing.get("buys_artifacts", False)),
            bool(existing.get("sells_food", False)),
            bool(existing.get("sells_drink", False)),
        )
        if name:
            existing["name"] = name
        if location_id:
            existing["location_id"] = location_id
        if world_turn > int(existing.get("last_seen_turn", 0) or 0):
            existing["last_seen_turn"] = world_turn
            changed_minor = True
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)
        if buys_artifacts:
            existing["buys_artifacts"] = True
        if sells_food:
            existing["sells_food"] = True
        if sells_drink:
            existing["sells_drink"] = True

        new_sig = (
            existing.get("location_id"),
            bool(existing.get("buys_artifacts", False)),
            bool(existing.get("sells_food", False)),
            bool(existing.get("sells_drink", False)),
        )
        changed_major = old_sig != new_sig

    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )
    return {
        "changed_major": changed_major,
        "changed_minor": bool(changed_minor or changed_major),
        "created": created,
        "reason": "new_entry" if created else ("capability_changed" if changed_major else "minor_refresh"),
    }


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
    changed_major = False
    changed_minor = False
    if existing is None:
        known_hazards[hazard_key] = {
            "location_id": location_id,
            "kind": kind,
            "last_seen_turn": world_turn,
            "confidence": confidence,
        }
        changed_major = True
    else:
        if world_turn > int(existing.get("last_seen_turn", 0) or 0):
            existing["last_seen_turn"] = world_turn
            changed_minor = True
        existing["confidence"] = max(float(existing.get("confidence", 0.5)), confidence)

    _refresh_stats(knowledge, world_turn)
    _apply_revision_update(
        knowledge,
        world_turn,
        changed_major=changed_major,
        changed_minor=changed_minor or changed_major,
    )


def build_knowledge_summary(agent: dict[str, Any], world_turn: int) -> dict[str, Any]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return {
            "revision": 0,
            "major_revision": 0,
            "minor_revision": 0,
            "known_npcs_count": 0,
            "detailed_known_npcs_count": 0,
            "known_corpses_count": 0,
            "hunt_evidence_targets_count": 0,
            "known_traders_count": 0,
            "known_hazards_count": 0,
            "top_recent_known_npcs": [],
        }

    known_npcs: dict[str, Any] = knowledge.get("known_npcs") or {}
    known_corpses: dict[str, Any] = knowledge.get("known_corpses") or {}
    known_traders: dict[str, Any] = knowledge.get("known_traders") or {}
    known_hazards: dict[str, Any] = knowledge.get("known_hazards") or {}
    hunt_evidence: dict[str, Any] = knowledge.get("hunt_evidence") or {}
    stats: dict[str, Any] = knowledge.get("stats") or {}

    sorted_npcs = sorted(
        known_npcs.values(),
        key=lambda e: int(e.get("last_seen_turn", 0) or 0),
        reverse=True,
    )
    top_recent: list[dict[str, Any]] = []
    for entry in sorted_npcs[:5]:
        eff_conf = effective_known_npc_confidence(entry, world_turn)
        top_recent.append(
            {
                "agent_id": entry.get("agent_id"),
                "name": entry.get("name"),
                "last_seen_location_id": entry.get("last_seen_location_id"),
                "last_seen_turn": entry.get("last_seen_turn"),
                "is_alive": entry.get("is_alive"),
                "effective_confidence": round(eff_conf, 3),
                "detail_level": entry.get("detail_level"),
            }
        )

    return {
        "revision": knowledge.get("revision", 0),
        "major_revision": knowledge.get("major_revision", 0),
        "minor_revision": knowledge.get("minor_revision", 0),
        "known_npcs_count": stats.get("known_npcs_count", len(known_npcs)),
        "detailed_known_npcs_count": stats.get("detailed_known_npcs_count", 0),
        "known_corpses_count": stats.get("known_corpses_count", len(known_corpses)),
        "hunt_evidence_targets_count": stats.get("hunt_evidence_targets_count", len(hunt_evidence)),
        "known_traders_count": len(known_traders),
        "known_hazards_count": len(known_hazards),
        "top_recent_known_npcs": top_recent,
    }
