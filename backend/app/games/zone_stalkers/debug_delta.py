"""
Zone Stalkers debug WebSocket delta builder.

Builds a compact, scoped debug delta for subscribed clients.
Only includes debug data relevant to the subscription scope.
"""
from __future__ import annotations

from typing import Any

_HUNT_RELEVANT_MEMORY_KINDS: frozenset[str] = frozenset({
    "target_seen", "target_last_known_location", "target_intel",
    "intel_from_stalker", "intel_from_trader", "target_not_found",
    "target_location_exhausted", "witness_source_exhausted", "no_tracks_found",
    "no_witnesses", "target_moved", "target_route_observed", "target_wounded",
    "target_combat_noise", "target_death_confirmed", "hunt_failed",
    "combat_initiated", "combat_resolved",
})

_MAX_LOCATION_TRACE_RECORDS = 50
_MAX_AGENT_LEAD_RECORDS = 30


def build_zone_debug_delta(
    *,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    subscription: dict[str, Any],
    debug_revision: int,
) -> dict[str, Any] | None:
    """
    Build a scoped debug delta for the given subscription.
    Returns None if nothing changed within the subscription scope.
    """
    mode = subscription.get("mode", "debug-map")
    hunter_id = subscription.get("hunter_id")
    target_id = subscription.get("target_id")
    visible_location_ids = set(subscription.get("visible_location_ids") or [])
    min_confidence = float(subscription.get("min_confidence", 0.0))

    changes: dict[str, Any] = {}

    old_debug = old_state.get("debug") or {}
    new_debug = new_state.get("debug") or {}

    # Hunt search by agent (filtered by hunter/target)
    if mode in ("debug-map", "agent-profile"):
        old_hs = old_debug.get("hunt_search_by_agent") or {}
        new_hs = new_debug.get("hunt_search_by_agent") or {}
        hs_changes: dict[str, Any] = {}
        agents_to_check = {hunter_id} if hunter_id else set(new_hs)
        for agent_id in agents_to_check:
            if agent_id not in new_hs:
                continue
            old_val = old_hs.get(agent_id)
            new_val = new_hs.get(agent_id)
            if old_val != new_val and isinstance(new_val, dict):
                filtered = _compact_hunt_search_entry(new_val, target_id=target_id, min_confidence=min_confidence)
                if filtered:
                    hs_changes[agent_id] = filtered
        if hs_changes:
            changes["hunt_search_by_agent"] = hs_changes

    # Location hunt traces (filtered by visible locations)
    if mode in ("debug-map", "location-profile"):
        old_lht = old_debug.get("location_hunt_traces") or {}
        new_lht = new_debug.get("location_hunt_traces") or {}
        lht_changes: dict[str, Any] = {}
        locs_to_check = visible_location_ids if visible_location_ids else set(new_lht) - set(old_lht)
        for loc_id in locs_to_check:
            if loc_id not in new_lht:
                continue
            old_loc = old_lht.get(loc_id)
            new_loc = new_lht.get(loc_id)
            if old_loc != new_loc and isinstance(new_loc, dict):
                lht_changes[loc_id] = _compact_location_trace(new_loc, hunter_id=hunter_id, target_id=target_id)
        if lht_changes:
            changes["location_hunt_traces"] = lht_changes

    # Selected agent brain summary
    selected_agent_id = subscription.get("selected_agent_id") or hunter_id
    if selected_agent_id and mode == "agent-profile":
        old_agent = (old_state.get("agents") or {}).get(selected_agent_id, {})
        new_agent = (new_state.get("agents") or {}).get(selected_agent_id, {})
        if isinstance(new_agent, dict) and old_agent != new_agent:
            changes["selected_agent_profile_summary"] = _compact_agent_brain_summary(new_agent)

    if not changes:
        return None

    return {
        "base_revision": old_state.get("state_revision", 0),
        "revision": new_state.get("state_revision", 0),
        "debug_revision": debug_revision,
        "scope": {
            "mode": mode,
            "hunter_id": hunter_id,
            "target_id": target_id,
        },
        "changes": changes,
    }


def _compact_hunt_search_entry(
    entry: dict[str, Any],
    *,
    target_id: str | None,
    min_confidence: float,
) -> dict[str, Any] | None:
    entry_target = entry.get("target_id")
    if target_id and entry_target != target_id:
        return None
    conf = entry.get("best_location_confidence") or 0.0
    if conf < min_confidence:
        return None
    return {
        "target_id": entry_target,
        "best_location_id": entry.get("best_location_id"),
        "best_location_confidence": conf,
        "lead_count": entry.get("lead_count"),
        "possible_locations": list((entry.get("possible_locations") or [])[:5]),
        "likely_routes": list((entry.get("likely_routes") or [])[:5]),
        "exhausted_locations": list((entry.get("exhausted_locations") or [])[:10]),
    }


def _compact_location_trace(
    trace: dict[str, Any],
    *,
    hunter_id: str | None,
    target_id: str | None,
) -> dict[str, Any]:
    records = trace.get("records") or trace.get("positive_leads") or []
    if isinstance(records, list):
        filtered = records
        if hunter_id:
            filtered = [r for r in filtered if isinstance(r, dict) and r.get("hunter_id") == hunter_id]
        if target_id:
            filtered = [r for r in filtered if isinstance(r, dict) and r.get("target_id") == target_id]
        filtered = filtered[:_MAX_LOCATION_TRACE_RECORDS]
    else:
        filtered = []
    return {
        "records_count": len(trace.get("records") or trace.get("positive_leads") or []),
        "records": filtered,
        "revision": trace.get("revision"),
    }


def _compact_agent_brain_summary(agent: dict[str, Any]) -> dict[str, Any]:
    ctx = agent.get("brain_v3_context") or {}
    plan = agent.get("active_plan_v3") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    current_step_idx = plan.get("current_step_index", 0) if isinstance(plan, dict) else 0
    current_step = steps[current_step_idx] if steps and 0 <= current_step_idx < len(steps) else None
    return {
        "agent_id": agent.get("id"),
        "location_id": agent.get("location_id"),
        "is_alive": agent.get("is_alive"),
        "hp": agent.get("hp"),
        "current_goal": agent.get("current_goal"),
        "global_goal": agent.get("global_goal"),
        "objective_key": ctx.get("objective_key") if isinstance(ctx, dict) else None,
        "intent_kind": ctx.get("intent_kind") if isinstance(ctx, dict) else None,
        "selected_plan_key": ctx.get("selected_plan_key") if isinstance(ctx, dict) else None,
        "active_plan_status": plan.get("status") if isinstance(plan, dict) else None,
        "current_step_kind": current_step.get("kind") if isinstance(current_step, dict) else None,
        "current_step_index": current_step_idx,
        "steps_count": len(steps),
    }
