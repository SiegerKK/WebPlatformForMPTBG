"""memory/legacy_bridge.py — Bridge legacy ``agent["memory"]`` to ``memory_v3``."""
from __future__ import annotations

import uuid
from typing import Any

from .models import (
    MemoryRecord,
    LAYER_EPISODIC,
    LAYER_SOCIAL,
    LAYER_THREAT,
    LAYER_SPATIAL,
)
from .store import ensure_memory_v3, add_memory_record, MEMORY_V3_IMPORT_LEGACY_LIMIT

_SKIP_ACTION_KINDS: frozenset[str] = frozenset({"sleep_interval_applied"})

_TYPE_TO_LAYER: dict[str, str] = {
    "observation": LAYER_EPISODIC,
    "decision": LAYER_EPISODIC,
    "action": LAYER_EPISODIC,
}

# action_kind -> (layer, kind, tags)
_ACTION_KIND_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # Sleep outcomes
    "sleep_completed": (LAYER_EPISODIC, "sleep_completed", ("sleep", "rest", "recovery")),
    "sleep_interrupted": (LAYER_EPISODIC, "sleep_interrupted", ("sleep", "rest", "plan_monitor")),
    "sleep_aborted": (LAYER_EPISODIC, "sleep_aborted", ("sleep", "rest", "emergency")),

    # Trade
    "trade_buy": (LAYER_EPISODIC, "item_bought", ("trade", "item")),
    "trade_sell": (LAYER_EPISODIC, "item_sold", ("trade", "item")),
    "trader_visit": (LAYER_EPISODIC, "trader_visited", ("trade", "trader")),

    # Plan monitor
    "plan_monitor_abort": (LAYER_EPISODIC, "action_aborted", ("plan_monitor", "scheduled_action")),

    # Threat / environment
    "emission_imminent": (LAYER_THREAT, "emission_warning", ("emission", "danger")),
    "emission_started": (LAYER_THREAT, "emission_started", ("emission", "danger")),
    "emission_ended": (LAYER_EPISODIC, "emission_ended", ("emission",)),
    "anomaly_detected": (LAYER_THREAT, "anomaly_detected", ("anomaly", "danger")),

    # Combat
    "combat_kill": (LAYER_THREAT, "combat_kill", ("combat", "kill")),
    "combat_killed": (LAYER_THREAT, "combat_killed", ("combat", "death")),
    "combat_wounded": (LAYER_THREAT, "combat_wounded", ("combat", "wounded")),
    "combat_flee": (LAYER_THREAT, "combat_flee", ("combat", "flee")),

    # Exploration
    "explore_confirmed_empty": (LAYER_SPATIAL, "location_empty", ("exploration", "spatial")),
    "travel_hop": (LAYER_EPISODIC, "travel_hop", ("travel",)),

    # PR3 hunt prerequisites (taxonomy only; no hunt logic here)
    "target_seen": (LAYER_SOCIAL, "target_seen", ("target", "tracking", "social")),
    "target_last_known_location": (LAYER_SPATIAL, "target_last_known_location", ("target", "tracking", "spatial")),
    "target_not_found": (LAYER_SPATIAL, "target_not_found", ("target", "tracking", "negative_observation")),
    "target_route_observed": (LAYER_SPATIAL, "target_route_observed", ("target", "route", "tracking")),
    "target_equipment_seen": (LAYER_THREAT, "target_equipment_seen", ("target", "equipment", "combat")),
    "target_combat_strength_observed": (LAYER_THREAT, "target_combat_strength_observed", ("target", "combat", "threat")),
    "target_death_confirmed": (LAYER_THREAT, "target_death_confirmed", ("target", "death", "confirmed")),
    "target_intel": (LAYER_SOCIAL, "target_intel", ("target", "intel", "social")),
}

_OBS_TYPE_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "stalkers": (LAYER_EPISODIC, "stalkers_seen", ("stalker", "social")),
    "mutants": (LAYER_THREAT, "mutants_seen", ("mutant", "danger")),
    "items": (LAYER_SPATIAL, "items_seen", ("item", "spatial")),
    "artifacts": (LAYER_SPATIAL, "artifact_seen", ("artifact", "item", "spatial")),
    "combat_kill": (LAYER_THREAT, "combat_kill", ("combat", "kill")),
    "combat_killed": (LAYER_THREAT, "combat_killed", ("combat", "death")),
    "combat_wounded": (LAYER_THREAT, "combat_wounded", ("combat", "wounded")),
}


def bridge_legacy_entry_to_memory_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    legacy_entry: dict[str, Any],
    world_turn: int,
) -> None:
    """Convert a single legacy memory entry into a MemoryRecord and store it."""
    effects: dict[str, Any] = legacy_entry.get("effects", {})
    action_kind = str(effects.get("action_kind", ""))
    if action_kind in _SKIP_ACTION_KINDS:
        return

    record = _map_legacy_to_record(
        agent_id=agent_id,
        agent=agent,
        entry=legacy_entry,
        world_turn=world_turn,
    )
    if record is not None:
        add_memory_record(agent, record)


def import_legacy_memory(agent: dict[str, Any], agent_id: str, world_turn: int) -> None:
    """Import last N legacy entries when memory_v3 is still empty."""
    mem_v3 = ensure_memory_v3(agent)
    if mem_v3["records"]:
        return

    legacy_mem: list[dict[str, Any]] = agent.get("memory", [])
    if not legacy_mem:
        return

    for entry in legacy_mem[-MEMORY_V3_IMPORT_LEGACY_LIMIT:]:
        record = _map_legacy_to_record(
            agent_id=agent_id,
            agent=agent,
            entry=entry,
            world_turn=world_turn,
        )
        if record is not None:
            add_memory_record(agent, record)


def _map_legacy_to_record(
    *,
    agent_id: str,
    agent: dict[str, Any],
    entry: dict[str, Any],
    world_turn: int,
) -> MemoryRecord | None:
    """Return a MemoryRecord for a legacy entry, or None to skip."""
    effects: dict[str, Any] = entry.get("effects", {})
    action_kind = str(effects.get("action_kind", ""))
    memory_type = str(entry.get("type", "observation"))
    created_turn = int(entry.get("world_turn", world_turn))

    if action_kind in _SKIP_ACTION_KINDS:
        return None

    record_id = "mem_leg_" + uuid.uuid4().hex[:10]
    layer, kind, base_tags = _resolve_layer_kind_tags(memory_type, action_kind, effects)

    extra_tags: list[str] = list(base_tags)
    if action_kind == "plan_monitor_abort":
        reason = effects.get("dominant_pressure") or effects.get("reason", "")
        if reason:
            extra_tags.append(str(reason))
        if effects.get("scheduled_action_type") == "sleep":
            extra_tags.extend(["sleep", "rest"])
            kind = "sleep_interrupted"

    item_types: tuple[str, ...] = ()
    if action_kind in ("trade_buy", "trade_sell"):
        itype = effects.get("item_type") or effects.get("item_category", "")
        if itype:
            itype_str = str(itype)
            extra_tags.append(itype_str)
            item_types = (itype_str,)

    location_id: str | None = (
        effects.get("location_id")
        or effects.get("to_location")
        or effects.get("destination")
    )

    confidence = float(effects.get("confidence", 0.7))
    importance_tier = str(effects.get("importance", ""))
    if importance_tier == "critical":
        importance = 1.0
    elif importance_tier == "tactical":
        importance = 0.7
    else:
        importance = 0.5

    # Retention guidance for target-related memory kinds.
    if kind in {"target_equipment_seen", "target_combat_strength_observed", "target_death_confirmed"}:
        importance = max(importance, 0.85)
    elif kind in {"target_seen", "target_not_found", "target_last_known_location", "target_route_observed"}:
        importance = max(importance, 0.65)

    details: dict[str, Any] = dict(effects)
    if action_kind in ("sleep_completed", "sleep_interrupted"):
        details = {
            k: v for k, v in effects.items()
            if k in {
                "sleep_intervals_applied",
                "turns_total",
                "turns_slept",
                "hours_slept",
                "wake_due_to_rested",
                "sleepiness_after",
                "sleep_progress_turns",
                "dominant_pressure",
                "scheduled_action_type",
                "reason",
                "first_seen_turn",
                "last_seen_turn",
                "times_seen",
            }
        }
        if action_kind == "sleep_interrupted":
            importance = max(importance, 0.7)

    entity_ids = _extract_entity_ids(effects)
    all_tags = tuple(dict.fromkeys(extra_tags))
    summary = str(entry.get("summary") or entry.get("title") or f"{kind} at {location_id or 'unknown'}")

    return MemoryRecord(
        id=record_id,
        agent_id=agent_id,
        layer=layer,
        kind=kind,
        created_turn=created_turn,
        last_accessed_turn=None,
        summary=summary,
        details=details,
        location_id=location_id,
        entity_ids=entity_ids,
        item_types=item_types,
        tags=all_tags,
        importance=importance,
        confidence=confidence,
        source="legacy_import",
    )


def _extract_entity_ids(effects: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for key in (
        "agent_id",
        "target_id",
        "target_agent_id",
        "trader_id",
        "killer_id",
        "victim_id",
        "source_agent_id",
        "other_agent_id",
    ):
        value = effects.get(key)
        if value:
            ids.append(str(value))
    return tuple(dict.fromkeys(ids))


def _resolve_layer_kind_tags(
    memory_type: str,
    action_kind: str,
    effects: dict[str, Any],
) -> tuple[str, str, tuple[str, ...]]:
    if action_kind and action_kind in _ACTION_KIND_MAP:
        return _ACTION_KIND_MAP[action_kind]

    obs_type = str(effects.get("observed", ""))
    if obs_type and obs_type in _OBS_TYPE_MAP:
        return _OBS_TYPE_MAP[obs_type]

    layer = _TYPE_TO_LAYER.get(memory_type, LAYER_EPISODIC)
    kind = action_kind or obs_type or memory_type
    return layer, kind, ()
