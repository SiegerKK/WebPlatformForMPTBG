"""memory/memory_events.py — Convert game memory events to ``memory_v3`` records.

This module is the sole canonical path for writing agent memory.  Every call
to ``_add_memory`` in tick_rules.py ultimately calls
``write_memory_event_to_v3`` here.
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import (
    MemoryRecord,
    LAYER_EPISODIC,
    LAYER_SOCIAL,
    LAYER_THREAT,
    LAYER_SPATIAL,
    LAYER_GOAL,
)
from .store import ensure_memory_v3, add_memory_record

# Events classified as trace-only: never written to memory_v3.
# These belong in brain_trace / debug timeline only.
MEMORY_EVENT_POLICY: dict[str, str] = {
    "active_plan_created": "trace_only",
    "active_plan_step_started": "trace_only",
    "active_plan_step_completed": "trace_only",
    "active_plan_completed": "trace_only",
    "sleep_interval_applied": "trace_only",

    # Still written to memory — semantically meaningful outcomes.
    "active_plan_step_failed": "memory",
    "active_plan_repair_requested": "memory_dedup",
    "active_plan_repaired": "memory_dedup",
    "active_plan_aborted": "memory",
    "plan_monitor_abort": "memory",
    "objective_decision": "memory_dedup",
    "global_goal_completed": "memory",
    "target_death_confirmed": "memory",
}

_SKIP_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "trace_only"
)

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

    # ActivePlan lifecycle
    "active_plan_created": (LAYER_GOAL, "active_plan_created", ("active_plan",)),
    "active_plan_step_started": (LAYER_EPISODIC, "active_plan_step_started", ("active_plan", "step")),
    "active_plan_step_completed": (LAYER_EPISODIC, "active_plan_step_completed", ("active_plan", "step")),
    "active_plan_step_failed": (LAYER_GOAL, "active_plan_step_failed", ("active_plan", "step", "threat")),
    "active_plan_repair_requested": (LAYER_GOAL, "active_plan_repair_requested", ("active_plan", "repair")),
    "active_plan_repaired": (LAYER_GOAL, "active_plan_repaired", ("active_plan", "repair")),
    "active_plan_paused": (LAYER_GOAL, "active_plan_paused", ("active_plan", "repair")),
    "active_plan_resumed": (LAYER_GOAL, "active_plan_resumed", ("active_plan", "repair")),
    "active_plan_aborted": (LAYER_GOAL, "active_plan_aborted", ("active_plan", "threat")),
    "active_plan_completed": (LAYER_GOAL, "active_plan_completed", ("active_plan",)),
    "global_goal_completed": (LAYER_GOAL, "global_goal_completed", ("goal", "completion")),
    "objective_decision": (LAYER_GOAL, "objective_decision", ("objective", "decision")),

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
    "target_moved": (LAYER_SPATIAL, "target_moved", ("target", "tracking", "movement")),
    "target_route_observed": (LAYER_SPATIAL, "target_route_observed", ("target", "route", "tracking")),
    "target_equipment_seen": (LAYER_THREAT, "target_equipment_seen", ("target", "equipment", "combat")),
    "target_combat_strength_observed": (LAYER_THREAT, "target_combat_strength_observed", ("target", "combat", "threat")),
    "target_death_confirmed": (LAYER_THREAT, "target_death_confirmed", ("target", "death", "confirmed")),
    "target_intel": (LAYER_SOCIAL, "target_intel", ("target", "intel", "social")),
    "intel_from_trader": (LAYER_SOCIAL, "target_intel", ("target", "intel", "social", "trader")),
    "intel_from_stalker": (LAYER_SOCIAL, "target_intel", ("target", "intel", "social", "stalker")),
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


# Low-value repeated observation kinds eligible for deduplication within a window.
# NOTE: stalkers_seen/items_seen are intentionally excluded — they have their own
# merge logic in memory_merge.py and tests rely on that path creating new records.
_DEDUP_KINDS: frozenset[str] = frozenset({
    "travel_hop",
    "trader_visited",
})
# Never deduplicate these — each occurrence is individually important.
_NO_DEDUP_KINDS: frozenset[str] = frozenset({
    "death",
    "combat_kill",
    "combat_killed",
    "target_death_confirmed",
    "global_goal_completed",
})
MEMORY_EVENT_DEDUP_WINDOW_TURNS = 30


def _find_recent_dedup_record(
    agent: dict[str, Any],
    kind: str,
    location_id: str | None,
    entity_ids: tuple[str, ...],
    world_turn: int,
) -> dict[str, Any] | None:
    """Return the most recent existing record that matches the dedup signature, or None."""
    from .store import ensure_memory_v3
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        return None
    records: dict[str, Any] = mem_v3.get("records", {})
    cutoff = world_turn - MEMORY_EVENT_DEDUP_WINDOW_TURNS

    best: dict[str, Any] | None = None
    best_turn = -1
    for raw in records.values():
        if raw.get("kind") != kind:
            continue
        rec_turn = int(raw.get("created_turn", 0))
        if rec_turn < cutoff:
            continue
        if location_id and raw.get("location_id") != location_id:
            continue
        if entity_ids:
            rec_entities = tuple(sorted(raw.get("entity_ids") or []))
            if rec_entities != tuple(sorted(entity_ids)):
                continue
        if rec_turn > best_turn:
            best_turn = rec_turn
            best = raw
    return best


def write_memory_event_to_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    legacy_entry: dict[str, Any],
    world_turn: int,
) -> None:
    """Convert a single memory event entry into a MemoryRecord and store it in memory_v3."""
    effects: dict[str, Any] = legacy_entry.get("effects", {})
    action_kind = str(effects.get("action_kind", ""))

    # Policy check — skip trace-only events entirely.
    if action_kind in _SKIP_ACTION_KINDS:
        return

    # Also skip by observed kind if action_kind absent.
    obs_type = str(effects.get("observed", ""))
    if not action_kind and obs_type in _SKIP_ACTION_KINDS:
        return

    record = _map_event_to_record(
        agent_id=agent_id,
        agent=agent,
        entry=legacy_entry,
        world_turn=world_turn,
    )
    if record is None:
        return

    # Dedup low-value repeated observations within the window.
    if record.kind in _DEDUP_KINDS and record.kind not in _NO_DEDUP_KINDS:
        existing = _find_recent_dedup_record(
            agent, record.kind, record.location_id, record.entity_ids, world_turn
        )
        if existing is not None:
            # Update in-place instead of appending a new record.
            details = existing.setdefault("details", {})
            details["times_seen"] = int(details.get("times_seen", 1)) + 1
            details["last_seen_turn"] = world_turn
            existing["confidence"] = min(1.0, float(existing.get("confidence", 0.7)) + 0.02)
            return

    add_memory_record(agent, record)


def _map_event_to_record(
    *,
    agent_id: str,
    agent: dict[str, Any],
    entry: dict[str, Any],
    world_turn: int,
) -> MemoryRecord | None:
    """Return a MemoryRecord for a memory event entry, or None to skip."""
    effects: dict[str, Any] = entry.get("effects", {})
    action_kind = str(effects.get("action_kind", ""))
    memory_type = str(entry.get("type", "observation"))
    created_turn = int(entry.get("world_turn", world_turn))

    if action_kind in _SKIP_ACTION_KINDS:
        return None

    record_id = "mem_ev_" + uuid.uuid4().hex[:10]
    layer, kind, base_tags = _resolve_layer_kind_tags(memory_type, action_kind, effects)

    extra_tags: list[str] = list(base_tags)
    if action_kind == "plan_monitor_abort":
        reason = effects.get("dominant_pressure") or effects.get("reason", "")
        if reason:
            extra_tags.append(str(reason))
        if effects.get("scheduled_action_type") == "sleep":
            extra_tags.extend(["sleep", "rest"])
            kind = "sleep_interrupted"
    if action_kind.startswith("active_plan_"):
        objective_key = effects.get("objective_key")
        step_kind = effects.get("step_kind")
        reason = effects.get("reason")
        if objective_key:
            extra_tags.append(f"objective:{objective_key}")
        if step_kind:
            extra_tags.append(f"step:{step_kind}")
        if reason:
            extra_tags.append(f"repair:{reason}")
    if action_kind == "global_goal_completed":
        global_goal = effects.get("global_goal")
        if global_goal:
            extra_tags.append(f"goal:{global_goal}")

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
    if kind in {"target_equipment_seen", "target_combat_strength_observed", "target_death_confirmed", "target_intel"}:
        importance = max(importance, 0.85)
    elif kind in {"target_seen", "target_not_found", "target_moved", "target_last_known_location", "target_route_observed"}:
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

    # CRITICAL: Store the original memory type so that read functions can filter
    # by "decision" / "observation" / "action" without inspecting the record kind.
    details["memory_type"] = memory_type
    # Store the original action_kind so that _v3_action_kind() always returns it
    # even when _ACTION_KIND_MAP remaps the kind (e.g. "emission_imminent" → "emission_warning").
    if action_kind:
        details["action_kind"] = action_kind

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
        source="event",
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
