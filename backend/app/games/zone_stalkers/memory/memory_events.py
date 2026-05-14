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
    LAYER_SEMANTIC,
)
from .store import ensure_memory_v3, add_memory_record

# PR3: Knowledge tables import — lazy to avoid circular imports at module load.
# Called inside write_memory_event_to_v3 only.
def _get_knowledge_upserts():
    from app.games.zone_stalkers.knowledge.knowledge_store import (  # noqa: PLC0415
        upsert_hunt_evidence_from_observation,
        upsert_known_corpse,
        upsert_known_npc_observation,
        upsert_known_location,
        upsert_known_trader,
    )
    return (
        upsert_known_npc_observation,
        upsert_known_corpse,
        upsert_hunt_evidence_from_observation,
        upsert_known_location,
        upsert_known_trader,
    )


KNOWLEDGE_FIRST_OBSERVATIONS_ENABLED = True
OBSERVATION_MEMORY_COMPAT_MODE = False

# Explicit memory event policy.
# Routing:
#   trace_only        → debug trace only, never written to memory_v3
#   memory_aggregate  → update one aggregate record, never append new episodic record
#   knowledge_only    → update knowledge only, never write memory_v3
#   knowledge_milestone → update knowledge; write memory_v3 only for milestones
#   knowledge_upsert  → legacy alias for knowledge_milestone (normalized in resolve_memory_event_policy)
#   memory_critical   → always write a memory_v3 record (never deduplicated/discarded)
#   discard           → write nothing
#   (missing key)     → default write path with dedup where applicable
MEMORY_EVENT_POLICY: dict[str, str] = {
    # Debug/trace only — never written to memory_v3.
    "active_plan_created": "trace_only",
    "active_plan_step_started": "trace_only",
    "active_plan_step_completed": "trace_only",
    "active_plan_completed": "trace_only",
    "active_plan_repaired": "trace_only",
    "sleep_interval_applied": "trace_only",

    # Aggregate into one record — repeated failures should not spam memory_v3.
    # Only pure plan-lifecycle failure signals are aggregated here. Events that
    # carry gameplay-critical data (target_id, location_id, cooldown_until_turn)
    # queried by planner.py / plan_monitor.py MUST remain as regular records
    # and are deduplicated via _DEDUP_KINDS instead.
    "active_plan_step_failed": "memory_aggregate",
    "active_plan_repair_requested": "memory_aggregate",
    "active_plan_aborted": "memory_aggregate",
    "plan_monitor_abort": "memory_aggregate",

    # Knowledge upsert — knowledge-first routing; selected kinds still keep
    # episodic writes for gameplay compatibility until PR3 knowledge tables.
    "stalkers": "knowledge_milestone",  # obs_type alias for stalkers_seen
    "stalkers_seen": "knowledge_milestone",
    "target_seen": "knowledge_milestone",
    "target_last_known_location": "knowledge_milestone",
    "target_not_found": "knowledge_only",
    "retreat_observed": "knowledge_milestone",
    "target_corpse_seen": "knowledge_milestone",
    "target_corpse_reported": "knowledge_milestone",
    "corpse_seen": "knowledge_milestone",
    "trader_seen": "knowledge_milestone",
    "location_visited": "knowledge_milestone",
    "travel_hop": "knowledge_milestone",
    # TODO(PR follow-up): route hazard events (anomaly/emission) into
    # known_hazards knowledge upserts when hazard knowledge tables are expanded.

    # Regular gameplay memory — deduped within cooldown window.
    "trade_sell_failed": "memory",
    "debt_created": "memory",
    "debt_credit_advanced": "memory",
    "debt_payment": "memory",
    "debt_repaid": "memory",
    "debt_rolled_over": "memory",
    "debt_defaulted": "memory_critical",
    "debt_escape_triggered": "memory_critical",

    # Important episodic memory — always written and never discarded.
    "death": "memory_critical",
    "combat_kill": "memory_critical",
    "combat_killed": "memory_critical",
    "target_death_confirmed": "memory_critical",
    "global_goal_completed": "memory_critical",
    "left_zone": "memory_critical",
    "emission_started": "memory_critical",
    "emission_warning": "memory_critical",
    "emission_ended": "memory_critical",
    "rare_artifact_found": "memory_critical",
}

_SKIP_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "trace_only"
)

_AGGREGATE_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "memory_aggregate"
)

_KNOWLEDGE_UPSERT_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "knowledge_upsert"
)

_KNOWLEDGE_ONLY_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "knowledge_only"
)

_KNOWLEDGE_MILESTONE_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v in {"knowledge_milestone", "knowledge_upsert"}
)

_CRITICAL_ACTION_KINDS: frozenset[str] = frozenset(
    k for k, v in MEMORY_EVENT_POLICY.items() if v == "memory_critical"
)


def resolve_memory_event_policy(action_kind: str, effects: dict[str, Any]) -> str:
    """Return the policy class for a given action_kind (or observed kind from effects)."""
    def _normalize(policy_value: str) -> str:
        if policy_value == "knowledge_upsert":
            return "knowledge_milestone"
        return policy_value

    if action_kind in MEMORY_EVENT_POLICY:
        return _normalize(MEMORY_EVENT_POLICY[action_kind])
    obs_type = str(effects.get("observed", ""))
    if obs_type in MEMORY_EVENT_POLICY:
        return _normalize(MEMORY_EVENT_POLICY[obs_type])
    return "memory"


# Aggregation caps.
MAX_ACTIVE_PLAN_FAILURE_AGGREGATES = 20
OBJECTIVE_DECISION_DEDUP_WINDOW_TURNS = 30

# Summary / details length limits.
MEMORY_SUMMARY_MAX_CHARS = 240
MEMORY_DETAILS_STRING_MAX_CHARS = 160
MEMORY_DETAILS_LIST_MAX_ITEMS = 5
MEMORY_DETAILS_CRITICAL_LIST_MAX_ITEMS = 20

# Fields that must never be truncated even if they are strings in details.
_CRITICAL_DETAIL_KEYS: frozenset[str] = frozenset({
    "target_id",
    "corpse_id",
    "location_id",
    "killer_id",
    "combat_id",
    "objective_key",
    "dead_agent_id",
    "source_agent_id",
    "plan_id",
    "active_plan_id",
})

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
    "anomaly_search_exhausted": (LAYER_GOAL, "anomaly_search_exhausted", ("support", "money", "cooldown")),
    "support_objective_progress": (LAYER_GOAL, "support_objective_progress", ("support", "progress")),
    "support_objective_failed": (LAYER_GOAL, "support_objective_failed", ("support", "failure")),
    "support_source_exhausted": (LAYER_GOAL, "support_source_exhausted", ("support", "cooldown")),
    "no_witnesses": (LAYER_SOCIAL, "no_witnesses", ("target", "intel", "social", "negative_observation")),
    "witness_source_exhausted": (LAYER_SOCIAL, "witness_source_exhausted", ("target", "intel", "social", "cooldown")),
    "no_tracks_found": (LAYER_SPATIAL, "no_tracks_found", ("target", "tracking", "negative_observation")),

    # Trade failures
    "trade_sell_failed": (LAYER_GOAL, "trade_sell_failed", ("trade", "failure", "cooldown")),
    "debt_created": (LAYER_GOAL, "debt_created", ("economy", "debt", "credit")),
    "debt_credit_advanced": (LAYER_GOAL, "debt_created", ("economy", "debt", "credit")),
    "debt_payment": (LAYER_GOAL, "debt_payment", ("economy", "debt", "repayment")),
    "debt_repaid": (LAYER_GOAL, "debt_repaid", ("economy", "debt", "repayment")),
    "debt_rolled_over": (LAYER_GOAL, "debt_rolled_over", ("economy", "debt", "rollover")),
    "debt_defaulted": (LAYER_GOAL, "debt_defaulted", ("economy", "debt", "default")),
    "debt_escape_triggered": (LAYER_GOAL, "debt_escape_triggered", ("economy", "debt", "escape")),

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
    "corpse_seen": (LAYER_THREAT, "corpse_seen", ("target", "corpse", "death")),
    "target_corpse_seen": (LAYER_THREAT, "target_corpse_seen", ("target", "corpse", "death", "confirmed")),
    "target_corpse_reported": (LAYER_SOCIAL, "target_corpse_reported", ("target", "corpse", "intel")),
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
# NOTE: stalkers_seen is excluded here — it is handled via dedicated semantic aggregation
# (_handle_stalkers_seen_event) that creates/updates a semantic_stalkers_seen aggregate.
# items_seen is also excluded as it writes individual spatial records without dedup.
_DEDUP_KINDS: frozenset[str] = frozenset({
    "travel_hop",
    "trade_sell_failed",
    "trader_visited",
    "no_tracks_found",
    "no_witnesses",
    "witness_source_exhausted",
    "support_source_exhausted",
    "anomaly_search_exhausted",
    "corpse_seen",
    "target_corpse_seen",
    "target_corpse_reported",
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
STALKERS_SEEN_DEDUP_WINDOW_TURNS = 60
STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION = 5
TRADE_SELL_FAILED_COOLDOWN_TURNS = 180


# ── Metrics counters ─────────────────────────────────────────────────────────
# Updated inline during write_memory_event_to_v3; read externally for monitoring.
_METRICS: dict[str, int] = {
    "memory_write_attempts": 0,
    "memory_write_written": 0,
    "memory_write_discarded": 0,
    "memory_write_trace_only": 0,
    "memory_write_aggregated": 0,
    "memory_write_knowledge_upserts": 0,
    "memory_write_critical": 0,
    "memory_evictions": 0,
    "memory_summary_truncations": 0,
    "memory_details_truncations": 0,
    "knowledge_upsert_attempts": 0,
    "knowledge_upsert_major_updates": 0,
    "knowledge_upsert_minor_refreshes": 0,
    "knowledge_only_events": 0,
    "observation_memory_milestones_written": 0,
    "stalkers_seen_memory_suppressed": 0,
    "corpse_seen_memory_suppressed": 0,
    "stale_corpse_removed": 0,
    "stale_corpse_seen_ignored": 0,
    "corpse_seen_alive_agent_ignored": 0,
    "valid_corpse_seen_knowledge_updates": 0,
    "hunt_evidence_upserts": 0,
}


def get_memory_metrics() -> dict[str, int]:
    """Return a snapshot of current memory write metrics."""
    return dict(_METRICS)


def reset_memory_metrics() -> None:
    """Reset all memory metrics to zero (useful in tests)."""
    for key in _METRICS:
        _METRICS[key] = 0


def record_stale_corpse_cleanup_metrics(cleanup_result: dict[str, Any] | None) -> None:
    """Merge stale-corpse cleanup counters into global memory metrics."""
    if not isinstance(cleanup_result, dict):
        return
    removed = int(
        cleanup_result.get("stale_corpse_removed")
        or cleanup_result.get("stale_corpses_removed")
        or 0
    )
    ignored = int(
        cleanup_result.get("stale_corpse_seen_ignored")
        or cleanup_result.get("stale_corpses_ignored")
        or 0
    )
    if removed > 0:
        _METRICS["stale_corpse_removed"] += removed
    if ignored > 0:
        _METRICS["stale_corpse_seen_ignored"] += ignored


# ── Summary / details sanitization ──────────────────────────────────────────


def _sanitize_record_payload(record: MemoryRecord, is_critical: bool = False) -> MemoryRecord:
    """Enforce length limits on summary and details fields.

    Critical records may have larger list limits.  Critical IDs (target_id etc.)
    are never truncated.  Returns a new MemoryRecord with capped fields.
    """
    list_limit = MEMORY_DETAILS_CRITICAL_LIST_MAX_ITEMS if is_critical else MEMORY_DETAILS_LIST_MAX_ITEMS
    summary = record.summary
    details = dict(record.details) if isinstance(record.details, dict) else {}
    changed = False

    if len(summary) > MEMORY_SUMMARY_MAX_CHARS:
        summary = summary[: MEMORY_SUMMARY_MAX_CHARS]
        _METRICS["memory_summary_truncations"] += 1
        changed = True

    for key, value in list(details.items()):
        if key in _CRITICAL_DETAIL_KEYS:
            continue
        if isinstance(value, str) and len(value) > MEMORY_DETAILS_STRING_MAX_CHARS:
            details[key] = value[: MEMORY_DETAILS_STRING_MAX_CHARS]
            _METRICS["memory_details_truncations"] += 1
            changed = True
        elif isinstance(value, list) and len(value) > list_limit:
            details[key] = value[:list_limit]
            _METRICS["memory_details_truncations"] += 1
            changed = True

    if not changed:
        return record
    return MemoryRecord(
        id=record.id,
        agent_id=record.agent_id,
        layer=record.layer,
        kind=record.kind,
        created_turn=record.created_turn,
        last_accessed_turn=record.last_accessed_turn,
        summary=summary,
        details=details,
        location_id=record.location_id,
        entity_ids=record.entity_ids,
        item_types=record.item_types,
        tags=record.tags,
        importance=record.importance,
        confidence=record.confidence,
        status=record.status,
        source=record.source,
    )


# ── Active-plan failure aggregation ─────────────────────────────────────────


def _make_failure_aggregate_key(effects: dict[str, Any]) -> tuple[str, str, str]:
    """Return (objective_key, step_kind, reason) as an aggregate signature."""
    return (
        str(effects.get("objective_key") or ""),
        str(effects.get("step_kind") or effects.get("step_kind_label") or ""),
        str(effects.get("reason") or ""),
    )


def _find_failure_aggregate(
    records: dict[str, Any],
    agg_key: tuple[str, str, str],
) -> dict[str, Any] | None:
    objective_key, step_kind, reason = agg_key
    for raw in records.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "") != "active_plan_failure_summary":
            continue
        d = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        if (
            str(d.get("objective_key") or "") == objective_key
            and str(d.get("step_kind") or "") == step_kind
            and str(d.get("reason") or "") == reason
        ):
            return raw
    return None


def _upsert_active_plan_failure_aggregate(
    *,
    agent_id: str,
    agent: dict[str, Any],
    effects: dict[str, Any],
    action_kind: str,
    world_turn: int,
    summary: str,
) -> None:
    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3.get("records", {})

    agg_key = _make_failure_aggregate_key(effects)

    # Enforce cap on failure aggregates.
    existing_agg_ids = [
        rid for rid, raw in records.items()
        if isinstance(raw, dict) and str(raw.get("kind") or "") == "active_plan_failure_summary"
    ]

    existing = _find_failure_aggregate(records, agg_key)
    if existing is not None:
        d = existing.setdefault("details", {})
        if action_kind == "active_plan_step_failed":
            d["failed_count"] = int(d.get("failed_count", 0)) + 1
        elif action_kind == "active_plan_repair_requested":
            d["repair_requested_count"] = int(d.get("repair_requested_count", 0)) + 1
        elif action_kind == "active_plan_aborted":
            d["aborted_count"] = int(d.get("aborted_count", 0)) + 1
        elif action_kind == "plan_monitor_abort":
            d["aborted_count"] = int(d.get("aborted_count", 0)) + 1
        elif action_kind in ("support_source_exhausted", "anomaly_search_exhausted",
                             "witness_source_exhausted", "no_tracks_found", "no_witnesses"):
            d["misc_count"] = int(d.get("misc_count", 0)) + 1
        d["last_turn"] = world_turn
        plan_id = effects.get("active_plan_id") or effects.get("plan_id") or ""
        if plan_id:
            d["last_plan_id"] = str(plan_id)
        existing["summary"] = summary
        _METRICS["memory_write_aggregated"] += 1
        return

    if len(existing_agg_ids) >= MAX_ACTIVE_PLAN_FAILURE_AGGREGATES:
        # Drop the aggregate and do nothing (budget exhausted).
        _METRICS["memory_write_discarded"] += 1
        return

    objective_key, step_kind, reason = agg_key
    init_counts: dict[str, Any] = {
        "objective_key": objective_key,
        "step_kind": step_kind,
        "reason": reason,
        "failed_count": 1 if action_kind == "active_plan_step_failed" else 0,
        "repair_requested_count": 1 if action_kind == "active_plan_repair_requested" else 0,
        "aborted_count": 1 if action_kind in ("active_plan_aborted", "plan_monitor_abort") else 0,
        "misc_count": 1 if action_kind not in (
            "active_plan_step_failed", "active_plan_repair_requested",
            "active_plan_aborted", "plan_monitor_abort",
        ) else 0,
        "first_turn": world_turn,
        "last_turn": world_turn,
        "memory_type": "decision",
        "action_kind": action_kind,
    }
    plan_id = effects.get("active_plan_id") or effects.get("plan_id") or ""
    if plan_id:
        init_counts["last_plan_id"] = str(plan_id)

    agg_record = MemoryRecord(
        id="mem_pf_" + uuid.uuid4().hex[:10],
        agent_id=agent_id,
        layer=LAYER_GOAL,
        kind="active_plan_failure_summary",
        created_turn=world_turn,
        last_accessed_turn=None,
        summary=summary,
        details=init_counts,
        location_id=None,
        entity_ids=(),
        tags=("active_plan", "failure", "aggregate"),
        importance=0.45,
        confidence=0.9,
        source="inferred",
    )
    stored = add_memory_record(agent, agg_record)
    if stored:
        _METRICS["memory_write_aggregated"] += 1
    else:
        _METRICS["memory_write_discarded"] += 1


# ── Objective-decision dedup ─────────────────────────────────────────────────


def _is_urgent_objective_decision(effects: dict[str, Any]) -> bool:
    """Return True if this objective_decision is too important to aggregate."""
    changed_from = effects.get("changed_from") or effects.get("previous_objective_key")
    changed_to = effects.get("changed_to") or effects.get("new_objective_key")
    if changed_from and changed_to and changed_from != changed_to:
        return True
    priority = str(effects.get("priority") or "").lower()
    if priority in ("urgent", "critical", "emergency"):
        return True
    reason_class = str(effects.get("reason_class") or effects.get("intent_kind") or "").lower()
    if any(tag in reason_class for tag in ("survival", "emission", "combat", "death", "target")):
        return True
    return False


def _find_objective_decision_aggregate(
    records: dict[str, Any],
    objective_key: str,
    intent_kind: str,
    world_turn: int,
) -> dict[str, Any] | None:
    cutoff = world_turn - OBJECTIVE_DECISION_DEDUP_WINDOW_TURNS
    for raw in records.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "") != "objective_decision_summary":
            continue
        d = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        if (
            str(d.get("objective_key") or "") != objective_key
            or str(d.get("intent_kind") or "") != intent_kind
        ):
            continue
        last = int(d.get("last_turn", 0))
        if last < cutoff:
            continue
        return raw
    return None


def _upsert_objective_decision_aggregate(
    *,
    agent_id: str,
    agent: dict[str, Any],
    effects: dict[str, Any],
    world_turn: int,
    summary: str,
) -> None:
    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3.get("records", {})
    objective_key = str(effects.get("objective_key") or "")
    intent_kind = str(effects.get("intent_kind") or effects.get("reason_class") or "routine")

    existing = _find_objective_decision_aggregate(records, objective_key, intent_kind, world_turn)
    if existing is not None:
        d = existing.setdefault("details", {})
        d["decision_count"] = int(d.get("decision_count", 1)) + 1
        d["last_turn"] = world_turn
        existing["summary"] = summary
        _METRICS["memory_write_aggregated"] += 1
        return

    agg_record = MemoryRecord(
        id="mem_od_" + uuid.uuid4().hex[:10],
        agent_id=agent_id,
        layer=LAYER_GOAL,
        kind="objective_decision_summary",
        created_turn=world_turn,
        last_accessed_turn=None,
        summary=summary,
        details={
            "objective_key": objective_key,
            "intent_kind": intent_kind,
            "decision_count": 1,
            "first_turn": world_turn,
            "last_turn": world_turn,
            "memory_type": "decision",
            "action_kind": "objective_decision",
        },
        location_id=None,
        entity_ids=(),
        tags=("objective", "decision", "aggregate"),
        importance=0.4,
        confidence=0.85,
        source="inferred",
    )
    stored = add_memory_record(agent, agg_record)
    if stored:
        _METRICS["memory_write_aggregated"] += 1
    else:
        _METRICS["memory_write_discarded"] += 1


def _dedup_signature(raw: MemoryRecord | dict[str, Any]) -> tuple[Any, ...] | None:
    details = raw.details if isinstance(raw, MemoryRecord) else raw.get("details", {})
    if not isinstance(details, dict):
        details = {}
    kind = str(raw.kind if isinstance(raw, MemoryRecord) else raw.get("kind") or "")
    location_id = str(
        (raw.location_id if isinstance(raw, MemoryRecord) else raw.get("location_id"))
        or details.get("location_id")
        or ""
    )
    if kind == "travel_hop":
        return (
            kind,
            str(details.get("from_location_id") or details.get("from_loc") or ""),
            str(details.get("to_location_id") or details.get("to_loc") or location_id),
        )
    if kind in {
        "trader_visited",
        "witness_source_exhausted",
        "support_source_exhausted",
        "anomaly_search_exhausted",
        "no_witnesses",
        "no_tracks_found",
    }:
        return (
            kind,
            location_id,
            str(details.get("target_id") or ""),
            str(details.get("trader_id") or ""),
            str(details.get("objective_key") or ""),
        )
    if kind in {"corpse_seen", "target_corpse_seen", "target_corpse_reported"}:
        return (
            kind,
            location_id,
            str(details.get("target_id") or details.get("dead_agent_id") or ""),
            str(details.get("corpse_id") or ""),
            str(details.get("source_agent_id") or ""),
        )
    if kind == "trade_sell_failed":
        return (
            kind,
            location_id,
            str(details.get("trader_id") or ""),
            str(sorted(details.get("item_types") or [])),
        )
    return None


def _find_recent_dedup_record(
    agent: dict[str, Any],
    signature: tuple[Any, ...],
    world_turn: int,
) -> dict[str, Any] | None:
    """Return the most recent existing record that matches the dedup signature, or None."""
    mem_v3 = agent.get("memory_v3")
    if not isinstance(mem_v3, dict):
        return None
    records: dict[str, Any] = mem_v3.get("records", {})
    cutoff = world_turn - MEMORY_EVENT_DEDUP_WINDOW_TURNS

    best: dict[str, Any] | None = None
    best_turn = -1
    for raw in records.values():
        if _dedup_signature(raw) != signature:
            continue
        rec_turn = int(raw.get("created_turn", 0))
        if rec_turn < cutoff:
            continue
        if rec_turn > best_turn:
            best_turn = rec_turn
            best = raw
    return best


def _entity_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    base = max(1, min(len(left), len(right)))
    return len(left.intersection(right)) / float(base)


def _find_stalkers_seen_match(
    *,
    records: dict[str, Any],
    location_id: str,
    world_turn: int,
    entity_set: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    cutoff = world_turn - STALKERS_SEEN_DEDUP_WINDOW_TURNS
    best_episodic: dict[str, Any] | None = None
    best_semantic: dict[str, Any] | None = None
    best_ep_turn = -1
    best_sem_turn = -1
    for raw in records.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("location_id") or "") != location_id:
            continue
        rec_turn = int(raw.get("created_turn", 0))
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        last_turn = int(details.get("last_seen_turn", rec_turn))
        if last_turn < cutoff:
            continue
        raw_entities = raw.get("entity_ids") or []
        rec_entities = {str(entity_id) for entity_id in raw_entities if entity_id}
        overlap = _entity_overlap_ratio(entity_set, rec_entities) if rec_entities else 0.0
        same_or_overlap = rec_entities == entity_set or overlap >= 0.7
        if not same_or_overlap:
            continue
        kind = str(raw.get("kind") or "")
        if kind == "stalkers_seen" and last_turn >= best_ep_turn:
            best_ep_turn = last_turn
            best_episodic = raw
        elif kind == "semantic_stalkers_seen" and last_turn >= best_sem_turn:
            best_sem_turn = last_turn
            best_semantic = raw
    return best_episodic, best_semantic


def _update_stalkers_seen_record(
    *,
    raw: dict[str, Any],
    world_turn: int,
    entity_ids: tuple[str, ...],
    seen_names: list[str],
) -> None:
    details = raw.setdefault("details", {})
    first_seen_turn = int(details.get("first_seen_turn", raw.get("created_turn", world_turn)))
    details["first_seen_turn"] = first_seen_turn
    details["last_seen_turn"] = world_turn
    details["times_seen"] = int(details.get("times_seen", 1)) + 1
    details["last_observed_group_size"] = len(entity_ids)
    details["unique_entity_count"] = len(entity_ids)
    if seen_names:
        prev_names = [str(name) for name in (details.get("seen_names") or []) if name]
        merged_names = list(dict.fromkeys(prev_names + seen_names))
        details["seen_names"] = merged_names
        details["names"] = merged_names
    details["observed"] = "stalkers"
    raw["entity_ids"] = list(entity_ids)
    raw["confidence"] = min(1.0, float(raw.get("confidence", 0.7)) + 0.01)


def _upsert_semantic_stalkers_seen(
    *,
    agent_id: str,
    agent: dict[str, Any],
    world_turn: int,
    location_id: str,
    entity_ids: tuple[str, ...],
    seen_names: list[str],
    base_summary: str,
    existing_semantic: dict[str, Any] | None,
) -> None:
    if existing_semantic is not None:
        _update_stalkers_seen_record(
            raw=existing_semantic,
            world_turn=world_turn,
            entity_ids=entity_ids,
            seen_names=seen_names,
        )
        existing_semantic["layer"] = LAYER_SEMANTIC
        existing_semantic["kind"] = "semantic_stalkers_seen"
        existing_semantic["summary"] = base_summary
        existing_semantic["tags"] = list(dict.fromkeys(list(existing_semantic.get("tags", [])) + ["social", "location_population", "stalkers_seen"]))
        return

    semantic_record = MemoryRecord(
        id="mem_sem_stalkers_" + uuid.uuid4().hex[:10],
        agent_id=agent_id,
        layer=LAYER_SEMANTIC,
        kind="semantic_stalkers_seen",
        created_turn=world_turn,
        last_accessed_turn=None,
        summary=base_summary,
        details={
            "first_seen_turn": world_turn,
            "last_seen_turn": world_turn,
            "times_seen": 1,
            "seen_names": list(dict.fromkeys(seen_names)),
            "names": list(dict.fromkeys(seen_names)),
            "unique_entity_count": len(entity_ids),
            "last_observed_group_size": len(entity_ids),
            "observed": "stalkers",
            "memory_type": "observation",
            "action_kind": "stalkers_seen",
        },
        location_id=location_id,
        entity_ids=entity_ids,
        tags=("social", "location_population", "stalkers_seen"),
        importance=0.65,
        confidence=0.72,
        source="inferred",
    )
    if not add_memory_record(agent, semantic_record):
        _METRICS["memory_write_discarded"] += 1


def _trim_stalkers_seen_per_location(
    *,
    records: dict[str, Any],
    location_id: str,
) -> None:
    episodic: list[tuple[str, int]] = []
    for record_id, raw in records.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "") != "stalkers_seen":
            continue
        if str(raw.get("location_id") or "") != location_id:
            continue
        episodic.append((record_id, int(raw.get("created_turn", 0))))
    if len(episodic) <= STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION:
        return
    episodic.sort(key=lambda pair: pair[1])
    to_archive = episodic[: max(0, len(episodic) - STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION)]
    for record_id, _ in to_archive:
        raw = records.get(record_id)
        if isinstance(raw, dict):
            raw["status"] = "archived"


def _handle_stalkers_seen_event(
    *,
    agent_id: str,
    agent: dict[str, Any],
    record: MemoryRecord,
    world_turn: int,
) -> bool:
    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3.get("records", {})
    location_id = str(record.location_id or "")
    if not location_id:
        return False

    seen_names = [str(name) for name in (record.details.get("names") or []) if name]
    entity_set = {str(entity_id) for entity_id in record.entity_ids if entity_id}
    _, semantic_match = _find_stalkers_seen_match(
        records=records,
        location_id=location_id,
        world_turn=world_turn,
        entity_set=entity_set,
    )

    _upsert_semantic_stalkers_seen(
        agent_id=agent_id,
        agent=agent,
        world_turn=world_turn,
        location_id=location_id,
        entity_ids=record.entity_ids,
        seen_names=seen_names,
        base_summary=record.summary,
        existing_semantic=semantic_match,
    )

    stored = add_memory_record(agent, record)
    _trim_stalkers_seen_per_location(records=records, location_id=location_id)
    return stored


def _upsert_semantic_route_traveled(
    *,
    agent_id: str,
    agent: dict[str, Any],
    record: MemoryRecord,
    world_turn: int,
) -> bool:
    details = record.details if isinstance(record.details, dict) else {}
    from_location_id = str(details.get("from_location_id") or details.get("from_loc") or "")
    to_location_id = str(details.get("to_location_id") or details.get("to_loc") or record.location_id or "")
    if not from_location_id or not to_location_id:
        return False

    mem_v3 = ensure_memory_v3(agent)
    records: dict[str, Any] = mem_v3.get("records", {})
    existing_semantic: dict[str, Any] | None = None
    for raw in records.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "") != "semantic_route_traveled":
            continue
        raw_details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        if str(raw_details.get("from_location_id") or "") != from_location_id:
            continue
        if str(raw_details.get("to_location_id") or "") != to_location_id:
            continue
        existing_semantic = raw
        break

    if existing_semantic is not None:
        raw_details = existing_semantic.setdefault("details", {})
        raw_details["times_traveled"] = int(raw_details.get("times_traveled", 1)) + 1
        raw_details["last_traveled_turn"] = world_turn
        existing_semantic["confidence"] = min(1.0, float(existing_semantic.get("confidence", 0.72)) + 0.01)
        return True

    semantic_record = MemoryRecord(
        id="mem_sem_route_" + uuid.uuid4().hex[:10],
        agent_id=agent_id,
        layer=LAYER_SPATIAL,
        kind="semantic_route_traveled",
        created_turn=world_turn,
        last_accessed_turn=None,
        summary=record.summary,
        details={
            "from_location_id": from_location_id,
            "to_location_id": to_location_id,
            "times_traveled": 1,
            "last_traveled_turn": world_turn,
            "known_safe": True,
            "known_risky": False,
            "memory_type": "action",
            "action_kind": "travel_hop",
        },
        location_id=to_location_id,
        tags=("route", "travel", "spatial"),
        importance=0.6,
        confidence=0.72,
        source="inferred",
    )
    if not add_memory_record(agent, semantic_record):
        _METRICS["memory_write_discarded"] += 1
    return True


def _upsert_knowledge_from_event(
    *,
    agent: dict[str, Any],
    kind: str,
    effects: dict[str, Any],
    world_turn: int,
) -> dict[str, Any]:
    """PR3: Upsert knowledge_v1 tables from memory event effects.

    Called in the knowledge_upsert policy block for events that carry NPC data.
    Does NOT write any episodic records — that is handled by the caller.
    """
    (
        _upsert_npc,
        _upsert_corpse,
        _upsert_hunt_evidence,
        _upsert_location,
        _upsert_trader,
    ) = _get_knowledge_upserts()

    result: dict[str, Any] = {
        "changed_major": False,
        "changed_minor": False,
        "created": False,
        "reasons": [],
        "ignored": False,
    }

    def _merge_update(update: dict[str, Any] | None) -> None:
        if not isinstance(update, dict):
            return
        result["changed_major"] = bool(result["changed_major"] or update.get("changed_major", False))
        result["changed_minor"] = bool(result["changed_minor"] or update.get("changed_minor", False))
        result["created"] = bool(result["created"] or update.get("created", False))
        reason = str(update.get("reason") or "")
        if reason:
            result["reasons"].append(reason)

    if kind == "stalkers_seen":
        # Effects: observed="stalkers", location_id, names, seen_agent_ids (if PR3 enhanced)
        loc_id = str(effects.get("location_id") or "")
        names: list[str] = [str(n) for n in (effects.get("names") or []) if n]
        seen_ids: list[str] = [str(i) for i in (effects.get("seen_agent_ids") or effects.get("entity_ids") or []) if i]
        observed_agents = effects.get("observed_agents") if isinstance(effects.get("observed_agents"), dict) else {}
        # Build id→name mapping if both lists exist and have same length
        id_name_map: dict[str, str] = {}
        if seen_ids and len(seen_ids) == len(names):
            id_name_map = dict(zip(seen_ids, names))
        for npc_id in seen_ids:
            _merge_update(_upsert_npc(
                agent,
                other_agent_id=npc_id,
                name=id_name_map.get(npc_id),
                location_id=loc_id or None,
                world_turn=world_turn,
                observed_agent=observed_agents.get(npc_id) if isinstance(observed_agents.get(npc_id), dict) else None,
                source="direct_observation",
                confidence=0.85,
                death_status={"is_alive": True},
            ))

    elif kind == "target_seen":
        target_id = str(effects.get("target_id") or effects.get("target_agent_id") or "")
        if not target_id:
            return result
        target_name = str(effects.get("target_name") or effects.get("target_agent_name") or "")
        loc_id = str(effects.get("location_id") or "")
        _merge_update(_upsert_npc(
            agent,
            other_agent_id=target_id,
            name=target_name or None,
            location_id=loc_id or None,
            world_turn=world_turn,
            source="direct_observation",
            confidence=0.95,
            death_status={"is_alive": True},
        ))
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=target_id,
            kind="target_seen",
            location_id=loc_id or None,
            world_turn=world_turn,
            confidence=float(effects.get("confidence", 0.95)),
            source="direct_observation",
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "target_last_known_location":
        target_id = str(effects.get("target_id") or "")
        if not target_id:
            return result
        loc_id = str(effects.get("location_id") or "")
        _merge_update(_upsert_npc(
            agent,
            other_agent_id=target_id,
            name=str(effects.get("target_name") or "") or None,
            location_id=loc_id or None,
            world_turn=world_turn,
            source="target_intel",
            confidence=0.85,
        ))
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=target_id,
            kind="target_last_known_location",
            location_id=loc_id or None,
            world_turn=world_turn,
            confidence=float(effects.get("confidence", 0.85)),
            source="target_intel",
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "corpse_seen":
        dead_agent_id = str(effects.get("dead_agent_id") or effects.get("target_id") or "")
        if not dead_agent_id:
            return result
        state_agents = effects.get("state_agents")
        dead_agent_alive = bool(effects.get("dead_agent_is_alive", False))
        if isinstance(state_agents, dict):
            raw_alive = state_agents.get(dead_agent_id)
            if isinstance(raw_alive, dict):
                dead_agent_alive = bool(raw_alive.get("is_alive", True))
            elif isinstance(raw_alive, bool):
                dead_agent_alive = raw_alive
        if dead_agent_alive:
            _METRICS["stale_corpse_seen_ignored"] += 1
            _METRICS["corpse_seen_alive_agent_ignored"] += 1
            result["ignored"] = True
            return result
        dead_name = str(effects.get("dead_agent_name") or effects.get("target_name") or "") or None
        loc_id = str(effects.get("location_id") or "")
        death_cause = str(effects.get("death_cause") or "") or None
        killer_id = str(effects.get("killer_id") or "") or None
        corpse_id = str(effects.get("corpse_id") or f"corpse_{dead_agent_id}_{world_turn}")
        _merge_update(_upsert_corpse(
            agent,
            corpse_id=corpse_id,
            dead_agent_id=dead_agent_id,
            dead_agent_name=dead_name,
            location_id=loc_id or None,
            world_turn=world_turn,
            death_cause=death_cause,
            killer_id=killer_id,
            source_agent_id=str(effects.get("source_agent_id") or "") or None,
            confidence=float(effects.get("confidence", 0.95)),
            directly_observed=bool(effects.get("directly_observed", True)),
        ))
        death_status: dict[str, Any] = {"is_alive": False}
        if death_cause:
            death_status["death_cause"] = death_cause
        if killer_id:
            death_status["killer_id"] = killer_id
        death_status["corpse_id"] = corpse_id
        death_status["reported_corpse_location_id"] = loc_id or None
        _merge_update(_upsert_npc(
            agent,
            other_agent_id=dead_agent_id,
            name=dead_name,
            location_id=loc_id or None,
            world_turn=world_turn,
            source="corpse_seen",
            confidence=0.95,
            death_status=death_status,
        ))
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=dead_agent_id,
            kind="corpse_seen",
            location_id=loc_id or None,
            world_turn=world_turn,
            confidence=float(effects.get("confidence", 0.95)),
            source="corpse_seen",
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind in {"target_corpse_seen", "target_corpse_reported"}:
        target_id = str(effects.get("target_id") or "")
        if not target_id:
            return result
        target_name = str(effects.get("target_name") or "") or None
        # target_corpse_seen: direct observation; target_corpse_reported: witness
        if kind == "target_corpse_seen":
            loc_id = str(effects.get("corpse_location_id") or effects.get("location_id") or "")
            src = "corpse_seen"
            conf = 0.95
            death_status = {
                "is_alive": False,
                "death_reported": True,
                "death_directly_confirmed": True,
                "reported_corpse_location_id": loc_id or None,
            }
        else:
            loc_id = str(effects.get("reported_corpse_location_id") or effects.get("location_id") or "")
            src = "witness_report"
            conf = float(effects.get("confidence", 0.75))
            death_status = {
                "is_alive": False,
                "death_reported": True,
                "death_directly_confirmed": False,
                "reported_corpse_location_id": loc_id or None,
            }
        if str(effects.get("death_cause") or ""):
            death_status["death_cause"] = effects.get("death_cause")
        if str(effects.get("killer_id") or ""):
            death_status["killer_id"] = effects.get("killer_id")
        corpse_id = str(effects.get("corpse_id") or f"corpse_{target_id}_{world_turn}")
        death_status["corpse_id"] = corpse_id
        _merge_update(_upsert_corpse(
            agent,
            corpse_id=corpse_id,
            dead_agent_id=target_id,
            dead_agent_name=target_name,
            location_id=loc_id or None,
            world_turn=world_turn,
            death_cause=str(effects.get("death_cause") or "") or None,
            killer_id=str(effects.get("killer_id") or "") or None,
            source_agent_id=str(effects.get("source_agent_id") or "") or None,
            confidence=conf,
            directly_observed=bool(kind == "target_corpse_seen"),
        ))
        _merge_update(_upsert_npc(
            agent,
            other_agent_id=target_id,
            name=target_name,
            location_id=loc_id or None,
            world_turn=world_turn,
            source=src,
            confidence=conf,
            death_status=death_status,
        ))
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=target_id,
            kind=kind,
            location_id=loc_id or None,
            world_turn=world_turn,
            confidence=conf,
            source=src,
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "location_visited":
        location_id = str(
            effects.get("location_id")
            or effects.get("to_location")
            or effects.get("destination")
            or ""
        )
        if not location_id:
            return result
        _upsert_location(
            agent,
            location_id=location_id,
            name=str(effects.get("location_name") or effects.get("name") or "") or None,
            world_turn=world_turn,
            safe_shelter=bool(effects.get("safe_shelter") or effects.get("is_shelter")),
            confidence=float(effects.get("confidence", 1.0)),
        )

    elif kind == "trader_seen":
        trader_id = str(
            effects.get("trader_id")
            or effects.get("agent_id")
            or effects.get("other_agent_id")
            or ""
        )
        if not trader_id:
            return result
        _upsert_trader(
            agent,
            trader_id=trader_id,
            location_id=str(
                effects.get("location_id")
                or effects.get("to_location")
                or effects.get("destination")
                or ""
            ) or None,
            world_turn=world_turn,
            name=str(effects.get("trader_name") or effects.get("name") or "") or None,
            buys_artifacts=bool(effects.get("buys_artifacts")),
            sells_food=bool(effects.get("sells_food")),
            sells_drink=bool(effects.get("sells_drink")),
            confidence=float(effects.get("confidence", 1.0)),
        )

    elif kind == "target_not_found":
        target_id = str(effects.get("target_id") or "")
        if not target_id:
            return result
        loc_id = str(effects.get("location_id") or "")
        if not loc_id:
            return result
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=target_id,
            kind="target_not_found",
            location_id=loc_id,
            world_turn=world_turn,
            confidence=float(effects.get("confidence", 0.75)),
            source=str(effects.get("source") or "observation"),
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "retreat_observed":
        # Hunter directly observed an enemy fleeing combat.  If the subject is
        # the agent's kill target, update hunt_evidence.last_seen to the
        # destination so the hunter can pursue.
        kill_target_id = str(agent.get("kill_target_id") or "")
        subject_id = str(effects.get("subject") or effects.get("target_id") or "")
        if not subject_id or subject_id != kill_target_id:
            return result
        to_loc = str(effects.get("to_location") or effects.get("to_location_id") or "")
        if not to_loc:
            return result
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=subject_id,
            kind="target_last_known_location",
            location_id=to_loc,
            world_turn=world_turn,
            confidence=0.85,
            source="combat_retreat_observed",
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "target_death_confirmed":
        target_id = str(effects.get("target_id") or "")
        if not target_id:
            return result
        target_name = str(effects.get("target_name") or "") or None
        loc_id = str(
            effects.get("location_id")
            or effects.get("corpse_location_id")
            or ""
        )
        _merge_update(_upsert_npc(
            agent,
            other_agent_id=target_id,
            name=target_name,
            location_id=loc_id or None,
            world_turn=world_turn,
            source="direct_observation",
            confidence=1.0,
            death_status={
                "is_alive": False,
                "confirmed_dead": True,
                "death_directly_confirmed": True,
                "death_cause": str(effects.get("target_death_cause") or effects.get("death_cause") or "") or None,
                "killer_id": str(effects.get("killer_id") or "") or None,
                "reported_corpse_location_id": loc_id or None,
            },
        ))
        _merge_update(_upsert_hunt_evidence(
            agent,
            target_id=target_id,
            kind="target_death_confirmed",
            location_id=loc_id or None,
            world_turn=world_turn,
            confidence=1.0,
            source="combat",
            details=effects,
        ))
        _METRICS["hunt_evidence_upserts"] += 1

    elif kind == "travel_hop":
        from_location_id = str(
            effects.get("from_location_id")
            or effects.get("from_location")
            or effects.get("from_loc")
            or ""
        )
        to_location_id = str(
            effects.get("to_location_id")
            or effects.get("to_location")
            or effects.get("to_loc")
            or effects.get("destination")
            or effects.get("location_id")
            or ""
        )
        if from_location_id:
            _upsert_location(
                agent,
                location_id=from_location_id,
                name=None,
                world_turn=world_turn,
                confidence=float(effects.get("confidence", 0.8)),
            )
        if to_location_id:
            _upsert_location(
                agent,
                location_id=to_location_id,
                name=None,
                world_turn=world_turn,
                confidence=float(effects.get("confidence", 0.85)),
            )
    return result


def should_write_observation_milestone(
    *,
    event_kind: str,
    update_result: dict[str, Any],
    agent: dict[str, Any],
    effects: dict[str, Any],
    world_turn: int = 0,  # reserved for future time-based predicates; unused for now
) -> bool:
    _ = world_turn  # reserved for future time-based milestone predicates
    if bool(agent.get("force_observation_memory_records", False)):
        return True

    changed_major = bool(update_result.get("changed_major", False))
    created = bool(update_result.get("created", False))
    reasons = {str(reason) for reason in (update_result.get("reasons") or []) if reason}
    kill_target_id = str(agent.get("kill_target_id") or "")
    target_id = str(effects.get("target_id") or effects.get("dead_agent_id") or "")
    is_kill_target = bool(kill_target_id and target_id and kill_target_id == target_id)

    if event_kind in {"stalkers_seen", "trader_seen"}:
        return False

    if event_kind == "target_seen":
        if OBSERVATION_MEMORY_COMPAT_MODE:
            return True
        # New evidence for the current kill target (first encounter or major update).
        if is_kill_target and (created or changed_major):
            return True
        return bool(changed_major and "death_status_changed" in reasons)

    if event_kind == "target_last_known_location":
        if OBSERVATION_MEMORY_COMPAT_MODE:
            return True
        return False

    if event_kind == "target_not_found":
        # Negative search evidence goes to hunt_evidence.failed_search_locations only.
        return False

    if event_kind == "corpse_seen":
        if bool(update_result.get("ignored", False)):
            return False
        if not OBSERVATION_MEMORY_COMPAT_MODE:
            return False
        return created

    if event_kind == "target_corpse_seen":
        if OBSERVATION_MEMORY_COMPAT_MODE:
            return True
        return bool(is_kill_target)

    if event_kind == "target_corpse_reported":
        if OBSERVATION_MEMORY_COMPAT_MODE:
            return True
        return False

    return True


def _should_write_observation_milestone(
    *,
    agent: dict[str, Any],
    kind: str,
    effects: dict[str, Any],
    knowledge_update: dict[str, Any],
) -> bool:
    return should_write_observation_milestone(
        event_kind=kind,
        update_result=knowledge_update,
        agent=agent,
        effects=effects,
        world_turn=int(effects.get("world_turn") or 0),
    )


def _record_suppressed_observation_metric(kind: str) -> None:
    if kind == "stalkers_seen":
        _METRICS["stalkers_seen_memory_suppressed"] += 1
    elif kind in {"corpse_seen", "target_corpse_seen", "target_corpse_reported"}:
        _METRICS["corpse_seen_memory_suppressed"] += 1


def write_memory_event_to_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    legacy_entry: dict[str, Any],
    world_turn: int,
    context_id: str = "default",
    cold_store_enabled: bool | None = None,
    redis_client: Any | None = None,
) -> None:
    """Convert a single memory event entry into a MemoryRecord and store it in memory_v3.

    Routing is determined by MEMORY_EVENT_POLICY:
      trace_only        → skipped entirely (debug trace only)
      memory_aggregate  → update one aggregate record (active_plan failures, noisy obs)
      knowledge_upsert  → update knowledge/semantic aggregate; stalkers_seen/travel_hop have own paths
      memory_critical   → always write a full episodic record; never deduplicated
      memory / missing  → default write with dedup where applicable
    """
    _METRICS["memory_write_attempts"] += 1

    effects: dict[str, Any] = legacy_entry.get("effects", {})
    action_kind = str(effects.get("action_kind", ""))
    obs_type = str(effects.get("observed", ""))

    effective_kind = action_kind or obs_type
    policy = resolve_memory_event_policy(effective_kind, effects)

    # trace_only → skip entirely.
    if policy == "trace_only" or action_kind in _SKIP_ACTION_KINDS:
        _METRICS["memory_write_trace_only"] += 1
        return

    _cold_enabled = bool(cold_store_enabled) or bool(agent.get("memory_ref"))
    _cold_redis_client = redis_client
    if _cold_enabled and _cold_redis_client is None:
        try:
            from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                get_zone_cold_memory_redis_client as _resolve_cold_redis_client,
            )

            _cold_redis_client = _resolve_cold_redis_client(None)
        except Exception:
            _cold_redis_client = None

    def _sync_cold_after_mutation() -> None:
        if not _cold_enabled:
            return
        try:
            from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                mark_agent_memory_dirty as _mark_dirty,
                refresh_agent_memory_summary as _refresh_summary,
            )
            _refresh_summary(agent, dirty=True, is_loaded=True)
            _mark_dirty(agent)
        except Exception as exc:
            from app.games.zone_stalkers.memory.cold_store import record_agent_cold_memory_error  # noqa: PLC0415

            record_agent_cold_memory_error(agent, "save_refresh_failed", exc)

    if _cold_enabled:
        try:
            from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                agent_memory_load_failed as _agent_load_failed,
                ensure_agent_memory_loaded as _ensure_cold_mem_loaded,
                migrate_agent_memory_to_cold_store as _migrate_to_cold,
                record_agent_cold_memory_error as _record_cold_error,
            )
            if agent.get("memory_ref"):
                _ensure_cold_mem_loaded(
                    context_id=context_id,
                    agent_id=str(agent_id),
                    agent=agent,
                    redis_client=_cold_redis_client,
                )
            elif bool(cold_store_enabled) and (
                isinstance(agent.get("memory_v3"), dict) or isinstance(agent.get("knowledge_v1"), dict)
            ):
                _migrate_to_cold(
                    context_id=context_id,
                    agent_id=str(agent_id),
                    agent=agent,
                    redis_client=_cold_redis_client,
                )
                _ensure_cold_mem_loaded(
                    context_id=context_id,
                    agent_id=str(agent_id),
                    agent=agent,
                    redis_client=_cold_redis_client,
                )
            if _agent_load_failed(agent):
                _METRICS["memory_write_discarded"] += 1
                return
        except Exception as exc:
            _record_cold_error(agent, "load_failed", exc)
            _METRICS["memory_write_discarded"] += 1
            return

    # memory_aggregate → route to failure/noisy aggregate, never episodic.
    # Exception: plan_monitor_abort for sleep produces a meaningful sleep_interrupted record.
    if policy == "memory_aggregate" and not (
        action_kind == "plan_monitor_abort"
        and str(effects.get("scheduled_action_type", "")) == "sleep"
    ):
        summary = str(legacy_entry.get("summary") or legacy_entry.get("title") or effective_kind)
        _upsert_active_plan_failure_aggregate(
            agent_id=agent_id,
            agent=agent,
            effects=effects,
            action_kind=effective_kind,
            world_turn=world_turn,
            summary=summary,
        )
        _sync_cold_after_mutation()
        return

    record = _map_event_to_record(
        agent_id=agent_id,
        agent=agent,
        entry=legacy_entry,
        world_turn=world_turn,
    )
    if record is None:
        _METRICS["memory_write_discarded"] += 1
        return

    # trade_sell_failed: inject cooldown_until_turn into details if not already present
    if record.kind == "trade_sell_failed":
        _tsf_details = dict(record.details)
        _tsf_details.setdefault("cooldown_until_turn", world_turn + TRADE_SELL_FAILED_COOLDOWN_TURNS)
        record = MemoryRecord(
            id=record.id,
            agent_id=record.agent_id,
            layer=record.layer,
            kind=record.kind,
            created_turn=record.created_turn,
            last_accessed_turn=record.last_accessed_turn,
            summary=record.summary,
            details=_tsf_details,
            location_id=record.location_id,
            entity_ids=record.entity_ids,
            item_types=record.item_types,
            tags=record.tags,
            importance=record.importance,
            confidence=record.confidence,
            emotional_weight=record.emotional_weight,
            decay_rate=record.decay_rate,
            status=record.status,
            source=record.source,
            evidence_refs=record.evidence_refs,
            world_time=record.world_time,
        )

    # objective_decision — routine (non-urgent) decisions are aggregated into a
    # summary record so repeated same-objective turns don't spam memory_v3.
    # Urgent decisions (objective changed, survival priority) fall through to the
    # normal episodic write so that gameplay code can always retrieve them.
    if record.kind == "objective_decision":
        summary = str(legacy_entry.get("summary") or legacy_entry.get("title") or "objective_decision")
        if not _is_urgent_objective_decision(effects):
            _upsert_objective_decision_aggregate(
                agent_id=agent_id,
                agent=agent,
                effects=effects,
                world_turn=world_turn,
                summary=summary,
            )
            _sync_cold_after_mutation()
            return
        # Urgent decision falls through to normal episodic write below.

    # knowledge_only / knowledge_milestone: PR10 strict knowledge-first observations.
    if policy in {"knowledge_only", "knowledge_milestone"}:
        _METRICS["memory_write_knowledge_upserts"] += 1
        _METRICS["knowledge_upsert_attempts"] += 1
        knowledge_update = _upsert_knowledge_from_event(
            agent=agent,
            kind=record.kind,
            effects=record.details if isinstance(record.details, dict) else {},
            world_turn=world_turn,
        )
        if knowledge_update.get("changed_major", False):
            _METRICS["knowledge_upsert_major_updates"] += 1
        elif knowledge_update.get("changed_minor", False):
            _METRICS["knowledge_upsert_minor_refreshes"] += 1
        _sync_cold_after_mutation()

        if record.kind == "corpse_seen" and not bool(knowledge_update.get("ignored", False)):
            _METRICS["valid_corpse_seen_knowledge_updates"] += 1

        if record.kind == "travel_hop":
            _upsert_semantic_route_traveled(
                agent_id=agent_id,
                agent=agent,
                record=record,
                world_turn=world_turn,
            )
            # Fall through to write episodic record so gameplay queries still work.
        elif policy == "knowledge_only":
            _METRICS["knowledge_only_events"] += 1
            if record.kind in {
                "stalkers_seen",
                "target_seen",
                "target_last_known_location",
                "corpse_seen",
                "target_corpse_seen",
                "target_corpse_reported",
                "trader_seen",
            }:
                _record_suppressed_observation_metric(record.kind)
            return
        elif KNOWLEDGE_FIRST_OBSERVATIONS_ENABLED and record.kind in {
            "stalkers_seen",
            "target_seen",
            "target_last_known_location",
            "corpse_seen",
            "target_corpse_seen",
            "target_corpse_reported",
            "trader_seen",
        }:
            if not should_write_observation_milestone(
                event_kind=record.kind,
                update_result=knowledge_update,
                agent=agent,
                effects=record.details if isinstance(record.details, dict) else {},
                world_turn=world_turn,
            ):
                _METRICS["knowledge_only_events"] += 1
                _record_suppressed_observation_metric(record.kind)
                return
            _METRICS["observation_memory_milestones_written"] += 1

    # Stalkers_seen from obs_type="stalkers" path also handled here.
    if record.kind == "stalkers_seen" and policy != "knowledge_upsert":
        if _handle_stalkers_seen_event(
            agent_id=agent_id,
            agent=agent,
            record=record,
            world_turn=world_turn,
        ):
            _METRICS["memory_write_written"] += 1
            _sync_cold_after_mutation()
            return

    if record.kind == "travel_hop" and policy != "knowledge_upsert":
        _upsert_semantic_route_traveled(
            agent_id=agent_id,
            agent=agent,
            record=record,
            world_turn=world_turn,
        )

    # Dedup low-value repeated observations within the window.
    if record.kind in _DEDUP_KINDS and record.kind not in _NO_DEDUP_KINDS:
        signature = _dedup_signature(record)
        existing = _find_recent_dedup_record(agent, signature, world_turn) if signature is not None else None
        if existing is not None:
            # Update in-place instead of appending a new record.
            details = existing.setdefault("details", {})
            incoming_details = record.details if isinstance(record.details, dict) else {}
            details["times_seen"] = int(details.get("times_seen", 1)) + 1
            details["last_seen_turn"] = world_turn
            for key in ("cooldown_until_turn", "location_id", "target_id", "objective_key", "source_kind"):
                if key in incoming_details:
                    details[key] = incoming_details.get(key)
            existing["confidence"] = min(1.0, float(existing.get("confidence", 0.7)) + 0.02)
            _METRICS["memory_write_aggregated"] += 1
            _sync_cold_after_mutation()
            return

    # Apply sanitization — truncate summary and details fields.
    is_critical = policy == "memory_critical" or record.kind in _NO_DEDUP_KINDS
    record = _sanitize_record_payload(record, is_critical=is_critical)

    # target_death_confirmed is memory_critical (always writes episodic) but also
    # needs to update knowledge tables so target_alive_from_knowledge is correct.
    if record.kind == "target_death_confirmed":
        _upsert_knowledge_from_event(
            agent=agent,
            kind=record.kind,
            effects=record.details if isinstance(record.details, dict) else {},
            world_turn=world_turn,
        )

    if is_critical:
        _METRICS["memory_write_critical"] += 1

    if add_memory_record(agent, record):
        _METRICS["memory_write_written"] += 1
        _sync_cold_after_mutation()
    else:
        _METRICS["memory_write_discarded"] += 1


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
    for key in ("entity_ids", "seen_agent_ids", "agent_ids"):
        value = effects.get(key)
        if isinstance(value, (list, tuple)):
            ids.extend(str(v) for v in value if v)
    for key in (
        "agent_id",
        "target_id",
        "target_agent_id",
        "trader_id",
        "killer_id",
        "victim_id",
        "dead_agent_id",
        "source_agent_id",
        "other_agent_id",
        "corpse_id",
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
