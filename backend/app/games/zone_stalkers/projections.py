from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any, Literal

from app.games.zone_stalkers.memory.memory_events import get_memory_metrics
from app.games.zone_stalkers.memory.store import get_tag_metrics

ProjectionMode = Literal["zone-lite", "game", "debug-map", "debug-map-lite", "full"]

INVENTORY_PREVIEW_LIMIT = 20
FULL_DEBUG_STORY_EVENTS_LIMIT = 50

_GAME_AGENT_STRIP_FIELDS = (
    "memory",
    "memory_v3",
    "brain_trace",
    "active_plan_v3",
    "brain_v3_context",
)


def _json_size_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def json_size_bytes(payload: Any) -> int:
    """Public helper: return the JSON-serialized byte size of *payload*."""
    return _json_size_bytes(payload)


def _compact_brain_context(agent: dict[str, Any]) -> dict[str, Any] | None:
    ctx = agent.get("brain_v3_context")
    if not isinstance(ctx, dict):
        return None
    compact: dict[str, Any] = {}
    for key in ("objective_key", "intent_kind", "selected_plan_key"):
        if key in ctx:
            compact[key] = ctx.get(key)
    hunt = ctx.get("hunt_target_belief")
    if isinstance(hunt, dict):
        compact["hunt_target_belief"] = {
            "target_id": hunt.get("target_id"),
            "best_location_id": hunt.get("best_location_id"),
            "best_location_confidence": hunt.get("best_location_confidence"),
            "possible_locations": list(hunt.get("possible_locations", [])[:5]),
            "likely_routes": list(hunt.get("likely_routes", [])[:5]),
            "exhausted_locations": list(hunt.get("exhausted_locations", [])[:10]),
            "lead_count": hunt.get("lead_count"),
        }
    return compact or None


def _compact_brain_runtime(agent: dict[str, Any]) -> dict[str, Any] | None:
    runtime = agent.get("brain_runtime")
    if not isinstance(runtime, dict):
        return None
    compact: dict[str, Any] = {}
    for key in (
        "last_decision_turn",
        "valid_until_turn",
        "decision_revision",
        "last_objective_key",
        "last_intent_kind",
        "last_plan_key",
        "invalidated",
        "queued",
        "queued_turn",
        "queued_priority",
        "last_skip_reason",
    ):
        if key in runtime:
            compact[key] = runtime.get(key)
    invalidators = runtime.get("invalidators")
    if isinstance(invalidators, list):
        compact["invalidators"] = list(invalidators[-5:])
    return compact or None


def _record_created_turn(record: dict[str, Any]) -> int:
    try:
        return int(record.get("created_turn", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _memory_stats(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    count = len(records)
    by_layer = Counter(str(record.get("layer") or "") for record in records if record.get("layer"))
    by_kind = Counter(str(record.get("kind") or "") for record in records if record.get("kind"))
    turns = [_record_created_turn(record) for record in records]
    top_kind, top_count = (None, 0)
    if by_kind:
        top_kind, top_count = by_kind.most_common(1)[0]
    stats = {
        "records_count": count,
        "records_cap": 500,
        "cap_utilization": (count / 500.0) if count > 0 else 0.0,
        "by_layer": dict(by_layer),
        "top_kinds": [[kind, value] for kind, value in by_kind.most_common(8)],
        "semantic_ratio": (by_layer.get("semantic", 0) / count) if count > 0 else 0.0,
        "episodic_ratio": (by_layer.get("episodic", 0) / count) if count > 0 else 0.0,
        "stalkers_seen_ratio": (by_kind.get("stalkers_seen", 0) / count) if count > 0 else 0.0,
        "travel_hop_ratio": (by_kind.get("travel_hop", 0) / count) if count > 0 else 0.0,
        "last_record_turn": max(turns) if turns else None,
        "oldest_record_turn": min(turns) if turns else None,
    }
    health = {
        "is_at_cap": count >= 500,
        "stalkers_seen_dominates": (by_kind.get("stalkers_seen", 0) / count) > 0.5 if count > 0 else False,
        "semantic_ratio_low": (by_layer.get("semantic", 0) / count) < 0.08 if count > 0 else True,
        "top_kind": top_kind,
    }
    return stats, health


def _agent_story_events(agent: dict[str, Any]) -> tuple[list[dict[str, Any]], int, bool]:
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records") if isinstance(memory_v3, dict) else {}
    rows: list[dict[str, Any]] = []
    if isinstance(records, dict):
        for raw in records.values():
            if not isinstance(raw, dict):
                continue
            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            kind = str(details.get("action_kind") or raw.get("kind") or "")
            world_turn = _record_created_turn(raw)
            title = str(raw.get("title") or raw.get("summary") or kind)
            rows.append({
                "world_turn": world_turn,
                "type": kind,
                "title": title,
                "summary": str(raw.get("summary") or kind),
                "source": "memory_v3",
                "effects": dict(details),
            })
    trace = agent.get("brain_trace")
    if isinstance(trace, dict):
        for event in trace.get("events", []) or []:
            if not isinstance(event, dict):
                continue
            mode = str(event.get("mode") or "")
            decision = str(event.get("decision") or mode or "trace_event")
            world_turn = int(event.get("turn", 0) or 0)
            rows.append({
                "world_turn": world_turn,
                "type": decision,
                "title": "Brain trace event",
                "summary": str(event.get("summary") or mode or "trace_event"),
                "source": "brain_trace",
                "effects": {
                    "mode": mode,
                    "decision": decision,
                    "objective_key": (event.get("active_objective") or {}).get("key") if isinstance(event.get("active_objective"), dict) else None,
                    "intent_kind": event.get("intent_kind"),
                },
            })
    rows.sort(key=lambda row: (int(row.get("world_turn", 0) or 0), str(row.get("type") or "")))
    if not rows:
        return [], 0, False

    total = len(rows)
    important_kinds = {
        "death",
        "combat_kill",
        "combat_killed",
        "emission_imminent",
        "emission_started",
        "objective_decision",
        "support_source_exhausted",
        "global_goal_completed",
    }
    tail = rows[-FULL_DEBUG_STORY_EVENTS_LIMIT:]
    tail_keys = {(row["world_turn"], row["type"], row["source"], row["summary"]) for row in tail}
    extra = [
        row for row in rows[-200:]
        if row.get("type") in important_kinds
        and (row["world_turn"], row["type"], row["source"], row["summary"]) not in tail_keys
    ]
    merged = (extra + tail)[-FULL_DEBUG_STORY_EVENTS_LIMIT:]
    return merged, total, total > len(merged)


def _project_terminal_agent(agent: dict[str, Any]) -> None:
    left_zone = bool(agent.get("has_left_zone"))
    if not left_zone:
        return
    terminal_turn = None
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records") if isinstance(memory_v3, dict) else {}
    if isinstance(records, dict):
        for raw in records.values():
            if not isinstance(raw, dict):
                continue
            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            action_kind = str(details.get("action_kind") or raw.get("kind") or "")
            if action_kind == "global_goal_completed":
                terminal_turn = _record_created_turn(raw)
    agent["current_goal"] = "left_zone"
    agent["scheduled_action"] = None
    agent["terminal_state"] = {
        "kind": "left_zone",
        "global_goal_achieved": bool(agent.get("global_goal_achieved")),
        "left_zone_turn": terminal_turn,
    }
    context = agent.get("brain_v3_context")
    if isinstance(context, dict):
        context["objective_key"] = "LEFT_ZONE"
        context["objective_score"] = 0
        context["objective_reason"] = "NPC покинул Зону"
        context["adapter_intent"] = None
        context["intent_kind"] = None
    agent["active_objective"] = {
        "key": "LEFT_ZONE",
        "score": 0,
        "source": "terminal_state",
        "reason": "NPC покинул Зону",
    }


def _enrich_agent_full_projection(agent: dict[str, Any]) -> None:
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records") if isinstance(memory_v3, dict) else {}
    record_rows = [
        raw for raw in (records.values() if isinstance(records, dict) else [])
        if isinstance(raw, dict)
    ]
    stats, health = _memory_stats(record_rows)
    write_metrics = get_memory_metrics()
    tag_metrics = get_tag_metrics()
    stats["runtime_global_metrics"] = {
        "memory_write_attempts": write_metrics.get("memory_write_attempts", 0),
        "memory_write_written": write_metrics.get("memory_write_written", 0),
        "memory_write_aggregated": write_metrics.get("memory_write_aggregated", 0),
        "memory_write_trace_only": write_metrics.get("memory_write_trace_only", 0),
        "memory_by_tag_refs": tag_metrics.get("memory_by_tag_refs", 0),
        "memory_by_tag_skipped_refs": tag_metrics.get("memory_by_tag_skipped_refs", 0),
    }
    agent["memory_v3_stats"] = stats
    agent["memory_health"] = health
    story_events, story_events_count, story_events_truncated = _agent_story_events(agent)
    agent["story_events"] = story_events
    agent["story_events_count"] = story_events_count
    agent["story_events_truncated"] = story_events_truncated
    raw_sleepiness = int(agent.get("sleepiness", 0) or 0)
    agent["sleep_need"] = {
        "raw_sleepiness": raw_sleepiness,
        "interpreted_fatigue": raw_sleepiness,
        "scale": "sleepiness_high_means_tired",
    }
    _project_terminal_agent(agent)


# ── Explicit game projection helpers ─────────────────────────────────────────
# These avoid a full deepcopy of the state dict by building output dicts
# field-by-field.  Only the fields required by the frontend ZoneMapState
# interface are included; heavy data (memory, brain_trace, debug, etc.) is
# never copied.


def _compact_scheduled_action(action: Any, world_turn: int | None = None) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    turns_total = action.get("turns_total")
    turns_remaining = action.get("turns_remaining")
    ends_turn = action.get("ends_turn")
    if turns_remaining is None and ends_turn is not None and world_turn is not None:
        try:
            turns_remaining = max(0, int(ends_turn) - int(world_turn))
        except (TypeError, ValueError):
            turns_remaining = None
    return {
        "type": action.get("type"),
        "turns_remaining": turns_remaining,
        "turns_total": turns_total,
        "target_id": action.get("target_id"),
        "started_turn": action.get("started_turn"),
        "ends_turn": action.get("ends_turn"),
        "revision": action.get("revision"),
        "interruptible": action.get("interruptible"),
    }


def _compact_active_plan(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    return {
        "status": plan.get("status"),
        "plan_key": plan.get("plan_key"),
        "current_step": plan.get("current_step"),
        "objective_key": plan.get("objective_key"),
    }


def _compact_equipment(equipment: Any) -> dict[str, Any] | None:
    if not isinstance(equipment, dict):
        return None
    return {
        "weapon": equipment.get("weapon"),
        "armor": equipment.get("armor"),
        "artifact_slots": equipment.get("artifact_slots"),
    }


def _compact_inventory(inventory: Any) -> list[dict[str, Any]]:
    if not isinstance(inventory, list):
        return []
    result = []
    for item in inventory[:INVENTORY_PREVIEW_LIMIT]:
        if isinstance(item, dict):
            result.append({
                "id": item.get("id"),
                "type": item.get("type"),
                "name": item.get("name"),
            })
    return result


def _project_agent_needs(agent: dict[str, Any], world_turn: int | None, lazy_enabled: bool) -> dict[str, Any]:
    """Compute projected needs without mutating the agent."""
    if lazy_enabled and world_turn is not None and isinstance(agent.get("needs_state"), dict):
        from app.games.zone_stalkers.needs.lazy_needs import project_needs as _pn  # noqa: PLC0415
        return _pn(agent, world_turn)
    return {
        "hunger": agent.get("hunger"),
        "thirst": agent.get("thirst"),
        "sleepiness": agent.get("sleepiness"),
    }


def _project_agent_game(agent: dict[str, Any], world_turn: int | None = None) -> dict[str, Any]:
    _lazy_enabled = isinstance(agent.get("needs_state"), dict)
    _needs = _project_agent_needs(agent, world_turn, _lazy_enabled)
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "archetype": agent.get("archetype"),
        "controller": agent.get("controller"),
        "location_id": agent.get("location_id"),
        "is_alive": agent.get("is_alive"),
        "has_left_zone": agent.get("has_left_zone"),
        "hp": agent.get("hp"),
        "max_hp": agent.get("max_hp"),
        "radiation": agent.get("radiation"),
        "hunger": _needs["hunger"],
        "thirst": _needs["thirst"],
        "sleepiness": _needs["sleepiness"],
        "money": agent.get("money"),
        "faction": agent.get("faction"),
        "reputation": agent.get("reputation"),
        "experience": agent.get("experience"),
        "skills": agent.get("skills"),
        "global_goal": agent.get("global_goal"),
        "current_goal": agent.get("current_goal"),
        "risk_tolerance": agent.get("risk_tolerance"),
        "action_used": agent.get("action_used"),
        "scheduled_action": _compact_scheduled_action(agent.get("scheduled_action"), world_turn),
        "active_plan_summary": _compact_active_plan(agent.get("active_plan_v3")),
        "equipment_summary": _compact_equipment(agent.get("equipment")),
        "inventory_summary": _compact_inventory(agent.get("inventory")),
        # Keep full equipment/inventory for player agent use in UI
        "equipment": agent.get("equipment"),
        "inventory": agent.get("inventory"),
    }


def _project_trader_game(trader: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trader.get("id"),
        "name": trader.get("name"),
        "archetype": trader.get("archetype"),
        "location_id": trader.get("location_id"),
        "is_alive": trader.get("is_alive"),
        "money": trader.get("money"),
        "inventory": trader.get("inventory"),
        "prices": trader.get("prices"),
    }


def _project_locations_game(locations: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for loc_id, loc in locations.items():
        if not isinstance(loc, dict):
            result[loc_id] = loc
            continue
        result[loc_id] = {
            "id": loc.get("id"),
            "name": loc.get("name"),
            "terrain_type": loc.get("terrain_type"),
            "anomaly_activity": loc.get("anomaly_activity"),
            "dominant_anomaly_type": loc.get("dominant_anomaly_type"),
            "connections": loc.get("connections"),
            "agents": loc.get("agents"),
            "exit_zone": loc.get("exit_zone"),
            "artifacts": loc.get("artifacts"),
            "items": loc.get("items"),
            "region": loc.get("region"),
            "image_url": loc.get("image_url"),
            "debug_layout": loc.get("debug_layout"),
        }
    return result


def _project_zone_game(state: dict[str, Any]) -> dict[str, Any]:
    """Build a game projection without deepcopy — only include frontend-needed fields."""
    agents_raw = state.get("agents", {})
    traders_raw = state.get("traders", {})
    return {
        "context_type": state.get("context_type"),
        "world_turn": state.get("world_turn"),
        "world_day": state.get("world_day"),
        "world_hour": state.get("world_hour"),
        "world_minute": state.get("world_minute"),
        "game_over": state.get("game_over"),
        "emission_active": state.get("emission_active"),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "emission_ends_turn": state.get("emission_ends_turn"),
        "auto_tick_enabled": state.get("auto_tick_enabled"),
        "auto_tick_speed": state.get("auto_tick_speed"),
        "debug_auto_tick": state.get("debug_auto_tick"),
        "player_agents": state.get("player_agents"),
        "active_events": state.get("active_events"),
        "debug_hunt_traces_enabled": state.get("debug_hunt_traces_enabled"),
        "debug_layout": state.get("debug_layout"),
        "max_turns": state.get("max_turns"),
        "state_revision": state.get("state_revision", 0),
        "map_revision": state.get("map_revision", 0),
        "agents": {
            id_: _project_agent_game(a, state.get("world_turn"))
            for id_, a in agents_raw.items()
            if isinstance(a, dict)
        },
        "traders": {id_: _project_trader_game(t) for id_, t in traders_raw.items() if isinstance(t, dict)},
        "locations": _project_locations_game(state.get("locations", {})),
        # mutants are small dicts, safe to pass through directly
        "mutants": state.get("mutants", {}),
    }


def _project_zone_debug_map_lite(state: dict[str, Any]) -> dict[str, Any]:
    """
    Lightweight debug-map projection — built selectively without deepcopy.

    Like debug-map but excludes heavy data (full location_hunt_traces,
    full hunt_search_by_agent). Debug detail comes via scoped endpoints.
    Avoids full deepcopy of the state (which can be 1-5 MB) by constructing
    only the fields needed by the frontend.
    """
    # ── Agents: compact per-agent dict (exclude memory, memory_v3) ────────
    agents_raw = state.get("agents") or {}
    agents_out: dict[str, Any] = {}
    for agent_id, agent in agents_raw.items():
        if not isinstance(agent, dict):
            agents_out[agent_id] = agent
            continue
        _lazy_enabled = isinstance(agent.get("needs_state"), dict)
        _needs = _project_agent_needs(agent, state.get("world_turn"), _lazy_enabled)
        compact_ctx = _compact_brain_context(agent)
        compact_brain_runtime = _compact_brain_runtime(agent)
        # Only include keys present in the agent dict (skip None to avoid size bloat)
        _agent_fields = {
            "id": agent.get("id"),
            "name": agent.get("name"),
            "archetype": agent.get("archetype"),
            "controller": agent.get("controller"),
            "location_id": agent.get("location_id"),
            "is_alive": agent.get("is_alive"),
            "has_left_zone": agent.get("has_left_zone"),
            "hp": agent.get("hp"),
            "max_hp": agent.get("max_hp"),
            "radiation": agent.get("radiation"),
            "hunger": _needs["hunger"],
            "thirst": _needs["thirst"],
            "sleepiness": _needs["sleepiness"],
            "money": agent.get("money"),
            "faction": agent.get("faction"),
            "reputation": agent.get("reputation"),
            "experience": agent.get("experience"),
            "skills": agent.get("skills"),
            "global_goal": agent.get("global_goal"),
            "current_goal": agent.get("current_goal"),
            "risk_tolerance": agent.get("risk_tolerance"),
            "action_used": agent.get("action_used"),
            "scheduled_action": _compact_scheduled_action(agent.get("scheduled_action")),
            "active_plan_summary": _compact_active_plan(agent.get("active_plan_v3")),
            "equipment_summary": _compact_equipment(agent.get("equipment")),
            "inventory_summary": _compact_inventory(agent.get("inventory")),
            "equipment": agent.get("equipment"),
            "inventory": agent.get("inventory"),
        }
        agent_dict: dict[str, Any] = {k: v for k, v in _agent_fields.items() if v is not None}
        if compact_ctx is not None:
            agent_dict["brain_v3_context"] = compact_ctx
        if compact_brain_runtime is not None:
            agent_dict["brain_runtime"] = compact_brain_runtime
        agents_out[agent_id] = agent_dict

    # ── Traders: compact per-trader dict (exclude memory, memory_v3, brain_trace) ─
    traders_raw = state.get("traders") or {}
    traders_out: dict[str, Any] = {}
    for trader_id, trader in traders_raw.items():
        if not isinstance(trader, dict):
            traders_out[trader_id] = trader
            continue
        traders_out[trader_id] = {
            "id": trader.get("id"),
            "name": trader.get("name"),
            "archetype": trader.get("archetype"),
            "location_id": trader.get("location_id"),
            "is_alive": trader.get("is_alive"),
            "money": trader.get("money"),
            "inventory": trader.get("inventory"),
            "prices": trader.get("prices"),
        }

    # ── Debug: compact version with summary counts only ───────────────────
    debug_raw = state.get("debug")
    debug_out: dict[str, Any] | None = None
    if isinstance(debug_raw, dict):
        debug_out = {}
        lht = debug_raw.get("location_hunt_traces")
        if isinstance(lht, dict):
            debug_out["location_hunt_traces_count"] = len(lht)
            debug_out["location_hunt_traces"] = {}  # stripped: details available via /debug/hunt-search/locations/{id}
        hsba = debug_raw.get("hunt_search_by_agent")
        if isinstance(hsba, dict):
            debug_out["hunt_search_by_agent"] = {
                aid: {
                    "target_id": v.get("target_id") if isinstance(v, dict) else None,
                    "best_location_id": v.get("best_location_id") if isinstance(v, dict) else None,
                    "best_location_confidence": v.get("best_location_confidence") if isinstance(v, dict) else None,
                    "lead_count": v.get("lead_count") if isinstance(v, dict) else None,
                }
                for aid, v in hsba.items()
            }

    # ── Top-level scalars and collections ─────────────────────────────────
    # Only include keys present in state (skip None to avoid size bloat on minimal states)
    _top_fields = {
        "context_type": state.get("context_type"),
        "world_turn": state.get("world_turn"),
        "world_day": state.get("world_day"),
        "world_hour": state.get("world_hour"),
        "world_minute": state.get("world_minute"),
        "game_over": state.get("game_over"),
        "emission_active": state.get("emission_active"),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "emission_ends_turn": state.get("emission_ends_turn"),
        "auto_tick_enabled": state.get("auto_tick_enabled"),
        "auto_tick_speed": state.get("auto_tick_speed"),
        "debug_auto_tick": state.get("debug_auto_tick"),
        "player_agents": state.get("player_agents"),
        "active_events": state.get("active_events"),
        "debug_hunt_traces_enabled": state.get("debug_hunt_traces_enabled"),
        "debug_layout": state.get("debug_layout"),
        "max_turns": state.get("max_turns"),
    }
    result: dict[str, Any] = {k: v for k, v in _top_fields.items() if v is not None}
    # Always include revision and collection fields
    result["state_revision"] = state.get("state_revision", 0)
    result["map_revision"] = state.get("map_revision", 0)
    result["agents"] = agents_out
    result["traders"] = traders_out
    result["locations"] = _project_locations_game(state.get("locations", {}))
    result["mutants"] = state.get("mutants", {})
    if debug_out is not None:
        result["debug"] = debug_out
    return result


def project_zone_state(*, state: dict[str, Any], mode: ProjectionMode) -> dict[str, Any]:
    if mode == "full":
        projected = copy.deepcopy(state)
        agents = projected.get("agents")
        if isinstance(agents, dict):
            for agent in agents.values():
                if isinstance(agent, dict):
                    _enrich_agent_full_projection(agent)
        return projected

    if mode in {"game", "zone-lite"}:
        return _project_zone_game(state)

    if mode == "debug-map-lite":
        return _project_zone_debug_map_lite(state)

    # debug-map: keep deepcopy approach (acceptable for now — called only on demand)
    projected = copy.deepcopy(state)
    agents = projected.get("agents")
    if isinstance(agents, dict):
        for agent in agents.values():
            if not isinstance(agent, dict):
                continue
            agent.pop("memory", None)
            agent.pop("memory_v3", None)
            compact_ctx = _compact_brain_context(agent)
            if compact_ctx is not None:
                agent["brain_v3_context"] = compact_ctx
            else:
                agent.pop("brain_v3_context", None)
            compact_runtime = _compact_brain_runtime(agent)
            if compact_runtime is not None:
                agent["brain_runtime"] = compact_runtime
            else:
                agent.pop("brain_runtime", None)

    traders = projected.get("traders")
    if isinstance(traders, dict):
        for trader in traders.values():
            if not isinstance(trader, dict):
                continue
            trader.pop("memory", None)
            trader.pop("memory_v3", None)
            trader.pop("brain_trace", None)

    debug = projected.get("debug")
    if isinstance(debug, dict):
        if isinstance(debug.get("hunt_search_by_agent"), dict):
            debug["hunt_search_by_agent"] = {
                key: value for idx, (key, value) in enumerate(debug["hunt_search_by_agent"].items()) if idx < 20
            }
        if isinstance(debug.get("location_hunt_traces"), dict):
            debug["location_hunt_traces"] = {
                key: value for idx, (key, value) in enumerate(debug["location_hunt_traces"].items()) if idx < 60
            }
    return projected


def build_zone_state_size_report(state: dict[str, Any]) -> dict[str, Any]:
    debug = state.get("debug") if isinstance(state.get("debug"), dict) else {}
    return {
        "state_size_bytes": _json_size_bytes(state),
        "zone_lite_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="zone-lite")),
        "game_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="game")),
        "debug_map_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="debug-map")),
        "debug_map_lite_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="debug-map-lite")),
        "full_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="full")),
        "debug_hunt_search_bytes": _json_size_bytes(debug.get("hunt_search_by_agent")),
        "location_hunt_traces_bytes": _json_size_bytes(debug.get("location_hunt_traces")),
    }
