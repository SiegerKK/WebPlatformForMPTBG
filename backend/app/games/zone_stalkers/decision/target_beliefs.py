from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.beliefs import BeliefState
from app.games.zone_stalkers.decision.models.target_belief import TargetBelief


def _iter_memory_v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        return []
    records = memory_v3.get("records")
    if not isinstance(records, dict):
        return []
    return [rec for rec in records.values() if isinstance(rec, dict)]


def build_target_belief(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    belief_state: BeliefState,
) -> TargetBelief:
    del world_turn

    target_id = str(agent.get("kill_target_id") or "")
    if not target_id:
        return TargetBelief(
            target_id="",
            is_known=False,
            is_alive=None,
            last_known_location_id=None,
            location_confidence=0.0,
            last_seen_turn=None,
            visible_now=False,
            co_located=False,
            equipment_known=False,
            combat_strength=None,
            combat_strength_confidence=0.0,
            route_hints=(),
            source_refs=(),
        )

    source_refs: list[str] = []
    route_hints: list[str] = []
    last_known_location_id: str | None = None
    location_confidence = 0.0
    last_seen_turn: int | None = None
    equipment_known = False
    combat_strength: float | None = None
    combat_strength_confidence = 0.0
    target_alive_from_memory: bool | None = None

    # Visible now: derived from perception layer, no omniscience.
    visible_entity = next(
        (
            e for e in belief_state.visible_entities
            if str(e.get("agent_id") or "") == target_id and not bool(e.get("is_trader"))
        ),
        None,
    )
    visible_now = visible_entity is not None
    co_located = visible_now
    if visible_now:
        last_known_location_id = str(agent.get("location_id") or belief_state.location_id)
        location_confidence = 1.0
        last_seen_turn = int(state.get("world_turn", 0))
        if visible_entity and visible_entity.get("hp") is not None:
            # Compact proxy for target combat strength: normalized hp.
            try:
                combat_strength = max(0.1, min(1.0, float(visible_entity.get("hp", 100)) / 100.0))
                combat_strength_confidence = 0.8
            except (TypeError, ValueError):
                pass
        source_refs.append("visible:target")

    # Parse memory_v3 target records.
    records = _iter_memory_v3_records(agent)
    for rec in records:
        if rec.get("status") in {"archived", "stale"}:
            continue
        kind = str(rec.get("kind") or "")
        details = rec.get("details", {}) if isinstance(rec.get("details"), dict) else {}
        rec_target_id = str(details.get("target_id") or details.get("target_agent_id") or "")
        if rec_target_id != target_id and target_id not in {str(v) for v in rec.get("entity_ids", [])}:
            continue

        rec_turn = int(rec.get("created_turn") or 0)
        rec_conf = max(0.0, min(1.0, float(rec.get("confidence") or 0.5)))
        rec_loc = rec.get("location_id") or details.get("location_id")

        if kind in {"target_seen", "target_last_known_location", "target_intel"} and rec_loc:
            if rec_turn >= (last_seen_turn or -1):
                last_seen_turn = rec_turn
                last_known_location_id = str(rec_loc)
                location_confidence = max(location_confidence, rec_conf)
                source_refs.append(f"memory:{rec.get('id')}")
        if kind == "target_route_observed" and rec_loc:
            route_hints.append(str(rec_loc))
            source_refs.append(f"memory:{rec.get('id')}")
        if kind == "target_not_found" and rec_loc and str(rec_loc) == str(last_known_location_id):
            # Downgrade confidence if the latest known location was checked and target absent.
            location_confidence = min(location_confidence, max(0.0, rec_conf - 0.35))
            source_refs.append(f"memory:{rec.get('id')}")
        if kind == "target_equipment_seen":
            equipment_known = True
            source_refs.append(f"memory:{rec.get('id')}")
        if kind == "target_combat_strength_observed":
            strength = details.get("combat_strength")
            if isinstance(strength, (int, float)):
                combat_strength = float(strength)
                combat_strength_confidence = max(combat_strength_confidence, rec_conf)
                source_refs.append(f"memory:{rec.get('id')}")
        if kind == "target_death_confirmed":
            target_alive_from_memory = False
            source_refs.append(f"memory:{rec.get('id')}")

    # State lookup used as a controlled fallback, marked explicitly.
    target = state.get("agents", {}).get(target_id)
    target_alive_from_state: bool | None = None
    if isinstance(target, dict):
        target_alive_from_state = bool(target.get("is_alive", True))
        if last_known_location_id is None and target.get("location_id"):
            last_known_location_id = str(target.get("location_id"))
            location_confidence = max(location_confidence, 0.55)
            source_refs.append("state:target_location:omniscient_debug")
        if combat_strength is None and target.get("hp") is not None:
            try:
                combat_strength = max(0.1, min(1.0, float(target.get("hp", 100)) / 100.0))
                combat_strength_confidence = max(combat_strength_confidence, 0.45)
                source_refs.append("state:target_hp:omniscient_debug")
            except (TypeError, ValueError):
                pass

    is_alive = target_alive_from_memory if target_alive_from_memory is not None else target_alive_from_state

    # Make sure location confidence is in [0, 1].
    location_confidence = max(0.0, min(1.0, location_confidence))
    combat_strength_confidence = max(0.0, min(1.0, combat_strength_confidence))

    # Avoid self-target corner case.
    if target_id == agent_id:
        is_alive = True
        co_located = True
        visible_now = True

    return TargetBelief(
        target_id=target_id,
        is_known=bool(target_id),
        is_alive=is_alive,
        last_known_location_id=last_known_location_id,
        location_confidence=location_confidence,
        last_seen_turn=last_seen_turn,
        visible_now=visible_now,
        co_located=co_located,
        equipment_known=equipment_known,
        combat_strength=combat_strength,
        combat_strength_confidence=combat_strength_confidence,
        route_hints=tuple(dict.fromkeys(route_hints)),
        source_refs=tuple(dict.fromkeys(source_refs)),
    )
