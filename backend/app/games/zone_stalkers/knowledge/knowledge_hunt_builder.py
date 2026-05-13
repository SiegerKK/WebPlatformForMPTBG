from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.hunt_lead import HuntLead

from .knowledge_store import effective_known_npc_confidence

_RECENT_TARGET_CONTACT_TURNS = 10
_LEAD_DECAY_WINDOWS: dict[str, int] = {
    "target_seen": 600,
    "target_last_known_location": 600,
    "target_route_observed": 300,
    "target_not_found": 1200,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _lead_freshness(kind: str, created_turn: int, world_turn: int) -> float:
    age_turns = max(0, int(world_turn) - int(created_turn))
    decay_window = max(1, int(_LEAD_DECAY_WINDOWS.get(kind, 600)))
    return _clamp01(1.0 - (age_turns / decay_window))


def _build_lead(
    *,
    target_id: str,
    kind: str,
    world_turn: int,
    created_turn: int | None,
    location_id: str | None = None,
    route_from_id: str | None = None,
    route_to_id: str | None = None,
    confidence: float = 0.5,
    source: str = "knowledge_v1",
    source_ref: str | None = None,
    source_agent_id: str | None = None,
    expires_turn: int | None = None,
    details: dict[str, Any] | None = None,
) -> HuntLead | None:
    created = int(created_turn or 0)
    freshness = _lead_freshness(kind, created, world_turn)
    if freshness <= 0.0 and kind != "target_not_found":
        return None
    return HuntLead(
        id=source_ref or f"{source}:{target_id}:{kind}:{created}:{location_id or route_to_id or 'none'}",
        target_id=target_id,
        kind=kind,
        location_id=location_id,
        route_from_id=route_from_id,
        route_to_id=route_to_id,
        created_turn=created,
        observed_turn=created,
        confidence=_clamp01(confidence),
        freshness=freshness,
        source=source,
        source_ref=source_ref,
        source_agent_id=source_agent_id,
        expires_turn=expires_turn,
        details=details or {},
    )


def _iter_known_target_corpses(agent: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return []
    known_corpses = knowledge.get("known_corpses")
    if not isinstance(known_corpses, dict):
        return []
    return [
        corpse
        for corpse in known_corpses.values()
        if isinstance(corpse, dict) and str(corpse.get("dead_agent_id") or "") == target_id
    ]


def build_hunt_leads_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> list[HuntLead]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict) or not target_id:
        return []

    leads: list[HuntLead] = []
    known_npcs = knowledge.get("known_npcs")
    npc_entry = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
    if isinstance(npc_entry, dict):
        eff_conf = effective_known_npc_confidence(npc_entry, world_turn)
        last_seen_location_id = str(npc_entry.get("last_seen_location_id") or "") or None
        last_seen_turn = _coerce_int(npc_entry.get("last_seen_turn")) or world_turn
        if last_seen_location_id and eff_conf > 0.0:
            lead = _build_lead(
                target_id=target_id,
                kind="target_last_known_location",
                world_turn=world_turn,
                created_turn=last_seen_turn,
                location_id=last_seen_location_id,
                confidence=eff_conf,
                source="knowledge_v1",
                source_ref=f"knowledge:known_npc:{target_id}",
                details={"source_kind": "known_npc"},
            )
            if lead is not None:
                leads.append(lead)

        death_evidence = npc_entry.get("death_evidence")
        if isinstance(death_evidence, dict):
            status = str(death_evidence.get("status") or "alive")
            if status not in {"alive", "contradicted"}:
                action_kind = {
                    "reported_dead": "target_corpse_reported",
                    "corpse_seen": "target_corpse_seen",
                    "confirmed_dead": "target_death_confirmed",
                }.get(status, "target_corpse_reported")
                death_location = (
                    str(death_evidence.get("corpse_location_id") or "") or last_seen_location_id
                )
                death_turn = (
                    _coerce_int(death_evidence.get("observed_turn"))
                    or _coerce_int(death_evidence.get("reported_turn"))
                    or last_seen_turn
                )
                death_confidence = _coerce_float(death_evidence.get("confidence"))
                lead = _build_lead(
                    target_id=target_id,
                    kind="target_last_known_location",
                    world_turn=world_turn,
                    created_turn=death_turn,
                    location_id=death_location,
                    confidence=death_confidence if death_confidence is not None else eff_conf,
                    source="knowledge_v1",
                    source_ref=f"knowledge:death:{target_id}",
                    source_agent_id=str(death_evidence.get("source_agent_id") or "") or None,
                    details={
                        "action_kind": action_kind,
                        "corpse_id": death_evidence.get("corpse_id"),
                        "corpse_location_id": death_evidence.get("corpse_location_id"),
                    },
                )
                if lead is not None:
                    leads.append(lead)

    hunt_evidence = knowledge.get("hunt_evidence")
    evidence_entry = hunt_evidence.get(target_id) if isinstance(hunt_evidence, dict) else None
    if isinstance(evidence_entry, dict):
        last_seen = evidence_entry.get("last_seen")
        if isinstance(last_seen, dict):
            last_seen_location = str(last_seen.get("location_id") or "") or None
            last_seen_turn = _coerce_int(last_seen.get("turn")) or world_turn
            kind = "target_seen" if str(last_seen.get("source") or "") == "direct_observation" else "target_last_known_location"
            lead = _build_lead(
                target_id=target_id,
                kind=kind,
                world_turn=world_turn,
                created_turn=last_seen_turn,
                location_id=last_seen_location,
                confidence=_coerce_float(last_seen.get("confidence")) or 0.8,
                source="knowledge_v1",
                source_ref=f"knowledge:hunt:last_seen:{target_id}",
                details={"source_kind": "hunt_evidence"},
            )
            if lead is not None:
                leads.append(lead)

        for index, route_hint in enumerate(evidence_entry.get("route_hints", []) or []):
            if isinstance(route_hint, str):
                route_hint = {"to_location_id": route_hint}
            if not isinstance(route_hint, dict):
                continue
            route_turn = _coerce_int(route_hint.get("turn")) or world_turn
            lead = _build_lead(
                target_id=target_id,
                kind="target_route_observed",
                world_turn=world_turn,
                created_turn=route_turn,
                location_id=str(route_hint.get("to_location_id") or route_hint.get("location_id") or "") or None,
                route_from_id=str(route_hint.get("from_location_id") or "") or None,
                route_to_id=str(route_hint.get("to_location_id") or route_hint.get("location_id") or "") or None,
                confidence=_coerce_float(route_hint.get("confidence")) or 0.65,
                source="knowledge_v1",
                source_ref=f"knowledge:hunt:route:{target_id}:{index}",
                source_agent_id=str(route_hint.get("source_agent_id") or "") or None,
                details=dict(route_hint),
            )
            if lead is not None:
                leads.append(lead)

        failed_locations = evidence_entry.get("failed_search_locations")
        if isinstance(failed_locations, dict):
            for location_id, failed_entry in failed_locations.items():
                if isinstance(failed_entry, dict):
                    created_turn = _coerce_int(failed_entry.get("turn")) or world_turn
                    failed_count = _coerce_int(failed_entry.get("count")) or 1
                    cooldown_until = _coerce_int(failed_entry.get("cooldown_until_turn"))
                    confidence = _coerce_float(failed_entry.get("confidence")) or 0.75
                else:
                    created_turn = world_turn
                    failed_count = _coerce_int(failed_entry) or 1
                    cooldown_until = None
                    confidence = 0.75
                lead = _build_lead(
                    target_id=target_id,
                    kind="target_not_found",
                    world_turn=world_turn,
                    created_turn=created_turn,
                    location_id=str(location_id) or None,
                    confidence=confidence,
                    source="knowledge_v1",
                    source_ref=f"knowledge:hunt:not_found:{target_id}:{location_id}",
                    expires_turn=cooldown_until,
                    details={
                        "failed_search_count": failed_count,
                        "cooldown_until_turn": cooldown_until,
                    },
                )
                if lead is not None:
                    leads.append(lead)

        death = evidence_entry.get("death")
        if isinstance(death, dict):
            death_status = str(death.get("status") or "reported_dead")
            action_kind = "target_corpse_seen" if death_status == "corpse_seen" else "target_corpse_reported"
            lead = _build_lead(
                target_id=target_id,
                kind="target_last_known_location",
                world_turn=world_turn,
                created_turn=_coerce_int(death.get("turn")) or world_turn,
                location_id=str(death.get("location_id") or "") or None,
                confidence=_coerce_float(death.get("confidence")) or 0.8,
                source="knowledge_v1",
                source_ref=f"knowledge:hunt:death:{target_id}",
                details={
                    "action_kind": action_kind,
                    "corpse_id": death.get("corpse_id"),
                },
            )
            if lead is not None:
                leads.append(lead)

    known_alive = isinstance(npc_entry, dict) and bool(npc_entry.get("is_alive", True))
    death_status = ""
    if isinstance(npc_entry, dict):
        death = npc_entry.get("death_evidence")
        if isinstance(death, dict):
            death_status = str(death.get("status") or "")
    for corpse in _iter_known_target_corpses(agent, target_id):
        if bool(corpse.get("is_stale")):
            continue
        if known_alive and death_status in {"alive", "contradicted"}:
            continue
        corpse_location = str(corpse.get("location_id") or "") or None
        corpse_turn = _coerce_int(corpse.get("last_seen_turn")) or _coerce_int(corpse.get("first_seen_turn")) or world_turn
        lead = _build_lead(
            target_id=target_id,
            kind="target_last_known_location",
            world_turn=world_turn,
            created_turn=corpse_turn,
            location_id=corpse_location,
            confidence=_coerce_float(corpse.get("confidence")) or 0.9,
            source="knowledge_v1",
            source_ref=f"knowledge:corpse:{corpse.get('corpse_id') or target_id}",
            source_agent_id=str(corpse.get("source_agent_id") or "") or None,
            details={
                "action_kind": "target_corpse_seen",
                "corpse_id": corpse.get("corpse_id"),
                "corpse_location_id": corpse.get("location_id"),
            },
        )
        if lead is not None:
            leads.append(lead)

    return leads


def build_recent_target_contact_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any] | None:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict) or not target_id:
        return None

    hunt_evidence = knowledge.get("hunt_evidence")
    evidence_entry = hunt_evidence.get(target_id) if isinstance(hunt_evidence, dict) else None
    recent_contact = evidence_entry.get("recent_contact") if isinstance(evidence_entry, dict) else None
    if isinstance(recent_contact, dict):
        recent_turn = _coerce_int(recent_contact.get("turn"))
        if recent_turn is not None and recent_turn >= world_turn - _RECENT_TARGET_CONTACT_TURNS:
            return {
                "turn": recent_turn,
                "location_id": str(recent_contact.get("location_id") or "") or None,
                "age": max(0, world_turn - recent_turn),
                "source": "hunt_evidence",
            }

    known_npcs = knowledge.get("known_npcs")
    npc_entry = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
    if not isinstance(npc_entry, dict):
        return None

    direct_seen_turn = _coerce_int(npc_entry.get("last_direct_seen_turn"))
    if direct_seen_turn is None or direct_seen_turn < world_turn - _RECENT_TARGET_CONTACT_TURNS:
        return None
    return {
        "turn": direct_seen_turn,
        "location_id": str(npc_entry.get("last_seen_location_id") or "") or None,
        "age": max(0, world_turn - direct_seen_turn),
        "source": "known_npc",
    }


def build_equipment_belief_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any]:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict) or not target_id:
        return {
            "equipment_known": False,
            "combat_strength": None,
            "combat_strength_confidence": 0.0,
            "source_refs": (),
        }

    known_npcs = knowledge.get("known_npcs")
    npc_entry = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
    if not isinstance(npc_entry, dict):
        return {
            "equipment_known": False,
            "combat_strength": None,
            "combat_strength_confidence": 0.0,
            "source_refs": (),
        }

    eff_conf = effective_known_npc_confidence(npc_entry, world_turn)
    equipment_summary = npc_entry.get("equipment_summary")
    if not isinstance(equipment_summary, dict):
        equipment_summary = {}

    combat_strength = _coerce_float(equipment_summary.get("combat_strength_estimate"))
    if combat_strength is None:
        combat_strength = _coerce_float(npc_entry.get("combat_strength_estimate"))
    if combat_strength is None:
        combat_strength = _coerce_float(npc_entry.get("threat_level"))

    equipment_known = any(
        equipment_summary.get(key) not in {None, "", "unknown"}
        for key in ("weapon_class", "armor_class")
    ) or equipment_summary.get("detector_tier") not in {None, 0}
    if combat_strength is not None:
        equipment_known = True

    return {
        "equipment_known": bool(equipment_known),
        "combat_strength": combat_strength,
        "combat_strength_confidence": _clamp01(max(eff_conf, 0.35 if combat_strength is not None else 0.0)),
        "source_refs": (f"knowledge:equipment:{target_id}",) if equipment_known or combat_strength is not None else (),
    }
