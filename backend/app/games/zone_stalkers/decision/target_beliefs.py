from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.games.zone_stalkers.decision.beliefs import BeliefState
from app.games.zone_stalkers.decision.models.hunt_lead import HuntLead
from app.games.zone_stalkers.decision.models.target_belief import (
    LocationHypothesis,
    RouteHypothesis,
    TargetBelief,
)
from app.games.zone_stalkers.knowledge.knowledge_hunt_builder import (
    build_equipment_belief_from_knowledge,
    build_hunt_leads_from_knowledge,
    build_recent_target_contact_from_knowledge,
)
from app.games.zone_stalkers.knowledge.knowledge_store import (
    effective_known_npc_confidence,
    upsert_known_npc,
)

_LEAD_CONFIDENCE_DEFAULTS: dict[str, float] = {
    "target_seen": 0.95,
    "target_last_known_location": 0.85,
    "target_intel": 0.65,
    "target_moved": 0.8,
    "target_route_observed": 0.65,
    "target_not_found": 0.75,
    "target_combat_noise": 0.35,
    "target_wounded": 0.55,
}

_LEAD_DECAY_WINDOWS: dict[str, int] = {
    "target_seen": 600,
    "target_last_known_location": 600,
    "target_intel": 900,
    "target_moved": 450,
    "target_route_observed": 300,
    "target_not_found": 1200,
    "target_combat_noise": 120,
    "target_wounded": 450,
}

_LEAD_WEIGHTS: dict[str, float] = {
    "target_seen": 1.0,
    "target_last_known_location": 1.0,
    "target_intel": 1.0,
    "target_moved": 1.0,
    "target_route_observed": 1.0,
    # target_not_found is applied as staged multiplicative suppression below.
    "target_not_found": 0.0,
    "target_combat_noise": 1.0,
    "target_wounded": 0.8,
}

_KIND_ALIASES: dict[str, str] = {
    "intel_from_trader": "target_intel",
    "intel_from_stalker": "target_intel",
    "target_corpse_reported": "target_last_known_location",
    "target_corpse_seen": "target_last_known_location",
    "target_death_confirmed": "target_last_known_location",
}

_SEARCH_EXHAUSTION_THRESHOLD = 3
_SEARCH_LOCATION_COOLDOWN_TURNS = 300
_MISS_SCORE_MULTIPLIERS: dict[int, float] = {
    1: 0.45,
    2: 0.20,
}

# Fix 4 — recently_seen TTL in turns
RECENT_TARGET_CONTACT_TURNS: int = 10
# Fix 8 — minimum confidence for a possible_location to be included
POSSIBLE_LOCATION_MIN_CONFIDENCE: float = 0.05


def _iter_memory_v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        return []
    records = memory_v3.get("records")
    if not isinstance(records, dict):
        return []
    return [rec for rec in records.values() if isinstance(rec, dict)]


def _ensure_target_belief_metrics(agent: dict[str, Any]) -> dict[str, Any]:
    metrics = agent.setdefault("brain_context_metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
        agent["brain_context_metrics"] = metrics
    metrics.setdefault("target_belief_knowledge_leads", 0)
    metrics.setdefault("target_belief_memory_leads", 0)
    metrics.setdefault("target_belief_memory_fallbacks", 0)
    metrics.setdefault("target_belief_memory_records_scanned", 0)
    metrics.setdefault("target_belief_known_npc_hits", 0)
    metrics.setdefault("target_belief_known_corpse_hits", 0)
    metrics.setdefault("target_belief_hunt_evidence_hits", 0)
    return metrics


def _count_known_target_corpses(agent: dict[str, Any], target_id: str) -> int:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return 0
    known_corpses = knowledge.get("known_corpses")
    if not isinstance(known_corpses, dict):
        return 0
    return sum(
        1
        for corpse in known_corpses.values()
        if isinstance(corpse, dict)
        and str(corpse.get("dead_agent_id") or "") == target_id
        and not bool(corpse.get("is_stale"))
    )


def _knowledge_has_sufficient_target_signal(
    agent: dict[str, Any],
    *,
    target_id: str,
    world_turn: int,
) -> bool:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return False

    known_npcs = knowledge.get("known_npcs")
    npc_entry = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
    if isinstance(npc_entry, dict) and effective_known_npc_confidence(npc_entry, world_turn) >= 0.05:
        return True

    hunt_evidence = knowledge.get("hunt_evidence")
    if isinstance(hunt_evidence, dict) and isinstance(hunt_evidence.get(target_id), dict):
        return True

    return _count_known_target_corpses(agent, target_id) > 0


def _store_target_knowledge_debug(
    agent: dict[str, Any],
    *,
    target_id: str,
    memory_fallback_used: bool,
    lead_sources: dict[str, int],
) -> None:
    brain_ctx = agent.get("brain_v3_context")
    if not isinstance(brain_ctx, dict):
        brain_ctx = {}
        agent["brain_v3_context"] = brain_ctx
    brain_ctx["_target_knowledge_debug"] = {
        "target_id": target_id,
        "legacy_memory_fallback_used": memory_fallback_used,
        "lead_sources": dict(lead_sources),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _canonical_kind(kind: str) -> str:
    return _KIND_ALIASES.get(kind, kind)


def _lead_decay_window(kind: str) -> int:
    return int(_LEAD_DECAY_WINDOWS.get(kind, 600))


def _lead_default_confidence(kind: str) -> float:
    return float(_LEAD_CONFIDENCE_DEFAULTS.get(kind, 0.5))


def _lead_freshness(kind: str, created_turn: int, world_turn: int) -> float:
    age_turns = max(0, int(world_turn) - int(created_turn))
    return _clamp01(1.0 - (age_turns / max(1, _lead_decay_window(kind))))


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


def _coerce_details(record: dict[str, Any]) -> dict[str, Any]:
    details = record.get("details")
    return details if isinstance(details, dict) else {}


def _record_source(record: dict[str, Any]) -> str:
    tags = record.get("tags")
    if isinstance(tags, (list, tuple)) and "trader" in tags:
        return "trader"
    kind = str(record.get("kind") or "")
    if kind == "intel_from_trader":
        return "trader"
    if kind == "intel_from_stalker":
        return "stalker"
    return str(record.get("layer") or "memory_v3")


def _record_to_hunt_lead(record: dict[str, Any], *, target_id: str, world_turn: int) -> HuntLead | None:
    if record.get("status") in {"archived", "stale"}:
        return None
    details = _coerce_details(record)
    rec_target_id = str(details.get("target_id") or details.get("target_agent_id") or "")
    if rec_target_id != target_id and target_id not in {str(v) for v in record.get("entity_ids", [])}:
        return None

    raw_kind = str(record.get("kind") or "")
    kind = _canonical_kind(raw_kind)
    supported_kinds = {
        "target_seen",
        "target_last_known_location",
        "target_intel",
        "target_not_found",
        "target_moved",
        "target_route_observed",
        "target_wounded",
        "target_combat_noise",
    }
    if kind not in supported_kinds:
        return None

    created_turn = _coerce_int(record.get("created_turn")) or 0
    location_id = (
        record.get("location_id")
        or details.get("location_id")
        or details.get("reported_corpse_location_id")
        or details.get("corpse_location_id")
    )
    route_from_id = details.get("from_location_id") or details.get("route_from_id")
    route_to_id = details.get("to_location_id") or details.get("route_to_id")
    if kind in {"target_moved", "target_route_observed"} and not route_to_id and location_id:
        route_to_id = location_id
    if kind == "target_moved" and location_id is None and route_to_id is not None:
        location_id = route_to_id

    confidence = _coerce_float(record.get("confidence"))
    if confidence is None:
        confidence = _lead_default_confidence(kind)
    confidence = _clamp01(confidence)

    freshness = _lead_freshness(kind, created_turn, world_turn)
    if freshness <= 0.0 and kind != "target_not_found":
        return None

    expires_turn = _coerce_int(details.get("cooldown_until_turn")) or _coerce_int(record.get("expires_turn"))
    return HuntLead(
        id=str(record.get("id") or ""),
        target_id=target_id,
        kind=kind,
        location_id=str(location_id) if location_id is not None else None,
        route_from_id=str(route_from_id) if route_from_id is not None else None,
        route_to_id=str(route_to_id) if route_to_id is not None else None,
        created_turn=created_turn,
        observed_turn=_coerce_int(details.get("observed_turn")),
        confidence=confidence,
        freshness=freshness,
        source=_record_source(record),
        source_ref=f"memory:{record.get('id')}" if record.get("id") else None,
        source_agent_id=(
            str(
                details.get("source_agent_id")
                or details.get("witness_id")
                or details.get("trader_id")
                or record.get("source_agent_id")
                or record.get("agent_id")
                or ""
            )
            or None
        ),
        expires_turn=expires_turn,
        details=details,
    )


def _build_visible_hunt_lead(
    *,
    target_id: str,
    location_id: str | None,
    world_turn: int,
) -> HuntLead | None:
    if not location_id:
        return None
    return HuntLead(
        id="visible:target",
        target_id=target_id,
        kind="target_seen",
        location_id=location_id,
        route_from_id=None,
        route_to_id=None,
        created_turn=world_turn,
        observed_turn=world_turn,
        confidence=1.0,
        freshness=1.0,
        source="visible",
        source_ref="visible:target",
        source_agent_id=None,
        expires_turn=None,
        details={},
    )


def _build_debug_hunt_lead(
    *,
    target_id: str,
    location_id: str | None,
    world_turn: int,
) -> HuntLead | None:
    if not location_id:
        return None
    return HuntLead(
        id="state:target_location:omniscient_debug",
        target_id=target_id,
        kind="target_last_known_location",
        location_id=location_id,
        route_from_id=None,
        route_to_id=None,
        created_turn=world_turn,
        observed_turn=world_turn,
        confidence=0.55,
        freshness=1.0,
        source="debug_state",
        source_ref="state:target_location:omniscient_debug",
        source_agent_id=None,
        expires_turn=None,
        details={"omniscient": True},
    )


def _collect_failed_search_stats(
    leads: list[HuntLead],
    *,
    world_turn: int,
) -> tuple[dict[str, int], dict[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    cooldowns: dict[str, int] = {}
    latest_turns: dict[str, int] = {}
    for lead in leads:
        if lead.kind != "target_not_found" or not lead.location_id:
            continue
        location_id = lead.location_id
        explicit_count = _coerce_int(lead.details.get("failed_search_count"))
        counts[location_id] = max(counts[location_id] + 1, explicit_count or 0)
        latest_turns[location_id] = max(latest_turns.get(location_id, 0), int(lead.created_turn))
        explicit_cooldown = _coerce_int(lead.details.get("cooldown_until_turn"))
        if explicit_cooldown is not None:
            cooldowns[location_id] = max(cooldowns.get(location_id, 0), explicit_cooldown)
    for location_id, count in counts.items():
        if count >= _SEARCH_EXHAUSTION_THRESHOLD and cooldowns.get(location_id, 0) <= world_turn:
            cooldowns[location_id] = max(
                cooldowns.get(location_id, 0),
                latest_turns.get(location_id, world_turn) + _SEARCH_LOCATION_COOLDOWN_TURNS,
            )
    return dict(counts), cooldowns


def _aggregate_location_hypotheses(
    *,
    leads: list[HuntLead],
    world_turn: int,
) -> tuple[tuple[LocationHypothesis, ...], tuple[RouteHypothesis, ...], tuple[str, ...]]:
    location_scores: dict[str, float] = defaultdict(float)
    location_positive_scores: dict[str, float] = defaultdict(float)
    location_freshness: dict[str, float] = defaultdict(float)
    location_reasons: dict[str, tuple[float, str]] = {}
    location_source_refs: dict[str, list[str]] = defaultdict(list)
    route_scores: dict[tuple[str | None, str | None], float] = defaultdict(float)
    route_source_refs: dict[tuple[str | None, str | None], list[str]] = defaultdict(list)
    route_reasons: dict[tuple[str | None, str | None], tuple[float, str]] = {}
    route_freshness: dict[tuple[str | None, str | None], float] = defaultdict(float)
    has_corpse_evidence: dict[str, bool] = defaultdict(bool)

    failed_counts, cooldowns = _collect_failed_search_stats(leads, world_turn=world_turn)
    exhausted_locations = tuple(
        sorted(location_id for location_id, cooldown_until in cooldowns.items() if cooldown_until > world_turn)
    )

    for lead in leads:
        effective = _clamp01(lead.confidence) * _clamp01(lead.freshness)
        weight = float(_LEAD_WEIGHTS.get(lead.kind, 0.0))
        location_id = lead.location_id or lead.route_to_id
        if location_id and weight != 0.0:
            contribution = weight * effective
            location_scores[location_id] += contribution
            if contribution > 0:
                location_positive_scores[location_id] += contribution
            location_freshness[location_id] = max(location_freshness[location_id], lead.freshness)
            if lead.source_ref:
                location_source_refs[location_id].append(lead.source_ref)
            reason_score = abs(contribution)
            prev_reason = location_reasons.get(location_id)
            if prev_reason is None or reason_score > prev_reason[0]:
                location_reasons[location_id] = (reason_score, lead.kind)
            action_kind = str((lead.details or {}).get("action_kind") or "")
            if action_kind in {"target_corpse_reported", "target_corpse_seen", "target_death_confirmed"}:
                has_corpse_evidence[location_id] = True

        if lead.kind in {"target_moved", "target_route_observed"} and (lead.route_from_id or lead.route_to_id):
            route_key = (lead.route_from_id, lead.route_to_id or lead.location_id)
            route_scores[route_key] += effective
            route_freshness[route_key] = max(route_freshness[route_key], lead.freshness)
            if lead.source_ref:
                route_source_refs[route_key].append(lead.source_ref)
            prev_route_reason = route_reasons.get(route_key)
            if prev_route_reason is None or effective > prev_route_reason[0]:
                route_reasons[route_key] = (effective, lead.kind)

    for location_id, score in list(location_scores.items()):
        if has_corpse_evidence.get(location_id):
            continue
        miss_count = int(failed_counts.get(location_id, 0))
        if miss_count <= 0:
            continue
        if miss_count >= _SEARCH_EXHAUSTION_THRESHOLD:
            multiplier = 0.0
        else:
            multiplier = _MISS_SCORE_MULTIPLIERS.get(miss_count, _MISS_SCORE_MULTIPLIERS[2])
        location_scores[location_id] = score * multiplier

    display_scores = {
        location_id: max(0.0, score)
        for location_id, score in location_scores.items()
        if max(location_positive_scores.get(location_id, 0.0), abs(score)) > 0.01
    }
    total_score = sum(display_scores.values())
    hypotheses: list[LocationHypothesis] = []
    for location_id, score in display_scores.items():
        refs = tuple(dict.fromkeys(location_source_refs.get(location_id, [])))
        confidence = _clamp01(score)
        reason = location_reasons.get(location_id, (0.0, "target_intel"))[1]
        if failed_counts.get(location_id, 0) >= _SEARCH_EXHAUSTION_THRESHOLD and location_id in exhausted_locations:
            reason = "target_not_found"
        hypotheses.append(
            LocationHypothesis(
                location_id=location_id,
                probability=(score / total_score) if total_score > 0 else 0.0,
                confidence=confidence,
                freshness=location_freshness.get(location_id, 0.0),
                reason=reason,
                source_refs=refs,
            )
        )
    hypotheses.sort(
        key=lambda item: (
            item.location_id in exhausted_locations,
            -item.probability,
            -item.confidence,
            item.location_id,
        )
    )

    route_hypotheses: list[RouteHypothesis] = []
    for route_key, score in route_scores.items():
        if score <= 0.01:
            continue
        # Fix 9: Heavily penalize routes that lead to exhausted locations
        to_loc = route_key[1]
        effective_score = score * 0.1 if (to_loc and to_loc in exhausted_locations) else score
        if effective_score <= 0.01:
            continue
        refs = tuple(dict.fromkeys(route_source_refs.get(route_key, [])))
        route_hypotheses.append(
            RouteHypothesis(
                from_location_id=route_key[0],
                to_location_id=route_key[1],
                confidence=_clamp01(effective_score),
                freshness=route_freshness.get(route_key, 0.0),
                reason=route_reasons.get(route_key, (0.0, "target_route_observed"))[1],
                source_refs=refs,
            )
        )
    route_hypotheses.sort(key=lambda item: (-item.confidence, -item.freshness, item.to_location_id or ""))

    return tuple(hypotheses), tuple(route_hypotheses), exhausted_locations


def build_hunt_leads_from_memory_v3_legacy(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any]:
    leads: list[HuntLead] = []
    source_refs: list[str] = []
    equipment_known = False
    combat_strength: float | None = None
    combat_strength_confidence = 0.0
    last_seen_turn: int | None = None
    target_alive_from_memory: bool | None = None
    records = _iter_memory_v3_records(agent)

    for record in records:
        details = _coerce_details(record)
        raw_kind = str(record.get("kind") or "")
        kind = _canonical_kind(raw_kind)
        if kind == "target_equipment_seen":
            equipment_known = True
            if record.get("id"):
                source_refs.append(f"memory:{record.get('id')}")
            continue
        if kind == "target_combat_strength_observed":
            strength = details.get("combat_strength")
            if isinstance(strength, (int, float)):
                combat_strength = float(strength)
                combat_strength_confidence = max(
                    combat_strength_confidence,
                    _clamp01(_coerce_float(record.get("confidence")) or 0.5),
                )
                if record.get("id"):
                    source_refs.append(f"memory:{record.get('id')}")
            continue
        if raw_kind == "target_death_confirmed":
            target_alive_from_memory = False
            if record.get("id"):
                source_refs.append(f"memory:{record.get('id')}")
            continue

        lead = _record_to_hunt_lead(record, target_id=target_id, world_turn=world_turn)
        if lead is None:
            continue
        leads.append(lead)
        if lead.source_ref:
            source_refs.append(lead.source_ref)
        if lead.kind == "target_seen":
            last_seen_turn = max(last_seen_turn or -1, int(lead.created_turn))

    return {
        "records_scanned": len(records),
        "leads": leads,
        "source_refs": source_refs,
        "equipment_known": equipment_known,
        "combat_strength": combat_strength,
        "combat_strength_confidence": combat_strength_confidence,
        "last_seen_turn": last_seen_turn,
        "target_alive_from_memory": target_alive_from_memory,
    }


def _build_recent_target_contact_from_memory_v3_legacy(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any] | None:
    recent_contact_turn: int | None = None
    recent_contact_location_id: str | None = None
    for record in _iter_memory_v3_records(agent):
        record_kind = _canonical_kind(str(record.get("kind") or ""))
        if record_kind != "target_seen":
            continue
        details = _coerce_details(record)
        record_target_id = str(details.get("target_id") or details.get("target_agent_id") or "")
        if record_target_id != target_id and target_id not in {str(v) for v in record.get("entity_ids", [])}:
            continue
        created_turn = _coerce_int(record.get("created_turn")) or 0
        if created_turn < world_turn - RECENT_TARGET_CONTACT_TURNS:
            continue
        if recent_contact_turn is None or created_turn > recent_contact_turn:
            recent_contact_turn = created_turn
            recent_contact_location_id = str(
                record.get("location_id")
                or details.get("location_id")
                or ""
            ) or None
    if recent_contact_turn is None:
        return None
    return {
        "turn": recent_contact_turn,
        "location_id": recent_contact_location_id,
        "age": max(0, world_turn - recent_contact_turn),
        "source": "memory_v3",
    }


def build_target_belief(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    belief_state: BeliefState,
) -> TargetBelief:
    target_id = str(agent.get("kill_target_id") or "")
    if not target_id:
        _store_target_knowledge_debug(
            agent,
            target_id="",
            memory_fallback_used=False,
            lead_sources={"visible": 0, "knowledge": 0, "memory_v3": 0, "debug_state": 0},
        )
        return TargetBelief(
            target_id="",
            is_known=False,
            is_alive=None,
            last_known_location_id=None,
            location_confidence=0.0,
            best_location_id=None,
            best_location_confidence=0.0,
            last_seen_turn=None,
            visible_now=False,
            co_located=False,
            equipment_known=False,
            combat_strength=None,
            combat_strength_confidence=0.0,
            possible_locations=(),
            likely_routes=(),
            exhausted_locations=(),
            lead_count=0,
            route_hints=(),
            source_refs=(),
            recently_seen=False,
            recent_contact_turn=None,
            recent_contact_location_id=None,
            recent_contact_age=None,
        )

    metrics = _ensure_target_belief_metrics(agent)
    source_refs: list[str] = []
    equipment_known = False
    combat_strength: float | None = None
    combat_strength_confidence = 0.0
    last_seen_turn: int | None = None
    target_alive_from_knowledge: bool | None = None
    target_alive_from_memory: bool | None = None
    leads: list[HuntLead] = []
    lead_sources = {"visible": 0, "knowledge": 0, "memory_v3": 0, "debug_state": 0}
    memory_fallback_used = False

    visible_entity = next(
        (
            entity for entity in belief_state.visible_entities
            if str(entity.get("agent_id") or "") == target_id and not bool(entity.get("is_trader"))
        ),
        None,
    )
    visible_now = visible_entity is not None
    co_located = visible_now
    current_loc = str(agent.get("location_id") or belief_state.location_id or "")
    if visible_now:
        knowledge = agent.get("knowledge_v1")
        known_npcs = knowledge.get("known_npcs") if isinstance(knowledge, dict) else None
        known_target = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
        death_evidence = known_target.get("death_evidence") if isinstance(known_target, dict) else None
        if isinstance(death_evidence, dict) and str(death_evidence.get("status") or "") in {
            "reported_dead",
            "corpse_seen",
            "confirmed_dead",
        }:
            target = state.get("agents", {}).get(target_id)
            upsert_known_npc(
                agent,
                other_agent_id=target_id,
                name=str((target or {}).get("name") or target_id),
                location_id=current_loc or None,
                world_turn=world_turn,
                source="direct_observation",
                confidence=1.0,
                observed_agent=target if isinstance(target, dict) else None,
                death_status={"is_alive": True},
            )
        visible_lead = _build_visible_hunt_lead(target_id=target_id, location_id=current_loc or None, world_turn=world_turn)
        if visible_lead is not None:
            leads.append(visible_lead)
            source_refs.append(visible_lead.source_ref or visible_lead.id)
            lead_sources["visible"] += 1
        last_seen_turn = world_turn
        if visible_entity and visible_entity.get("hp") is not None:
            try:
                combat_strength = max(0.1, min(1.0, float(visible_entity.get("hp", 100)) / 100.0))
                combat_strength_confidence = 0.8
            except (TypeError, ValueError):
                pass

    knowledge_leads = build_hunt_leads_from_knowledge(agent=agent, target_id=target_id, world_turn=world_turn)
    if knowledge_leads:
        metrics["target_belief_knowledge_leads"] = int(metrics.get("target_belief_knowledge_leads", 0)) + len(knowledge_leads)
        leads.extend(knowledge_leads)
        lead_sources["knowledge"] += len(knowledge_leads)
        for lead in knowledge_leads:
            if lead.source_ref:
                source_refs.append(lead.source_ref)
            if lead.kind in {"target_seen", "target_last_known_location"}:
                last_seen_turn = max(last_seen_turn or -1, int(lead.created_turn))

    knowledge = agent.get("knowledge_v1")
    known_npcs = knowledge.get("known_npcs") if isinstance(knowledge, dict) else None
    if isinstance(known_npcs, dict) and isinstance(known_npcs.get(target_id), dict):
        target_alive_from_knowledge = bool(known_npcs[target_id].get("is_alive", True))
        metrics["target_belief_known_npc_hits"] = int(metrics.get("target_belief_known_npc_hits", 0)) + 1
    hunt_evidence = knowledge.get("hunt_evidence") if isinstance(knowledge, dict) else None
    if isinstance(hunt_evidence, dict) and isinstance(hunt_evidence.get(target_id), dict):
        metrics["target_belief_hunt_evidence_hits"] = int(metrics.get("target_belief_hunt_evidence_hits", 0)) + 1
    corpse_hits = _count_known_target_corpses(agent, target_id)
    if corpse_hits:
        metrics["target_belief_known_corpse_hits"] = int(metrics.get("target_belief_known_corpse_hits", 0)) + corpse_hits

    equipment_belief = build_equipment_belief_from_knowledge(agent=agent, target_id=target_id, world_turn=world_turn)
    equipment_known = bool(equipment_belief.get("equipment_known"))
    combat_strength = equipment_belief.get("combat_strength")
    combat_strength_confidence = _clamp01(equipment_belief.get("combat_strength_confidence") or 0.0)
    source_refs.extend(str(ref) for ref in equipment_belief.get("source_refs", ()) if ref)

    recent_contact = build_recent_target_contact_from_knowledge(agent=agent, target_id=target_id, world_turn=world_turn)
    needs_memory_fallback = not _knowledge_has_sufficient_target_signal(agent, target_id=target_id, world_turn=world_turn)
    if recent_contact is None:
        needs_memory_fallback = True
    if not equipment_known and combat_strength is None:
        needs_memory_fallback = True

    if needs_memory_fallback:
        memory_fallback_used = True
        metrics["target_belief_memory_fallbacks"] = int(metrics.get("target_belief_memory_fallbacks", 0)) + 1
        legacy = build_hunt_leads_from_memory_v3_legacy(agent=agent, target_id=target_id, world_turn=world_turn)
        metrics["target_belief_memory_records_scanned"] = int(
            metrics.get("target_belief_memory_records_scanned", 0)
        ) + int(legacy.get("records_scanned", 0))
        legacy_leads = list(legacy.get("leads", []))
        metrics["target_belief_memory_leads"] = int(metrics.get("target_belief_memory_leads", 0)) + len(legacy_leads)
        leads.extend(legacy_leads)
        lead_sources["memory_v3"] += len(legacy_leads)
        source_refs.extend(str(ref) for ref in legacy.get("source_refs", []) if ref)
        if legacy.get("last_seen_turn") is not None:
            last_seen_turn = max(last_seen_turn or -1, int(legacy["last_seen_turn"]))
        if not equipment_known:
            equipment_known = bool(legacy.get("equipment_known"))
        if combat_strength is None and legacy.get("combat_strength") is not None:
            combat_strength = float(legacy["combat_strength"])
            combat_strength_confidence = max(
                combat_strength_confidence,
                _clamp01(legacy.get("combat_strength_confidence") or 0.0),
            )
        if legacy.get("target_alive_from_memory") is not None:
            target_alive_from_memory = bool(legacy["target_alive_from_memory"])
        if recent_contact is None:
            recent_contact = _build_recent_target_contact_from_memory_v3_legacy(
                agent=agent,
                target_id=target_id,
                world_turn=world_turn,
            )

    target = state.get("agents", {}).get(target_id)
    target_alive_from_state: bool | None = None
    omniscient_targets = bool(state.get("debug_omniscient_targets"))
    if isinstance(target, dict):
        target_alive_from_state = bool(target.get("is_alive", True))
        if omniscient_targets and not visible_now:
            debug_lead = _build_debug_hunt_lead(
                target_id=target_id,
                location_id=str(target.get("location_id") or "") or None,
                world_turn=world_turn,
            )
            if debug_lead is not None:
                leads.append(debug_lead)
                source_refs.append(debug_lead.source_ref or debug_lead.id)
                lead_sources["debug_state"] += 1
        if omniscient_targets and combat_strength is None and target.get("hp") is not None:
            try:
                combat_strength = max(0.1, min(1.0, float(target.get("hp", 100)) / 100.0))
                combat_strength_confidence = max(combat_strength_confidence, 0.45)
                source_refs.append("state:target_hp:omniscient_debug")
            except (TypeError, ValueError):
                pass

    possible_locations, likely_routes, exhausted_locations = _aggregate_location_hypotheses(
        leads=leads,
        world_turn=world_turn,
    )
    possible_locations = tuple(
        hypothesis for hypothesis in possible_locations if hypothesis.confidence > POSSIBLE_LOCATION_MIN_CONFIDENCE
    )
    best_hypothesis = next(
        (item for item in possible_locations if item.location_id not in exhausted_locations and item.confidence > 0),
        next((item for item in possible_locations if item.confidence > 0), None),
    )
    best_location_id = best_hypothesis.location_id if best_hypothesis is not None else None
    best_location_confidence = best_hypothesis.confidence if best_hypothesis is not None else 0.0
    exhausted_set = set(exhausted_locations)
    route_hints = tuple(
        dict.fromkeys(
            route.to_location_id
            for route in likely_routes
            if route.to_location_id and route.to_location_id not in exhausted_set
        )
    )

    is_alive = target_alive_from_memory
    if is_alive is None:
        is_alive = target_alive_from_knowledge
    if is_alive is None:
        is_alive = target_alive_from_state
    combat_strength_confidence = _clamp01(combat_strength_confidence)

    if target_id == agent_id:
        is_alive = True
        co_located = True
        visible_now = True

    recently_seen = False
    recent_contact_turn: int | None = None
    recent_contact_location_id: str | None = None
    recent_contact_age: int | None = None
    if visible_now:
        recently_seen = True
        recent_contact_turn = world_turn
        recent_contact_location_id = current_loc or None
        recent_contact_age = 0
    elif isinstance(recent_contact, dict):
        recent_contact_turn = _coerce_int(recent_contact.get("turn"))
        recent_contact_location_id = str(recent_contact.get("location_id") or "") or None
        recent_contact_age = _coerce_int(recent_contact.get("age"))
        recently_seen = recent_contact_turn is not None

    _store_target_knowledge_debug(
        agent,
        target_id=target_id,
        memory_fallback_used=memory_fallback_used,
        lead_sources=lead_sources,
    )
    return TargetBelief(
        target_id=target_id,
        is_known=bool(target_id),
        is_alive=is_alive,
        last_known_location_id=best_location_id,
        location_confidence=_clamp01(best_location_confidence),
        best_location_id=best_location_id,
        best_location_confidence=_clamp01(best_location_confidence),
        last_seen_turn=last_seen_turn,
        visible_now=visible_now,
        co_located=co_located,
        equipment_known=equipment_known,
        combat_strength=combat_strength,
        combat_strength_confidence=combat_strength_confidence,
        possible_locations=possible_locations,
        likely_routes=likely_routes,
        exhausted_locations=exhausted_locations,
        lead_count=len(leads),
        route_hints=route_hints,
        source_refs=tuple(dict.fromkeys(source_refs)),
        recently_seen=recently_seen,
        recent_contact_turn=recent_contact_turn,
        recent_contact_location_id=recent_contact_location_id,
        recent_contact_age=recent_contact_age,
    )
