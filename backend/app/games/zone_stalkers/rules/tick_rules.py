"""
tick_rules.py — World-turn advancement for Zone Stalkers.

Called once per game-hour (real-time ticker or manual /tick endpoint).
Processes:
  - Scheduled agent actions (travel, explore, sleep, event)
  - AI bot decisions
  - Hour/day counter advancement
  - Turn reset (action_used → False)
  - Random event spawning
"""
import collections
import contextlib
import heapq
import copy
import random
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from app.games.zone_stalkers.decision.debug.brain_trace import (
    ensure_brain_trace_for_tick,
    write_npc_brain_v3_decision_trace,
    write_plan_monitor_trace,
)
from app.games.zone_stalkers.decision.active_plan_manager import (
    clear_active_plan,
    create_active_plan,
    get_active_plan,
    save_active_plan,
)
from app.games.zone_stalkers.decision.active_plan_composer import compose_active_plan_steps
from app.games.zone_stalkers.decision.active_plan_runtime import (
    active_plan_trace_payload as _active_plan_trace_payload,
    finish_active_plan as _finish_active_plan,
    handle_v3_monitor_abort as _handle_v3_monitor_abort,
    mark_active_plan_step_failed as _mark_active_plan_step_failed,
    migrate_legacy_scheduled_action_to_active_plan as _migrate_legacy_scheduled_action_to_active_plan,
    on_active_plan_scheduled_action_completed as _on_active_plan_scheduled_action_completed,
    process_active_plan_v3 as _process_active_plan_v3,
    start_or_continue_active_plan_step as _start_or_continue_active_plan_step,
    write_active_plan_memory_event as _write_active_plan_memory_event,
    write_active_plan_trace_event as _write_active_plan_trace_event,
)
from app.games.zone_stalkers.decision.brain_runtime import (
    clear_brain_invalidators,
    ensure_brain_runtime,
    highest_invalidator_priority,
    invalidate_brain,
    latest_invalidator_reason,
    max_priority,
    normalize_priority,
    promote_priority,
    should_run_brain,
)
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep
from app.games.zone_stalkers.decision.plan_monitor import (
    PlanMonitorResult,
    assess_scheduled_action_v3,
    is_v3_monitored_bot,
)
from app.games.zone_stalkers.rules.agent_lifecycle import (
    cleanup_stale_corpses,
    is_valid_corpse_object,
    kill_agent,
)
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    HUNGER_INCREASE_PER_HOUR,
    HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER,
    HP_DAMAGE_PER_HOUR_CRITICAL_THIRST,
    SLEEPINESS_INCREASE_PER_HOUR,
    THIRST_INCREASE_PER_HOUR,
    SLEEP_EFFECT_INTERVAL_TURNS,
    SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL,
    HUNGER_INCREASE_PER_SLEEP_INTERVAL,
    THIRST_INCREASE_PER_SLEEP_INTERVAL,
)
from app.games.zone_stalkers.runtime.scheduler import cleanup_old_tasks, pop_due_tasks, schedule_task
from app.games.zone_stalkers.needs.lazy_needs import (
    ensure_needs_state,
    get_need,
    schedule_need_thresholds,
    set_needs,
)

# 1 game turn = 1 real minute
MINUTES_PER_TURN = 1

# Derived constants (update MINUTES_PER_TURN above to rescale the whole system)
_HOUR_IN_TURNS = 60 // MINUTES_PER_TURN       # turns needed to pass 1 in-game hour
EXPLORE_DURATION_TURNS = 30 // MINUTES_PER_TURN  # turns needed for a 30-min exploration
DEFAULT_SLEEP_HOURS = 6                         # default hours of sleep when no 'hours' key is present in sched

# ── Memory v3 query helpers ────────────────────────────────────────────────────

def _v3_records_desc(agent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return memory_v3 records sorted by created_turn descending (newest first)."""
    records = ((agent.get("memory_v3") or {}).get("records") or {})
    if not isinstance(records, dict):
        return []
    return sorted(
        (r for r in records.values() if isinstance(r, dict)),
        key=lambda r: int(r.get("created_turn", 0) or 0),
        reverse=True,
    )


def _v3_details(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Return the details dict from a memory_v3 record."""
    d = rec.get("details")
    return d if isinstance(d, dict) else {}


def _v3_action_kind(rec: Dict[str, Any]) -> str:
    """Return the action_kind from a memory_v3 record (from details or kind)."""
    d = _v3_details(rec)
    return str(d.get("action_kind") or rec.get("kind") or "")


def _v3_memory_type(rec: Dict[str, Any]) -> str:
    """Return the original memory type (decision/observation/action) from v3 record."""
    return str(_v3_details(rec).get("memory_type") or "")


def _v3_turn(rec: Dict[str, Any]) -> int:
    """Return the created_turn of a memory_v3 record."""
    return int(rec.get("created_turn", 0) or 0)

PLAN_MONITOR_MEMORY_DEDUP_TURNS = 10
MAX_DECISION_QUEUE_SIZE = 512


def _is_routine_scheduled_action_continuation(agent: Dict[str, Any]) -> bool:
    """True when agent is continuing a routine scheduled action this tick."""
    sched = agent.get("scheduled_action")
    if not isinstance(sched, dict):
        return False
    action_type = str(sched.get("type") or "")
    if action_type not in {"sleep", "travel", "wait_in_shelter"}:
        return False
    turns_remaining = sched.get("turns_remaining")
    if turns_remaining is None:
        return True
    try:
        return int(turns_remaining) > 0
    except (TypeError, ValueError):
        return True


def _ensure_agent_current_location_knowledge(agent: Dict[str, Any], state: Dict[str, Any], world_turn: int) -> None:
    try:
        from app.games.zone_stalkers.knowledge.location_knowledge import (  # noqa: PLC0415
            get_known_location,
            mark_location_visited,
            mark_neighbor_locations_known,
        )
    except Exception:
        return

    location_id = str(agent.get("location_id") or "")
    if not location_id:
        return

    known = get_known_location(agent, location_id)
    if not isinstance(known, dict) or not bool(known.get("visited")):
        mark_location_visited(agent, state=state, location_id=location_id, world_turn=world_turn)
        mark_neighbor_locations_known(agent, state=state, location_id=location_id, world_turn=world_turn)

# ── Human-readable Russian labels for intent kinds (used in decision memory entries) ──
_INTENT_LABEL_RU: dict = {
    "escape_danger":        "Бегство (крит. HP)",
    "heal_self":            "Срочное лечение",
    "flee_emission":        "Бегство от выброса",
    "wait_in_shelter":      "Укрытие от выброса",
    "seek_water":           "Поиск воды",
    "seek_food":            "Поиск еды",
    "rest":                 "Отдых (сон)",
    "resupply":             "Получить снаряжение",
    "sell_artifacts":       "Продажа артефактов",
    "trade":                "Торговля",
    "upgrade_equipment":    "Апгрейд снаряжения",
    "loot":                 "Мародёрство",
    "explore":              "Исследование",
    "get_rich":             "Накопление богатства",
    "hunt_target":          "Охота на цель",
    "search_information":   "Поиск информации",
    "leave_zone":           "Покинуть Зону",
    "negotiate":            "Переговоры",
    "assist_ally":          "Помощь союзнику",
    "form_group":           "Создать группу",
    "follow_group_plan":    "Следовать плану группы",
    "maintain_group":       "Сохранить группу",
    "idle":                 "Ожидание",
}

# ── Intent → current_goal mapping (Fix 3) ────────────────────────────────────
# Maps intent.kind to the canonical agent current_goal string.
# Used in _run_npc_brain_v3_decision_inner to update current_goal BEFORE writing
# the decision memory entry so that memory reflects the new goal.
_INTENT_TO_GOAL: Dict[str, str] = {
    "escape_danger":       "emergency_heal",
    "heal_self":           "emergency_heal",
    "flee_emission":       "emergency_shelter",
    "seek_water":          "restore_needs",
    "seek_food":           "restore_needs",
    "rest":                "restore_needs",
    "resupply":            "resupply",
    "sell_artifacts":      "get_rich",
    "get_rich":            "get_rich",
    "hunt_target":         "kill_stalker",
    "search_information":  "unravel_zone",
    "leave_zone":          "leave_zone",
    "idle":                "idle",
}

_OBJECTIVE_TO_GOAL: Dict[str, str] = {
    "RESTORE_WATER": "restore_needs",
    "RESTORE_FOOD": "restore_needs",
    "HEAL_SELF": "emergency_heal",
    "REST": "restore_needs",
    "GET_MONEY_FOR_RESUPPLY": "get_money_for_resupply",
    "FIND_ARTIFACTS": "get_rich",
    "SELL_ARTIFACTS": "get_rich",
    "RESUPPLY_WEAPON": "resupply",
    "RESUPPLY_AMMO": "resupply",
    "REACH_SAFE_SHELTER": "emergency_shelter",
    "WAIT_IN_SHELTER": "emergency_shelter",
    "HUNT_TARGET": "kill_stalker",
    "GATHER_INTEL": "kill_stalker",
    "PREPARE_FOR_HUNT": "prepare_for_hunt",
    "LOCATE_TARGET": "kill_stalker",
    "VERIFY_LEAD": "kill_stalker",
    "TRACK_TARGET": "kill_stalker",
    "ENGAGE_TARGET": "kill_stalker",
    "CONFIRM_KILL": "kill_stalker",
    "IDLE": "idle",
}


_INTENT_TO_OBJECTIVE_KEY_FALLBACK: Dict[str, str] = {
    # Preserve canonical objective key when objective pipeline falls back to intent-only path.
    "leave_zone": "LEAVE_ZONE",
    "flee_emission": "REACH_SAFE_SHELTER",
    "wait_in_shelter": "WAIT_IN_SHELTER",
    "seek_water": "RESTORE_WATER",
    "seek_food": "RESTORE_FOOD",
    "rest": "REST",
    "heal_self": "HEAL_SELF",
    "escape_danger": "ESCAPE_DANGER",
    "sell_artifacts": "SELL_ARTIFACTS",
    "get_rich": "FIND_ARTIFACTS",
    "hunt_target": "HUNT_TARGET",
}

_RESUPPLY_INTENT_CATEGORY_TO_OBJECTIVE_KEY: Dict[str, str] = {
    "weapon": "RESUPPLY_WEAPON",
    "armor": "RESUPPLY_ARMOR",
    "ammo": "RESUPPLY_AMMO",
    "food": "RESUPPLY_FOOD",
    "drink": "RESUPPLY_DRINK",
    "medical": "RESUPPLY_MEDICINE",
    "medicine": "RESUPPLY_MEDICINE",
}


def _sell_artifacts_blocked_by_trade_cooldown(
    *,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> bool:
    """Return True when every known trader is under a trader_no_money cooldown for this agent's artifacts.

    Mirrors the suppression semantics in needs._score_trade:
    - block local sale if any same-trader-or-same-location cooldown is active for artifact types;
    - do NOT block globally if another known trader is available and not under cooldown.
    """
    # Lazy import to avoid decision<->tick_rules import cycles on module load.
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES  # noqa: PLC0415
    from app.games.zone_stalkers.decision.trade_sell_failures import has_recent_trade_sell_failure_for_agent  # noqa: PLC0415

    artifact_types = frozenset(ARTIFACT_TYPES.keys())
    artifact_item_types = {
        str(item.get("type") or "")
        for item in (agent.get("inventory") or [])
        if item.get("type") in artifact_types
    }
    current_location_id = str(agent.get("location_id") or "")
    traders = state.get("traders")
    known_traders = tuple(traders.values()) if isinstance(traders, dict) else ()
    current_location_traders = tuple(
        trader for trader in known_traders
        if isinstance(trader, dict) and str(trader.get("location_id") or "") == current_location_id
    )
    local_sell_blocked = has_recent_trade_sell_failure_for_agent(
        agent,
        trader_id=None,
        location_id=current_location_id,
        item_types=artifact_item_types,
        world_turn=world_turn,
    ) or any(
        has_recent_trade_sell_failure_for_agent(
            agent,
            trader_id=str(trader.get("agent_id") or trader.get("id") or ""),
            location_id=current_location_id,
            item_types=artifact_item_types,
            world_turn=world_turn,
        )
        for trader in current_location_traders
    )
    if not local_sell_blocked:
        return False
    # Still allow selling if an alternative un-cooldown-ed trader exists anywhere.
    alternative_trader_available = any(
        not has_recent_trade_sell_failure_for_agent(
            agent,
            trader_id=str(trader.get("agent_id") or trader.get("id") or ""),
            location_id=str(trader.get("location_id") or ""),
            item_types=artifact_item_types,
            world_turn=world_turn,
        )
        for trader in known_traders
        if isinstance(trader, dict)
    )
    return not alternative_trader_available


def _fallback_objective_key_for_intent(
    intent: Any,
    *,
    agent: Dict[str, Any] | None = None,
    state: Dict[str, Any] | None = None,
    world_turn: int | None = None,
) -> str | None:
    metadata = intent.metadata if hasattr(intent, "metadata") and isinstance(intent.metadata, dict) else {}
    intent_kind = str(getattr(intent, "kind", "") or "")
    metadata_objective_key = str(metadata.get("objective_key") or "")

    # IMPORTANT: sell_artifacts cooldown suppression must run BEFORE returning
    # metadata_objective_key so that adapter/current-context intents carrying
    # metadata["objective_key"] = "SELL_ARTIFACTS" cannot bypass the cooldown.
    if (intent_kind == "sell_artifacts" or metadata_objective_key == "SELL_ARTIFACTS") and isinstance(agent, dict):
        if _sell_artifacts_blocked_by_trade_cooldown(
            agent=agent,
            state=state or {},
            world_turn=int(world_turn or 0),
        ):
            return "FIND_ARTIFACTS"

    if metadata_objective_key:
        return metadata_objective_key

    if intent_kind == "resupply":
        forced_category = str(metadata.get("forced_resupply_category") or "")
        if forced_category:
            return _RESUPPLY_INTENT_CATEGORY_TO_OBJECTIVE_KEY.get(forced_category)
        return None

    return _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get(intent_kind)


def _migrate_brain_v3_context(agent: Dict[str, Any]) -> None:
    legacy_context = agent.pop("_v2_context", None)
    if "brain_v3_context" not in agent and isinstance(legacy_context, dict):
        agent["brain_v3_context"] = legacy_context


_OBJECTIVE_MEMORY_USED_FOR: Dict[str, str] = {
    "RESTORE_FOOD": "find_food",
    "RESTORE_WATER": "find_water",
    "SELL_ARTIFACTS": "sell_artifacts",
    "GET_MONEY_FOR_RESUPPLY": "find_money_source",
    "REACH_SAFE_SHELTER": "avoid_threat",
    "WAIT_IN_SHELTER": "find_shelter",
}

_WAIT_ALLOWED_OBJECTIVES: frozenset[str] = frozenset({"IDLE", "WAIT_IN_SHELTER", "REACH_SAFE_SHELTER"})
_NON_WAIT_ACTIONABLE_OBJECTIVES: frozenset[str] = frozenset(
    {
        "RESTORE_FOOD", "RESTORE_WATER", "GET_MONEY_FOR_RESUPPLY", "SELL_ARTIFACTS",
        # Hunt objectives that always have a concrete next step
        "LOCATE_TARGET", "TRACK_TARGET", "ENGAGE_TARGET", "CONFIRM_KILL",
    }
)

# Default risk_tolerance used when an agent or item does not specify one.
DEFAULT_RISK_TOLERANCE = 0.5

# Wealth buffer all stalkers must accumulate before pursuing their global goal.
# Valid range for material_threshold is [3000, 10000].
DEFAULT_MATERIAL_THRESHOLD = 3000
MATERIAL_THRESHOLD_MIN = 3000
MATERIAL_THRESHOLD_MAX = 10000

# Wealth required to COMPLETE the get_rich global goal (random per agent: 50 000–100 000).
GET_RICH_COMPLETION_MIN = 50_000
GET_RICH_COMPLETION_MAX = 100_000

# Emission (Выброс) mechanic constants
# 1 game day = 1440 turns (1 turn = 1 minute)
_EMISSION_MIN_INTERVAL_TURNS = 1440   # earliest next emission: 1 game day after last
_EMISSION_MAX_INTERVAL_TURNS = 2880   # latest next emission: 2 game days after last
_EMISSION_MIN_DURATION_TURNS = 5      # shortest emission: 5 game minutes
_EMISSION_MAX_DURATION_TURNS = 10     # longest emission: 10 game minutes
_EMISSION_WARNING_TURNS = 30          # legacy flee-window kept for emergency fallback (active emission)
_EMISSION_WARNING_MIN_TURNS = 10      # warning observation written min 10 turns before emission
_EMISSION_WARNING_MAX_TURNS = 15      # warning observation written max 15 turns before emission
# Terrain types where stalkers are killed by an emission
_EMISSION_DANGEROUS_TERRAIN: frozenset = frozenset({
    "plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge",
})

# Human-readable Russian names for terrain types (used in memory summaries).
_TERRAIN_NAME_RU: Dict[str, str] = {
    "plain": "Равнина",
    "hills": "Холмы",
    "swamp": "Болото",
    "field_camp": "Полевой лагерь",
    "slag_heaps": "Шлаковые отвалы",
    "bridge": "Мост",
    "industrial": "Промзона",
    "buildings": "Постройки",
    "military_buildings": "Военные объекты",
    "hamlet": "Хутор",
    "farm": "Ферма",
    "dungeon": "Подземелье",
    "tunnel": "Тоннель",
    "x_lab": "Лаборатория «Икс»",
    "scientific_bunker": "Научный бункер",
}

# Anomaly search parameters for the get_rich NPC goal path.
# Search radius is skill-based: 4 + agent["skill_stalker"] hops × default travel_time minutes.
# Distance penalty is expressed per travel-minute so that long roads (high travel_time) are
# correctly penalised relative to short ones.  With a default edge of 12 min, this is
# equivalent to the old per-hop penalty of 5.0 / 12 ≈ 0.42 per min.
_ANOMALY_DISTANCE_PENALTY_PER_MIN = 5.0 / 12.0  # Score reduction per travel-minute of distance
_ANOMALY_SCORE_NOISE = 0.5             # Small random jitter to break ties between equal-scoring locations
_ANOMALY_RISK_MISMATCH_PENALTY = 150.0  # Penalty for full risk-tolerance mismatch; exceeds max base score (10*10=100) so agents strongly prefer zones that match their risk profile
# Max travel-time radius for anomaly search (minutes).  Based on default edge of 12 min;
# previously the radius was (4 + skill_stalker) hops × 12 min/hop = (4+1)*12 = 60 min default.
_ANOMALY_SEARCH_MINUTES_PER_HOP = 12   # one "hop" in minutes (default travel_time)

# Item purchase scoring — weights must sum to 1.0.
# Three factors are balanced: risk-tolerance fit (dominant), value/quality, and inverse weight.
_ITEM_SCORE_WEIGHT_RISK = 0.7        # Risk-tolerance match is the primary factor
_ITEM_SCORE_WEIGHT_VALUE = 0.2       # Higher base value (better quality item) is preferred
_ITEM_SCORE_WEIGHT_INV_WEIGHT = 0.1  # Lighter items are preferred

# Price (in money units) that a hunter pays the trader for intel about a kill target's location.
_HUNT_INTEL_PRICE = 200


from typing import Any as _Any
_last_tick_runtime: _Any = None
_current_tick_runtime: _Any = None


def _cow_runtime() -> Any | None:
    runtime = _current_tick_runtime
    if runtime is None:
        return None
    if not hasattr(runtime, "set_agent_field"):
        return None
    return runtime


def _runtime_agent(agent_id: str, fallback_agent: Dict[str, Any]) -> Dict[str, Any]:
    runtime = _cow_runtime()
    if runtime is None:
        return fallback_agent
    try:
        return runtime.agent(agent_id)
    except Exception:
        return fallback_agent


def _runtime_inc_counter(counter_name: str) -> None:
    runtime = _cow_runtime()
    if runtime is None:
        return
    profiler = getattr(runtime, "profiler", None)
    if profiler is None:
        return
    try:
        profiler.inc(counter_name)
    except Exception:
        pass


def _runtime_set_agent_field(agent_id: str, key: str, value: Any, fallback_agent: Dict[str, Any]) -> None:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            runtime.set_agent_field(agent_id, key, value)
            return
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                agent = runtime.agent(agent_id)
                if agent.get(key) != value:
                    agent[key] = value
                    runtime.mark_agent_dirty(agent_id)
                return
            except Exception:
                return
    fallback_agent[key] = value


def _runtime_set_state_field(state: Dict[str, Any], key: str, value: Any) -> None:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            runtime.set_state_field(key, value)
            return
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                if runtime.state.get(key) != value:
                    runtime.state[key] = value
                    runtime.mark_state_dirty(key)
                return
            except Exception:
                return
    state[key] = value


def _event_driven_actions_enabled(state: Dict[str, Any]) -> bool:
    return bool(state.get("cpu_event_driven_actions_enabled", False))


def _lazy_needs_enabled(state: Dict[str, Any]) -> bool:
    return bool(state.get("cpu_lazy_needs_enabled", False))


def _ensure_ai_budget_config(state: Dict[str, Any]) -> Dict[str, Any]:
    cfg = state.get("ai_budget")
    if not isinstance(cfg, dict):
        cfg = {}
    defaults = {
        "enabled": True,
        "max_normal_decisions_per_tick": 5,
        "max_background_decisions_per_tick": 2,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    changed = False
    for key, value in defaults.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    if changed or state.get("ai_budget") is not cfg:
        _runtime_set_state_field(state, "ai_budget", cfg)
    return cfg


def _ensure_decision_queue_state(state: Dict[str, Any]) -> list[dict[str, Any]]:
    queue = state.get("decision_queue")
    if not isinstance(queue, list):
        queue = []
        _runtime_set_state_field(state, "decision_queue", queue)
    return queue


def _brain_priority_from_run_reason(agent: Dict[str, Any], run_reason: str) -> tuple[str, str]:
    if run_reason == "invalidated":
        return highest_invalidator_priority(agent), latest_invalidator_reason(agent) or run_reason
    if run_reason == "expired":
        return "normal", "cache_expired"
    if run_reason == "no_plan_or_action":
        return "normal", "no_plan_or_action"
    return "low", run_reason


_GOAL_COMPLETION_INVALIDATION_REASONS = {
    "goal_completed",
    "global_goal_completed",
    "target_death_confirmed",
    "goal_achieved",
}


def _enqueue_brain_decision(
    queue_by_agent: dict[str, dict[str, Any]],
    *,
    agent_id: str,
    agent: dict[str, Any],
    world_turn: int,
    reason: str | None = None,
    priority: str | None = None,
) -> None:
    entry_priority = normalize_priority(priority or highest_invalidator_priority(agent))
    entry_reason = str(reason or latest_invalidator_reason(agent) or "invalidated")
    existing = queue_by_agent.get(agent_id)
    if existing is not None:
        entry_priority = max_priority(entry_priority, existing.get("priority"))
        existing_queued_turn = existing.get("queued_turn")
        queued_turn = int(existing_queued_turn) if existing_queued_turn is not None else world_turn
        queued_turn = min(queued_turn, world_turn)
    else:
        queued_turn = world_turn
    queue_by_agent[agent_id] = {
        "agent_id": agent_id,
        "priority": normalize_priority(entry_priority),
        "reason": entry_reason,
        "queued_turn": int(queued_turn),
    }


def _brain_valid_until_turn(agent: Dict[str, Any], state: Dict[str, Any], world_turn: int) -> int:
    objective_key = str((agent.get("brain_v3_context") or {}).get("objective_key") or "")

    if objective_key == "WAIT_IN_SHELTER":
        # Stay valid until emission ends to avoid plan-churn spam.
        emission_ends_turn = state.get("emission_ends_turn")
        if emission_ends_turn is not None:
            return max(world_turn + 1, min(int(emission_ends_turn), world_turn + 30))
        return world_turn + 5

    if objective_key == "REACH_SAFE_SHELTER":
        # Re-evaluate frequently while still trying to reach shelter.
        return world_turn

    if objective_key == "ENGAGE_TARGET":
        return world_turn

    sched = agent.get("scheduled_action")
    if isinstance(sched, dict):
        ends_turn = int(sched.get("ends_turn") or world_turn + 1)
        return max(world_turn, min(ends_turn, world_turn + 60))

    if objective_key in {"SELL_ARTIFACTS", "GET_MONEY_FOR_RESUPPLY", "RESUPPLY_AMMO", "RESUPPLY_WEAPON"}:
        return world_turn + 20
    if objective_key == "IDLE":
        return world_turn + 60
    return world_turn + 5


def _post_brain_decision_runtime_update(agent: Dict[str, Any], state: Dict[str, Any], world_turn: int) -> None:
    br = ensure_brain_runtime(agent, world_turn)
    clear_brain_invalidators(agent)
    br["last_decision_turn"] = int(world_turn)
    br["valid_until_turn"] = int(_brain_valid_until_turn(agent, state, world_turn))
    br["decision_revision"] = int(br.get("decision_revision") or 0) + 1
    ctx = agent.get("brain_v3_context") or {}
    br["last_objective_key"] = ctx.get("objective_key") if isinstance(ctx, dict) else None
    br["last_intent_kind"] = ctx.get("intent_kind") if isinstance(ctx, dict) else None
    active_plan = agent.get("active_plan_v3") or {}
    if isinstance(active_plan, dict) and active_plan.get("plan_key") is not None:
        br["last_plan_key"] = active_plan.get("plan_key")
    elif isinstance(ctx, dict):
        br["last_plan_key"] = ctx.get("selected_plan_key")
    br["queued"] = False
    br["queued_turn"] = None
    br["queued_priority"] = None
    br["last_skip_reason"] = None


def _scheduled_action_remaining_turns(sched: Dict[str, Any], world_turn: int) -> int:
    if "ends_turn" in sched:
        return max(0, int(sched.get("ends_turn", world_turn)) - int(world_turn))
    return max(0, int(sched.get("turns_remaining", 0)))


def _migrate_scheduled_action_timing(
    agent_id: str,
    agent: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
) -> bool:
    sched = agent.get("scheduled_action")
    if not isinstance(sched, dict):
        return False
    changed = False
    if "ends_turn" not in sched:
        turns_remaining = max(0, int(sched.get("turns_remaining", 0)))
        turns_total = max(1, int(sched.get("turns_total", turns_remaining or 1)))
        started_turn = int(
            sched.get(
                "started_turn",
                int(world_turn) - max(0, turns_total - turns_remaining),
            )
        )
        sched["started_turn"] = started_turn
        sched["ends_turn"] = int(world_turn) + turns_remaining
        sched["turns_total"] = turns_total
        sched["revision"] = int(sched.get("revision", 0)) + 1
        sched.setdefault("interruptible", True)
        changed = True
    else:
        sched.setdefault("revision", 1)
        sched.setdefault("interruptible", True)

    if _event_driven_actions_enabled(state):
        from app.games.zone_stalkers.runtime.task_processor import ACTION_TYPE_TO_TASK_KIND as _ACTION_TASK_MAPPING  # noqa: PLC0415
        action_type = str(sched.get("type") or "")
        revision = int(sched.get("revision", 0))
        ends_turn = int(sched.get("ends_turn", world_turn))
        completion_kind = _ACTION_TASK_MAPPING.get(action_type, "scheduled_action_complete")
        if (
            sched.get("_completion_task_revision") != revision
            or int(sched.get("_completion_task_turn", -1)) != ends_turn
        ):
            schedule_task(
                state,
                _cow_runtime(),
                ends_turn,
                {
                    "kind": completion_kind,
                    "agent_id": agent_id,
                    "scheduled_action_revision": revision,
                },
            )
            sched["_completion_task_revision"] = revision
            sched["_completion_task_turn"] = ends_turn
            changed = True

        # Schedule sleep tick tasks for sleep actions
        if action_type == "sleep" and sched.get("_sleep_ticks_scheduled_revision") != revision:
            started_turn_v = int(sched.get("started_turn", world_turn))
            ends_turn_v = int(sched.get("ends_turn", world_turn + 1))
            elapsed = max(0, int(world_turn) - started_turn_v)
            next_idx = elapsed // SLEEP_EFFECT_INTERVAL_TURNS + 1
            tick_turn = started_turn_v + next_idx * SLEEP_EFFECT_INTERVAL_TURNS
            count = 0
            while tick_turn < ends_turn_v and count < 100:
                schedule_task(state, _cow_runtime(), tick_turn, {
                    "kind": "sleep_tick",
                    "agent_id": agent_id,
                    "scheduled_action_revision": revision,
                })
                tick_turn += SLEEP_EFFECT_INTERVAL_TURNS
                count += 1
            sched["_sleep_ticks_scheduled_revision"] = revision
            changed = True

    if changed:
        _runtime_set_agent_field(agent_id, "scheduled_action", sched, agent)
    return changed


def _runtime_mutable_location_agents(state: Dict[str, Any], location_id: str) -> list[str]:
    location = state.get("locations", {}).get(location_id, {})
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            return runtime.mutable_location_list(location_id, "agents")
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                location_rt = runtime.location(location_id)
                value = copy.deepcopy(location_rt.get("agents", []))
                location_rt["agents"] = value
                runtime.mark_location_dirty(location_id)
                return value
            except Exception:
                return list(location.get("agents", []))
    return location.setdefault("agents", [])


def _runtime_set_action_used(agent: Dict[str, Any], value: bool) -> None:
    agent_id = str(agent.get("id") or "")
    runtime = _cow_runtime()
    if runtime is not None and agent_id:
        try:
            runtime.set_agent_field(agent_id, "action_used", value)
            return
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                safe_agent = runtime.agent(agent_id)
                if safe_agent.get("action_used") != value:
                    safe_agent["action_used"] = value
                    runtime.mark_agent_dirty(agent_id)
                return
            except Exception:
                return
    agent["action_used"] = value


def _runtime_set_location_field(state: Dict[str, Any], location_id: str, key: str, value: Any) -> None:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            runtime.set_location_field(location_id, key, value)
            return
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                location = runtime.location(location_id)
                if location.get(key) != value:
                    location[key] = value
                    runtime.mark_location_dirty(location_id)
                return
            except Exception:
                return
    loc = state.get("locations", {}).get(location_id)
    if loc is not None:
        loc[key] = value


def _runtime_mutable_location_list(state: Dict[str, Any], location_id: str, key: str) -> list:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            return runtime.mutable_location_list(location_id, key)
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                location = runtime.location(location_id)
                value = copy.deepcopy(location.get(key, []))
                if not isinstance(value, list):
                    value = []
                location[key] = value
                runtime.mark_location_dirty(location_id)
                return value
            except Exception:
                pass
    loc = state.get("locations", {}).get(location_id, {})
    val = loc.get(key, [])
    if not isinstance(val, list):
        val = []
    loc[key] = val
    return val


def _runtime_set_trader_field(state: Dict[str, Any], trader_id: str, key: str, value: Any) -> None:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            runtime.set_trader_field(trader_id, key, value)
            return
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                trader = runtime.trader(trader_id)
                if trader.get(key) != value:
                    trader[key] = value
                    runtime.mark_trader_dirty(trader_id)
                return
            except Exception:
                return
    trader = state.get("traders", {}).get(trader_id)
    if trader is not None:
        trader[key] = value


def _runtime_mutable_trader_list(state: Dict[str, Any], trader_id: str, key: str) -> list:
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            return runtime.mutable_trader_list(trader_id, key)
        except Exception:
            _runtime_inc_counter("cow_mutation_fallback_errors")
            try:
                trader = runtime.trader(trader_id)
                value = copy.deepcopy(trader.get(key, []))
                if not isinstance(value, list):
                    value = []
                trader[key] = value
                runtime.mark_trader_dirty(trader_id)
                return value
            except Exception:
                pass
    trader = state.get("traders", {}).get(trader_id, {})
    val = trader.get(key, [])
    if not isinstance(val, list):
        val = []
    trader[key] = val
    return val

def tick_zone_map(state: Dict[str, Any], *, copy_state: bool = True) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Advance the world by one turn.

    Returns (new_state, events_emitted).
    """
    # Keep PR2 COW invariant: default single-tick path must not do a full deepcopy.
    # Batch mode performs one upfront copy in tick_zone_map_many and then calls
    # tick_zone_map(..., copy_state=False) for each inner tick.
    source_state = state

    # ── PR1/PR2: TickProfiler + TickRuntime/ZoneTickRuntime ─────────────────
    try:
        from app.games.zone_stalkers.performance.tick_profiler import TickProfiler as _TickProfiler
        _tick_profiler = _TickProfiler()
    except Exception:
        _tick_profiler = None
    _tick_runtime = None
    _cpu_copy_on_write_enabled = bool(source_state.get("cpu_copy_on_write_enabled", True))
    _cow_fallback_to_deepcopy = 0

    _copy_ctx = _tick_profiler.section("deepcopy_ms") if _tick_profiler else contextlib.nullcontext()
    with _copy_ctx:
        if not _cpu_copy_on_write_enabled:
            state = copy.deepcopy(source_state)
            try:
                from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime as _TickRuntime
                _tick_runtime = _TickRuntime(profiler=_tick_profiler)
            except Exception:
                _tick_runtime = None
        else:
            try:
                from app.games.zone_stalkers.runtime.zone_tick_runtime import ZoneTickRuntime as _ZoneTickRuntime
                _tick_runtime = _ZoneTickRuntime(source_state=source_state, profiler=_tick_profiler)
                state = _tick_runtime.state
            except Exception:
                _cow_fallback_to_deepcopy = 1
                state = copy.deepcopy(source_state)
                try:
                    from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime as _TickRuntime
                    _tick_runtime = _TickRuntime(profiler=_tick_profiler)
                except Exception:
                    _tick_runtime = None
    if _tick_profiler:
        try:
            _tick_profiler.set_counter("cow_fallback_to_deepcopy", int(_cow_fallback_to_deepcopy))
        except Exception:
            pass

    events: List[Dict[str, Any]] = []
    world_turn = state.get("world_turn", 1)
    global _current_tick_runtime
    previous_runtime = _current_tick_runtime
    _current_tick_runtime = _tick_runtime

    _profiler_ctx_migration = _tick_profiler.section("migration_ms") if _tick_profiler else __import__("contextlib").nullcontext()
    with _profiler_ctx_migration:
        for _agent_id in list(state.get("agents", {}).keys()):
            _raw_agent = state["agents"][_agent_id]
            # Determine if any mutation might happen before getting the COW copy.
            _missing_optional = (
                "brain_trace" not in _raw_agent
                or "active_plan_v3" not in _raw_agent
                or "memory_v3" not in _raw_agent
            )
            _is_v3_bot = is_v3_monitored_bot(_raw_agent)
            _has_legacy_context = "_v2_context" in _raw_agent
            _needs_migration_cow = _missing_optional or _has_legacy_context or _is_v3_bot
            if _needs_migration_cow:
                _agent = _runtime_agent(_agent_id, _raw_agent)
            else:
                _agent = _raw_agent
            if "brain_trace" not in _agent:
                _runtime_set_agent_field(_agent_id, "brain_trace", None, _agent)
                _agent = _runtime_agent(_agent_id, _agent)
            if "active_plan_v3" not in _agent:
                _runtime_set_agent_field(_agent_id, "active_plan_v3", None, _agent)
                _agent = _runtime_agent(_agent_id, _agent)
            if "memory_v3" not in _agent:
                _runtime_set_agent_field(_agent_id, "memory_v3", None, _agent)
                _agent = _runtime_agent(_agent_id, _agent)
            _runtime = _cow_runtime()
            if _runtime is not None and isinstance(_agent.get("scheduled_action"), dict):
                try:
                    _runtime.mutable_agent_scheduled_action(_agent_id)
                except Exception:
                    _runtime_inc_counter("cow_mutation_fallback_errors")
            _migrate_brain_v3_context(_agent)
            _migrate_legacy_scheduled_action_to_active_plan(
                _agent,
                world_turn=world_turn,
                default_sleep_hours=DEFAULT_SLEEP_HOURS,
            )
            if _is_v3_bot and get_active_plan(_agent) is not None:
                _runtime_set_agent_field(_agent_id, "action_queue", [], _agent)

    # PR 3: ensure memory_v3 structure exists, lazy-import legacy memory, run decay.
    # Decay runs every MEMORY_DECAY_INTERVAL_TURNS to reduce CPU; legacy import
    # still happens on every turn when memory_v3 is empty.
    MEMORY_DECAY_INTERVAL_TURNS = 30
    from app.games.zone_stalkers.memory.store import (  # noqa: PLC0415
        MEMORY_V3_MAX_RECORDS as _MEMORY_V3_MAX_RECORDS,
        ensure_memory_v3 as _ensure_mem_v3,
        normalize_agent_memory_state as _normalize_agent_memory_state,
    )
    from app.games.zone_stalkers.memory.decay import decay_memory as _decay_mem  # noqa: PLC0415
    _profiler_ctx_memory = _tick_profiler.section("memory_v3_ensure_ms") if _tick_profiler else __import__("contextlib").nullcontext()
    _is_decay_turn = (world_turn - 1) % MEMORY_DECAY_INTERVAL_TURNS == 0
    _MEM_IDX_KEYS = frozenset({"by_layer", "by_kind", "by_location", "by_entity", "by_item_type", "by_tag"})
    with _profiler_ctx_memory:
        for _pr3_agent_id, _pr3_agent in state.get("agents", {}).items():
            # Only COW-copy the agent if we know a mutation is actually needed.
            _mem_v3 = _pr3_agent.get("memory_v3")
            _mem_complete = (
                isinstance(_mem_v3, dict)
                and "records" in _mem_v3
                and "indexes" in _mem_v3
                and "stats" in _mem_v3
                and _MEM_IDX_KEYS.issubset(_mem_v3["indexes"])
            )
            _records_populated = bool(_mem_complete and _mem_v3.get("records"))
            _records_oversized = (
                isinstance(_mem_v3, dict)
                and isinstance(_mem_v3.get("records"), dict)
                and len(_mem_v3.get("records", {})) > _MEMORY_V3_MAX_RECORDS
            )
            _needs_normalization = not _mem_complete or _records_oversized
            _needs_memory_cow = (
                not _mem_complete
                or not _records_populated
                or _is_decay_turn
                or _needs_normalization
            )
            if _needs_memory_cow:
                _pr3_agent = _runtime_agent(_pr3_agent_id, _pr3_agent)
            if _needs_normalization:
                _normalize_agent_memory_state(_pr3_agent)
            _ensure_mem_v3(_pr3_agent)
            if _is_decay_turn:
                _decay_mem(_pr3_agent, world_turn)

    # PR5: Migrate agents that still carry hot memory_v3 to the cold store.
    # After migration the agent has memory_ref + memory_summary; memory_v3 is
    # stripped from hot state and saved to the cold blob.  Agents that already
    # have memory_ref are skipped.
    _pr5_context_id: str = str(state.get("context_id") or state.get("_context_id") or "default")
    _pr5_cold_store_enabled = bool(state.get("cpu_cold_memory_store_enabled", False))
    _pr5_redis_client = None
    try:
        from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
            get_zone_cold_memory_redis_client as _resolve_cold_redis_client,
        )
        _pr5_redis_client = _resolve_cold_redis_client(state)
    except Exception:
        _pr5_redis_client = None
    if _pr5_cold_store_enabled:
        try:
            from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                migrate_agent_memory_to_cold_store as _migrate_to_cold,
                record_agent_cold_memory_error as _record_cold_error,
            )
            for _pr5_agent_id, _pr5_raw_agent in list(state.get("agents", {}).items()):
                if not isinstance(_pr5_raw_agent, dict):
                    continue
                # Skip agents that are already migrated.
                if _pr5_raw_agent.get("memory_ref"):
                    continue
                # Only migrate agents that have memory_v3 records (non-trivial blobs).
                _pr5_mem_v3 = _pr5_raw_agent.get("memory_v3")
                if not isinstance(_pr5_mem_v3, dict):
                    continue
                _pr5_agent = _runtime_agent(_pr5_agent_id, _pr5_raw_agent)
                _migrate_to_cold(
                    context_id=_pr5_context_id,
                    agent_id=_pr5_agent_id,
                    agent=_pr5_agent,
                    redis_client=_pr5_redis_client,
                )
        except Exception as exc:
            for _pr5_raw_agent in (state.get("agents") or {}).values():
                if isinstance(_pr5_raw_agent, dict) and _pr5_raw_agent.get("memory_ref"):
                    _record_cold_error(_pr5_raw_agent, "load_failed", exc)

    # One-time migration: normalize terrain types that were removed in the v3 update
    # (urban → plain, underground → plain) and any other unknown types.
    if not state.get("_terrain_migrated_v3"):
        _valid_v3: frozenset = frozenset({
            "plain", "hills", "slag_heaps", "industrial", "buildings", "military_buildings",
            "hamlet", "farm", "field_camp", "dungeon", "x_lab", "bridge",
            "tunnel", "swamp", "scientific_bunker",
        })
        for _loc_id, _loc in state.get("locations", {}).items():
            if _loc.get("terrain_type") not in _valid_v3:
                _runtime_set_location_field(state, _loc_id, "terrain_type", "plain")
        _runtime_set_state_field(state, "_terrain_migrated_v3", True)

    _event_driven_actions = _event_driven_actions_enabled(state)
    _due_action_tasks: dict[str, set[int]] = {}
    if _event_driven_actions:
        from app.games.zone_stalkers.runtime.task_processor import process_due_tasks as _process_due_tasks  # noqa: PLC0415
        cleanup_old_tasks(state, _cow_runtime(), world_turn)
        _task_events, _due_action_tasks = _process_due_tasks(state, _cow_runtime(), world_turn, profiler=_tick_profiler)
        events.extend(_task_events)

    # 1. Process scheduled actions for each alive stalker agent
    _pr_sched = _tick_profiler.section("scheduled_actions_ms") if _tick_profiler else __import__("contextlib").nullcontext()
    _pr_sched.__enter__()
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):
            continue
        _ensure_agent_current_location_knowledge(agent, state, world_turn)
        if _lazy_needs_enabled(state):
            agent = _runtime_agent(agent_id, agent)
            _rt_needs = _cow_runtime()
            _needs_migrated = ensure_needs_state(agent, world_turn, runtime=_rt_needs, agent_id=agent_id)
            if _needs_migrated:
                _runtime_set_agent_field(agent_id, "needs_state", agent.get("needs_state"), agent)
                agent = _runtime_agent(agent_id, agent)
            _needs_state = agent.get("needs_state", {})
            _needs_revision = int(_needs_state.get("revision", 0)) if isinstance(_needs_state, dict) else 0
            _threshold_tasks = _needs_state.get("_threshold_tasks", {}) if isinstance(_needs_state, dict) else {}
            _needs_reschedule = _needs_migrated
            if not _needs_reschedule:
                for _need_key in ("hunger", "thirst", "sleepiness"):
                    for _threshold_name in ("soft", "critical"):
                        if _threshold_tasks.get(f"{_need_key}:{_threshold_name}") != _needs_revision:
                            _needs_reschedule = True
                            break
                    if _needs_reschedule:
                        break
            if _needs_reschedule:
                schedule_need_thresholds(state, _rt_needs, agent_id, agent, world_turn)
        sched = agent.get("scheduled_action")
        if sched:
            agent = _runtime_agent(agent_id, agent)
            _migrate_scheduled_action_timing(agent_id, agent, world_turn, state)
            agent = _runtime_agent(agent_id, agent)
            sched = agent.get("scheduled_action")
            if not isinstance(sched, dict):
                continue
            if _event_driven_actions:
                sched_revision = int(sched.get("revision", 0))
                due_revisions = _due_action_tasks.get(agent_id, set())
                if sched_revision not in due_revisions:
                    if (
                        sched.get("type") in ("explore_anomaly_location", "travel")
                        and not sched.get("emergency_flee")
                        and _is_emission_threat(agent, state)
                        and sched.get("interruptible", True)
                    ):
                        sched["revision"] = sched_revision + 1
                        _runtime_set_agent_field(agent_id, "scheduled_action", None, agent)
                    continue
            if is_v3_monitored_bot(agent):
                try:
                    monitor_result = assess_scheduled_action_v3(
                        agent_id=agent_id,
                        agent=agent,
                        scheduled_action=sched,
                        state=state,
                        world_turn=world_turn,
                    )
                except Exception:
                    monitor_result = PlanMonitorResult(
                        decision="continue",
                        reason="monitor_error",
                    )
                    write_plan_monitor_trace(
                        agent,
                        world_turn=world_turn,
                        decision="continue",
                        reason="monitor_error",
                        summary="PlanMonitor дал ошибку; продолжаю legacy действие.",
                        scheduled_action_type=sched.get("type"),
                        state=state,
                    )
                if monitor_result.decision == "abort":
                    events.extend(
                        _handle_v3_monitor_abort(
                            agent_id,
                            agent,
                            sched,
                            state,
                            world_turn,
                            monitor_result=monitor_result,
                            add_memory=_add_memory,
                            should_write_plan_monitor_memory_event=should_write_plan_monitor_memory_event,
                            sleep_effect_interval_turns=SLEEP_EFFECT_INTERVAL_TURNS,
                        )
                    )
                    continue
                write_plan_monitor_trace(
                    agent,
                    world_turn=world_turn,
                    decision="continue",
                    reason=monitor_result.reason,
                    summary=(
                        f"Продолжаю {sched.get('type')} — {monitor_result.reason}."
                        + (
                            f" objective={monitor_result.debug_context.get('objective_key')},"
                            f" intent={monitor_result.debug_context.get('intent_kind')}."
                            if monitor_result.debug_context
                            and (
                                monitor_result.debug_context.get("objective_key")
                                or monitor_result.debug_context.get("intent_kind")
                            )
                            else ""
                        )
                        + (
                            f" Не атакую: {', '.join(monitor_result.debug_context.get('not_attacking_reasons') or [])}."
                            if monitor_result.debug_context
                            and monitor_result.debug_context.get("not_attacking_reasons")
                            else ""
                        )
                    ),
                    scheduled_action_type=sched.get("type"),
                    extra_context=monitor_result.debug_context,
                    state=state,
                )
            new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
            events.extend(new_evs)

    _pr_sched.__exit__(None, None, None)

    # 2. Degrade survival needs and apply critical penalties (once per in-game hour)
    _pr_survive = _tick_profiler.section("survival_needs_ms") if _tick_profiler else __import__("contextlib").nullcontext()
    _pr_survive.__enter__()
    # Determine current minute after advancing (before committing to state)
    _new_minute = (state.get("world_minute", 0) + 1) % 60
    if _new_minute == 0:  # hour boundary reached
        for agent_id, agent in state.get("agents", {}).items():
            agent = _runtime_agent(agent_id, agent)
            if not agent.get("is_alive", True):
                continue
            if agent.get("has_left_zone"):  # departed agents need no hunger/thirst/sleep degradation
                continue

            if not _lazy_needs_enabled(state):
                # Legacy: increment needs each hour
                _runtime_set_agent_field(
                    agent_id,
                    "hunger",
                    min(100, agent.get("hunger", 0) + HUNGER_INCREASE_PER_HOUR),
                    agent,
                )
                _runtime_set_agent_field(
                    agent_id,
                    "thirst",
                    min(100, agent.get("thirst", 0) + THIRST_INCREASE_PER_HOUR),
                    agent,
                )
                _runtime_set_agent_field(
                    agent_id,
                    "sleepiness",
                    min(100, agent.get("sleepiness", 0) + SLEEPINESS_INCREASE_PER_HOUR),
                    agent,
                )
                agent = _runtime_agent(agent_id, agent)

            # Critical damage: skip if event-driven+lazy (handled by need_damage tasks)
            if _lazy_needs_enabled(state) and _event_driven_actions_enabled(state):
                continue

            # Determine effective need values for damage checks
            if _lazy_needs_enabled(state):
                _eff_thirst = get_need(agent, "thirst", world_turn)
                _eff_hunger = get_need(agent, "hunger", world_turn)
            else:
                _eff_thirst = agent.get("thirst", 0)
                _eff_hunger = agent.get("hunger", 0)

            if _tick_runtime:
                from app.games.zone_stalkers.runtime.dirty import mark_agent_dirty as _mad
                _mad(_tick_runtime, agent_id)
            # Critical thirst causes HP damage faster than hunger
            if _eff_thirst >= CRITICAL_THIRST_THRESHOLD:
                _runtime_set_agent_field(
                    agent_id,
                    "hp",
                    max(0, agent.get("hp", 0) - HP_DAMAGE_PER_HOUR_CRITICAL_THIRST),
                    agent,
                )
                agent = _runtime_agent(agent_id, agent)
                invalidate_brain(
                    agent,
                    _cow_runtime(),
                    reason="critical_thirst",
                    priority="urgent",
                    world_turn=world_turn,
                )
            if _eff_hunger >= CRITICAL_HUNGER_THRESHOLD:
                _runtime_set_agent_field(
                    agent_id,
                    "hp",
                    max(0, agent.get("hp", 0) - HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER),
                    agent,
                )
                agent = _runtime_agent(agent_id, agent)
                invalidate_brain(
                    agent,
                    _cow_runtime(),
                    reason="critical_hunger",
                    priority="urgent",
                    world_turn=world_turn,
                )
            if int(agent.get("hp", 0)) <= max(1, int(agent.get("max_hp", 100) * 0.3)):
                invalidate_brain(
                    agent,
                    _cow_runtime(),
                    reason="critical_hp",
                    priority="urgent",
                    world_turn=world_turn,
                )
            if agent["hp"] <= 0 and agent.get("is_alive", True):
                if _tick_runtime:
                    from app.games.zone_stalkers.runtime.dirty import mark_agent_dirty as _mad3
                    _mad3(_tick_runtime, agent_id)
                _hunger = agent.get("hunger", 0)
                _thirst = agent.get("thirst", 0)
                _death_cause_str = (
                    "обезвоживание" if _thirst >= _hunger else "голод"
                )
                _mark_agent_dead(
                    agent_id=agent_id,
                    agent=agent,
                    state=state,
                    world_turn=world_turn,
                    cause="starvation_or_thirst",
                    memory_title="💀 Смерть",
                    memory_effects={"action_kind": "death", "cause": "starvation_or_thirst",
                                    "hunger": _hunger, "thirst": _thirst},
                    memory_summary=f"Погиб от {_death_cause_str} — голод {_hunger}%, жажда {_thirst}%",
                    events=events,
                )


    _pr_survive.__exit__(None, None, None)

    # 2b. Emission (Выброс) mechanic — replaces the old midnight artifact spawn.
    # The emission spawns artifacts in anomaly locations, kills stalkers on open
    # terrain (plains / hills), and notifies all alive stalkers via memory.
    _emission_rng = random.Random(str(state.get("seed", 0)) + str(world_turn))

    # ── Emission warning: write "скоро выброс" observation once, 10–15 turns ──
    # before the scheduled emission.  A random per-cycle offset is chosen when
    # no offset is cached yet; the observation is broadcast exactly once
    # (tracked by `emission_warning_written_turn`).
    _emission_scheduled = state.get("emission_scheduled_turn")
    if (
        _emission_scheduled is not None
        and not state.get("emission_active", False)
        and state.get("emission_warning_written_turn") is None
    ):
        _turns_until = _emission_scheduled - world_turn
        # Determine the exact warning turn for this cycle (choose once, cache it).
        if state.get("emission_warning_offset") is None:
            # Pick a random offset: warn between MIN and MAX turns before emission
            _warn_offset = _emission_rng.randint(
                _EMISSION_WARNING_MIN_TURNS, _EMISSION_WARNING_MAX_TURNS
            )
            _runtime_set_state_field(state, "emission_warning_offset", _warn_offset)
        _warn_offset = state["emission_warning_offset"]
        if _turns_until == _warn_offset:
            _runtime_set_state_field(state, "emission_warning_written_turn", world_turn)
            for _ew_agent_id, _ew_agent in state.get("agents", {}).items():
                if not _ew_agent.get("is_alive", True):
                    continue
                _add_memory(
                    _ew_agent, world_turn, state, "observation",
                    "⚠️ Скоро выброс!",
                    {"action_kind": "emission_imminent",
                     "turns_until": _warn_offset,
                     "emission_scheduled_turn": _emission_scheduled},
                    summary=f"Скоро будет выброс — примерно через {_warn_offset} ходов",
                )
            events.append({
                "event_type": "emission_warning",
                "payload": {
                    "world_turn": world_turn,
                    "emission_scheduled_turn": _emission_scheduled,
                    "turns_until": _warn_offset,
                },
            })

    # ── Start emission when the scheduled turn is reached ──────────────────────
    if not state.get("emission_active", False) and state.get("emission_scheduled_turn") == world_turn:
        _emission_duration = _emission_rng.randint(
            _EMISSION_MIN_DURATION_TURNS, _EMISSION_MAX_DURATION_TURNS
        )
        _runtime_set_state_field(state, "emission_active", True)
        _runtime_set_state_field(state, "emission_ends_turn", world_turn + _emission_duration)

        # Spawn artifacts in all anomaly locations during emission start
        for _em_loc_id, _em_loc in state.get("locations", {}).items():
            if _em_loc.get("anomaly_activity", 0) <= 0:
                continue
            _spawn_chance = _em_loc.get("anomaly_activity", 0) / 10.0
            if _emission_rng.random() < _spawn_chance:
                _art_type = _emission_rng.choice(list(ARTIFACT_TYPES.keys()))
                _art_info = ARTIFACT_TYPES[_art_type]
                _art_id = f"art_emission_{_em_loc_id}_{world_turn}"
                _em_artifacts = _runtime_mutable_location_list(state, _em_loc_id, "artifacts")
                _em_artifacts.append({
                    "id": _art_id,
                    "type": _art_type,
                    "name": _art_info["name"],
                    "value": _art_info["value"],
                })
                events.append({
                    "event_type": "artifact_spawned",
                    "payload": {"location_id": _em_loc_id, "artifact_type": _art_type},
                })

        # Kill stalkers caught in the open (dangerous terrain) during emission
        for _em_agent_id, _em_agent in state.get("agents", {}).items():
            if not _em_agent.get("is_alive", True):
                continue
            _em_agent_loc = state.get("locations", {}).get(_em_agent.get("location_id", ""), {})
            _em_terrain = _em_agent_loc.get("terrain_type", "")
            if _em_terrain in _EMISSION_DANGEROUS_TERRAIN:
                _em_loc_name = _em_agent_loc.get("name", _em_agent.get("location_id", "?"))
                _mark_agent_dead(
                    agent_id=_em_agent_id,
                    agent=_em_agent,
                    state=state,
                    world_turn=world_turn,
                    cause="emission",
                    memory_title="💀 Смерть",
                    memory_effects={"action_kind": "death", "cause": "emission",
                                    "location_id": _em_agent.get("location_id"),
                                    "terrain": _em_terrain},
                    memory_summary=f"Погиб от выброса в локации «{_em_loc_name}» (местность: {_TERRAIN_NAME_RU.get(_em_terrain, _em_terrain)})",
                    events=events,
                )

        # Write observation memory for every still-alive stalker
        _em_world_day = state.get("world_day", 1)
        _em_world_hour = state.get("world_hour", 0)
        _em_world_minute = state.get("world_minute", 0)
        for _em_agent_id, _em_agent in state.get("agents", {}).items():
            if not _em_agent.get("is_alive", True):
                continue
            _add_memory(
                _em_agent, world_turn, state, "observation",
                "⚡ Начался выброс!",
                {"action_kind": "emission_started"},
                summary="Начался выброс — все на открытой местности в опасности",
            )

        events.append({
            "event_type": "emission_started",
            "payload": {
                "world_turn": world_turn,
                "world_day": state.get("world_day", 1),
                "world_hour": state.get("world_hour", 0),
                "world_minute": state.get("world_minute", 0),
                "ends_turn": state["emission_ends_turn"],
            },
        })

    # ── End emission when its duration has passed ──────────────────────────────
    if state.get("emission_active", False) and world_turn >= state.get("emission_ends_turn", 0):
        _runtime_set_state_field(state, "emission_active", False)
        # Schedule next emission 1–2 in-game days from now
        _next_emission_delay = _emission_rng.randint(
            _EMISSION_MIN_INTERVAL_TURNS, _EMISSION_MAX_INTERVAL_TURNS
        )
        _runtime_set_state_field(state, "emission_scheduled_turn", world_turn + _next_emission_delay)
        # Reset warning state so the next cycle gets a fresh random offset.
        _runtime_set_state_field(state, "emission_warning_written_turn", None)
        _runtime_set_state_field(state, "emission_warning_offset", None)

        # Write observation memory for every still-alive stalker.
        # This is the signal used to invalidate stale confirmed-empty zone records:
        # a stalker who sees this entry knows that artifacts may have been refilled
        # and will be willing to re-explore zones it previously found empty.
        for _em_agent_id, _em_agent in state.get("agents", {}).items():
            if not _em_agent.get("is_alive", True):
                continue
            _add_memory(
                _em_agent, world_turn, state, "observation",
                "✅ Выброс закончился",
                {"action_kind": "emission_ended"},
                summary="Выброс закончился — аномальные зоны обновились, возможно появились артефакты",
            )

        events.append({
            "event_type": "emission_ended",
            "payload": {
                "world_turn": world_turn,
                "next_emission_turn": state["emission_scheduled_turn"],
            },
        })

    # 2c. Initialize combat_interactions dict if missing
    if "combat_interactions" not in state:
        state["combat_interactions"] = {}

    # 2d. Process all active combat interactions BEFORE normal bot decisions
    _combat_events = _process_all_combat_interactions(state, world_turn)
    events.extend(_combat_events)

    # 3. AI bot agent decisions — NPC Brain v3 pipeline
    _pr_npc = _tick_profiler.section("npc_brain_total_ms") if _tick_profiler else __import__("contextlib").nullcontext()
    _pr_npc.__enter__()
    _npc_brain_decision_count = 0
    _npc_brain_skipped_count = 0
    _npc_brain_budget_deferred_count = 0
    _ai_budget = _ensure_ai_budget_config(state)
    _existing_queue = _ensure_decision_queue_state(state)
    _queue_by_agent: dict[str, dict[str, Any]] = {}
    for _q in _existing_queue:
        if not isinstance(_q, dict):
            continue
        _q_agent_id = str(_q.get("agent_id") or "")
        if not _q_agent_id:
            continue
        _queue_by_agent[_q_agent_id] = {
            "agent_id": _q_agent_id,
            "priority": normalize_priority(str(_q.get("priority") or "normal")),
            "reason": str(_q.get("reason") or "queued"),
            "queued_turn": int(_q.get("queued_turn") or world_turn),
        }

    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("controller", {}).get("kind") != "bot":
            continue

        agent = _runtime_agent(agent_id, agent)
        br = ensure_brain_runtime(agent, world_turn)

        if agent.get("has_left_zone"):
            br["last_skip_reason"] = "left_zone"
            _npc_brain_skipped_count += 1
            continue
        # Urgent-invalidated agents bypass scheduled_action so the brain can react
        # immediately to critical events (combat, emission, critical needs).
        _enqueue_urgent_invalidated = (
            br.get("invalidated") and highest_invalidator_priority(agent) == "urgent"
        )
        # Track any invalidation so plan-running agents are still enqueued for brain re-eval.
        _enqueue_invalidated = bool(br.get("invalidated"))
        _goal_completion_invalidated = _enqueue_invalidated and any(
            isinstance(inv, dict)
            and str(inv.get("reason") or "") in _GOAL_COMPLETION_INVALIDATION_REASONS
            for inv in br.get("invalidators", [])
        )
        _should_bypass_active_plan_for_brain = _enqueue_urgent_invalidated or _goal_completion_invalidated
        if agent.get("scheduled_action") and not _enqueue_urgent_invalidated:
            br["last_skip_reason"] = "scheduled_action"
            _npc_brain_skipped_count += 1
            continue
        if agent.get("action_used"):
            br["last_skip_reason"] = "action_used"
            _npc_brain_skipped_count += 1
            continue
        # Skip bot decisions for agents in active combat (not fled)
        if _agent_in_active_combat(agent_id, state):
            br["last_skip_reason"] = "active_combat"
            _npc_brain_skipped_count += 1
            continue

        _has_active_plan = is_v3_monitored_bot(agent) and get_active_plan(agent) is not None
        if _has_active_plan and not _should_bypass_active_plan_for_brain:
            handled, active_plan_events = _process_active_plan_v3(
                agent_id,
                agent,
                state,
                world_turn,
                add_memory=_add_memory,
            )
            events.extend(active_plan_events)
            if handled:
                br["last_skip_reason"] = "active_plan_runtime"
                _npc_brain_skipped_count += 1
                # Per PR4 spec: "NPC follows plan until invalidated". Even when the active
                # plan advances its current step, a non-urgent invalidation must still
                # produce a queued brain re-evaluation so the invalidation is not silently
                # lost. The plan step runs now; the brain re-evaluates later (budget allows).
                if _enqueue_invalidated:
                    _enqueue_brain_decision(
                        _queue_by_agent,
                        agent_id=agent_id,
                        agent=agent,
                        world_turn=world_turn,
                    )
                continue

        should_run, run_reason = should_run_brain(agent, world_turn)
        if not should_run:
            br["last_skip_reason"] = run_reason
            _npc_brain_skipped_count += 1
            continue

        priority, queue_reason = _brain_priority_from_run_reason(agent, run_reason)
        if run_reason == "invalidated":
            priority = max_priority(priority, highest_invalidator_priority(agent))
        _enqueue_brain_decision(
            _queue_by_agent,
            agent_id=agent_id,
            agent=agent,
            world_turn=world_turn,
            reason=str(queue_reason),
            priority=priority,
        )

    _queue_items: list[dict[str, Any]] = []
    _max_delay = max(1, int(_ai_budget.get("max_decision_delay_turns", 10)))
    for _entry in _queue_by_agent.values():
        _agent_id = _entry["agent_id"]
        _agent = _runtime_agent(_agent_id, state.get("agents", {}).get(_agent_id, {}))
        _br = ensure_brain_runtime(_agent, world_turn)
        _priority = normalize_priority(_entry.get("priority"))
        _queued_turn = int(_entry.get("queued_turn") or world_turn)
        _delay = max(0, world_turn - _queued_turn)
        _promotions = _delay // _max_delay
        for _ in range(_promotions):
            _priority = promote_priority(_priority)
            if _priority == "urgent":
                break
        _entry["effective_priority"] = _priority
        _br["queued"] = True
        _br["queued_turn"] = _queued_turn
        _br["queued_priority"] = _priority
        _queue_items.append(_entry)

    _queue_items.sort(
        key=lambda item: (
            -{"low": 0, "normal": 1, "high": 2, "urgent": 3}[normalize_priority(item.get("effective_priority"))],
            int(item.get("queued_turn") or world_turn),
            str(item.get("agent_id") or ""),
        )
    )

    _budget_enabled = bool(_ai_budget.get("enabled", True))
    _max_normal = max(0, int(_ai_budget.get("max_normal_decisions_per_tick", 5)))
    _max_background = max(0, int(_ai_budget.get("max_background_decisions_per_tick", 2)))
    _urgent_bypass = bool(_ai_budget.get("urgent_decisions_ignore_budget", True))
    _normal_used = 0
    _background_used = 0
    _next_queue: list[dict[str, Any]] = []

    for _entry in _queue_items:
        _agent_id = str(_entry.get("agent_id") or "")
        if not _agent_id:
            continue
        _agent = _runtime_agent(_agent_id, state.get("agents", {}).get(_agent_id, {}))
        _br = ensure_brain_runtime(_agent, world_turn)
        _priority = normalize_priority(_entry.get("effective_priority"))

        _blocked_reason = None
        if not _agent.get("is_alive", True):
            _blocked_reason = "dead"
        elif _agent.get("has_left_zone"):
            _blocked_reason = "left_zone"
        elif _agent.get("scheduled_action") and _priority != "urgent":
            # Urgent priority bypasses scheduled_action so the brain can react
            # immediately to critical events (combat, emission, critical needs).
            _blocked_reason = "scheduled_action"
        elif _agent.get("action_used"):
            _blocked_reason = "action_used"
        elif _agent.get("controller", {}).get("kind") != "bot":
            _blocked_reason = "not_bot"
        elif _agent_in_active_combat(_agent_id, state):
            _blocked_reason = "active_combat"

        if _blocked_reason is not None:
            _br["last_skip_reason"] = _blocked_reason
            _npc_brain_skipped_count += 1
            _next_queue.append(
                {
                    "agent_id": _agent_id,
                    "priority": _priority,
                    "reason": _entry.get("reason"),
                    "queued_turn": int(_entry.get("queued_turn") or world_turn),
                }
            )
            continue

        _run_now = True
        if _budget_enabled:
            if _priority == "urgent" and _urgent_bypass:
                _run_now = True
            elif _priority in {"high", "normal"}:
                _run_now = _normal_used < _max_normal
            else:
                _run_now = _background_used < _max_background

        if not _run_now:
            _br["last_skip_reason"] = "budget_deferred"
            _npc_brain_skipped_count += 1
            _npc_brain_budget_deferred_count += 1
            _next_queue.append(
                {
                    "agent_id": _agent_id,
                    "priority": _priority,
                    "reason": _entry.get("reason"),
                    "queued_turn": int(_entry.get("queued_turn") or world_turn),
                }
            )
            continue

        # PR5: Load cold memory on demand before brain decision.
        if _agent.get("memory_ref") and not _is_routine_scheduled_action_continuation(_agent):
            try:
                from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                    ensure_agent_memory_loaded as _ensure_cold_mem,
                    record_agent_cold_memory_error as _record_cold_error,
                )
                _ensure_cold_mem(
                    context_id=_pr5_context_id,
                    agent_id=_agent_id,
                    agent=_agent,
                    redis_client=_pr5_redis_client,
                )
            except Exception as exc:
                _record_cold_error(_agent, "load_failed", exc)

        bot_evs = _run_npc_brain_v3_decision(_agent_id, _agent, state, world_turn)
        _npc_brain_decision_count += 1
        events.extend(bot_evs)
        _agent = _runtime_agent(_agent_id, _agent)
        _post_brain_decision_runtime_update(_agent, state, world_turn)

        if _budget_enabled:
            if _priority in {"high", "normal"}:
                _normal_used += 1
            elif _priority == "low":
                _background_used += 1

    # Hard cap queue size to prevent unbounded state growth across long runs.
    _runtime_set_state_field(state, "decision_queue", _next_queue[:MAX_DECISION_QUEUE_SIZE])

    _pr_npc.__exit__(None, None, None)

    debug_brain_trace_enabled = state.get("debug_brain_trace_enabled", False)
    debug_brain_trace_agent_ids = set(state.get("debug_brain_trace_agent_ids") or [])
    for _agent in state.get("agents", {}).values():
        if not is_v3_monitored_bot(_agent):
            continue
        if debug_brain_trace_enabled:
            if not debug_brain_trace_agent_ids or _agent.get("id") in debug_brain_trace_agent_ids:
                ensure_brain_trace_for_tick(_agent, world_turn=world_turn, state=state)
        # When disabled: brain_trace is not grown; only latest_decision_summary
        # (written inside _run_npc_brain_v3_decision) is kept.

    _stale_cleanup_metrics = cleanup_stale_corpses(state)
    try:
        from app.games.zone_stalkers.memory.memory_events import (  # noqa: PLC0415
            record_stale_corpse_cleanup_metrics as _record_stale_cleanup_metrics,
        )
        _record_stale_cleanup_metrics(_stale_cleanup_metrics)
    except Exception:
        pass
    _update_corpse_visibility(state=state, world_turn=world_turn)

    # 3b. Per-turn location observations for every alive stalker agent.
    # Writes a new observation entry only when content has changed since the last
    # entry of the same category; merges repeated observations via the semantic
    # merge system; marks stale entries before writing new ones.
    # Gate observations to every LOCATION_OBSERVATION_INTERVAL_TURNS turns to
    # reduce memory churn; travel-triggered observations still happen inline.
    LOCATION_OBSERVATION_INTERVAL_TURNS = 10
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):  # departed agents are no longer at any location
            continue
        if agent.get("archetype") != "stalker_agent":
            continue
        loc_id = agent.get("location_id")
        if loc_id:
            # Write observation every LOCATION_OBSERVATION_INTERVAL_TURNS turns.
            # (observation on travel is still written inline in travel logic)
            if (world_turn - 1) % LOCATION_OBSERVATION_INTERVAL_TURNS == 0:
                _write_location_observations(agent_id, agent, loc_id, state, world_turn)

    # 4. Advance world time (1 tick = 1 minute)
    world_minute = state.get("world_minute", 0)
    world_hour = state.get("world_hour", 6)
    world_day = state.get("world_day", 1)
    world_minute += 1
    if world_minute >= 60:
        world_minute = 0
        world_hour += 1
        if world_hour >= 24:
            world_hour = 0
            world_day += 1
            events.append({"event_type": "day_changed", "payload": {"world_day": world_day}})
    _runtime_set_state_field(state, "world_minute", world_minute)
    _runtime_set_state_field(state, "world_hour", world_hour)
    _runtime_set_state_field(state, "world_day", world_day)
    _runtime_set_state_field(state, "world_turn", world_turn + 1)

    # 5. Reset action_used for next turn
    for agent_id, agent in state.get("agents", {}).items():
        if agent.get("is_alive", True) and not agent.get("has_left_zone"):
            _runtime_set_agent_field(agent_id, "action_used", False, agent)
        _v3_keys = [k for k in list(agent.keys()) if k.startswith("_v3_")]
        if _v3_keys:
            agent = _runtime_agent(agent_id, agent)
            for _k in _v3_keys:
                agent.pop(_k, None)

    # Check game-over (max_turns=0 means unlimited)
    max_turns = state.get("max_turns", 0)
    if max_turns and state["world_turn"] > max_turns:
        state["game_over"] = True
        events.append({"event_type": "game_over", "payload": {"reason": "max_turns_reached"}})

    events.append({
        "event_type": "world_turn_advanced",
        "payload": {
            "world_turn": state["world_turn"],
            "world_minute": world_minute,
            "world_hour": world_hour,
            "world_day": world_day,
        },
    })
    try:
        if state.get("debug_hunt_traces_enabled", False):
            from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload  # noqa: PLC0415
            refresh_interval = int(state.get("debug_hunt_traces_refresh_interval", 10))
            last_built = int(state.get("_debug_hunt_traces_built_turn", -999999))
            if state["world_turn"] - last_built >= refresh_interval:
                debug_payload = build_hunt_debug_payload(
                    state=state,
                    world_turn=state["world_turn"],
                )
                state.setdefault("debug", {}).update(debug_payload)
                state["_debug_hunt_traces_built_turn"] = state["world_turn"]
        else:
            # Clear stale debug payload when disabled to avoid state bloat
            state.get("debug", {}).pop("location_hunt_traces", None)
            state.get("debug", {}).pop("hunt_search_by_agent", None)
    except Exception:
        pass
    state.setdefault("debug", {})
    try:
        from app.games.zone_stalkers.economy.debts import (
            DEBT_ESCAPE_THRESHOLD,
            apply_due_rollovers_with_affected_debtors,
            get_debtor_debt_total,
            refresh_debtor_economic_states,
        )  # noqa: PLC0415

        current_turn = int(state.get("world_turn") or 0)
        rollover_events, affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
            state=state,
            world_turn=current_turn,
        )
        events.extend(rollover_events)
        if affected_debtor_ids:
            refresh_debtor_economic_states(state, affected_debtor_ids, world_turn=current_turn)
        for _agent_id, _agent in (state.get("agents") or {}).items():
            if not isinstance(_agent, dict):
                continue
            if not bool(_agent.get("is_alive", True)) or bool(_agent.get("has_left_zone")):
                continue
            if bool(_agent.get("_debt_escape_triggered")):
                continue
            debt_total = int(get_debtor_debt_total(state, str(_agent_id), world_turn=current_turn))
            if debt_total < int(DEBT_ESCAPE_THRESHOLD):
                continue
            _agent["_debt_escape_triggered"] = True
            _agent["debt_escape_pending"] = True
            _ensure_exit_zone_mode(
                _agent,
                reason="debt_escape",
                world_turn=current_turn,
            )
            events.append({
                "event_type": "debt_escape_triggered",
                "payload": {
                    "debtor_id": str(_agent_id),
                    "debt_total": debt_total,
                    "threshold": int(DEBT_ESCAPE_THRESHOLD),
                },
            })
    except Exception:
        pass

    # ── PR1: Store profiler counters + last runtime ───────────────────────────
    try:
        if _tick_profiler:
            _agents_all = state.get("agents", {})
            _tick_profiler.set_counter("agents_total", len(_agents_all))
            _tick_profiler.set_counter(
                "agents_processed_count",
                len([a for a in _agents_all.values() if isinstance(a, dict) and a.get("controller", {}).get("kind") == "bot"]),
            )
            _tick_profiler.set_counter("npc_brain_decision_count", _npc_brain_decision_count)
            _tick_profiler.set_counter("npc_brain_skipped_count", _npc_brain_skipped_count)
            _tick_profiler.set_counter("npc_brain_budget_deferred_count", _npc_brain_budget_deferred_count)
            _tick_profiler.set_counter("npc_brain_queue_size", len(state.get("decision_queue", [])))
            _tick_profiler.set_counter("dirty_agents_count", len(_tick_runtime.dirty_agents) if _tick_runtime else 0)
            _tick_profiler.set_counter("dirty_locations_count", len(_tick_runtime.dirty_locations) if _tick_runtime else 0)
            if _tick_runtime is not None and hasattr(_tick_runtime, "to_debug_counters"):
                for _counter_name, _counter_value in _tick_runtime.to_debug_counters().items():
                    _tick_profiler.set_counter(_counter_name, int(_counter_value))
    except Exception:
        pass
    finally:
        global _last_tick_runtime
        _last_tick_runtime = _tick_runtime
        _current_tick_runtime = previous_runtime

    # PR5: Flush dirty cold memories and strip loaded cold memory from hot state.
    # Runs after all tick processing so that memory writes during the tick
    # (observations, brain decisions, plan events) are saved to the cold store
    # before the state is persisted.
    if _pr5_cold_store_enabled:
        try:
            from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                flush_dirty_agent_memories as _flush_cold_memories,
                record_agent_cold_memory_error as _record_cold_error,
            )
            _flush_cold_memories(
                context_id=_pr5_context_id,
                state=state,
                redis_client=_pr5_redis_client,
            )
        except Exception as exc:
            for _agent in (state.get("agents") or {}).values():
                if isinstance(_agent, dict) and _agent.get("memory_ref"):
                    _record_cold_error(_agent, "save_failed", exc)

    return state, events


def _is_human_agent(state: Dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return False
    agent = (state.get("agents") or {}).get(agent_id)
    if not isinstance(agent, dict):
        return False
    controller = agent.get("controller") or {}
    if isinstance(controller, dict):
        if controller.get("kind") == "human":
            return True
        if controller.get("participant_id"):
            return True
    player_agents = state.get("player_agents") or {}
    if isinstance(player_agents, dict) and agent_id in {str(v) for v in player_agents.values()}:
        return True
    if agent.get("is_player") is True or agent.get("controlled_by") == "player":
        return True
    return False


def _event_involves_human_or_viewed_agent(
    state: Dict[str, Any],
    event: Dict[str, Any],
    *,
    viewed_agent_id: str | None = None,
) -> bool:
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if viewed_agent_id:
        candidate_fields = ("agent_id", "actor_id", "target_id", "attacker_id", "defender_id")
        for key in candidate_fields:
            value = payload.get(key)
            if value is not None and str(value) == str(viewed_agent_id):
                return True
        participants = payload.get("participants") or payload.get("agent_ids") or []
        if isinstance(participants, list) and str(viewed_agent_id) in {str(x) for x in participants}:
            return True

    candidate_agent_ids: set[str] = set()
    for key in ("agent_id", "actor_id", "target_id", "attacker_id", "defender_id"):
        value = payload.get(key)
        if value is not None:
            candidate_agent_ids.add(str(value))
    for key in ("participants", "agent_ids"):
        values = payload.get(key)
        if isinstance(values, list):
            candidate_agent_ids.update(str(x) for x in values)

    return any(_is_human_agent(state, agent_id) for agent_id in candidate_agent_ids)


def tick_zone_map_many(
    state: Dict[str, Any],
    max_ticks: int,
    *,
    stop_on_decision: bool = False,
    viewed_agent_id: str | None = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int, str | None]:
    """
    Advance world for up to max_ticks in-memory ticks with a single initial copy.
    Returns (new_state, all_events, ticks_advanced, stop_reason).
    """
    if max_ticks <= 0:
        return copy.deepcopy(state), [], 0, "no_ticks_due"

    new_state = copy.deepcopy(state)
    all_events: List[Dict[str, Any]] = []
    ticks_advanced = 0
    stop_reason: str | None = None

    for _ in range(int(max_ticks)):
        new_state, tick_events = tick_zone_map(new_state, copy_state=False)
        all_events.extend(tick_events)
        ticks_advanced += 1
        if new_state.get("game_over"):
            stop_reason = "game_over"
            break
        stop_reason = _batch_stop_reason(
            new_state,
            tick_events,
            stop_on_decision=stop_on_decision,
            viewed_agent_id=viewed_agent_id,
        )
        if stop_reason:
            break

    return new_state, all_events, ticks_advanced, stop_reason


def _batch_stop_reason(
    state: Dict[str, Any],
    tick_events: List[Dict[str, Any]],
    *,
    stop_on_decision: bool = False,
    viewed_agent_id: str | None = None,
) -> str | None:
    if state.get("game_over"):
        return "game_over"

    for ev in tick_events or []:
        event_type = ev.get("event_type")
        if event_type in {"emission_warning", "emission_started", "emission_ended"}:
            return str(event_type)
        if event_type in {"requires_resync", "serious_error", "tick_error"}:
            return "requires_resync"
        if event_type in {"zone_event_choice_required", "active_event_choice_required"}:
            return "zone_event_choice_required"
        if event_type in {"player_action_completed", "human_action_completed"}:
            return "human_action_completed"
        if event_type in {"scheduled_action_completed", "agent_scheduled_action_completed"}:
            if _event_involves_human_or_viewed_agent(state, ev, viewed_agent_id=viewed_agent_id):
                return "human_action_completed"
        if event_type == "combat_started":
            if _event_involves_human_or_viewed_agent(state, ev, viewed_agent_id=viewed_agent_id):
                return "human_combat_started"
        if event_type == "agent_died":
            if _event_involves_human_or_viewed_agent(state, ev, viewed_agent_id=viewed_agent_id):
                return "human_agent_died"
        if stop_on_decision and event_type in {"decision_required", "player_decision_required"}:
            return "player_decision_required"
    return None


# ─────────────────────────────────────────────────────────────────
# Scheduled action processing
# ─────────────────────────────────────────────────────────────────

def _mark_agent_dead(
    *,
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    cause: str,
    memory_title: str,
    memory_effects: Dict[str, Any],
    memory_summary: str,
    events: List[Dict[str, Any]],
    event_payload_extra: Dict[str, Any] | None = None,
) -> None:
    """Compatibility wrapper around the canonical death helper."""
    runtime_agent = _runtime_agent(agent_id, agent)
    temp_agent = copy.deepcopy(runtime_agent)
    kill_agent(
        agent_id=agent_id,
        agent=temp_agent,
        state=state,
        world_turn=world_turn,
        cause=cause,
        location_id=str(memory_effects.get("location_id") or runtime_agent.get("location_id") or ""),
        memory_title=memory_title,
        memory_summary=memory_summary,
        memory_effects=memory_effects,
        event_payload_extra=event_payload_extra,
        events=events,
        use_runtime=False,
    )
    for key in (
        "is_alive",
        "hp",
        "scheduled_action",
        "action_queue",
        "current_goal",
        "active_plan_v3",
        "brain_runtime",
        "brain_v3_context",
        "brain_trace",
        "memory_v3",
    ):
        _runtime_set_agent_field(agent_id, key, temp_agent.get(key), runtime_agent)
    _runtime_set_agent_field(agent_id, "action_used", False, runtime_agent)


def _update_corpse_visibility(
    *,
    state: Dict[str, Any],
    world_turn: int,
) -> None:
    for loc_id, location in state.get("locations", {}).items():
        if not isinstance(location, dict):
            continue
        corpses = location.get("corpses")
        if not isinstance(corpses, list) or not corpses:
            continue
        updated: list[dict[str, Any]] = []
        for corpse in corpses:
            if not isinstance(corpse, dict):
                continue
            decay_turn = corpse.get("decay_turn")
            is_visible = bool(corpse.get("visible", True))
            if isinstance(decay_turn, (int, float)) and int(decay_turn) <= world_turn:
                is_visible = False
            corpse["visible"] = is_visible
            if is_visible:
                updated.append(corpse)
            else:
                corpse["lootable"] = False
                corpse["inventory"] = []
                corpse["money"] = 0
                corpse["fully_looted"] = True
                dead_agent = state.get("agents", {}).get(str(corpse.get("agent_id") or ""))
                if isinstance(dead_agent, dict):
                    dead_agent["corpse_visible"] = False
        _runtime_set_location_field(state, loc_id, "corpses", updated)


def _is_emission_threat(agent: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Return True if an active emission or a live emission_imminent warning
    (not yet superseded by emission_ended) is present in the agent's memory."""
    if state.get("emission_active", False):
        return True
    _last_ended: int = 0
    _last_imminent: int = 0
    for _rec in _v3_records_desc(agent):
        _mk = _v3_action_kind(_rec)
        _mt = _v3_turn(_rec)
        if _mk == "emission_ended" and _mt > _last_ended:
            _last_ended = _mt
        elif _mk == "emission_imminent" and _mt > _last_imminent:
            _last_imminent = _mt
    return _last_imminent > _last_ended


def _process_scheduled_action(
    agent_id: str,
    agent: Dict[str, Any],
    sched: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    agent = _runtime_agent(agent_id, agent)
    runtime = _cow_runtime()
    if runtime is not None:
        try:
            sched = runtime.mutable_agent_dict(agent_id, "scheduled_action")
            agent = _runtime_agent(agent_id, agent)
        except Exception:
            pass
    action_type = sched["type"]
    if _event_driven_actions_enabled(state):
        ends_turn = int(sched.get("ends_turn", world_turn))
        started_turn = int(sched.get("started_turn", world_turn))
        turns_remaining = _scheduled_action_remaining_turns(sched, world_turn)
        turns_total = int(
            sched.get(
                "turns_total",
                max(
                    1,
                    ends_turn - started_turn,
                ),
            )
        )
        sched["turns_total"] = max(1, turns_total)
        sched["turns_remaining"] = turns_remaining
    else:
        turns_remaining = sched["turns_remaining"] - 1
        sched["turns_remaining"] = turns_remaining

    # ── Per-tick sleep interval processing ──────────────────────────────────
    # Apply effects every 30 turns regardless of whether the action completes
    # this tick.  This ensures partial recovery on abort.
    # Event-driven mode avoids per-tick sleep polling; sleep completion resolves
    # aggregate sleep effects using action timing at the due turn.
    if action_type == "sleep" and not _event_driven_actions_enabled(state):
        events.extend(_process_sleep_tick(agent_id, agent, sched, state, world_turn))
        # _process_sleep_tick may force early wake-up when sleepiness reaches 0.
        if sched.pop("wake_due_to_rested", False):
            sched["turns_remaining"] = 0
        turns_remaining = int(sched.get("turns_remaining", turns_remaining))

    if turns_remaining > 0:
        # ── Emergency interrupt: emission warning during any long-running action ──
        # If an emission becomes active or the agent received an ``emission_imminent``
        # observation, cancel the current action immediately so that the bot decision
        # loop runs on this same tick and can choose to flee or shelter.
        # This fires for both exploration (30-turn) and multi-hop travel actions.
        # EXCEPTION: travel that was itself scheduled as an emergency flee-to-shelter
        # must NEVER be interrupted — doing so creates an infinite cancel/reschedule
        # loop that prevents the agent from reaching safety.
        if action_type in ("explore_anomaly_location", "travel"):
            if not sched.get("emergency_flee") and _is_emission_threat(agent, state):
                _runtime_set_agent_field(agent_id, "scheduled_action", None, agent)
                agent = _runtime_agent(agent_id, agent)
                _int_loc_name = state.get("locations", {}).get(
                    agent.get("location_id", ""), {}
                ).get("name", "текущей позиции")
                if action_type == "explore_anomaly_location":
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю исследование из-за выброса",
                        {"action_kind": "exploration_interrupted", "reason": "emission_warning",
                         "location_id": agent.get("location_id")},
                        summary=f"Я решил прервать исследование локации «{_int_loc_name}», потому что идёт выброс",
                    )
                else:  # travel
                    _cancelled = sched.get("final_target_id", sched.get("target_id"))
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю движение из-за выброса",
                        {"action_kind": "travel_interrupted", "reason": "emission_warning",
                         "location_id": agent.get("location_id"),
                         "cancelled_target": _cancelled},
                        summary=f"Я решил прервать движение к цели, потому что идёт выброс",
                    )
                # Do NOT set action_used — let the bot decision loop run this same tick
                # so the agent can immediately choose to flee or shelter.
                return events
        return events

    # Action complete — resolve effects
    completed_sched = dict(sched)
    _runtime_set_agent_field(agent_id, "scheduled_action", None, agent)
    agent = _runtime_agent(agent_id, agent)

    if action_type == "travel":
        # target_id is the IMMEDIATE next hop; final_target_id is the ultimate goal.
        # remaining_route lists hops that come AFTER target_id.
        destination = sched.get("target_id")
        final_target = sched.get("final_target_id", destination)
        remaining_route = sched.get("remaining_route", [])
        if destination and destination in state.get("locations", {}):
            old_loc = agent["location_id"]
            # Move agent to this hop's location
            old_agents = _runtime_mutable_location_agents(state, old_loc)
            if agent_id in old_agents:
                old_agents.remove(agent_id)
            _runtime_set_agent_field(agent_id, "location_id", destination, agent)
            agent = _runtime_agent(agent_id, agent)
            _ensure_agent_current_location_knowledge(agent, state, world_turn)
            # PR1: mark agent + locations dirty via module-level current-tick runtime
            try:
                from app.games.zone_stalkers.rules.tick_rules import _current_tick_runtime as _ctr
                if _ctr is not None:
                    from app.games.zone_stalkers.runtime.dirty import mark_agent_dirty as _mad2, mark_location_dirty as _mld2
                    _mad2(_ctr, agent_id)
                    _mld2(_ctr, old_loc)
                    _mld2(_ctr, destination)
            except Exception:
                pass
            new_agents = _runtime_mutable_location_agents(state, destination)
            if agent_id not in new_agents:
                new_agents.append(agent_id)
            # Apply anomaly damage for this single hop
            hop_loc = state["locations"].get(destination, {})
            hop_anomaly_activity = hop_loc.get("anomaly_activity", 0)
            total_dmg = 0
            if hop_anomaly_activity > 0:
                _hop_rng = random.Random(agent_id + str(world_turn) + destination)
                if _hop_rng.random() < hop_anomaly_activity / 20.0:
                    total_dmg = 5 + hop_anomaly_activity
                    _runtime_set_agent_field(agent_id, "hp", max(0, agent.get("hp", 0) - total_dmg), agent)
                    agent = _runtime_agent(agent_id, agent)
            if remaining_route:
                # ── Emergency interrupt: don't continue travel when emission is active/warned ──
                # Even if the route is still open, staying put on dangerous terrain is a
                # better choice than continuing to move.  The bot decision loop runs on
                # the same tick (scheduled_action is None already) and will order a flee
                # or shelter action.
                # EXCEPTION: emergency flee travel is never interrupted (same reason as above).
                if not sched.get("emergency_flee") and _is_emission_threat(agent, state):
                    _dest_name_em = state.get("locations", {}).get(destination, {}).get("name", destination)
                    _final_name_em = state.get("locations", {}).get(final_target, {}).get("name", final_target)
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю движение из-за выброса",
                        {"action_kind": "travel_interrupted", "reason": "emission_warning",
                         "stopped_at": destination, "cancelled_target": final_target},
                        summary=f"Я решил остановиться в «{_dest_name_em}» и не продолжать движение к «{_final_name_em}», потому что идёт выброс",
                    )
                    _write_location_observations(agent_id, agent, destination, state, world_turn)
                    return events
                # More hops to go — verify the pre-computed next hop is still accessible.
                next_hop = remaining_route[0]
                conns = state["locations"].get(destination, {}).get("connections", [])
                conn_to_next = next(
                    (c for c in conns if c["to"] == next_hop), None
                )
                if conn_to_next is None or conn_to_next.get("closed"):
                    # The pre-planned route is blocked — try to re-route from here.
                    from app.games.zone_stalkers.rules.world_rules import _bfs_route
                    new_route = _bfs_route(state["locations"], destination, final_target)
                    if new_route:
                        # Alternative path found — record the re-route decision and continue.
                        next_hop = new_route[0]
                        remaining_route = new_route[1:]
                        final_name_rr = state["locations"].get(final_target, {}).get("name", final_target)
                        dest_name_rr = state["locations"].get(destination, {}).get("name", destination)
                        _add_memory(
                            agent, world_turn, state, "decision",
                            "Смена маршрута из-за недоступности перехода",
                            {"action_kind": "route_changed", "rerouted_at": destination,
                             "final_target": final_target, "new_next_hop": next_hop},
                            summary=f"Я решил сменить маршрут к «{final_name_rr}» через другой путь, потому что переход из «{dest_name_rr}» заблокирован",
                        )
                    else:
                        # Final target is completely unreachable — cancel travel.
                        final_name = state["locations"].get(final_target, {}).get("name", final_target)
                        dest_name_hop = state["locations"].get(destination, {}).get("name", destination)
                        _add_memory(
                            agent, world_turn, state, "decision",
                            "Смена решения из-за недоступности цели",
                            {"action_kind": "goal_cancelled", "cancelled_target": final_target,
                             "blocked_at": destination},
                            summary=f"Я решил отменить движение к «{final_name}», потому что цель полностью недоступна из «{dest_name_hop}»",
                        )
                        events.append({
                            "event_type": "travel_aborted",
                            "payload": {
                                "agent_id": agent_id,
                                "at": destination,
                                "final_target": final_target,
                                "reason": "route_blocked",
                            },
                        })
                        # Write observations for what's visible at the current location
                        _write_location_observations(agent_id, agent, destination, state, world_turn)
                        return events

                hop_time = next(
                    (c.get("travel_time", 12) for c in conns if c["to"] == next_hop),
                    12,
                )
                # Override the None set above — agent is still travelling.
                _next_sched: Dict[str, Any] = {
                    "type": "travel",
                    "turns_remaining": hop_time,
                    "turns_total": hop_time,
                    "target_id": next_hop,
                    "final_target_id": final_target,
                    "remaining_route": remaining_route[1:],
                    "started_turn": world_turn,
                    "ends_turn": world_turn + hop_time,
                    "revision": int(sched.get("revision", 0)) + 1,
                    "interruptible": True,
                }
                # Carry over the emergency_flee flag so that each hop of an
                # emission-flee journey is never interrupted by the emission
                # warning — otherwise the agent would cancel and restart the
                # flee on every hop, creating an infinite loop.
                if sched.get("emergency_flee"):
                    _next_sched["emergency_flee"] = True
                if sched.get("active_plan_id") is not None:
                    _next_sched["active_plan_id"] = sched.get("active_plan_id")
                    _next_sched["active_plan_step_index"] = sched.get("active_plan_step_index")
                    _next_sched["active_plan_objective_key"] = sched.get("active_plan_objective_key")
                _runtime_set_agent_field(agent_id, "scheduled_action", _next_sched, agent)
                agent = _runtime_agent(agent_id, agent)
                events.append({
                    "event_type": "travel_hop_completed",
                    "payload": {
                        "agent_id": agent_id,
                        "from": old_loc,
                        "to": destination,
                        "final_target": final_target,
                        "hops_remaining": len(remaining_route),
                        "anomaly_damage": total_dmg,
                    },
                })
                # Record the hop as an action so the full travel path appears in memory
                dest_name = state["locations"].get(destination, {}).get("name", destination)
                final_name = state["locations"].get(final_target, {}).get("name", final_target)
                _add_memory(
                    agent, world_turn, state, "action",
                    f"Проход через «{dest_name}»",
                    {"action_kind": "travel_hop", "to_loc": destination,
                     "final_target": final_target, "damage_taken": total_dmg},
                    summary=f"Прошёл через «{dest_name}» по пути к «{final_name}»" + (f", получил {total_dmg} урона от аномалий" if total_dmg > 0 else ""),
                )
                # Write observations for what's visible at this intermediate hop
                _write_location_observations(agent_id, agent, destination, state, world_turn)
            else:
                # Reached the final destination.
                events.append({
                    "event_type": "travel_completed",
                    "payload": {
                        "agent_id": agent_id,
                        "from": old_loc,
                        "to": destination,
                        "route": [],
                        "anomaly_damage": total_dmg,
                    },
                })
                _add_memory(agent, world_turn, state, "action",
                            f"Прибыл в «{state['locations'][destination].get('name', destination)}»",
                            {"action_kind": "travel_arrived", "to_loc": destination, "damage_taken": total_dmg},
                            summary=f"Прибыл в «{state['locations'][destination].get('name', destination)}»" + (f", получил {total_dmg} урона от аномалий" if total_dmg > 0 else ""))
                # Write observations for what's visible at the final destination
                _write_location_observations(agent_id, agent, destination, state, world_turn)
            if agent["hp"] <= 0:
                _travel_loc_name = state.get("locations", {}).get(destination, {}).get("name", destination)
                _mark_agent_dead(
                    agent_id=agent_id,
                    agent=agent,
                    state=state,
                    world_turn=world_turn,
                    cause="travel_anomaly",
                    memory_title="💀 Смерть",
                    memory_effects={"action_kind": "death", "cause": "travel_anomaly", "location_id": destination},
                    memory_summary=f"Погиб от урона аномалии при путешествии в локацию «{_travel_loc_name}»",
                    events=events,
                )

    elif action_type == "explore_anomaly_location":
        loc_id = agent["location_id"]
        loc = state["locations"].get(loc_id, {})
        result_evs = _resolve_exploration(agent_id, agent, loc, loc_id, state, world_turn)
        events.extend(result_evs)

    elif action_type == "sleep":
        _resolve_sleep(agent, sched, world_turn, state, agent_id=agent_id)
        turns_slept = int(
            sched.get(
                "sleep_turns_slept",
                max(0, int(sched.get("turns_total", 0)) - max(0, int(sched.get("turns_remaining", 0)))),
            )
        )
        events.append({
            "event_type": "sleep_completed",
            "payload": {
                "agent_id": agent_id,
                "hours_slept": round(turns_slept / _HOUR_IN_TURNS, 2),
                "turns_slept": turns_slept,
                "hp_after": agent["hp"],
                "radiation_after": agent["radiation"],
            },
        })

    elif action_type == "event":
        # Event context completion is handled by the event context itself
        events.append({
            "event_type": "event_participation_ended",
            "payload": {"agent_id": agent_id},
        })

    if completed_sched.get("active_plan_id") is not None:
        events.extend(
            _on_active_plan_scheduled_action_completed(
                agent_id,
                agent,
                completed_sched,
                state,
                world_turn,
                add_memory=_add_memory,
            )
        )

    # Pop next queued action if available
    queue = agent.get("action_queue", [])
    if (
        completed_sched.get("active_plan_id") is None
        and queue
        and not agent.get("scheduled_action")
    ):
        next_action = queue.pop(0)
        agent["action_queue"] = queue
        agent["scheduled_action"] = next_action
        events.append({
            "event_type": "queue_action_started",
            "payload": {"agent_id": agent_id, "action_type": next_action["type"]},
        })

    return events


def _resolve_exploration(
    agent_id: str,
    agent: Dict[str, Any],
    loc: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Resolve exploration: pick up an existing artifact or mark the location.

    New logic (replaces the old loot-table approach):
    - Checks existing artifacts on the location rather than conjuring one from thin air.
    - If artifacts exist, the agent deterministically picks one artifact.
    - If no artifacts exist, the location is marked as explored-empty.
    """
    from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
    events: List[Dict[str, Any]] = []

    # Seed using agent id + turn for per-agent variance
    rng = random.Random(agent_id + str(world_turn))

    existing_artifacts = loc.get("artifacts", [])
    found_artifacts: List[Dict[str, Any]] = []
    loc_name = loc.get("name", loc_id)
    active_plan = get_active_plan(agent)
    objective_key = (
        active_plan.objective_key
        if active_plan is not None
        else str((agent.get("brain_v3_context") or {}).get("objective_key") or "")
    )
    money_objective = objective_key in {"GET_MONEY_FOR_RESUPPLY", "FIND_ARTIFACTS"}

    artifact_found = bool(existing_artifacts)
    if artifact_found:
        # Pick one artifact at random and transfer it to the agent
        art = rng.choice(existing_artifacts)
        loc["artifacts"] = [a for a in existing_artifacts if a["id"] != art["id"]]
        agent.setdefault("inventory", []).append(art)
        found_artifacts.append(art)
        art_name = art.get("name", art.get("type", "артефакт"))

        # Action memory: agent picked up the artifact
        _add_memory(
            agent, world_turn, state, "action",
            f"Подобрал артефакт {art_name}",
            {
                "action_kind": "pickup",
                "artifact_id": art["id"],
                "artifact_type": art["type"],
                "artifact_value": art.get("value", 0),
                "location_id": loc_id,
            },
            summary=f"Подобрал артефакт «{art_name}» в локации «{loc_name}»",
        )
        # ── Survival skill XP gain ────────────────────────────────────────────
        art_value = art.get("value", 0)
        xp_gain = art_value / 100.0
        agent["skill_survival_xp"] = agent.get("skill_survival_xp", 0.0) + xp_gain
        # Level up: cost = current_level * 10 XP; cap at 20 level-ups per tick to guard against corrupt data
        for _ in range(20):
            current_level = int(agent.get("skill_survival", 1))
            xp_needed = current_level * 10
            if agent["skill_survival_xp"] >= xp_needed:
                agent["skill_survival_xp"] -= xp_needed
                agent["skill_survival"] = current_level + 1
                events.append({
                    "event_type": "skill_leveled_up",
                    "payload": {
                        "agent_id": agent_id, "skill": "survival",
                        "new_level": agent["skill_survival"],
                    },
                })
            else:
                break
        events.append({
            "event_type": "exploration_found_artifact",
            "payload": {"agent_id": agent_id, "artifact": art},
        })
        if money_objective:
            _add_memory(
                agent,
                world_turn,
                state,
                "decision",
                "📈 Прогресс добычи",
                {
                    "action_kind": "support_objective_progress",
                    "objective_key": objective_key,
                    "location_id": loc_id,
                    "artifact_type": art.get("type"),
                    "artifact_value": art.get("value", 0),
                },
                summary=f"По цели {objective_key} добыл артефакт в «{loc_name}».",
            )
    else:
        # Did not pick up an artifact — write an observation that blocks re-searching
        # this location.  We treat both a genuinely empty zone and a bad-luck miss
        # identically: the stalker notes the search as fruitless and moves on.
        # The block is lifted by a future emission that refreshes anomaly zones
        # (see `_confirmed_empty_locations` invalidation logic).
        _add_memory(
            agent, world_turn, state, "observation",
            f"Аномалия в «{loc_name}» не дала артефакт",
            {"action_kind": "explore_confirmed_empty", "location_id": loc_id},
            summary=f"Исследовал аномалию в «{loc_name}», но артефакт не найден",
        )
        if money_objective:
            recent_attempts = sum(
                1
                for rec in _v3_records_desc(agent)
                if _v3_action_kind(rec) in {"explore_confirmed_empty", "anomaly_search_exhausted"}
                and str(_v3_details(rec).get("location_id") or rec.get("location_id") or "") == loc_id
                and str(_v3_details(rec).get("objective_key") or objective_key or "") == objective_key
            )
            _cooldown_until_turn = world_turn + 180
            location_cooldowns = agent.get("location_search_cooldowns")
            if not isinstance(location_cooldowns, dict):
                location_cooldowns = {}
                agent["location_search_cooldowns"] = location_cooldowns
            location_cooldowns[str(loc_id)] = int(_cooldown_until_turn)
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                "�� Источник денег исчерпан",
                {
                    "action_kind": "anomaly_search_exhausted",
                    "objective_key": objective_key,
                    "location_id": loc_id,
                    "reason": "no_artifact_found_after_exploration",
                    "attempt_count": recent_attempts,
                    "cooldown_until_turn": _cooldown_until_turn,
                },
                summary=(
                    f"Локация «{loc_name}» временно исчерпана для {objective_key}: "
                    f"после {recent_attempts} попыток артефакты не найдены."
                ),
            )

    # Always record that an exploration action was performed (satisfies memory tests
    # and gives the agent a record of every search attempt regardless of outcome).
    _add_memory(
        agent, world_turn, state, "action",
        f"Исследовал «{loc_name}»",
        {"action_kind": "explore", "location_id": loc_id, "artifacts_found": len(found_artifacts)},
        summary=f"Исследовал локацию «{loc_name}»" + (f", нашёл {len(found_artifacts)} артефакт(ов)" if found_artifacts else ", ничего не нашёл"),
    )

    # ── Possible anomaly encounter during exploration ────────────────────────
    anomaly_activity = loc.get("anomaly_activity", 0)
    if anomaly_activity > 0 and rng.random() < 0.15 * (anomaly_activity / 10):
        dmg = 5 + anomaly_activity
        agent["hp"] = max(0, agent["hp"] - dmg)
        anomaly_type = loc.get("dominant_anomaly_type") or "unknown"
        events.append({
            "event_type": "anomaly_damage",
            "payload": {
                "agent_id": agent_id,
                "anomaly_type": anomaly_type,
                "damage": dmg,
                "hp_remaining": agent["hp"],
            },
        })
        if agent["hp"] <= 0:
            _mark_agent_dead(
                agent_id=agent_id,
                agent=agent,
                state=state,
                world_turn=world_turn,
                cause="anomaly_exploration",
                memory_title="💀 Смерть",
                memory_effects={"action_kind": "death", "cause": "anomaly_exploration",
                                "location_id": loc_id, "anomaly_type": anomaly_type},
                memory_summary=f"Погиб от аномалии «{anomaly_type}» при исследовании «{loc.get('name', loc_id)}»",
                events=events,
            )

    events.insert(0, {
        "event_type": "exploration_completed",
        "payload": {
            "agent_id": agent_id,
            "location_id": loc_id,
            "location_name": loc_name,
            "found_items": [],
            "found_artifacts": found_artifacts,
        },
    })
    return events


def _apply_sleep_interval_effect(
    agent_id: str,
    agent: Dict[str, Any],
    sched: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Apply one 30-minute sleep interval: recover sleepiness, increase hunger/thirst."""
    old_sleepiness = agent.get("sleepiness", 0)
    old_hunger = agent.get("hunger", 0)
    old_thirst = agent.get("thirst", 0)

    agent["sleepiness"] = max(0, old_sleepiness - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL)
    agent["hunger"] = min(100, old_hunger + HUNGER_INCREASE_PER_SLEEP_INTERVAL)
    agent["thirst"] = min(100, old_thirst + THIRST_INCREASE_PER_SLEEP_INTERVAL)

    interval_index = int(sched.get("sleep_intervals_applied", 0))
    sched["sleep_intervals_applied"] = interval_index + 1

    return [{
        "event_type": "sleep_interval_applied",
        "payload": {
            "agent_id": agent_id,
            "interval_index": interval_index,
            "sleepiness_before": old_sleepiness,
            "sleepiness_after": agent["sleepiness"],
            "hunger_after": agent["hunger"],
            "thirst_after": agent["thirst"],
        },
    }]


def _process_sleep_tick(
    agent_id: str,
    agent: Dict[str, Any],
    sched: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Advance sleep by one turn, applying interval effects every 30 turns."""
    events: List[Dict[str, Any]] = []

    sched.setdefault("sleep_progress_turns", 0)
    sched.setdefault("sleep_intervals_applied", 0)
    sched.setdefault("sleep_turns_slept", 0)

    # If sleepiness is already gone, stop sleeping on this tick.
    if agent.get("sleepiness", 0) <= 0:
        sched["wake_due_to_rested"] = True
        return events

    sched["sleep_progress_turns"] = int(sched["sleep_progress_turns"]) + 1
    sched["sleep_turns_slept"] = int(sched["sleep_turns_slept"]) + 1

    while sched["sleep_progress_turns"] >= SLEEP_EFFECT_INTERVAL_TURNS:
        sched["sleep_progress_turns"] -= SLEEP_EFFECT_INTERVAL_TURNS
        events.extend(_apply_sleep_interval_effect(agent_id, agent, sched, state, world_turn))
        if agent.get("sleepiness", 0) <= 0:
            sched["wake_due_to_rested"] = True
            break

    return events


def _resolve_sleep(
    agent: Dict[str, Any],
    sched: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    agent_id: str | None = None,
) -> None:
    """Write sleep-completion memory.

    Interval effects (sleepiness, hunger, thirst) were already applied during
    tick-by-tick processing via ``_process_sleep_tick``.  This function only
    records a final summary and applies HP/radiation recovery.

    ``sched`` must contain either:
    * ``hours``       – preferred; the number of in-game hours slept.
    * ``turns_total`` – fallback; total turns of the sleep action (converted via
                        ``turns_total // _HOUR_IN_TURNS``).
    """
    # Prefer the 'hours' key (set by the v3 scheduler) when no turns_total present.
    if "hours" in sched and "turns_total" not in sched:
        hours_slept = float(sched["hours"])
        turns_slept = int(hours_slept * _HOUR_IN_TURNS)
    else:
        turns_total = int(sched.get("turns_total", DEFAULT_SLEEP_HOURS * _HOUR_IN_TURNS))
        turns_slept = int(
            sched.get(
                "sleep_turns_slept",
                max(0, turns_total - max(0, int(sched.get("turns_remaining", 0)))),
            )
        )
        hours_slept = max(0.0, turns_slept / _HOUR_IN_TURNS)

    # Heal HP / reduce radiation by actual slept time (not planned full duration).
    hp_regen = min(int(15 * hours_slept), agent["max_hp"] - agent["hp"])
    agent["hp"] = min(agent["max_hp"], agent["hp"] + hp_regen)
    rad_reduce = int(5 * hours_slept)
    agent["radiation"] = max(0, agent.get("radiation", 0) - rad_reduce)
    # Reset sleepiness to 0 on sleep completion when the agent slept for at least
    # 1 full in-game hour.  Very short sleeps (< 1 h) leave residual sleepiness
    # so that gradual per-interval recovery is not bypassed artificially.
    if hours_slept >= 1.0:
        if _lazy_needs_enabled(state):
            _rt_needs = _cow_runtime()
            ensure_needs_state(agent, world_turn, runtime=_rt_needs, agent_id=agent_id)
            set_needs(agent, {"sleepiness": 0.0}, world_turn, runtime=_rt_needs, agent_id=agent_id)
            if agent_id:
                _runtime_set_agent_field(agent_id, "needs_state", agent.get("needs_state"), agent)
                schedule_need_thresholds(state, _rt_needs, agent_id, agent, world_turn)
        else:
            agent["sleepiness"] = 0
    intervals = int(sched.get("sleep_intervals_applied", 0))
    _add_memory(
        agent,
        world_turn,
        state,
        "action",
        "😴 Сон завершён",
        {
            "action_kind": "sleep_completed",
            "sleep_intervals_applied": intervals,
            "turns_total": sched.get("turns_total"),
            "turns_slept": turns_slept,
            "hours_slept": round(hours_slept, 2),
            "hp_gained": hp_regen,
            "radiation_reduced": rad_reduce,
        },
        summary=(
            f"Проснулся после сна ({hours_slept:.1f} ч): восстановлено {hp_regen} HP, "
            f"снято {rad_reduce} радиации ({intervals} интервал(ов) сна)"
        ),
    )


def _turn_to_time_label(world_turn: int) -> str:
    """Convert a *world_turn* counter to a human-readable in-game date/time label.

    Uses ``MINUTES_PER_TURN`` (defined at the top of this module) as the scale
    factor.  The frontend ``turnToTime()`` function in AgentProfileModal.tsx
    uses the same formula — keep them in sync when changing the scale factor.

    Example: world_turn=90, MINUTES_PER_TURN=1 → "День 1 · 01:30"
    """
    total_minutes = world_turn * MINUTES_PER_TURN
    day = 1 + total_minutes // (24 * 60)
    hour = (total_minutes // 60) % 24
    minute = total_minutes % 60
    return f"День {day} · {hour:02d}:{minute:02d}"


def _add_memory(
    agent: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    memory_type: str,
    title: str,
    *call_args: Any,
    reason: str = "",
    summary: str = "",
    agent_id: str | None = None,
) -> None:
    """Append a memory entry to an agent.

    Supports two calling conventions for backward compatibility:

    New-style (6 positional args + optional summary= kwarg):
        _add_memory(agent, world_turn, state, memory_type, title, effects_dict, summary="...")
    Legacy-style (7 positional args):
        _add_memory(agent, world_turn, state, memory_type, title, summary_str, effects_dict)

    The summary= keyword argument (preferred) is a plain Russian-language sentence
    describing what happened.  It is stored as memory_entry["summary"] and displayed
    in the frontend instead of the raw effects dict.
    The legacy reason= keyword is accepted for backward compat but deprecated.
    summary= (preferred) is a plain Russian-language sentence stored in memory_entry["summary"].
    """
    if len(call_args) == 2 and isinstance(call_args[0], str):
        # Legacy call: (summary_str, effects_dict)
        _legacy_summary: str = call_args[0]
        effects: Dict[str, Any] = call_args[1] if isinstance(call_args[1], dict) else {}
        # summary= kwarg takes precedence over legacy positional summary
        if not summary:
            summary = _legacy_summary
    elif len(call_args) == 1:
        # New-style call: (effects_dict,)
        effects = call_args[0] if isinstance(call_args[0], dict) else {}
    else:
        effects = {}

    # Legacy reason= kwarg (deprecated; use summary= instead)
    if reason and memory_type == "decision" and "reason" not in effects:
        effects = {**effects, "reason": reason}

    # Auto-inject aggregate tracking fields for observation-type entries.
    # This ensures every observation written via _add_memory carries the
    # new schema fields (first_seen_turn, last_seen_turn, times_seen,
    # confidence, importance, status) regardless of call site.
    if memory_type == "observation":
        from app.games.zone_stalkers.rules.memory_merge import (  # noqa: PLC0415
            new_obs_aggregate_fields,
        )
        _agg = new_obs_aggregate_fields(effects, world_turn)
        # Only fill in fields that the caller has NOT explicitly set.
        effects = {**_agg, **effects}

    memory_entry: Dict[str, Any] = {
        "world_turn": world_turn,
        "type": memory_type,
        "title": title,
        "effects": effects,
    }
    if summary:
        memory_entry["summary"] = summary

    # Resolve agent_id before accessing memory to enable COW-safe mutation.
    resolved_agent_id = agent_id
    if not resolved_agent_id:
        resolved_agent_id = agent.get("agent_id")
    if not resolved_agent_id:
        resolved_agent_id = agent.get("id") or agent.get("name")
    if not resolved_agent_id:
        # Best effort: find actual key in state["agents"] by object identity.
        for _aid, _a in state.get("agents", {}).items():
            if _a is agent:
                resolved_agent_id = _aid
                break
    if not resolved_agent_id:
        resolved_agent_id = "unknown"

    from app.games.zone_stalkers.memory.memory_events import (  # noqa: PLC0415
        write_memory_event_to_v3,
    )

    # Ensure memory_v3 is COW-copied before in-place mutations inside the bridge.
    _cow = _cow_runtime()
    if _cow is not None and resolved_agent_id and resolved_agent_id != "unknown":
        try:
            _cow.mutable_agent_dict(resolved_agent_id, "memory_v3")
            agent = _cow.agent(resolved_agent_id)
        except Exception:
            pass

    _add_mem_ctx_id: str = str(state.get("context_id") or state.get("_context_id") or "default")
    _add_mem_redis_client = None
    try:
        from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
            get_zone_cold_memory_redis_client as _resolve_cold_redis_client,
        )
        _add_mem_redis_client = _resolve_cold_redis_client(state)
    except Exception:
        _add_mem_redis_client = None

    # Write exclusively to memory_v3.
    write_memory_event_to_v3(
        agent_id=str(resolved_agent_id),
        agent=agent,
        legacy_entry=memory_entry,
        world_turn=world_turn,
        context_id=_add_mem_ctx_id,
        cold_store_enabled=bool(state.get("cpu_cold_memory_store_enabled", False)),
        redis_client=_add_mem_redis_client,
    )

    _action_kind = str(effects.get("action_kind") or "")
    _observed_kind = str(effects.get("observed") or "")
    _is_emission_interrupt = (
        _action_kind in {"travel_interrupted", "exploration_interrupted"}
        and effects.get("reason") == "emission_warning"
    )
    _inv_reason: str | None = None
    _inv_priority = "normal"
    if _action_kind == "emission_imminent":
        _inv_reason, _inv_priority = "emission_warning_started", "urgent"
    elif _action_kind == "emission_started":
        _inv_reason, _inv_priority = "emission_started", "urgent"
    elif _action_kind == "target_seen":
        _inv_reason, _inv_priority = "target_seen", "urgent"
    elif _action_kind in {"target_intel", "intel_from_stalker", "intel_from_trader"}:
        _inv_reason, _inv_priority = "target_intel_received", "high"
    elif _action_kind == "target_not_found":
        _inv_reason, _inv_priority = "target_not_found", "high"
    elif _action_kind == "travel_arrived":
        _inv_reason, _inv_priority = "agent_arrived", "high"
    elif _action_kind == "active_plan_completed":
        _inv_reason, _inv_priority = "plan_completed", "high"
    elif _action_kind in {"active_plan_aborted", "active_plan_step_failed"}:
        _inv_reason, _inv_priority = "plan_failed", "high"
    elif _action_kind in {"combat_initiated", "combat_joined"}:
        _inv_reason, _inv_priority = "combat_started", "urgent"
    elif _action_kind == "witness_source_exhausted":
        _inv_reason, _inv_priority = "target_location_exhausted", "high"
    elif _is_emission_interrupt:
        _inv_reason, _inv_priority = "emission_warning_started", "urgent"
    elif _action_kind == "pickup":
        if effects.get("artifact_type"):
            _inv_reason, _inv_priority = "artifact_found", "high"
        else:
            _inv_reason, _inv_priority = "item_acquired", "low"
    elif _action_kind in {"trade_sell", "trade_sell_for_cash", "trade_buy", "trade_decision"}:
        _inv_reason, _inv_priority = "trade_completed", "normal"
    elif _action_kind == "equip":
        _inv_reason, _inv_priority = "equipment_changed", "normal"
    elif _action_kind in {"global_goal_completed", "goal_achieved", "target_death_confirmed"}:
        _inv_reason, _inv_priority = "goal_completed", "high"
    elif _action_kind in {"exploration_interrupted", "travel_interrupted"}:
        if not _is_emission_interrupt:
            _inv_reason, _inv_priority = "action_interrupted", "high"
    elif _action_kind == "plan_monitor_abort":
        _pm_reason = str(effects.get("reason") or "")
        if _pm_reason in {"critical_hp", "critical_thirst", "critical_hunger"}:
            _inv_reason, _inv_priority = _pm_reason, "urgent"
        elif _pm_reason in {"emission_interrupt", "emission_threat"}:
            _inv_reason, _inv_priority = "emission_warning_started", "urgent"
    elif _observed_kind == "combat_wounded":
        _inv_reason, _inv_priority = "combat_damage_taken", "urgent"

    if _inv_reason:
        invalidate_brain(
            agent,
            _cow,
            reason=_inv_reason,
            priority=_inv_priority,
            world_turn=world_turn,
        )


def should_write_plan_monitor_memory_event(
    agent: Dict[str, Any],
    world_turn: int,
    *,
    action_kind: str,
    signature: Dict[str, Any],
    dedup_turns: int = PLAN_MONITOR_MEMORY_DEDUP_TURNS,
) -> bool:
    """Return False when a semantically identical memory exists in the recent window."""
    records = ((agent.get("memory_v3") or {}).get("records") or {}).values()
    for record in records:
        if not isinstance(record, dict):
            continue
        rec_turn = int(record.get("created_turn", 0))
        if world_turn - rec_turn > dedup_turns:
            continue
        details = record.get("details", {})
        if str(record.get("kind", "")) != action_kind and details.get("action_kind") != action_kind:
            continue
        if details.get("dedup_signature") == signature:
            return False
    return True


# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─
# Combat Interaction System
# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─# ─

def _agent_in_active_combat(agent_id: str, state: Dict[str, Any]) -> bool:
    """Return True if agent is non-fled participant in active combat."""
    for ci in state.get("combat_interactions", {}).values():
        if ci.get("ended", False):
            continue
        parts = ci.get("participants", {})
        if agent_id in parts and not parts[agent_id].get("fled", False):
            return True
    return False


def _get_combat_heal_item(agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return first healing item in inventory, or None."""
    for item in agent.get("inventory", []):
        item_type = item.get("type", "")
        item_info = ITEM_TYPES.get(item_type, {})
        if item_info.get("type") == "medical" and item_info.get("effects", {}).get("hp", 0) > 0:
            return item
    return None


def _agent_has_weapon_and_ammo(agent: Dict[str, Any]) -> bool:
    """Return True if agent has an equipped weapon AND matching ammo."""
    weapon = agent.get("equipment", {}).get("weapon")
    if not weapon:
        return False
    weapon_type = weapon.get("type")
    required_ammo = AMMO_FOR_WEAPON.get(weapon_type)
    if required_ammo is None:
        return True
    return any(i.get("type") == required_ammo for i in agent.get("inventory", []))


def _choose_combat_action(
    agent: Dict[str, Any],
    participant: Dict[str, Any],
    rng: "random.Random",
) -> str:
    """Choose a combat action: flee/shoot/heal."""
    _FLEE = "убежать"
    _SHOOT = "стрелять"
    _HEAL = "лечиться"
    # Participant with no enemies has nothing to fight — flee immediately.
    if not participant.get("enemies"):
        return _FLEE
    hp = agent.get("hp", 100)
    max_hp = agent.get("max_hp", 100)
    motive = participant.get("motive", "выжить")
    risk = float(agent.get("risk_tolerance", 0.5))
    hp_ratio = hp / max(max_hp, 1)
    has_weapon_ammo = _agent_has_weapon_and_ammo(agent)
    heal_item = _get_combat_heal_item(agent)
    if not has_weapon_ammo:
        if heal_item and hp_ratio < 0.8:
            return _HEAL
        return _FLEE
    if hp_ratio <= 0.25:
        if motive in ("выжить", "нажиться"):
            return _FLEE
        if heal_item:
            return _HEAL
        return _FLEE
    if hp_ratio <= 0.30:
        if heal_item and rng.random() < 0.6:
            return _HEAL
        if motive == "победить" and risk >= 0.7:
            return _SHOOT
        return _FLEE
    if motive == "победить":
        weights = {
            _SHOOT: 0.6 + risk * 0.3,
            _HEAL: 0.1 if heal_item and hp_ratio < 0.7 else 0.0,
            _FLEE: max(0.0, 0.2 - risk * 0.15),
        }
    elif motive == "нажиться":
        weights = {
            _SHOOT: 0.3 + risk * 0.2,
            _HEAL: 0.2 if heal_item and hp_ratio < 0.8 else 0.0,
            _FLEE: 0.3 + (1.0 - risk) * 0.2,
        }
    else:
        weights = {
            _SHOOT: 0.15 + risk * 0.15,
            _HEAL: 0.35 if heal_item and hp_ratio < 0.9 else 0.0,
            _FLEE: 0.4 + (1.0 - risk) * 0.1,
        }
    total = sum(weights.values())
    if total <= 0:
        return _FLEE
    roll = rng.random() * total
    cumulative = 0.0
    for action, w in weights.items():
        cumulative += w
        if roll <= cumulative:
            return action
    return _SHOOT


def _combat_flee(
    agent_id: str,
    agent: Dict[str, Any],
    participant: Dict[str, Any],
    combat: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Execute flee action for a combat participant."""
    events: List[Dict[str, Any]] = []
    loc_id = agent.get("location_id")
    loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
    cid = combat["id"]
    _add_memory(
        agent, world_turn, state, "decision",
        f"🏃 Бегу с поля боя",
        {"action_kind": "combat_flee", "combat_id": cid, "location_id": loc_id},
        summary=f"Я решил бежать с поля боя из «{loc_name}»",
    )
    participant["fled"] = True
    prev_loc_id = None
    for rec in _v3_records_desc(agent):
        fx = _v3_details(rec)
        if _v3_action_kind(rec) == "travel_arrived":
            arr_loc = fx.get("to_loc")
            if arr_loc and arr_loc != loc_id:
                prev_loc_id = arr_loc
                break
    if not prev_loc_id:
        connections = state.get("locations", {}).get(loc_id, {}).get("connections", [])
        open_conns = [c for c in connections if not c.get("closed")]
        if open_conns:
            rng_flee = random.Random(agent_id + str(world_turn) + "flee")
            prev_loc_id = rng_flee.choice(open_conns)["to"]
    if prev_loc_id and prev_loc_id in state.get("locations", {}):
        connections = state.get("locations", {}).get(loc_id, {}).get("connections", [])
        travel_time = next(
            (c.get("travel_time", 12) for c in connections if c["to"] == prev_loc_id),
            12,
        )
        flee_time = max(1, travel_time // 2)
        participant["fled_to"] = prev_loc_id
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": flee_time,
            "turns_total": flee_time,
            "target_id": prev_loc_id,
            "final_target_id": prev_loc_id,
            "remaining_route": [],
            "started_turn": world_turn,
            "ends_turn": world_turn + flee_time,
            "revision": 1,
            "interruptible": True,
            "combat_flee": True,
        }
        events.append({
            "event_type": "combat_fled",
            "payload": {"agent_id": agent_id, "combat_id": cid,
                        "from": loc_id, "to": prev_loc_id},
        })
        # Notify all other non-fled, alive participants who are still at this location.
        to_loc_name = state.get("locations", {}).get(prev_loc_id, {}).get("name", prev_loc_id)
        agent_name = agent.get("name", agent_id)
        for _obs_id, _obs_pdata in combat.get("participants", {}).items():
            if _obs_id == agent_id:
                continue
            if _obs_pdata.get("fled", False):
                continue
            _obs_agent = state.get("agents", {}).get(_obs_id)
            if not _obs_agent or not _obs_agent.get("is_alive", True):
                continue
            if _obs_agent.get("location_id") != loc_id:
                continue
            _add_memory(
                _obs_agent, world_turn, state, "observation",
                f"🏃 «{agent_name}» отступил из боя",
                {
                    "action_kind": "retreat_observed",
                    "subject": agent_id,
                    "subject_name": agent_name,
                    "from_location": loc_id,
                    "to_location": prev_loc_id,
                    "note": "Видел, как участник отступил",
                },
                summary=f"«{agent_name}» отступил с «{loc_name}» на «{to_loc_name}»",
            )
    _runtime_set_action_used(agent, True)
    return events


def _combat_shoot(
    agent_id: str,
    agent: Dict[str, Any],
    participant: Dict[str, Any],
    combat: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    rng: "random.Random",
) -> List[Dict[str, Any]]:
    """Execute shoot action for a combat participant."""
    events: List[Dict[str, Any]] = []
    agents = state.get("agents", {})
    cid = combat["id"]
    loc_id = agent.get("location_id")
    living_enemies = []
    for eid in participant.get("enemies", []):
        ep = combat.get("participants", {}).get(eid, {})
        ea = agents.get(eid, {})
        if (ea.get("is_alive", True)
                and not ep.get("fled", False)
                and ea.get("location_id") == loc_id):
            living_enemies.append(eid)
    if not living_enemies:
        return _combat_flee(agent_id, agent, participant, combat, state, world_turn)
    target_id = rng.choice(living_enemies)
    target = agents.get(target_id, {})
    target_name = target.get("name", target_id)
    weapon = agent.get("equipment", {}).get("weapon") or {}
    w_type = weapon.get("type", "")
    w_info = ITEM_TYPES.get(w_type, {})
    accuracy = float(weapon.get("accuracy", w_info.get("accuracy", 0.6)))
    base_damage = int(weapon.get("damage", w_info.get("damage", 15)))
    hit = rng.random() < accuracy
    damage_dealt = 0
    if hit:
        damage_dealt = base_damage
        target_armor = target.get("equipment", {}).get("armor") or {}
        if target_armor:
            t_armor_type = target_armor.get("type", "")
            t_armor_info = ITEM_TYPES.get(t_armor_type, {})
            armor_def = target_armor.get("defense", t_armor_info.get("defense", 0))
            damage_dealt = max(1, damage_dealt - armor_def // 3)
        target["hp"] = max(0, target.get("hp", 100) - damage_dealt)
    atk_name = agent.get("name", agent_id)
    _add_memory(
        agent, world_turn, state, "decision",
        f"🔫 Стреляю в «{target_name}»",
        {"action_kind": "combat_shoot", "observed": "combat_shoot",
         "combat_id": cid, "target": target_id, "target_name": target_name,
         "hit": hit, "damage": damage_dealt},
        summary=f"Я выстрелил в «{target_name}»" + (f" и попал, нанес {damage_dealt} урона" if hit else " и промахнулся"),
    )
    events.append({
        "event_type": "combat_shoot",
        "payload": {"agent_id": agent_id, "target_id": target_id,
                    "hit": hit, "damage": damage_dealt, "combat_id": cid},
    })
    if hit and damage_dealt > 0:
        _add_memory(
            target, world_turn, state, "observation",
            f"💥 Получил ранение от «{atk_name}»",
            {"observed": "combat_wounded", "combat_id": cid,
             "attacker_id": agent_id, "attacker_name": atk_name,
             "damage": damage_dealt},
            summary=f"Получил {damage_dealt} урона от «{atk_name}» в бою",
        )
        events.append({
            "event_type": "combat_wounded",
            "payload": {"agent_id": target_id, "by": agent_id,
                        "damage": damage_dealt, "hp_remaining": target.get("hp", 0),
                        "combat_id": cid},
        })
        if target.get("hp", 0) <= 0:
            _add_memory(
                agent, world_turn, state, "observation",
                f"💀 Убил «{target_name}»",
                {"observed": "combat_kill", "combat_id": cid,
                 "target_id": target_id, "target_name": target_name},
                summary=f"Я убил «{target_name}» в бою",
            )
            # If this kill completes the hunter's global kill_stalker goal, record it
            # so that _check_global_goal_completion can detect completion on the same tick.
            if agent.get("kill_target_id") == target_id:
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"🎯 Цель ликвидирована: «{target_name}» уничтожен",
                    {"action_kind": "hunt_target_killed",
                     "target_id": target_id, "target_name": target_name,
                     "combat_id": cid},
                    summary=f"Я выполнил охотничье задание — уничтожил «{target_name}» в бою",
                )
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"✅ Подтверждена ликвидация цели: «{target_name}»",
                    {
                        "action_kind": "target_death_confirmed",
                        "target_id": target_id,
                        "target_name": target_name,
                        "confirmation_source": "personal_combat_kill",
                        "directly_observed": True,
                        "killer_id": agent_id,
                        "combat_id": cid,
                        "corpse_location_id": loc_id,
                        "location_id": loc_id,
                        "target_death_cause": "combat",
                    },
                    summary=f"Цель подтверждена мёртвой — «{target_name}»",
                )
            # Use canonical kill helper for all death-state invariants.
            _mark_agent_dead(
                agent_id=target_id,
                agent=target,
                state=state,
                world_turn=world_turn,
                cause="combat",
                memory_title="💀 Убит в бою",
                memory_effects={"observed": "combat_killed", "combat_id": cid,
                                "killer_id": agent_id, "killer_name": atk_name},
                memory_summary=f"Я был убит «{atk_name}» в бою",
                event_payload_extra={"killer_id": agent_id, "combat_id": cid},
                events=events,
            )
            if agent.get("kill_target_id") == target_id:
                _mark_kill_stalker_goal_achieved(
                    agent_id,
                    agent,
                    state,
                    world_turn,
                    str(target_id),
                    confirmation_source="personal_combat_kill",
                )
    _runtime_set_action_used(agent, True)
    return events


def _combat_heal_action(
    agent_id: str,
    agent: Dict[str, Any],
    participant: Dict[str, Any],
    combat: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Execute heal action for a combat participant."""
    events: List[Dict[str, Any]] = []
    cid = combat["id"]
    heal_item = _get_combat_heal_item(agent)
    if not heal_item:
        return _combat_flee(agent_id, agent, participant, combat, state, world_turn)
    item_type = heal_item.get("type", "")
    item_info = ITEM_TYPES.get(item_type, {})
    heal_amount = item_info.get("heal_value",
        item_info.get("effects", {}).get("hp", 30))
    old_hp = agent.get("hp", 100)
    max_hp = agent.get("max_hp", 100)
    actual_restore = min(heal_amount, max_hp - old_hp)
    agent["hp"] = min(max_hp, old_hp + heal_amount)
    inv = agent.get("inventory", [])
    item_id = heal_item.get("id")
    removed = False
    new_inv = []
    for it in inv:
        if not removed and (it.get("id") == item_id or (not item_id and it.get("type") == item_type)):
            removed = True
        else:
            new_inv.append(it)
    if removed:
        agent["inventory"] = new_inv
    item_name = heal_item.get("name", item_type)
    _add_memory(
        agent, world_turn, state, "decision",
        f"💊 Использовал «{item_name}», восстановил {actual_restore} HP",
        {"observed": "combat_heal", "action_kind": "combat_heal",
         "combat_id": cid, "item_type": item_type, "hp_restored": actual_restore},
        summary=f"Использовал «{item_name}» в бою, восстановил {actual_restore} HP (текущее: {agent.get('hp')})",
    )
    events.append({
        "event_type": "combat_heal",
        "payload": {"agent_id": agent_id, "item_type": item_type,
                    "hp_restored": actual_restore, "combat_id": cid},
    })
    _runtime_set_action_used(agent, True)
    return events


def _process_all_combat_interactions(
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Process all active combat interactions for this tick."""
    events: List[Dict[str, Any]] = []
    agents = state.get("agents", {})
    _FLEE = "убежать"
    _SHOOT = "стрелять"
    for cid, combat in list(state.get("combat_interactions", {}).items()):
        if combat.get("ended", False):
            continue
        loc_id = combat.get("location_id")
        loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
        participants = combat.get("participants", {})
        # Step 1: Check for new participants who should join
        existing_enemy_set: set = set()
        for pid, pdata in participants.items():
            existing_enemy_set.update(pdata.get("enemies", []))
        for aid, agent in list(agents.items()):
            if not agent.get("is_alive", True):
                continue
            if agent.get("has_left_zone"):
                continue
            if aid in participants:
                continue
            if agent.get("location_id") != loc_id:
                continue
            kt = agent.get("kill_target_id")
            should_join = False
            join_enemies = []
            join_motive = "выжить"
            if kt and kt in participants:
                should_join = True
                join_enemies = [kt]
                join_motive = "победить"
            elif aid in existing_enemy_set:
                should_join = True
                join_enemies = [
                    pid for pid, pdata in participants.items()
                    if aid in pdata.get("enemies", [])
                ]
                join_motive = "выжить"
            if should_join:
                participants[aid] = {
                    "motive": join_motive,
                    "enemies": join_enemies,
                    "friends": [],
                    "fled": False,
                    "fled_to": None,
                }
                for je in join_enemies:
                    if je in participants:
                        if aid not in participants[je]["enemies"]:
                            participants[je]["enemies"].append(aid)
                _add_memory(
                    agent, world_turn, state, "decision",
                    "⚔️ Вступаю в боевое взаимодействие",
                    {"action_kind": "combat_joined", "combat_id": cid, "motive": join_motive,
                     "enemies": join_enemies, "location_id": loc_id},
                    summary=f"Я вступил в боевое взаимодействие в «{loc_name}» с мотивом «{join_motive}»",
                )
        # Step 2: Each non-fled living participant takes an action
        for aid, participant in list(participants.items()):
            if participant.get("fled", False):
                continue
            agent = agents.get(aid)
            if not agent or not agent.get("is_alive", True):
                continue
            if agent.get("action_used"):
                continue
            rng = random.Random(aid + str(world_turn) + "combat")
            action = _choose_combat_action(agent, participant, rng)
            if action == _FLEE:
                evs = _combat_flee(aid, agent, participant, combat, state, world_turn)
            elif action == _SHOOT:
                evs = _combat_shoot(aid, agent, participant, combat, state, world_turn, rng)
            else:
                evs = _combat_heal_action(aid, agent, participant, combat, state, world_turn)
            events.extend(evs)
        # Step 3: Check if combat should end
        combat_should_end = True
        for aid, participant in participants.items():
            if participant.get("fled", False):
                continue
            agent = agents.get(aid)
            if not agent or not agent.get("is_alive", True):
                continue
            for eid in participant.get("enemies", []):
                ep = participants.get(eid, {})
                ea = agents.get(eid, {})
                if (ea and ea.get("is_alive", True)
                        and not ep.get("fled", False)
                        and ea.get("location_id") == loc_id):
                    combat_should_end = False
                    break
            if not combat_should_end:
                break
        if combat_should_end:
            combat["ended"] = True
            combat["ended_turn"] = world_turn
            for aid, participant in participants.items():
                agent = agents.get(aid)
                if not agent:
                    continue
                survived = agent.get("is_alive", True)
                _add_memory(
                    agent, world_turn, state, "decision",
                    "⚔️ Боевое взаимодействие завершено",
                    {"action_kind": "combat_ended", "combat_id": cid,
                     "survived": survived, "location_id": loc_id},
                    summary="Боевое взаимодействие завершилось",
                )
            events.append({
                "event_type": "combat_ended",
                "payload": {"combat_id": cid, "location_id": loc_id,
                            "world_turn": world_turn},
            })
    return events




def _score_location(loc: Dict[str, Any], goal_kind: str) -> float:
    """Score a location for a given goal kind.

    goal_kind="artifacts": weighted by anomaly_activity and already-present artifacts.
    goal_kind="loot"      : weighted by total value of items present (future use).

    The scores are intentionally comparable so future goal branches can pick the
    highest-scoring option across goal kinds.
    """
    if goal_kind == "artifacts":
        return loc.get("anomaly_activity", 0) * 10.0
    if goal_kind == "loot":
        # TODO: expand when item loot collection is implemented
        return sum(item.get("value", 0) for item in loc.get("items", []))
    return 0.0


def _bfs_reachable_locations(
    from_loc_id: str,
    locations: Dict[str, Any],
    max_hops: int = 5,
) -> Dict[str, int]:
    """Return {loc_id: distance} for every location reachable from *from_loc_id*
    within *max_hops* hops via open (non-closed) connections.

    *from_loc_id* itself is not included in the result.
    """
    visited: Dict[str, int] = {}
    queue: collections.deque = collections.deque([(from_loc_id, 0)])
    seen = {from_loc_id}
    while queue:
        current, dist = queue.popleft()
        if dist >= max_hops:
            continue
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn["to"]
            if conn.get("closed") or nxt in seen:
                continue
            seen.add(nxt)
            visited[nxt] = dist + 1
            queue.append((nxt, dist + 1))
    return visited


def _dijkstra_reachable_locations(
    from_loc_id: str,
    locations: Dict[str, Any],
    max_minutes: float,
    map_revision: Any = None,
) -> Dict[str, float]:
    """Return {loc_id: travel_minutes} for every location reachable from *from_loc_id*
    within *max_minutes* of total travel time via open (non-closed) connections.

    Uses Dijkstra's algorithm so that connections with different ``travel_time`` values
    are compared fairly.  *from_loc_id* itself is not included in the result.

    When *map_revision* is provided the result is memoised in the pathfinding cache so
    subsequent calls for the same origin and radius hit the cache instead of re-running
    the full Dijkstra traversal.
    """
    if map_revision is not None:
        try:
            from app.games.zone_stalkers.pathfinding_cache import get_cached as _cache_get
            _cached = _cache_get(
                map_revision=map_revision,
                from_loc_id=from_loc_id,
                query_kind="dijkstra_reachable",
                extra_key=str(max_minutes),
            )
            if _cached is not None:
                return _cached
        except Exception:
            pass

    dist: Dict[str, float] = {}
    # heap entries: (total_minutes, loc_id)
    heap: list = [(0.0, from_loc_id)]
    while heap:
        cur_min, cur_id = heapq.heappop(heap)
        if cur_id in dist:
            continue
        if cur_id != from_loc_id:
            if cur_min > max_minutes:
                continue
            dist[cur_id] = cur_min
        for conn in locations.get(cur_id, {}).get("connections", []):
            if conn.get("closed"):
                continue
            nxt = conn["to"]
            if nxt in dist:
                continue
            edge_min = float(conn.get("travel_time", 12)) * MINUTES_PER_TURN
            nxt_min = cur_min + edge_min
            if nxt_min <= max_minutes:
                heapq.heappush(heap, (nxt_min, nxt))

    if map_revision is not None:
        try:
            from app.games.zone_stalkers.pathfinding_cache import set_cached as _cache_set
            _cache_set(
                map_revision=map_revision,
                from_loc_id=from_loc_id,
                query_kind="dijkstra_reachable",
                extra_key=str(max_minutes),
                value=dist,
            )
        except Exception:
            pass

    return dist


def _last_obs_content(agent: Dict[str, Any], obs_type: str, loc_id: str) -> Optional[List[str]]:
    """Return the content list from the most recent observation of *obs_type* at *loc_id*.

    Returns ``None`` when no such entry exists yet.  Used for deduplication so that
    identical observations are not written to memory on every turn.
    """
    key = "names" if obs_type in ("stalkers", "mutants") else (
        "artifact_types" if obs_type == "artifacts" else "item_types"
    )
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        fx = _v3_details(rec)
        if fx.get("observed") == obs_type and fx.get("location_id") == loc_id:
            return fx.get(key)
    return None


def _find_obs_entry(
    agent: Dict[str, Any], obs_type: str, loc_id: str
) -> Optional[Dict[str, Any]]:
    """Legacy in-place merge helper.  Always returns None now that
    memory_v3 is the sole store — callers fall back to writing a fresh entry.
    """
    return None


def _confirmed_empty_locations(agent: Dict[str, Any]) -> "frozenset[str]":
    """Return the set of location IDs the agent has confirmed as artifact-free.

    An entry is written when an exploration action finds the location genuinely
    empty (no artifacts present at all).  The agent relies entirely on its own
    memory — no real-map data is consulted.

    **Emission invalidation (memory-based):** When an emission ends, every alive
    stalker receives an ``emission_ended`` observation memory entry.  Because
    emissions reliably spawn new artifacts in anomaly zones, any
    ``explore_confirmed_empty`` entry that is *older* than the most recent
    ``emission_ended`` memory entry is treated as stale — the agent knows the
    location may have been refilled and will be willing to re-explore it.
    """
    # Find the world_turn of the most recent emission_ended observation the agent holds.
    last_emission_ended_turn: int = 0
    for rec in _v3_records_desc(agent):
        if _v3_action_kind(rec) == "emission_ended":
            t = _v3_turn(rec)
            if t > last_emission_ended_turn:
                last_emission_ended_turn = t

    # A confirmed_empty entry is valid only when it was recorded AFTER the last
    # emission end the agent is aware of.
    result: set = set()
    for rec in _v3_records_desc(agent):
        ak = _v3_action_kind(rec)
        kind = str(rec.get("kind") or "")
        if ak == "explore_confirmed_empty" or kind == "location_empty":
            d = _v3_details(rec)
            loc_id_v = rec.get("location_id") or d.get("location_id")
            t = _v3_turn(rec)
            if loc_id_v and t > last_emission_ended_turn:
                result.add(loc_id_v)
    return frozenset(result)


def _write_location_observations(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> None:
    """Write 'observation' memory entries for everything the agent currently sees.

    Called both on arrival AND each turn so that observations stay up-to-date as
    entities enter or leave the agent's location.

    Uses the semantic merge system (memory_merge.py) to aggregate repeated
    observations into a single entry with ``times_seen``, ``first_seen_turn``,
    ``last_seen_turn``, ``confidence``, ``importance``, and ``status`` fields,
    rather than appending a new entry on every turn.

    Merge rules per category
    ------------------------
    * **stalkers** (TACTICAL, window=20): union of names; same-location entries
      are merged regardless of which specific stalkers are present.
    * **mutants** (TACTICAL, window=20): merged only when the exact same group
      is visible; a different group composition triggers a new entry.
    * **items** (AMBIENT, window=40): current item list replaces the stored one
      on each merge-update; a new entry is created only when outside the window.
    """
    from app.games.zone_stalkers.decision.perception import is_perception_suppressed
    # Sleeping / dead / left-zone agents cannot observe anything.
    if is_perception_suppressed(agent):
        return
    loc = state.get("locations", {}).get(loc_id, {})
    loc_name = loc.get("name", loc_id)

    # ── Stalkers + traders at this location (excluding self) ──────────────────
    # PR3: Track (agent_id, name) pairs so knowledge_v1 upserts have stable agent IDs.
    _stalker_id_name_pairs: List[tuple] = []
    _observed_agents: Dict[str, Dict[str, Any]] = {}
    for aid, ag in state.get("agents", {}).items():
        if (aid != agent_id
                and ag.get("location_id") == loc_id
                and ag.get("is_alive", True)
                and not ag.get("has_left_zone")
                and ag.get("archetype") == "stalker_agent"):
            _stalker_id_name_pairs.append((aid, ag.get("name", aid)))
            _observed_agents[aid] = {
                "equipment": dict(ag.get("equipment") or {}),
                "global_goal": ag.get("global_goal"),
                "archetype": ag.get("archetype"),
                "combat_strength_estimate": max(
                    0.1,
                    min(
                        1.0,
                        float(ag.get("hp", ag.get("max_hp", 100)) or 100)
                        / max(1.0, float(ag.get("max_hp", 100) or 100)),
                    ),
                ),
            }
    for tid, tr in state.get("traders", {}).items():
        if tr.get("location_id") == loc_id:
            _stalker_id_name_pairs.append((tid, tr.get("name", tid)))
            _observed_agents[tid] = {
                "equipment": dict(tr.get("equipment") or {}),
                "global_goal": tr.get("global_goal"),
                "archetype": "trader_agent",
                "combat_strength_estimate": 0.0,
            }
    _stalker_id_name_pairs.sort(key=lambda x: x[1])
    stalker_names: List[str] = [n for _, n in _stalker_id_name_pairs]
    _seen_agent_ids: List[str] = [aid for aid, _ in _stalker_id_name_pairs]

    if stalker_names:
        _add_memory(
            agent, world_turn, state, "observation",
            f"Вижу персонажей в «{loc_name}»",
            {
                "observed": "stalkers",
                "location_id": loc_id,
                "names": stalker_names,
                "seen_agent_ids": _seen_agent_ids,  # PR3: stable IDs for knowledge upserts
                "observed_agents": _observed_agents,
            },
            summary=f"В локации «{loc_name}» замечены: {', '.join(stalker_names)}",
        )

    # ── Mutants at this location ──────────────────────────────────────────────
    mutant_names = sorted(
        m.get("name", m.get("type", "?"))
        for m in state.get("mutants", {}).values()
        if m.get("location_id") == loc_id and m.get("is_alive", True)
    )
    if mutant_names:
        _add_memory(
            agent, world_turn, state, "observation",
            f"Вижу мутантов в «{loc_name}»",
            {"observed": "mutants", "location_id": loc_id, "names": mutant_names},
            summary=f"В локации «{loc_name}» замечены мутанты: {', '.join(mutant_names)}",
        )

    visible_corpses = [
        corpse for corpse in (loc.get("corpses") or [])
        if isinstance(corpse, dict) and bool(corpse.get("visible", True))
    ]
    stale_corpse_seen = False
    for corpse in visible_corpses:
        if not is_valid_corpse_object(corpse, state):
            stale_corpse_seen = True
            continue
        dead_agent_id = str(corpse.get("agent_id") or "")
        if not dead_agent_id:
            continue
        dead_agent_name = str(corpse.get("agent_name") or dead_agent_id)
        corpse_id = str(corpse.get("corpse_id") or f"corpse_{dead_agent_id}_{corpse.get('created_turn')}")
        death_cause = str(corpse.get("death_cause") or "")
        killer_id = corpse.get("killer_id")
        _dead_agent_raw = state.get("agents", {}).get(dead_agent_id)
        _dead_agent_live = bool(isinstance(_dead_agent_raw, dict) and _dead_agent_raw.get("is_alive", True))
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            f"☠️ Вижу тело: «{dead_agent_name}»",
            {
                "action_kind": "corpse_seen",
                "dead_agent_id": dead_agent_id,
                "dead_agent_name": dead_agent_name,
                "corpse_id": corpse_id,
                "location_id": loc_id,
                "death_cause": death_cause,
                "killer_id": killer_id,
                "directly_observed": True,
                "confidence": 0.95,
                "dead_agent_is_alive": _dead_agent_live,
            },
            summary=f"Обнаружено тело «{dead_agent_name}» в «{loc_name}».",
        )
        if str(agent.get("kill_target_id") or "") == dead_agent_id:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                f"🎯 Нашёл тело цели: «{dead_agent_name}»",
                {
                    "action_kind": "target_corpse_seen",
                    "target_id": dead_agent_id,
                    "target_name": dead_agent_name,
                    "corpse_id": corpse_id,
                    "corpse_location_id": loc_id,
                    "location_id": loc_id,
                    "death_cause": death_cause,
                    "killer_id": killer_id,
                    "directly_observed": True,
                    "confidence": 0.95,
                    "dead_agent_is_alive": _dead_agent_live,
                },
                summary=f"Лично подтвердил тело цели «{dead_agent_name}» в «{loc_name}».",
            )

    if stale_corpse_seen:
        loc["corpses"] = [
            corpse
            for corpse in (loc.get("corpses") or [])
            if isinstance(corpse, dict) and is_valid_corpse_object(corpse, state)
        ]

    # NOTE: Artifacts are NOT recorded as a location observation on arrival.
    # They can only be discovered and recorded through the explore action.
    # See _resolve_exploration() for the artifact pickup observation.

    # ── Loose items on the ground ─────────────────────────────────────────────
    item_types = sorted(it.get("type", "?") for it in loc.get("items", []))
    if item_types:
        _add_memory(
            agent, world_turn, state, "observation",
            f"Вижу предметы в «{loc_name}»",
            {"observed": "items", "location_id": loc_id, "item_types": item_types},
            summary=f"В локации «{loc_name}» на земле: {', '.join(item_types)}",
        )


# ─────────────────────────────────────────────────────────────────
# Bot decisions
# ─────────────────────────────────────────────────────────────────

# Import centralised item-type sets from balance data (single source of truth)
from app.games.zone_stalkers.balance.items import (
    HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES,
    WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_ITEM_TYPES, AMMO_FOR_WEAPON,
    SECRET_DOCUMENT_ITEM_TYPES, ITEM_TYPES,
)
from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES

# Set of artifact type keys (used to identify artifacts in inventory)
_ARTIFACT_ITEM_TYPES: frozenset = frozenset(ARTIFACT_TYPES.keys())


# ─────────────────────────────────────────────────────────────────
# NPC planning helpers
# ─────────────────────────────────────────────────────────────────

def _agent_wealth(agent: Dict[str, Any]) -> int:
    """Sum of money + inventory item values + equipped item values."""
    inv_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    eq_value = sum(
        item.get("value", 0)
        for item in agent.get("equipment", {}).values()
        if item is not None
    )
    return agent.get("money", 0) + inv_value + eq_value


def _agent_artifacts_in_inventory(agent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of artifact items currently in the agent's inventory."""
    return [i for i in agent.get("inventory", []) if i.get("type") in _ARTIFACT_ITEM_TYPES]


def _find_trader_at_location(loc_id: str, state: Dict[str, Any]) -> Any:
    """Return the first alive trader at *loc_id*, or None."""
    loc_agents = state.get("locations", {}).get(loc_id, {}).get("agents", [])
    traders = state.get("traders", {})
    for tid in loc_agents:
        t = traders.get(tid)
        if t is not None:
            return t
    return None


def _find_nearest_trader_location(
    from_loc_id: str,
    state: Dict[str, Any],
) -> Optional[str]:
    """BFS to find the nearest location with at least one living trader.

    Returns the ``loc_id`` of the nearest trader location, or ``None`` if
    no reachable trader exists within 20 hops.  Skips closed connections.
    """
    traders = state.get("traders", {})
    locations = state.get("locations", {})
    _map_revision = int(state.get("map_revision", 0))
    # Include a signature of active trader locations in the cache key so that
    # different trader configurations (e.g. across tests) never share stale entries.
    _trader_sig = "|".join(
        sorted(t.get("location_id", "") for t in traders.values() if t.get("is_alive", True))
    )
    try:
        from app.games.zone_stalkers.pathfinding_cache import get_cached as _cache_get
        _cached = _cache_get(
            map_revision=_map_revision,
            from_loc_id=from_loc_id,
            query_kind="nearest_trader_location",
            extra_key=_trader_sig,
        )
        if _cached is not None:
            return _cached or None
    except Exception:
        pass
    trader_locs: set = {
        t["location_id"] for t in traders.values() if t.get("is_alive", True)
    }
    if from_loc_id in trader_locs:
        try:
            from app.games.zone_stalkers.pathfinding_cache import set_cached as _cache_set
            _cache_set(
                map_revision=_map_revision,
                from_loc_id=from_loc_id,
                query_kind="nearest_trader_location",
                extra_key=_trader_sig,
                value=from_loc_id,
            )
        except Exception:
            pass
        return from_loc_id
    queue: collections.deque = collections.deque([(from_loc_id, 0)])
    seen = {from_loc_id}
    while queue:
        current, dist = queue.popleft()
        if dist >= 20:
            continue
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn.get("to")
            if not nxt or conn.get("closed") or nxt in seen:
                continue
            seen.add(nxt)
            if nxt in trader_locs:
                try:
                    from app.games.zone_stalkers.pathfinding_cache import set_cached as _cache_set
                    _cache_set(
                        map_revision=_map_revision,
                        from_loc_id=from_loc_id,
                        query_kind="nearest_trader_location",
                        extra_key=_trader_sig,
                        value=nxt,
                    )
                except Exception:
                    pass
                return nxt
            queue.append((nxt, dist + 1))
    try:
        from app.games.zone_stalkers.pathfinding_cache import set_cached as _cache_set
        _cache_set(
            map_revision=_map_revision,
            from_loc_id=from_loc_id,
            query_kind="nearest_trader_location",
            extra_key=_trader_sig,
            value="",
        )
    except Exception:
        pass
    return None


def _find_richest_artifact_location(
    state: dict,
    exclude_loc_id: str | None = None,
    from_loc_id: str | None = None,
) -> tuple:
    """
    Survey every location and return *(loc_id, total_artifact_value)* for the
    location whose artifacts have the highest combined base value.

    Returns *(None, 0)* when no artifacts exist anywhere on the map.
    The caller's current location can be excluded via *exclude_loc_id*.
    When *from_loc_id* is provided, only locations reachable (via non-closed
    connections) from that location are considered.

    NOTE: This function is retained for unit-test use only.  Bot decision logic
    does NOT call it — NPCs must not have omniscient knowledge of where artifacts
    are located; they discover them by exploring anomaly zones.
    """
    # Build reachability set when a starting location is given
    reachable: set | None = None
    if from_loc_id:
        locations_map = state.get("locations", {})
        reachable = {from_loc_id}
        queue = [from_loc_id]
        while queue:
            current = queue.pop(0)
            for conn in locations_map.get(current, {}).get("connections", []):
                nxt = conn.get("to")
                if nxt and not conn.get("closed") and nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)

    best_loc_id = None
    best_value = 0
    for loc_id, loc in state.get("locations", {}).items():
        if exclude_loc_id and loc_id == exclude_loc_id:
            continue
        if reachable is not None and loc_id not in reachable:
            continue
        arts = loc.get("artifacts", [])
        if arts:
            total = sum(a.get("value", 0) for a in arts)
            if total > best_value:
                best_value = total
                best_loc_id = loc_id
    return best_loc_id, best_value


def _add_trader_memory(
    trader: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    memory_type: str,
    title: str,
    *call_args: Any,
) -> None:
    """Trader memory is no longer used; trade events are recorded on the stalker agent."""
    pass


def _bot_sell_to_trader(
    agent_id: str,
    agent: Dict[str, Any],
    trader: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """
    Perform a direct (inline) sale of all artifacts from *agent* to *trader*.

    Pricing: each artifact sells for 60 % of its base ``value``.
    Items are skipped (not sold) if the trader cannot afford the asking price
    at the time of that individual transaction; remaining artifacts continue
    to be processed.

    Returns early with an empty event list if the agent carries no artifacts.

    Updates money on both sides, removes artifacts from agent inventory,
    appends them to trader inventory, and writes memory entries for
    both the stalker and the trader.
    """
    events: List[Dict[str, Any]] = []
    artifacts = _agent_artifacts_in_inventory(agent)
    if not artifacts:
        return events

    # COW the trader so mutations stay in the runtime copy
    _trader_id_st = trader.get("id", "")
    _rt_st = _cow_runtime()
    if _rt_st is not None and _trader_id_st:
        try:
            trader = _rt_st.trader(_trader_id_st)
        except Exception:
            _rt_st = None
    trader_id_resolved = str(trader.get("id") or _trader_id_st or "")
    trader_inventory_st = (
        _rt_st.mutable_trader_list(_trader_id_st, "inventory")
        if _rt_st is not None and _trader_id_st
        else trader.setdefault("inventory", [])
    )
    agent_money_st = agent.get("money", 0)
    trader_money_st = int(trader.get("money", 0))
    sell_price_total = 0
    sold_items = []
    for art in artifacts:
        art_type = str(art.get("type") or "")
        artifact_cfg = ARTIFACT_TYPES.get(art_type, {})
        art_value_raw = art.get("value")
        artifact_value = int(
            art_value_raw if art_value_raw is not None else (artifact_cfg.get("value") or 0)
        )
        if artifact_value <= 0:
            continue
        sell_price = int(artifact_value * 0.6)  # 60% of base value
        if sell_price <= 0:
            continue
        if trader_money_st < sell_price:
            continue  # trader too poor; skip this item
        # Transfer money (agent money accumulated locally; trader updated in-place on COW copy)
        agent_money_st += sell_price
        trader_money_st -= sell_price
        _runtime_set_trader_field(state, trader_id_resolved, "money", trader_money_st)
        sell_price_total += sell_price
        # Transfer item
        sold_item = dict(art)
        sold_item["stock"] = 1
        trader_inventory_st.append(sold_item)
        sold_items.append(art)
        events.append({
            "event_type": "bot_sold_artifact",
            "payload": {
                "agent_id": agent_id,
                "trader_id": trader_id_resolved,
                "item_id": art["id"],
                "item_type": art["type"],
                "price": sell_price,
            },
        })

    if not sold_items:
        return events

    # Remove sold items from inventory; commit accumulated agent money
    sold_ids = {i["id"] for i in sold_items}
    _runtime_set_agent_field(agent_id, "money", agent_money_st, agent)
    _runtime_set_agent_field(agent_id, "inventory", [i for i in agent.get("inventory", []) if i["id"] not in sold_ids], agent)
    _runtime_set_action_used(agent, True)

    # ── Stalker memory (Step 7) ───────────────────────────────────
    item_names = ", ".join(i.get("name", i.get("type", "?")) for i in sold_items)
    trader_name = trader.get("name", trader_id_resolved)
    loc_name = state.get("locations", {}).get(agent.get("location_id", ""), {}).get("name", "?")
    _add_memory(
        agent, world_turn, state, "action",
        f"Продал {len(sold_items)} артефактов на {sell_price_total} денег",
        {"action_kind": "trade_sell", "money_gained": sell_price_total,
         "items_sold": [i["type"] for i in sold_items], "trader_id": trader_id_resolved},
        summary=f"Продал {item_names} торговцу {trader_name} в «{loc_name}» за {sell_price_total} денег",
    )

    # ── Trader memory ─────────────────────────────────────────────
    stalker_name = agent.get("name", agent_id)
    if _rt_st is not None and _trader_id_st:
        try:
            _rt_st.mutable_trader_list(_trader_id_st, "memory")
        except Exception:
            pass
    _add_trader_memory(
        trader, world_turn, state, "trade_buy",
        f"Купил {item_names} у сталкера {stalker_name}",
        {"money_spent": sell_price_total, "items_bought": [i["type"] for i in sold_items],
         "stalker_id": agent_id},
    )

    return events


def _bot_sell_items_for_cash(
    agent_id: str,
    agent: Dict[str, Any],
    trader: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    target_amount: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Sell non-critical inventory items to raise cash for urgent needs.

    Sells items in descending priority:
      1. Artifacts (highest resale value per slot)
      2. Detectors
      3. Spare weapons  (in inventory, NOT the equipped weapon)
      4. Spare armor    (in inventory, NOT the equipped armor)

    Items that are never sold here:
      - Consumables (food / drink / medical / ammo) — needed for survival / combat
      - Secret documents — needed for the ``unravel_zone_mystery`` goal

    Stops as soon as ``agent["money"] >= target_amount`` (if given), or when all
    eligible items have been sold / the trader can no longer afford more.

    Returns a list of ``bot_sold_item`` events.
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _IT
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART

    _SELL_RATIO = 0.6
    _art_set: frozenset = frozenset(_ART.keys())
    _non_sellable_base_types: frozenset = frozenset(
        ["medical", "consumable", "ammo", "secret_document"]
    )

    def _item_priority(item: Dict[str, Any]) -> int:
        """Lower number = sell first."""
        t = item.get("type", "")
        if t in _art_set:
            return 0
        base = _IT.get(t, {}).get("type", t)
        if base == "detector":
            return 1
        if base == "weapon":
            return 2
        if base == "armor":
            return 3
        return 99  # not sold

    # Collect candidates from inventory only (equipped items are in agent["equipment"])
    candidates: List[Dict[str, Any]] = []
    for item in agent.get("inventory", []):
        t = item.get("type", "")
        base = _IT.get(t, {}).get("type", t)
        if base in _non_sellable_base_types:
            continue
        val = item.get("value", _IT.get(t, {}).get("value", 0))
        if val <= 0:
            continue
        pri = _item_priority(item)
        if pri == 99:
            continue
        candidates.append(item)

    # Sort: highest priority first; within same priority sell most valuable first
    candidates.sort(key=lambda i: (_item_priority(i), -i.get("value", 0)))

    if not candidates:
        _runtime_set_action_used(agent, True)
        return []

    # COW the trader so mutations stay in the runtime copy
    _trader_id_sfc = trader.get("id", "")
    _rt_sfc = _cow_runtime()
    if _rt_sfc is not None and _trader_id_sfc:
        try:
            trader = _rt_sfc.trader(_trader_id_sfc)
        except Exception:
            _rt_sfc = None
    trader_id_resolved = str(trader.get("id") or _trader_id_sfc or "")
    trader_inventory_sfc = (
        _rt_sfc.mutable_trader_list(_trader_id_sfc, "inventory")
        if _rt_sfc is not None and _trader_id_sfc
        else trader.setdefault("inventory", [])
    )
    agent_money_sfc = agent.get("money", 0)
    trader_money_sfc = int(trader.get("money", 0))

    events: List[Dict[str, Any]] = []
    sold_items: List[Dict[str, Any]] = []
    total_earned = 0

    for item in candidates:
        if target_amount is not None and agent_money_sfc >= target_amount:
            break
        t = item.get("type", "")
        val = item.get("value", _IT.get(t, {}).get("value", 0))
        sell_price = int(val * _SELL_RATIO)
        if sell_price <= 0:
            continue
        if trader_money_sfc < sell_price:
            continue  # trader cannot afford this item
        agent_money_sfc += sell_price
        trader_money_sfc -= sell_price
        _runtime_set_trader_field(state, trader_id_resolved, "money", trader_money_sfc)
        total_earned += sell_price
        sold_item = dict(item)
        sold_item["stock"] = 1
        trader_inventory_sfc.append(sold_item)
        sold_items.append(item)
        events.append({
            "event_type": "bot_sold_item",
            "payload": {
                "agent_id": agent_id,
                "trader_id": trader_id_resolved,
                "item_type": t,
                "price": sell_price,
            },
        })

    if not sold_items:
        _runtime_set_action_used(agent, True)
        return []

    # Remove sold items (compare by object identity); commit accumulated agent money
    sold_obj_ids = {id(i) for i in sold_items}
    _runtime_set_agent_field(agent_id, "money", agent_money_sfc, agent)
    _runtime_set_agent_field(agent_id, "inventory", [i for i in agent.get("inventory", []) if id(i) not in sold_obj_ids], agent)
    _runtime_set_action_used(agent, True)

    item_names = ", ".join(
        _IT.get(i.get("type", ""), {}).get("name", i.get("type", "?"))
        for i in sold_items
    )
    trader_name = trader.get("name", trader.get("id", "?"))
    loc_name = (
        state.get("locations", {})
        .get(agent.get("location_id", ""), {})
        .get("name", "?")
    )
    _add_memory(
        agent, world_turn, state, "action",
        f"Продал {len(sold_items)} предметов на {total_earned} денег (экстренная продажа)",
        {
            "action_kind": "trade_sell_for_cash",
            "money_gained": total_earned,
            "items_sold": [i.get("type") for i in sold_items],
            "trader_id": trader_id_resolved,
        },
        summary=(
            f"Продал {item_names} торговцу {trader_name} в «{loc_name}» "
            f"за {total_earned} денег, чтобы покрыть критические нужды"
        ),
    )

    stalker_name = agent.get("name", agent_id)
    if _rt_sfc is not None and _trader_id_sfc:
        try:
            _rt_sfc.mutable_trader_list(_trader_id_sfc, "memory")
        except Exception:
            pass
    _add_trader_memory(
        trader, world_turn, state, "trade_buy",
        f"Купил {item_names} у сталкера {stalker_name}",
        {
            "money_spent": total_earned,
            "items_bought": [i.get("type") for i in sold_items],
            "stalker_id": agent_id,
        },
    )

    return events


def _bot_schedule_travel(
    agent_id: str,
    agent: Dict[str, Any],
    target_loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
    emergency_flee: bool = False,
) -> List[Dict[str, Any]]:
    """Schedule hop-by-hop travel for a bot toward target_loc_id. Returns events.

    Set *emergency_flee=True* when the travel is a direct response to an
    emission warning.  This flag is stored on the ``scheduled_action`` and
    prevents the emission-interrupt logic from cancelling the very flee that
    was just scheduled.
    """
    from app.games.zone_stalkers.rules.world_rules import _bfs_route
    route = _bfs_route(state["locations"], agent["location_id"], target_loc_id)
    if not route:
        return []
    first_hop = route[0]
    conns = state["locations"].get(agent["location_id"], {}).get("connections", [])
    hop_time = next(
        (c.get("travel_time", 12) for c in conns if c["to"] == first_hop),
        12,
    )
    sched: Dict[str, Any] = {
        "type": "travel",
        "turns_remaining": hop_time,
        "turns_total": hop_time,
        "target_id": first_hop,
        "final_target_id": target_loc_id,
        "remaining_route": route[1:],
        "started_turn": world_turn,
        "ends_turn": world_turn + hop_time,
        "revision": 1,
        "interruptible": True,
    }
    if emergency_flee:
        sched["emergency_flee"] = True
    _runtime_set_agent_field(agent_id, "scheduled_action", sched, agent)
    _runtime_set_action_used(agent, True)
    return [{
        "event_type": "agent_travel_started",
        "payload": {"agent_id": agent_id, "destination": target_loc_id, "turns": hop_time, "bot": True},
    }]


def _score_item_for_purchase(
    info: Dict[str, Any],
    agent_risk: float,
    max_value: float,
    max_weight: float,
) -> float:
    """Return a composite purchase score for *info* relative to *agent_risk*.

    Higher score = better fit.  Three factors contribute:

    * **Risk-tolerance match** (weight ``_ITEM_SCORE_WEIGHT_RISK``): the closer
      the item's ``risk_tolerance`` is to the agent's own, the better.
    * **Value / quality** (weight ``_ITEM_SCORE_WEIGHT_VALUE``): a more
      expensive item is considered higher quality and preferred.
    * **Inverse weight** (weight ``_ITEM_SCORE_WEIGHT_INV_WEIGHT``): lighter
      items are preferred (lower carry burden).

    All three sub-scores are normalised to [0, 1] using the per-candidate-set
    maximums supplied by the caller.
    """
    rt_score = 1.0 - abs(info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE) - agent_risk)
    val_score = (info.get("value", 0) / max_value) if max_value > 0 else 0.0
    weight_score = (1.0 - info.get("weight", 0.0) / max_weight) if max_weight > 0 else 1.0
    return (
        _ITEM_SCORE_WEIGHT_RISK * rt_score
        + _ITEM_SCORE_WEIGHT_VALUE * val_score
        + _ITEM_SCORE_WEIGHT_INV_WEIGHT * weight_score
    )


def _select_item_by_risk_tolerance(
    item_types: "frozenset[str]",
    agent_risk: float,
) -> "tuple[str, float] | None":
    """Return the ``(item_key, buy_price)`` with the highest composite purchase
    score among all items in *item_types* that exist in the catalogue.

    Scoring is multi-factor (see :func:`_score_item_for_purchase`):
    risk-tolerance match dominates, with higher item value and lower item
    weight as secondary/tertiary factors.

    Returns ``None`` when *item_types* is empty or no matching entries exist.
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES

    candidates = [(k, ITEM_TYPES[k]) for k in item_types if k in ITEM_TYPES]
    if not candidates:
        return None

    max_value = max(info.get("value", 0) for _, info in candidates) or 1
    max_weight = max(info.get("weight", 0.0) for _, info in candidates) or 1

    best_key = max(
        candidates,
        key=lambda kv: _score_item_for_purchase(kv[1], agent_risk, max_value, max_weight),
    )[0]
    return best_key, ITEM_TYPES[best_key].get("value", 0)


def _can_afford_cheapest(agent: Dict[str, Any], item_types: "frozenset[str]") -> bool:
    """Return True if the agent can afford at least the cheapest item in
    *item_types* at the standard trader markup (base_value × 1.5).

    Used to guard "travel to trader" decisions: if the agent cannot afford
    anything the trip would be pointless and would cause an infinite loop
    (seek trader → can't buy → fall through to goal → seek trader again).
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    money = agent.get("money", 0)
    return any(
        money >= int(ITEM_TYPES[k].get("value", 0) * 1.5)
        for k in item_types
        if k in ITEM_TYPES
    )


def _bot_buy_from_trader(
    agent_id: str,
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    state: Dict[str, Any],
    world_turn: int,
    purchase_reason: str = "",
) -> List[Dict[str, Any]]:
    """Buy the best-matching item from a trader at the agent's current location.

    *purchase_reason* is a short human-readable string explaining WHY the agent
    needs this item (e.g. "нет оружия", "критически низкое HP").  When provided
    it is prepended to the ``trade_decision`` memory entry so the stalker's
    memory clearly shows the motivation for the purchase.

    Item selection is based on ``risk_tolerance``: the item whose
    ``risk_tolerance`` value is closest to the agent's own ``risk_tolerance``
    is preferred.  If the agent cannot afford that item the function falls back
    to the next-closest affordable option, descending by matching score.

    Implements an infinite-stock stub: the trader does not need to have the
    item in their inventory — they always can supply it.  The agent is
    charged 150 % of the item's base value.

    A "decision" memory entry is written explaining the choice before the
    purchase, followed by the usual "action" entry on completion.

    Returns a non-empty event list on success, empty list when the agent
    cannot afford anything or no trader is present.
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES

    loc_id = agent.get("location_id")
    traders = state.get("traders", {})

    # Find a living trader at the same location
    trader = next(
        (t for t in traders.values()
         if t.get("location_id") == loc_id and t.get("is_alive", True)),
        None,
    )
    if trader is None:
        return []

    # COW the trader so mutations stay in the runtime copy
    _trader_id_bt = trader.get("id", "")
    _rt_bt = _cow_runtime()
    if _rt_bt is not None and _trader_id_bt:
        try:
            trader = _rt_bt.trader(_trader_id_bt)
        except Exception:
            _rt_bt = None
    _trader_id_bt_resolved = str(trader.get("id") or _trader_id_bt or "")

    agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))

    # Build scored candidate list: each entry is (item_key, base_value, item_risk, score).
    # Normalise value and weight within this candidate set so scores are comparable.
    raw = [
        (k, ITEM_TYPES[k].get("value", 0), ITEM_TYPES[k].get("risk_tolerance", DEFAULT_RISK_TOLERANCE),
         ITEM_TYPES[k].get("weight", 0.0))
        for k in item_types if k in ITEM_TYPES
    ]
    if not raw:
        return []
    max_value = max(v for _, v, _, _ in raw) or 1
    max_weight = max(w for _, _, _, w in raw) or 1
    candidates = sorted(
        [
            (k, base_value, item_risk, _score_item_for_purchase(ITEM_TYPES[k], agent_risk, max_value, max_weight))
            for k, base_value, item_risk, _ in raw
        ],
        key=lambda x: x[3],
        reverse=True,
    )

    for idx, (item_type, base_value, item_risk, score) in enumerate(candidates):
        buy_price = int(base_value * 1.5)
        if agent.get("money", 0) < buy_price:
            continue
        # Transaction — deduct from agent, credit trader
        _runtime_set_agent_field(agent_id, "money", agent.get("money", 0) - buy_price, agent)
        agent = _runtime_agent(agent_id, agent)
        _trader_money_new = int(trader.get("money", 0)) + buy_price
        _runtime_set_trader_field(state, _trader_id_bt_resolved, "money", _trader_money_new)
        # Create a fresh item instance and place it in agent inventory
        new_item: Dict[str, Any] = {
            "id": f"{item_type}_{agent_id}_{world_turn}",
            "type": item_type,
            "name": ITEM_TYPES[item_type].get("name", item_type),
            "value": base_value,
        }
        if _rt_bt is not None:
            _rt_bt.mutable_agent_inventory(agent_id).append(new_item)
        else:
            agent.setdefault("inventory", []).append(new_item)
        _runtime_set_action_used(agent, True)
        item_name = ITEM_TYPES[item_type].get("name", item_type)
        trader_name = trader.get("name", trader.get("id", "trader"))
        # Collect up to 2 runner-ups (next candidates in the scored list, regardless of affordability)
        runners_up = [
            {
                "item_type": rk,
                "item_name": ITEM_TYPES[rk].get("name", rk),
                "score": round(rs, 3),
                "price": int(ITEM_TYPES[rk].get("value", 0) * 1.5),
            }
            for rk, _, _, rs in candidates[idx + 1: idx + 3]
        ]
        runners_up_text = ""
        if runners_up:
            parts = [
                f"«{r['item_name']}» (счёт {r['score']:.3f}, {r['price']} монет)"
                for r in runners_up
            ]
            runners_up_text = " Ближайшие конкуренты: " + ", ".join(parts) + "."
        # Decision memory: explain the composite score and the reason for purchase
        _add_memory(
            agent, world_turn, state, "decision",
            f"Решил купить «{item_name}»",
            {
                "action_kind": "trade_decision",
                "item_type": item_type,
                "agent_risk_tolerance": agent_risk,
                "item_risk_tolerance": item_risk,
                "score": round(score, 3),
                "price": buy_price,
                "runners_up": runners_up,
                "reason": purchase_reason,
            },
            summary=f"Я решил купить «{item_name}» за {buy_price} денег, потому что {purchase_reason}",
        )
        # Action memory: record the completed purchase
        _add_memory(
            agent, world_turn, state, "action",
            f"Купил «{item_name}» у торговца",
            {"action_kind": "trade_buy", "item_type": item_type, "price": buy_price},
            summary=f"Купил «{item_name}» у торговца {trader_name} за {buy_price} денег",
        )
        # Trader memory: record the sale from the trader's perspective
        agent_name = agent.get("name", agent_id)
        loc_name = state.get("locations", {}).get(agent.get("location_id", ""), {}).get("name", "?")
        if _rt_bt is not None and _trader_id_bt:
            try:
                _rt_bt.mutable_trader_list(_trader_id_bt, "memory")
            except Exception:
                pass
        _add_trader_memory(
            trader, world_turn, state, "trade_sale",
            f"Продал «{item_name}» сталкеру {agent_name}",
            {
                "item_type": item_type,
                "price": buy_price,
                "buyer_id": agent_id,
            },
        )
        return [{
            "event_type": "bot_bought_item",
            "payload": {
                "agent_id": agent_id, "trader_id": (_trader_id_bt_resolved or "trader"),
                "item_type": item_type, "price": buy_price,
                "agent_risk_tolerance": agent_risk, "item_risk_tolerance": item_risk,
                "score": round(score, 3),
            },
        }]
    return []


def _bot_consume(
    agent_id: str,
    agent: Dict[str, Any],
    item: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    action_kind: str = "consume",
) -> List[Dict[str, Any]]:
    """Apply a consumable item to agent and remove it from inventory. Returns events."""
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    from app.games.zone_stalkers.rules.world_rules import _apply_item_effects
    item_info = ITEM_TYPES.get(item["type"], {})
    effects = item_info.get("effects", {})
    _apply_item_effects(agent, effects)
    if _lazy_needs_enabled(state):
        _rt_needs = _cow_runtime()
        ensure_needs_state(agent, world_turn, runtime=_rt_needs, agent_id=agent_id)
        _need_updates: dict[str, float] = {}
        for _need_key in ("hunger", "thirst", "sleepiness"):
            if _need_key in effects:
                _need_updates[_need_key] = float(agent.get(_need_key, 0.0))
        if _need_updates:
            set_needs(agent, _need_updates, world_turn, runtime=_rt_needs, agent_id=agent_id)
            _runtime_set_agent_field(agent_id, "needs_state", agent.get("needs_state"), agent)
        schedule_need_thresholds(state, _rt_needs, agent_id, agent, world_turn)
    _runtime_set_agent_field(agent_id, "inventory", [i for i in agent.get("inventory", []) if i["id"] != item["id"]], agent)
    _runtime_set_action_used(agent, True)
    item_name = item_info.get("name", item.get("name", item["type"]))
    _add_memory(
        agent, world_turn, state, "action",
        f"Использовал «{item_name}»",
        {"action_kind": action_kind, "item_type": item["type"]},
        summary=f"Использовал «{item_name}»",
    )
    return [{
        "event_type": "item_consumed",
        "payload": {"agent_id": agent_id, "item_id": item["id"], "item_type": item["type"], "effects": effects},
    }]


def _bot_equip_from_inventory(
    agent_id: str,
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    slot: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Move a matching item from the agent's inventory into an equipment slot.

    The old item in the slot (if any) is returned to the inventory.
    Returns a non-empty event list on success, empty list if no matching item found.
    """
    inventory = agent.get("inventory", [])
    item = next((i for i in inventory if i["type"] in item_types), None)
    if item is None:
        return []
    _rt_eq = _cow_runtime()
    if _rt_eq is not None:
        equipment = _rt_eq.mutable_agent_equipment(agent_id)
    else:
        equipment = agent.setdefault("equipment", {})
    # Return old equipment to inventory (if any)
    old_item = equipment.get(slot)
    if old_item:
        _runtime_set_agent_field(agent_id, "inventory", [i for i in inventory if i["id"] != item["id"]] + [old_item], agent)
    else:
        _runtime_set_agent_field(agent_id, "inventory", [i for i in inventory if i["id"] != item["id"]], agent)
    equipment[slot] = item
    _runtime_set_action_used(agent, True)
    item_name = item.get("name", item["type"])
    _add_memory(
        agent, world_turn, state, "action",
        f"Экипировал «{item_name}»",
        {"action_kind": "equip", "item_type": item["type"], "slot": slot},
        summary=f"Экипировал «{item_name}» в слот {slot}",
    )
    return [{"event_type": "item_equipped",
             "payload": {"agent_id": agent_id, "item_type": item["type"], "slot": slot}}]


# Mapping from seek_item "item_category" to the corresponding frozenset of item types.
# Used by _bot_pickup_on_arrival to resolve the category stored in memory.
# "ammo" is handled separately (uses the "ammo_type" field from the decision effects).
_SEEK_CATEGORY_TO_TYPES: Dict[str, "frozenset[str]"] = {
    "weapon": WEAPON_ITEM_TYPES,
    "armor": ARMOR_ITEM_TYPES,
    "medical": HEAL_ITEM_TYPES,
    "food": FOOD_ITEM_TYPES,
    "drink": DRINK_ITEM_TYPES,
    "secret_document": SECRET_DOCUMENT_ITEM_TYPES,
}


def _bot_pickup_on_arrival(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """If the agent's most recent *decision* memory is a ``seek_item`` whose
    ``destination`` matches the agent's current location, immediately attempt to
    pick up items of that category from the ground.

    This prevents the bot from re-evaluating priorities on the arrival tick and
    travelling away before collecting the item it specifically came for.

    When the item is not found on the ground **and** the location is not a trader
    (traders are visited to *buy*, not pick up), ``item_not_found_here`` is
    recorded immediately.  This is critical: without it, an emergency that fires
    on the same arrival tick redirects the agent before the normal priority tree
    can record the dead end, causing the agent to loop back to the same empty
    location on the next search.

    Returns pickup events on success, or an empty list if no item was found
    (the caller then falls through to the normal priority tree).
    """
    loc_id = agent.get("location_id")
    # Find the most recent *decision* memory entry.
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "decision":
            continue
        # We found the latest decision.
        effects = _v3_details(rec)
        if _v3_action_kind(rec) != "seek_item":
            return []  # Latest decision was something else — no pending seek.
        if effects.get("destination") != loc_id:
            return []  # Still en-route to a different location.
        category = effects.get("item_category", "")
        if category == "ammo":
            ammo_type = effects.get("ammo_type")
            if not ammo_type:
                return []
            item_types: "frozenset[str]" = frozenset({ammo_type})
        else:
            item_types = _SEEK_CATEGORY_TO_TYPES.get(category)  # type: ignore[assignment]
            if not item_types:
                return []
        # If the seek was already resolved on a prior tick (item was picked up or
        # confirmed absent), don't re-trigger the arrival logic.  Without this
        # guard, the most-recent-decision is still the old seek_item entry on tick
        # T+1 and beyond, causing spurious "item_not_found_here" writes.
        if loc_id in _item_not_found_locations(agent, item_types):
            return []
        # suppress_not_found=True: the caller writes item_picked_up_here on success,
        # so the "📭 Предметы закончились" note from _bot_pickup_item_from_ground
        # would be a misleading duplicate.  The blocking is handled by item_picked_up_here.
        pickup_events = _bot_pickup_item_from_ground(
            agent_id, agent, item_types, state, world_turn, suppress_not_found=True
        )
        loc = state.get("locations", {}).get(loc_id, {})
        if not pickup_events:
            # Item not on the ground.  If this is not a trader location, record
            # the dead end immediately so that any interrupt (emergency, higher-
            # priority need) that fires next cannot cause the agent to loop back
            # here on the next search cycle.  Trader locations are skipped because
            # the agent came to *buy*, not pick up, so "not found on ground" is
            # expected and must not blacklist the trader.
            if _find_trader_at_location(loc_id, state) is None:
                _maybe_record_item_not_found(
                    agent, world_turn, state, loc_id, loc, item_types, category
                )
        else:
            # Item found and picked up — record a success observation so the agent
            # won't plan a second trip back here for the same item category.
            _maybe_record_item_picked_up(
                agent, world_turn, state, loc_id, loc, item_types, category
            )
        return pickup_events
    return []


def _bot_sell_on_arrival(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """If the most recent decision was a ``sell_at_trader`` whose destination
    matches the agent's current location, sell artifacts to the trader now.

    Called after emergency checks but before equipment-maintenance so that
    the agent completes the purpose of its trip (selling artifacts) before
    being redirected to satisfy secondary needs like buying new gear.

    Returns sell events on success, or an empty list if not applicable.
    """
    loc_id = agent.get("location_id")
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "decision":
            continue
        effects = _v3_details(rec)
        if _v3_action_kind(rec) != "sell_at_trader":
            return []  # Latest decision was something else — no pending sell.
        if effects.get("destination") != loc_id:
            return []  # Still en-route to a different location.
        # Arrived at the target trader location — attempt the sale.
        trader = _find_trader_at_location(loc_id, state)
        if trader is None:
            return []  # Trader not present (edge case).
        return _bot_sell_to_trader(agent_id, agent, trader, state, world_turn)
    return []


def _bot_pickup_item_from_ground(
    agent_id: str,
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    state: Dict[str, Any],
    world_turn: int,
    *,
    suppress_not_found: bool = False,
) -> List[Dict[str, Any]]:
    """Pick up a matching item from the ground at the agent's current location.

    Removes the item from ``location["items"]`` and adds it to the agent's
    inventory.  Returns a non-empty event list on success.

    After a successful pickup, if no items of ``item_types`` remain on the ground
    at this location and *suppress_not_found* is False, an ``item_not_found_here``
    observation is written so that the agent does not return looking for more of
    those items until a fresh ``observed:items`` entry supersedes the block.

    Pass ``suppress_not_found=True`` when the caller (e.g. ``_bot_pickup_on_arrival``)
    will itself write the appropriate resolution record (``item_picked_up_here``), to
    avoid writing a misleading "items exhausted" note alongside the success record.
    """
    loc_id = agent.get("location_id")
    loc = state.get("locations", {}).get(loc_id, {})
    ground_items = loc.get("items", [])
    item = next((i for i in ground_items if i.get("type") in item_types), None)
    if item is None:
        return []
    new_ground_items = [i for i in ground_items if i["id"] != item["id"]]
    _runtime_set_location_field(state, loc_id, "items", new_ground_items)
    _rt_pu = _cow_runtime()
    if _rt_pu is not None:
        _pu_inv = _rt_pu.mutable_agent_inventory(agent_id)
    else:
        _pu_inv = agent.setdefault("inventory", [])
    _pu_inv.append(item)
    _runtime_set_action_used(agent, True)
    item_name = item.get("name", item["type"])
    loc_name = loc.get("name", loc_id)
    _add_memory(
        agent, world_turn, state, "action",
        f"Поднял «{item_name}» с земли",
        {"action_kind": "pickup_ground", "item_type": item["type"], "location_id": loc_id},
        summary=f"Поднял «{item_name}» с земли в локации «{loc_name}»",
    )
    # If no more items of the requested types remain AND the caller has not asked to
    # suppress this note, record it so the agent won't plan another trip here.
    remaining = new_ground_items
    if not suppress_not_found and not any(i.get("type") in item_types for i in remaining):
        _add_memory(
            agent, world_turn, state, "observation",
            f"📭 Предметы закончились в «{loc_name}»",
            {
                "action_kind": "item_not_found_here",
                "source": "pickup",
                "location_id": loc_id,
                "item_types": sorted(item_types),
            },
            summary=f"Предметы нужных типов в «{loc_name}» закончились",
        )
    return [{"event_type": "item_picked_up",
             "payload": {"agent_id": agent_id, "item_type": item["type"],
                         "item_id": item["id"], "location_id": loc_id}}]


def _item_not_found_locations(
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
) -> "set[str]":
    """Return loc_ids where the agent has already resolved a seek_item trip.

    A location is blocked when the agent either:
    * arrived at a ``seek_item`` destination and the item was not there
      (``item_not_found_here`` observation), **or**
    * arrived at a ``seek_item`` destination and successfully picked the item
      up (``item_picked_up_here`` observation).

    Both cases mean the agent has "used up" that lead and should look elsewhere.

    An entry is superseded — and the block lifted — when a *newer* ``observed:items``
    memory for those item types exists at the same location (e.g. a fresh item spawn
    or new intel from another stalker).
    """
    last_item_obs_turn: Dict[str, int] = {}   # loc_id → most recent observed-items turn
    last_resolved_turn: Dict[str, int] = {}   # loc_id → most recent resolved-search turn

    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        effects = _v3_details(rec)
        turn = _v3_turn(rec)
        loc_id = rec.get("location_id") or effects.get("location_id")
        if not loc_id:
            continue

        if _v3_action_kind(rec) in ("item_not_found_here", "item_picked_up_here"):
            mem_types = frozenset(effects.get("item_types", []))
            if item_types.intersection(mem_types):
                last_resolved_turn[loc_id] = max(last_resolved_turn.get(loc_id, 0), turn)

        elif effects.get("observed") == "items":
            obs_types = set(effects.get("item_types", []))
            if item_types.intersection(obs_types):
                last_item_obs_turn[loc_id] = max(last_item_obs_turn.get(loc_id, 0), turn)

    result: set = set()
    for loc_id, resolved_turn in last_resolved_turn.items():
        # Only block if the resolved observation is newer than (or same turn as) the
        # most recent item observation at that location.
        obs_turn = last_item_obs_turn.get(loc_id, 0)
        if resolved_turn >= obs_turn:
            result.add(loc_id)
    return result


def _maybe_record_item_not_found(
    agent: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    loc_id: str,
    loc: Dict[str, Any],
    item_types: "frozenset[str]",
    item_category: str,
) -> None:
    """Write an ``item_not_found_here`` observation if the agent arrived at *loc_id*
    specifically to retrieve an item of *item_category* but found nothing on the ground.

    The check: the agent must have a prior ``seek_item`` decision memory that named
    *loc_id* as the destination for *item_category*.  If no such decision exists, the
    visit is incidental and no observation is written.

    Suppression: if the location is already in ``_item_not_found_locations`` (blocked by
    any prior ``item_not_found_here`` or ``item_picked_up_here`` not yet superseded by a
    fresher item observation), no additional entry is written.  This prevents the
    ``_bot_pursue_goal`` fallback from writing a spurious arrival note on the tick after
    a successful ``_bot_pickup_on_arrival`` already resolved the seek.
    """
    # Only record if the agent explicitly decided to travel here for this item.
    found_seek = False
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "decision":
            continue
        effects = _v3_details(rec)
        if effects.get("destination") != loc_id or effects.get("item_category") != item_category:
            continue
        ak = _v3_action_kind(rec)
        if ak == "seek_item":
            if effects.get("emergency"):
                # Emergency seek_item decisions always target a trader for buying,
                # never for ground pickup.  "No item on ground" at a trader is
                # expected and must not be recorded as a dead end.
                return
            found_seek = True
            break
        elif ak == "buy_item":
            # The most recent relevant decision was to travel here for purchasing,
            # not for picking up from the ground.  "No ground items" is expected
            # at a trader location, so do not record a dead-end observation.
            return
    if not found_seek:
        return

    # Suppress if already resolved (not_found or picked_up) and not superseded
    # by a fresh item observation.  This handles both same-turn and cross-turn
    # duplicates, and prevents writing "item missing" after a prior successful pickup.
    if loc_id in _item_not_found_locations(agent, item_types):
        return

    loc_name = loc.get("name", loc_id)
    _add_memory(
        agent, world_turn, state, "observation",
        f"⚠️ Предмет исчез из «{loc_name}»",
        {
            "action_kind": "item_not_found_here",
            "source": "arrival",
            "location_id": loc_id,
            "item_types": sorted(item_types),
        },
        summary=f"Искомый предмет исчез из «{loc_name}» — кто-то забрал раньше меня",
    )


def _maybe_record_item_picked_up(
    agent: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    loc_id: str,
    loc: Dict[str, Any],
    item_types: "frozenset[str]",
    item_category: str,
) -> None:
    """Write an ``item_picked_up_here`` observation when the agent arrived at *loc_id*
    specifically to retrieve an item of *item_category* and successfully picked one up.

    The observation marks this location as "already resolved" for items of these types
    so that ``_find_item_memory_location`` (via ``_item_not_found_locations``) does not
    send the agent back to the same spot on the next search cycle.

    The check: the agent must have a prior ``seek_item`` decision memory that named
    *loc_id* as the destination for *item_category*.  If no such decision exists, the
    visit is incidental and no observation is written.
    Duplicate entries for the same location and item category within the same turn are
    suppressed.
    """
    # Only record if the agent explicitly decided to travel here for this item.
    found_seek = False
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "decision":
            continue
        effects = _v3_details(rec)
        if effects.get("destination") != loc_id or effects.get("item_category") != item_category:
            continue
        ak = _v3_action_kind(rec)
        if ak == "seek_item":
            if effects.get("emergency"):
                # Emergency seek_item decisions always target a trader for buying.
                # If the agent happened to pick up an item on the ground there, do
                # not write item_picked_up_here — that would blacklist the trader
                # for future purchases of the same category.
                return
            found_seek = True
            break
        elif ak == "buy_item":
            return
    if not found_seek:
        return

    # Suppress duplicates: don't write the same entry twice in the same turn.
    for rec in _v3_records_desc(agent):
        if _v3_turn(rec) != world_turn:
            break
        fx = _v3_details(rec)
        if (
            _v3_action_kind(rec) == "item_picked_up_here"
            and (rec.get("location_id") or fx.get("location_id")) == loc_id
            and frozenset(fx.get("item_types", [])).intersection(item_types)
        ):
            return  # already recorded this turn

    loc_name = loc.get("name", loc_id)
    _add_memory(
        agent, world_turn, state, "observation",
        f"✅ Нашёл {item_category} в «{loc_name}»",
        {
            "action_kind": "item_picked_up_here",
            "source": "seek_item_arrival",
            "location_id": loc_id,
            "item_types": sorted(item_types),
        },
        summary=f"Нашёл и забрал {item_category} в «{loc_name}»",
    )


def _find_item_memory_location(
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    state: Dict[str, Any],
) -> Optional[str]:
    """Scan agent memory for the most recent observation of a needed item type.

    Returns the ``loc_id`` of the most recently remembered location where
    one of the requested item types was seen, or ``None`` if no such memory
    exists.  Locations that are unreachable (all paths blocked by closed
    connections) or that the agent has already resolved a search at
    (``item_not_found_here`` or ``item_picked_up_here`` observation recorded
    via ``_maybe_record_item_not_found`` / ``_maybe_record_item_picked_up``) are
    excluded.

    Relies purely on the agent's memory — no omniscient ground-truth check.
    """
    from app.games.zone_stalkers.rules.world_rules import _bfs_route

    excluded = _item_not_found_locations(agent, item_types)
    locations = state.get("locations", {})
    agent_loc = agent.get("location_id", "")
    # Collect candidate locations from memory (newest first)
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        effects = _v3_details(rec)
        if effects.get("observed") != "items":
            continue
        loc_id = rec.get("location_id") or effects.get("location_id")
        if not loc_id or loc_id not in locations:
            continue
        if loc_id in excluded:
            continue
        remembered_types = set(effects.get("item_types", []))
        if not remembered_types.intersection(item_types):
            continue
        # Skip the agent's current location — ground pickup is handled by the caller
        # before this function is ever called, so returning the current loc would
        # yield a zero-distance "travel" that never moves the agent.
        if loc_id == agent_loc:
            continue
        # Skip unreachable locations (closed connections may cut off the path)
        if not _bfs_route(locations, agent_loc, loc_id):
            continue
        return loc_id
    return None


def _bot_ask_colocated_stalkers_about_item(
    agent_id: str,
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    item_category_name: str,
    state: Dict[str, Any],
    world_turn: int,
) -> Optional[str]:
    """Ask co-located stalker agents about the location of items of the given types.

    Scans ALL memories of EVERY co-located alive stalker.  For each unique
    (source_agent, location) pair not already known, writes one
    ``intel_from_stalker`` observation to *agent*'s memory so that a subsequent
    call to ``_find_item_memory_location`` can use it.

    Returns the ``loc_id`` of the first new piece of intel written (oldest
    co-located stalker processed first), or ``None`` if nothing new was found.

    Deduplication: pairs already recorded during the current turn are skipped.

    Staleness filter: if the asking agent has already resolved a search at the
    reported location (``item_not_found_here`` or ``item_picked_up_here``) at a
    world_turn >= the other stalker's observation world_turn, the intel is
    considered stale (the agent already acted on it or newer state supersedes it)
    and is silently ignored.
    """
    loc_id = agent.get("location_id", "")
    agents = state.get("agents", {})

    # Build a set of (source_agent_id, loc_id) pairs the asking agent already
    # knows (any turn) so we don't write duplicate entries.
    already_known: set = set()
    for rec in _v3_records_desc(agent):
        fx = _v3_details(rec)
        if _v3_action_kind(rec) == "intel_from_stalker":
            ak_loc = rec.get("location_id") or fx.get("location_id")
            already_known.add((fx.get("source_agent_id"), ak_loc))

    # Precompute the most recent "resolved" turn per location for the asking agent.
    # Intel whose observation turn is <= the resolved turn is stale and should be skipped.
    resolved_turns: Dict[str, int] = {}
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        fx = _v3_details(rec)
        if _v3_action_kind(rec) not in ("item_not_found_here", "item_picked_up_here"):
            continue
        mem_types = frozenset(fx.get("item_types", []))
        if not item_types.intersection(mem_types):
            continue
        r_loc = rec.get("location_id") or fx.get("location_id")
        if r_loc:
            r_turn = _v3_turn(rec)
            resolved_turns[r_loc] = max(resolved_turns.get(r_loc, 0), r_turn)

    first_loc: Optional[str] = None

    for other_id, other in agents.items():
        if other_id == agent_id:
            continue
        if not other.get("is_alive", True):
            continue
        if other.get("location_id") != loc_id:
            continue
        if other.get("archetype") != "stalker_agent":
            continue

        other_name = other.get("name", other_id)

        # Track which locations we've already recorded for *this stalker* in
        # this call to avoid writing multiple entries for the same location from
        # one stalker (keep only the newest observation per location).
        seen_locs_this_stalker: set = set()

        # Scan the other agent's memory (newest first) for items observations.
        for other_rec in _v3_records_desc(other):
            if _v3_memory_type(other_rec) != "observation":
                continue
            other_fx = _v3_details(other_rec)
            if other_fx.get("observed") != "items":
                continue
            obs_loc = other_rec.get("location_id") or other_fx.get("location_id")
            if not obs_loc:
                continue
            obs_types = frozenset(other_fx.get("item_types", []))
            if not obs_types.intersection(item_types):
                continue

            # Skip if we already wrote an entry for this (stalker, location)
            # in this call.  Because the loop iterates newest-first, the first
            # observation we encounter for a given location is always the most
            # recent one — older observations for the same location are redundant
            # and will be skipped by the `seen_locs_this_stalker` guard below.
            if obs_loc in seen_locs_this_stalker:
                continue

            # Skip stale intel: if the asking agent already resolved this location
            # (found or not-found) at a turn >= the other stalker's observation turn,
            # the intel predates our knowledge and is no longer actionable.
            obs_turn = _v3_turn(other_rec)
            if obs_turn <= resolved_turns.get(obs_loc, -1):
                continue

            # Compute in-game date/time of the informant's observation so the
            # asking agent remembers *when* the intel was gathered.
            _obs_time_label = _turn_to_time_label(obs_turn)

            obs_loc_name = state.get("locations", {}).get(obs_loc, {}).get("name", obs_loc)
            current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
            matched_types = sorted(obs_types.intersection(item_types))

            if (other_id, obs_loc) in already_known:
                # Entry already exists in memory_v3 — we skip re-writing it
                # (memory_v3 cap handles size; no in-place mutation needed).
                seen_locs_this_stalker.add(obs_loc)
                if first_loc is None:
                    first_loc = obs_loc
                continue

            _add_memory(
                agent, world_turn, state, "observation",
                f"💬 Разведданные от {other_name}",
                {
                    "action_kind": "intel_from_stalker",
                    "observed": "items",
                    "location_id": obs_loc,
                    "item_types": matched_types,
                    "source_agent_id": other_id,
                    "source_agent_name": other_name,
                    "obs_world_turn": obs_turn,
                },
                summary=(
                    f"{other_name} рассказал, что видел {item_category_name} "
                    f"в «{obs_loc_name}» (в {current_loc_name}). "
                    f"Видел {_obs_time_label}."
                ),
            )
            seen_locs_this_stalker.add(obs_loc)
            already_known.add((other_id, obs_loc))  # prevent cross-stalker duplicates
            if first_loc is None:
                first_loc = obs_loc

    return first_loc


def _bot_ask_colocated_stalkers_about_agent(
    agent_id: str,
    agent: Dict[str, Any],
    target_agent_id: str,
    target_agent_name: str,
    state: Dict[str, Any],
    world_turn: int,
) -> Optional[str]:
    """Ask co-located stalker agents whether they have seen a specific agent.

    Scans the ``observed="stalkers"`` observations in every co-located alive
    stalker's memory.  For each unique (source_agent, location) pair whose
    ``names`` list contained *target_agent_name*, writes an
    ``intel_from_stalker`` + ``observed="agent_location"`` entry to the caller's
    memory.

    Returns the ``loc_id`` of the first new piece of intel written, or ``None``
    if nothing new was found.  Already-exhausted locations (recorded via
    ``hunt_area_exhausted``) are filtered out.
    """
    loc_id = agent.get("location_id", "")
    agents = state.get("agents", {})

    # Build set of (source_agent_id, loc_id) pairs already known (any turn).
    already_known: set = set()
    for rec in _v3_records_desc(agent):
        fx = _v3_details(rec)
        if (
            _v3_action_kind(rec) == "intel_from_stalker" and fx.get("observed") == "agent_location"
        ) or _v3_action_kind(rec) == "target_corpse_reported":
            ak_loc = rec.get("location_id") or fx.get("location_id")
            already_known.add((fx.get("source_agent_id"), ak_loc))

    # Collect exhausted intel locations (base locations whose full neighbourhood
    # has already been searched and target was not found there).
    exhausted_locs: set = set()
    for rec in _v3_records_desc(agent):
        fx = _v3_details(rec)
        if (_v3_action_kind(rec) == "hunt_area_exhausted"
                and fx.get("target_id") == target_agent_id):
            exhausted_locs.add(rec.get("location_id") or fx.get("location_id"))

    first_loc: Optional[str] = None

    for other_id, other in agents.items():
        if other_id == agent_id:
            continue
        if not other.get("is_alive", True):
            continue
        if other.get("location_id") != loc_id:
            continue
        if other.get("archetype") != "stalker_agent":
            continue

        other_name = other.get("name", other_id)
        seen_locs_this_stalker: set = set()

        other_knowledge = other.get("knowledge_v1") if isinstance(other.get("knowledge_v1"), dict) else {}
        known_target = (other_knowledge.get("known_npcs") or {}).get(target_agent_id) if isinstance(other_knowledge, dict) else None
        if isinstance(known_target, dict):
            death_evidence = known_target.get("death_evidence") if isinstance(known_target.get("death_evidence"), dict) else {}
            known_loc = str(
                death_evidence.get("corpse_location_id")
                or known_target.get("reported_corpse_location_id")
                or known_target.get("last_seen_location_id")
                or ""
            )
            death_status = str(death_evidence.get("status") or "alive")
            is_target_corpse_report = death_status in {"reported_dead", "corpse_seen", "confirmed_dead"} and bool(known_loc)
            is_target_location_intel = bool(known_loc and not is_target_corpse_report)
            if known_loc and (is_target_location_intel or is_target_corpse_report):
                if known_loc not in exhausted_locs or is_target_corpse_report:
                    if known_loc not in seen_locs_this_stalker and (other_id, known_loc) not in already_known:
                        obs_turn = int(
                            death_evidence.get("reported_turn")
                            or death_evidence.get("observed_turn")
                            or known_target.get("last_seen_turn")
                            or 0
                        )
                        _obs_time_label = _turn_to_time_label(obs_turn)
                        obs_loc_name = state.get("locations", {}).get(known_loc, {}).get("name", known_loc)
                        current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
                        if is_target_corpse_report:
                            _add_memory(
                                agent, world_turn, state, "observation",
                                f"💬 Свидетель сообщил о теле цели ({other_name})",
                                {
                                    "action_kind": "target_corpse_reported",
                                    "target_id": target_agent_id,
                                    "target_name": target_agent_name,
                                    "reported_corpse_location_id": known_loc,
                                    "location_id": known_loc,
                                    "source_agent_id": other_id,
                                    "source_agent_name": other_name,
                                    "obs_world_turn": obs_turn,
                                    "confidence": 0.75,
                                    "directly_observed": False,
                                },
                                summary=(
                                    f"{other_name} сообщил, что видел тело цели «{target_agent_name}» "
                                    f"в «{obs_loc_name}» (в {current_loc_name}). "
                                    f"Свидетельство {_obs_time_label}."
                                ),
                            )
                        else:
                            _add_memory(
                                agent, world_turn, state, "observation",
                                f"💬 Разведданные от {other_name}",
                                {
                                    "action_kind": "intel_from_stalker",
                                    "observed": "agent_location",
                                    "location_id": known_loc,
                                    "target_agent_id": target_agent_id,
                                    "target_agent_name": target_agent_name,
                                    "source_agent_id": other_id,
                                    "source_agent_name": other_name,
                                    "obs_world_turn": obs_turn,
                                    "confidence": 0.55,
                                },
                                summary=(
                                    f"{other_name} рассказал, что видел «{target_agent_name}» "
                                    f"в «{obs_loc_name}» (в {current_loc_name}). "
                                    f"Видел {_obs_time_label}."
                                ),
                            )
                        seen_locs_this_stalker.add(known_loc)
                        already_known.add((other_id, known_loc))
                        if first_loc is None:
                            first_loc = known_loc

        for other_rec in _v3_records_desc(other):
            if _v3_memory_type(other_rec) != "observation":
                continue
            other_fx = _v3_details(other_rec)
            obs_loc = other_rec.get("location_id") or other_fx.get("location_id")
            if not obs_loc:
                continue
            is_target_location_intel = False
            is_target_corpse_report = False
            if other_fx.get("observed") == "stalkers":
                names_seen = other_fx.get("names", [])
                if target_agent_name in names_seen:
                    is_target_location_intel = True
            else:
                dead_agent_id = str(other_fx.get("dead_agent_id") or other_fx.get("target_id") or "")
                if dead_agent_id == target_agent_id and _v3_action_kind(other_rec) in {
                    "corpse_seen",
                    "target_corpse_seen",
                    "target_death_confirmed",
                }:
                    is_target_corpse_report = True
            if not is_target_location_intel and not is_target_corpse_report:
                continue
            if obs_loc in exhausted_locs and not is_target_corpse_report:
                continue
            if obs_loc in seen_locs_this_stalker:
                continue

            obs_turn = _v3_turn(other_rec)
            _obs_time_label = _turn_to_time_label(obs_turn)
            obs_loc_name = state.get("locations", {}).get(obs_loc, {}).get("name", obs_loc)
            current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)

            if (other_id, obs_loc) in already_known:
                # Entry already exists in memory_v3 — skip re-writing (no in-place mutation).
                seen_locs_this_stalker.add(obs_loc)
                if first_loc is None:
                    first_loc = obs_loc
                continue

            if is_target_corpse_report:
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"💬 Свидетель сообщил о теле цели ({other_name})",
                    {
                        "action_kind": "target_corpse_reported",
                        "target_id": target_agent_id,
                        "target_name": target_agent_name,
                        "reported_corpse_location_id": obs_loc,
                        "location_id": obs_loc,
                        "source_agent_id": other_id,
                        "source_agent_name": other_name,
                        "obs_world_turn": obs_turn,
                        "confidence": 0.75,
                        "directly_observed": False,
                    },
                    summary=(
                        f"{other_name} сообщил, что видел тело цели «{target_agent_name}» "
                        f"в «{obs_loc_name}» (в {current_loc_name}). "
                        f"Свидетельство {_obs_time_label}."
                    ),
                )
            else:
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"💬 Разведданные от {other_name}",
                    {
                        "action_kind": "intel_from_stalker",
                        "observed": "agent_location",
                        "location_id": obs_loc,
                        "target_agent_id": target_agent_id,
                        "target_agent_name": target_agent_name,
                        "source_agent_id": other_id,
                        "source_agent_name": other_name,
                        "obs_world_turn": obs_turn,
                        # Stalker witnesses are less reliable than trader network intel.
                        "confidence": 0.55,
                    },
                    summary=(
                        f"{other_name} рассказал, что видел «{target_agent_name}» "
                        f"в «{obs_loc_name}» (в {current_loc_name}). "
                        f"Видел {_obs_time_label}."
                    ),
                )
            seen_locs_this_stalker.add(obs_loc)
            already_known.add((other_id, obs_loc))
            if first_loc is None:
                first_loc = obs_loc

    return first_loc


def _bot_buy_hunt_intel_from_trader(
    agent_id: str,
    agent: Dict[str, Any],
    target_id: str,
    target_name: str,
    state: Dict[str, Any],
    world_turn: int,
) -> bool:
    """Buy location intel about a kill target from a trader at the current location.

    The trader knows the current whereabouts of every alive stalker in the zone
    (via their trade network).  The hunter pays ``_HUNT_INTEL_PRICE`` to receive
    an ``intel_from_trader`` observation that ``_find_hunt_intel_location`` can
    use to resume pursuit.

    Returns ``True`` if intel was purchased; ``False`` if the purchase was not
    possible (no trader, target dead/unknown, insufficient funds, or intel for
    this target was already bought this turn).
    """
    loc_id = agent.get("location_id", "")
    locations = state.get("locations", {})
    loc_name = locations.get(loc_id, {}).get("name", loc_id)

    # Only purchase once per world_turn for this target.
    for rec in _v3_records_desc(agent):
        if _v3_turn(rec) != world_turn:
            continue
        fx = _v3_details(rec)
        if (
            (_v3_action_kind(rec) == "intel_from_trader" and fx.get("target_agent_id") == target_id)
            or (_v3_action_kind(rec) == "target_corpse_reported" and str(fx.get("target_id") or "") == str(target_id))
        ):
            return False  # Already bought this turn

    target = state.get("agents", {}).get(target_id)
    if not target:
        return False

    target_is_alive = bool(target.get("is_alive", True))
    target_loc = target.get("location_id")
    corpse_loc = None
    if not target_is_alive:
        for _loc_id, _loc in state.get("locations", {}).items():
            for _corpse in (_loc.get("corpses") or []):
                if (
                    isinstance(_corpse, dict)
                    and bool(_corpse.get("visible", True))
                    and str(_corpse.get("agent_id") or "") == str(target_id)
                ):
                    corpse_loc = str(_corpse.get("location_id") or _loc_id)
                    break
            if corpse_loc:
                break
        if not corpse_loc:
            return False
    elif not target_loc:
        return False

    traders = state.get("traders", {})
    trader = next(
        (t for t in traders.values()
         if t.get("location_id") == loc_id and t.get("is_alive", True)),
        None,
    )
    if trader is None:
        return False

    if agent.get("money", 0) < _HUNT_INTEL_PRICE:
        return False

    # Deduct money and credit trader
    agent["money"] = agent.get("money", 0) - _HUNT_INTEL_PRICE
    trader["money"] = trader.get("money", 0) + _HUNT_INTEL_PRICE

    trader_name = trader.get("name", trader.get("id", "Торговец"))
    if target_is_alive:
        target_loc_name = locations.get(target_loc, {}).get("name", target_loc)
        _add_memory(
            agent, world_turn, state, "observation",
            f"💰 Купил информацию: «{target_name}» замечен в «{target_loc_name}»",
            {
                "action_kind": "intel_from_trader",
                "observed": "agent_location",
                "location_id": target_loc,
                "target_agent_id": target_id,
                "target_agent_name": target_name,
                "source_agent_id": trader.get("id", ""),
                "source_agent_name": trader_name,
                "price_paid": _HUNT_INTEL_PRICE,
                # Trader network intel is more reliable than stalker word-of-mouth.
                "confidence": 0.70,
            },
            summary=(
                f"Я купил у торговца «{trader_name}» информацию о местонахождении "
                f"«{target_name}» за {_HUNT_INTEL_PRICE} руб. "
                f"По данным торговца, цель сейчас в «{target_loc_name}»."
            ),
        )
    else:
        corpse_loc_name = locations.get(corpse_loc, {}).get("name", corpse_loc)
        _add_memory(
            agent, world_turn, state, "observation",
            f"💰 Купил информацию о теле цели в «{corpse_loc_name}»",
            {
                "action_kind": "target_corpse_reported",
                "target_id": target_id,
                "target_name": target_name,
                "reported_corpse_location_id": corpse_loc,
                "location_id": corpse_loc,
                "source_agent_id": trader.get("id", ""),
                "source_agent_name": trader_name,
                "price_paid": _HUNT_INTEL_PRICE,
                "confidence": 0.80,
                "directly_observed": False,
            },
            summary=(
                f"Я купил у торговца «{trader_name}» информацию о месте, где видели тело "
                f"«{target_name}»: «{corpse_loc_name}»."
            ),
        )
    return True


def _find_hunt_intel_location(
    agent: Dict[str, Any],
    target_agent_id: str,
    state: Dict[str, Any],
) -> Optional[str]:
    """Return the most recent non-exhausted intel location for a hunt target.

    Scans memory for observations that indicate where the target was last seen:
    - ``intel_from_stalker`` / ``intel_from_trader`` + ``observed="agent_location"``
      entries referencing *target_agent_id*;
    - ``retreat_observed`` entries where ``subject == target_agent_id`` —
      the ``to_location`` field tells us where the target fled.

    Locations recorded as ``hunt_area_exhausted`` for this target are skipped.
    Returns the most recent (last written) matching loc_id, or ``None``.
    """
    exhausted_locs: set = set()
    for rec in _v3_records_desc(agent):
        fx = _v3_details(rec)
        if (_v3_action_kind(rec) == "hunt_area_exhausted"
                and fx.get("target_id") == target_agent_id):
            exhausted_locs.add(rec.get("location_id") or fx.get("location_id"))

    best_loc: Optional[str] = None
    best_turn: int = -1
    for rec in _v3_records_desc(agent):
        if _v3_memory_type(rec) != "observation":
            continue
        fx = _v3_details(rec)
        ak = _v3_action_kind(rec)

        # Source 1: stalker / trader location intel
        if ak in ("intel_from_stalker", "intel_from_trader"):
            if fx.get("observed") != "agent_location":
                continue
            if fx.get("target_agent_id") != target_agent_id:
                continue
            obs_loc = rec.get("location_id") or fx.get("location_id")
            if not obs_loc or obs_loc in exhausted_locs:
                continue
            t = _v3_turn(rec)
            if t > best_turn:
                best_turn = t
                best_loc = obs_loc

        # Source 2: witness reports about target corpse location
        elif ak == "target_corpse_reported":
            if str(fx.get("target_id") or "") != str(target_agent_id):
                continue
            corpse_loc = fx.get("reported_corpse_location_id") or rec.get("location_id") or fx.get("location_id")
            if not corpse_loc:
                continue
            t = _v3_turn(rec)
            if t > best_turn:
                best_turn = t
                best_loc = corpse_loc

        # Source 3: direct corpse observations
        elif ak in {"target_corpse_seen", "target_death_confirmed"}:
            rec_target = str(fx.get("target_id") or fx.get("dead_agent_id") or "")
            if rec_target != str(target_agent_id):
                continue
            corpse_loc = fx.get("corpse_location_id") or rec.get("location_id") or fx.get("location_id")
            if not corpse_loc:
                continue
            t = _v3_turn(rec)
            if t > best_turn:
                best_turn = t
                best_loc = corpse_loc

        # Source 4: retreat_observed — the target was seen fleeing to to_location
        elif ak == "retreat_observed":
            if fx.get("subject") != target_agent_id:
                continue
            retreat_dest = fx.get("to_location")
            if not retreat_dest or retreat_dest in exhausted_locs:
                continue
            t = _v3_turn(rec)
            if t > best_turn:
                best_turn = t
                best_loc = retreat_dest

    return best_loc


def _get_searched_locations_for_target(
    agent: Dict[str, Any],
    target_agent_id: str,
    since_turn: int = 0,
) -> set:
    """Return the set of location_ids recorded as searched for *target_agent_id*.

    Only entries at ``world_turn >= since_turn`` are considered, allowing the
    caller to limit the scope to the current search cycle.
    """
    searched: set = set()
    for rec in _v3_records_desc(agent):
        if _v3_turn(rec) < since_turn:
            continue
        fx = _v3_details(rec)
        if (_v3_action_kind(rec) == "hunt_location_searched"
                and fx.get("target_id") == target_agent_id):
            loc = rec.get("location_id") or fx.get("location_id")
            if loc:
                searched.add(loc)
    return searched


def _find_upgrade_target(
    item_types: "frozenset[str]",
    current_item_type: "str | None",
    agent_risk: float,
    agent_money: int,
) -> "str | None":
    """Return the item key that would be a meaningful upgrade over *current_item_type*.

    An item is considered an upgrade when ALL of the following hold:
    1. Its ``risk_tolerance`` is *closer* to *agent_risk* than the current item's.
    2. Its base ``value`` is *higher* than the current item's (it is a "better" item).
    3. The agent can afford it at trader price (base_value × 1.5).

    Returns ``None`` when no upgrade target exists.
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES

    current_info = ITEM_TYPES.get(current_item_type or "", {})
    if not current_item_type:
        # No current item in slot — initial equipment handled by maintenance layer, not upgrade
        return None
    current_rt = float(current_info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
    current_value = int(current_info.get("value", 0))
    current_dist = abs(current_rt - agent_risk)

    best_key: "str | None" = None
    best_dist = current_dist
    best_value = current_value

    for k in item_types:
        if k == current_item_type:
            continue
        info = ITEM_TYPES.get(k)
        if info is None:
            continue
        dist = abs(float(info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE)) - agent_risk)
        value = int(info.get("value", 0))
        # Must be a strictly better risk-tolerance match (or same distance) AND more expensive
        if dist > current_dist:
            continue
        if value <= current_value:
            continue
        buy_price = int(value * 1.5)
        if agent_money < buy_price:
            continue
        # Pick the candidate with the best (smallest) risk distance; tie-break: higher value
        if dist < best_dist or (dist == best_dist and value > best_value):
            best_key = k
            best_dist = dist
            best_value = value

    return best_key


def _bot_try_upgrade_equipment(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Attempt to upgrade an equipped weapon or armor with a better-matching item.

    "Better" means: closer ``risk_tolerance`` to the agent's own AND a higher
    base value (more expensive = higher tier).  The agent must be able to
    afford the upgrade at trader price (base_value × 1.5).

    Upgrade cascade per slot:
      1) Buy from trader at current location (if present)
      2) Travel to nearest trader if not local
    Equipping the new item automatically returns the old one to inventory.

    Returns a non-empty event list when an upgrade action is taken, else [].
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES

    equipment = agent.get("equipment", {})
    agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
    agent_money = agent.get("money", 0)

    upgrade_slots = [
        ("weapon", WEAPON_ITEM_TYPES),
        ("armor", ARMOR_ITEM_TYPES),
    ]

    for slot, item_types in upgrade_slots:
        current = equipment.get(slot)
        if current is None:
            continue  # no existing item to upgrade — handled by initial equipment layer
        current_type = current.get("type")

        upgrade_key = _find_upgrade_target(item_types, current_type, agent_risk, agent_money)
        if upgrade_key is None:
            continue

        upgrade_info = ITEM_TYPES[upgrade_key]
        upgrade_name = upgrade_info.get("name", upgrade_key)
        upgrade_value = int(upgrade_info.get("value", 0))
        upgrade_rt = float(upgrade_info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
        buy_price = int(upgrade_value * 1.5)

        current_info = ITEM_TYPES.get(current_type or "", {})
        current_name = current.get("name", current_type or "?")
        current_rt = float(current_info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))

        trader_loc = _find_nearest_trader_location(loc_id, state)

        if trader_loc == loc_id:
            # Decision memory: explain WHY the upgrade is chosen
            _add_memory(
                agent, world_turn, state, "decision",
                f"Решил обновить {slot}: «{current_name}» → «{upgrade_name}»",
                {
                    "action_kind": "upgrade_decision",
                    "slot": slot,
                    "old_item": current_type,
                    "new_item": upgrade_key,
                    "agent_risk_tolerance": agent_risk,
                    "old_item_risk_tolerance": current_rt,
                    "new_item_risk_tolerance": upgrade_rt,
                    "price": buy_price,
                },
                summary=f"Я решил обновить снаряжение в слоте {slot}: заменить «{current_name}» на «{upgrade_name}» за {buy_price} денег, потому что текущее можно улучшить",
            )
            # Buy the upgrade — _bot_buy_from_trader will also write "decision" + "action" memories
            bought = _bot_buy_from_trader(agent_id, agent, frozenset({upgrade_key}), state, world_turn,
                                          purchase_reason=f"апгрейд снаряжения в слоте «{slot}»")
            if bought:
                # Now equip the freshly bought item (it was added to inventory)
                agent["current_goal"] = "upgrade_equipment"
                evs = _bot_equip_from_inventory(agent_id, agent, frozenset({upgrade_key}), slot, state, world_turn)
                return bought + evs

        elif trader_loc is not None:
            # Need to travel to a trader first
            agent["current_goal"] = "upgrade_equipment"
            trader_name_obj = next(
                (t for t in state.get("traders", {}).values()
                 if t.get("location_id") == trader_loc and t.get("is_alive", True)),
                None,
            )
            trader_name = trader_name_obj.get("name", "торговец") if trader_name_obj else "торговец"
            trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Иду к торговцу за апгрейдом {slot}",
                {
                    "action_kind": "upgrade_travel",
                    "slot": slot,
                    "old_item": current_type,
                    "new_item": upgrade_key,
                    "destination": trader_loc,
                },
                summary=f"Я решил идти к торговцу в «{trader_loc_name}» за апгрейдом {slot}: «{current_name}» → «{upgrade_name}»",
            )
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    return []




def _update_current_goal_from_intent(
    agent: Dict[str, Any],
    intent: Any,
) -> None:
    """Update agent current_goal to reflect the v2 intent kind."""
    _INTENT_GOAL_MAP: Dict[str, str] = {
        "escape_danger":        "emergency_heal",
        "heal_self":            "emergency_heal",
        "seek_food":            "emergency_eat",
        "seek_water":           "emergency_drink",
        "rest":                 "sleep",
        "flee_emission":        "flee_emission",
        "wait_in_shelter":      "shelter",
        "sell_artifacts":       "sell_artifacts",
        "get_rich":             "gather_resources",
        "hunt_target":          "goal_kill_stalker",
        "search_information":   "goal_unravel_zone_mystery",
        "leave_zone":           "leave_zone",
        "upgrade_equipment":    "upgrade_equipment",
        "explore":              "explore",
        "explore_frontier":     "explore",
        "gather_location_intel": "explore",
        "idle":                 "idle",
    }
    if intent.kind == "resupply":
        # Determine which need is most urgent for a descriptive goal name,
        # following the same priority order as planner._plan_resupply.
        from app.games.zone_stalkers.balance.items import (
            FOOD_ITEM_TYPES, DRINK_ITEM_TYPES, HEAL_ITEM_TYPES, AMMO_FOR_WEAPON,
        )
        eq = agent.get("equipment", {})
        inv = agent.get("inventory", [])
        risk_tolerance = float(agent.get("risk_tolerance", 0.5))
        desired_food = 1 + round((1.0 - risk_tolerance) * 2)
        desired_drink = desired_food
        desired_medicine = 2 + round((1.0 - risk_tolerance) * 2)
        food_count = sum(1 for i in inv if i.get("type") in FOOD_ITEM_TYPES)
        drink_count = sum(1 for i in inv if i.get("type") in DRINK_ITEM_TYPES)
        medicine_count = sum(1 for i in inv if i.get("type") in HEAL_ITEM_TYPES)
        weapon = eq.get("weapon")
        weapon_type = weapon.get("type") if isinstance(weapon, dict) else None
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        ammo_count = sum(1 for i in inv if i.get("type") == required_ammo) if required_ammo else 3

        if food_count < desired_food or drink_count < desired_drink:
            agent["current_goal"] = "get_supplies"
        elif not eq.get("armor"):
            agent["current_goal"] = "get_armor"
        elif not eq.get("weapon"):
            agent["current_goal"] = "get_weapon"
        elif ammo_count < 3:
            agent["current_goal"] = "get_ammo"
        elif medicine_count < desired_medicine:
            agent["current_goal"] = "get_medicine"
        else:
            agent["current_goal"] = "upgrade_equipment"
        return
    new_goal = _INTENT_GOAL_MAP.get(intent.kind)
    if new_goal:
        agent["current_goal"] = new_goal


def _run_npc_brain_v3_decision(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """NPC Brain v3 entry point — wraps inner function and emits bot_decision on goal change."""
    agent = _runtime_agent(agent_id, agent)
    prev_goal = agent.get("current_goal")
    events = _run_npc_brain_v3_decision_inner(agent_id, agent, state, world_turn)
    new_goal = agent.get("current_goal")
    if new_goal and prev_goal is not None and new_goal != prev_goal:
        events.append({
            "event_type": "bot_decision",
            "payload": {
                "agent_id": agent_id,
                "agent_name": agent.get("name", agent_id),
                "prev_goal": prev_goal,
                "new_goal": new_goal,
            },
        })
    return events


def _pre_decision_equipment_maintenance(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> Optional[List[Dict[str, Any]]]:
    """Phase-independent pre-decision equipment checks.

    Runs before the needs/intent/plan pipeline.  Returns a non-None event list
    when an immediate action is taken (and the pipeline should be skipped for
    this tick), or ``None`` to indicate "nothing to do — run the pipeline".

    Priority order:
      1. Equip weapon from inventory  → equip in-place
      2. Pick up weapon from ground   → pick up, equip on next tick
      3. Seek weapon from memory      → schedule travel
      4. Equip armor from inventory   → equip in-place
      5. Pick up armor from ground    → pick up
      6. Pick up matching ammo from ground
      7. Seek ammo from memory        → schedule travel
      8. Seek heal items from memory  → proactive supply

    Steps 1-2 equip/pickup happen via the existing v1 helpers so that all
    existing memory / event contracts are preserved.
    """
    eq = agent.get("equipment", {})
    inv = agent.get("inventory", [])
    loc_id = agent.get("location_id", "")

    # 1–3: weapon
    if not eq.get("weapon"):
        evs = _bot_equip_from_inventory(agent_id, agent, WEAPON_ITEM_TYPES, "weapon", state, world_turn)
        if evs:
            return evs
        evs = _bot_pickup_item_from_ground(agent_id, agent, WEAPON_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        mem_loc = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            agent["current_goal"] = "get_weapon"
            _add_memory(
                agent, world_turn, state, "decision",
                "🔫 Ищу оружие по памяти",
                {"action_kind": "seek_item", "item_category": "weapon",
                 "destination": mem_loc},
                summary=f"Иду за оружием в {state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}",
            )
            _runtime_set_action_used(agent, True)
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)

    # 4–5: armor
    if not eq.get("armor"):
        evs = _bot_equip_from_inventory(agent_id, agent, ARMOR_ITEM_TYPES, "armor", state, world_turn)
        if evs:
            return evs
        evs = _bot_pickup_item_from_ground(agent_id, agent, ARMOR_ITEM_TYPES, state, world_turn)
        if evs:
            return evs

    # 6–7: ammo for equipped weapon
    weapon = eq.get("weapon")
    if weapon and isinstance(weapon, dict):
        weapon_type = weapon.get("type")
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        if required_ammo:
            has_ammo = any(i.get("type") == required_ammo for i in inv)
            if not has_ammo:
                evs = _bot_pickup_item_from_ground(
                    agent_id, agent, frozenset([required_ammo]), state, world_turn
                )
                if evs:
                    return evs
                mem_loc = _find_item_memory_location(agent, frozenset([required_ammo]), state)
                if mem_loc and mem_loc != loc_id:
                    agent["current_goal"] = "get_ammo"
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "🔫 Ищу патроны по памяти",
                        {"action_kind": "seek_item", "item_category": "ammo",
                         "ammo_type": required_ammo, "destination": mem_loc},
                        summary=f"Иду за патронами в {state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}",
                    )
                    _runtime_set_action_used(agent, True)
                    return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)

    # 8: proactive seek heal items from memory
    has_heal = any(i.get("type") in HEAL_ITEM_TYPES for i in inv)
    if not has_heal:
        mem_loc = _find_item_memory_location(agent, HEAL_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            _add_memory(
                agent, world_turn, state, "decision",
                "💊 Ищу медикаменты по памяти",
                {"action_kind": "seek_item", "item_category": "medical",
                 "destination": mem_loc},
                summary=f"Иду за медикаментами в {state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}",
            )
            _runtime_set_action_used(agent, True)
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)

    return None  # nothing to do — run the main pipeline


def _build_active_plan_summary(agent: Dict[str, Any]) -> dict[str, Any] | None:
    active_plan = get_active_plan(agent)
    if active_plan is not None:
        steps_count = max(1, len(active_plan.steps))
        remaining_steps = max(0, steps_count - active_plan.current_step_index)
        return {
            "scheduled_action_type": _active_plan_step_label(active_plan.current_step),
            "urgency": 0.45,
            "remaining_value": max(0.0, min(1.0, 0.95 - (active_plan.current_step_index / steps_count) * 0.5)),
            "risk": 0.2,
            "remaining_time": max(0.0, min(1.0, remaining_steps / steps_count)),
            "resource_cost": 0.0,
            "confidence": 0.8,
            "goal_alignment": 0.8,
        }

    sched = agent.get("scheduled_action")
    if not isinstance(sched, dict):
        return None

    turns_total = max(1, int(sched.get("turns_total") or 1))
    turns_remaining = max(0, int(sched.get("turns_remaining") or 0))
    progress = 1.0 - (turns_remaining / turns_total)

    return {
        "scheduled_action_type": sched.get("type"),
        "urgency": 0.45,
        "remaining_value": max(0.0, min(1.0, 0.9 - progress * 0.4)),
        "risk": 0.2,
        "remaining_time": max(0.0, min(1.0, turns_remaining / turns_total)),
        "resource_cost": 0.0,
        "confidence": 0.7,
        "goal_alignment": 0.75,
    }


def _is_wait_only_plan(plan: Any) -> bool:
    if not getattr(plan, "steps", None):
        return True
    first_step = plan.steps[0]
    return str(getattr(first_step, "kind", "")) == "wait"


def _objective_plan_is_meaningful(objective: Any, plan: Any) -> bool:
    if not _is_wait_only_plan(plan):
        return True
    if objective.key in _WAIT_ALLOWED_OBJECTIVES:
        return True
    if bool((objective.metadata or {}).get("allows_wait")):
        return True
    if objective.key in _NON_WAIT_ACTIONABLE_OBJECTIVES:
        return False
    return not bool((objective.metadata or {}).get("is_blocking"))


def _build_objective_memory_used_payload(
    *,
    selected_objective: Any | None,
    planner_memory_used: list[dict[str, Any]],
    belief: Any,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for mem in planner_memory_used:
        memory_id = str(mem.get("id") or "")
        if memory_id:
            by_id[memory_id] = dict(mem)

    for mem in belief.relevant_memories:
        kind = str(mem.get("kind") or "")
        if kind in {"semantic_v2_decision", "v2_decision"}:
            continue
        memory_id = str(mem.get("id") or "")
        if memory_id and memory_id not in by_id:
            by_id[memory_id] = {
                "id": memory_id,
                "kind": kind or "memory",
                "summary": mem.get("summary", ""),
                "confidence": round(float(mem.get("confidence", 0.0)), 3),
                "used_for": "general_context",
            }

    for known in list(getattr(belief, "known_items", ())) + list(getattr(belief, "known_traders", ())):
        memory_id = str(known.get("memory_id") or "")
        if memory_id and memory_id not in by_id:
            by_id[memory_id] = {
                "id": memory_id,
                "kind": str(known.get("kind") or "memory"),
                "summary": str(known.get("summary") or ""),
                "confidence": round(float(known.get("confidence", 0.0)), 3),
                "used_for": "general_context",
            }

    ordered: list[dict[str, Any]] = []
    if selected_objective is not None:
        selected_used_for = _OBJECTIVE_MEMORY_USED_FOR.get(selected_objective.key, "general_context")
        selected_refs = tuple(
            ref for ref in (selected_objective.source_refs or ()) if isinstance(ref, str) and ref.startswith("memory:")
        )
        for ref in selected_refs:
            memory_id = ref.split("memory:", 1)[1]
            payload = dict(by_id.get(memory_id) or {})
            if not payload:
                payload = {
                    "id": memory_id,
                    "kind": "memory",
                    "summary": "",
                    "confidence": 0.5,
                }
            payload["used_for"] = selected_used_for
            if not any(existing.get("id") == payload["id"] for existing in ordered):
                ordered.append(payload)

    for mem in planner_memory_used:
        if not any(existing.get("id") == mem.get("id") and existing.get("used_for") == mem.get("used_for") for existing in ordered):
            ordered.append(mem)

    for payload in by_id.values():
        if not any(existing.get("id") == payload.get("id") and existing.get("used_for") == payload.get("used_for") for existing in ordered):
            ordered.append(payload)

    return ordered[:5]


def _passive_location_knowledge_exchange(
    agent_id: str,
    agent: "Dict[str, Any]",
    state: "Dict[str, Any]",
    world_turn: int,
) -> int:
    """Passive bounded location knowledge exchange with one co-located stalker.

    Returns the count of location entries updated on this agent.
    O(K) where K <= MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION.
    """
    try:
        from app.games.zone_stalkers.knowledge.location_knowledge_exchange import (  # noqa: PLC0415
            build_location_knowledge_share_packets,
            receive_location_knowledge_packets,
        )
    except Exception:
        return 0

    loc_id = agent.get("location_id", "")
    agents = state.get("agents", {})
    if not loc_id or not agents:
        return 0

    for other_id, other in agents.items():
        if other_id == agent_id:
            continue
        if not other.get("is_alive", True):
            continue
        if other.get("location_id") != loc_id:
            continue
        if other.get("archetype") not in {"stalker_agent"}:
            continue
        try:
            packets = build_location_knowledge_share_packets(
                other,
                world_turn=world_turn,
                target_needs_shelter=True,
                target_needs_trader=True,
                target_needs_artifacts=True,
            )
            if packets:
                return receive_location_knowledge_packets(agent, packets, world_turn=world_turn)
        except Exception:
            pass
        break
    return 0


def _run_npc_brain_v3_decision_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Core NPC Brain v3 pipeline: Objective → adapter intent → plan → ActivePlan/runtime."""
    agent = _runtime_agent(agent_id, agent)
    from app.games.zone_stalkers.decision.context_builder import build_agent_context
    from app.games.zone_stalkers.decision.needs import evaluate_need_result
    from app.games.zone_stalkers.decision.intents import select_intent
    from app.games.zone_stalkers.decision.planner import build_plan
    from app.games.zone_stalkers.decision.executors import execute_plan_step
    from app.games.zone_stalkers.decision.models.objective import (
        Objective,
        ObjectiveDecision,
        ObjectiveGenerationContext,
        ObjectiveScore,
    )
    from app.games.zone_stalkers.decision.objectives.generator import generate_objectives
    from app.games.zone_stalkers.decision.objectives.selection import choose_objective
    from app.games.zone_stalkers.decision.objectives.intent_adapter import objective_to_intent
    from app.games.zone_stalkers.decision.target_beliefs import build_target_belief

    # ── Commitment logic: handle scheduled arrivals first ─────────────────
    arrival_evs = _bot_pickup_on_arrival(agent_id, agent, state, world_turn)
    if arrival_evs:
        return arrival_evs
    sell_evs = _bot_sell_on_arrival(agent_id, agent, state, world_turn)
    if sell_evs:
        return sell_evs

    # ── Passive location knowledge exchange with co-located agents ──────────
    # Bounded top-K: at most MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION per tick.
    _passive_location_exchange_result = _passive_location_knowledge_exchange(
        agent_id, agent, state, world_turn
    )
    # This is fire-and-forget: even if exchange updates knowledge, we continue
    # the normal decision pipeline (no early return).

    # ── Check and handle global goal completion ────────────────────────────
    if not agent.get("has_left_zone") and agent.get("is_alive", True) and not agent.get("global_goal_achieved"):
        _check_global_goal_completion(agent_id, agent, state, world_turn)

    # If goal is complete and agent is already at exit, leave immediately.
    # Otherwise route selection goes through objective pipeline (LEAVE_ZONE).
    if not agent.get("has_left_zone") and agent.get("is_alive", True) and agent.get("global_goal_achieved"):
        _ensure_exit_zone_mode(
            agent,
            reason="global_goal_completed",
            world_turn=world_turn,
        )
        loc = state.get("locations", {}).get(agent.get("location_id", ""), {})
        if loc.get("exit_zone"):
            return _execute_leave_zone(agent_id, agent, state, world_turn)

    # ── Pre-decision: phase-independent equipment maintenance ─────────────
    # Equip / pick-up / seek from memory happen before the needs pipeline so
    # that Phase-1 resource-gathering is not blocked by reload_or_rearm=1.0.
    exit_mode = agent.get("exit_zone_mode")
    exit_mode_active = isinstance(exit_mode, dict) and bool(exit_mode.get("active"))
    if not exit_mode_active and not bool(agent.get("global_goal_achieved")) and not bool(agent.get("debt_escape_pending")):
        _eq_evs = _pre_decision_equipment_maintenance(agent_id, agent, state, world_turn)
        if _eq_evs is not None:
            return _eq_evs

    # ── V2 pipeline ────────────────────────────────────────────────────────
    ctx = build_agent_context(agent_id, agent, state)

    # PR 3: build BeliefState adapter and compute memory-backed lookup hints.
    from app.games.zone_stalkers.decision.beliefs import (  # noqa: PLC0415
        build_belief_state,
        find_trader_memory_candidate_from_beliefs,
        find_food_memory_candidate_from_beliefs,
        find_water_memory_candidate_from_beliefs,
    )
    belief = build_belief_state(ctx, agent, world_turn)

    _memory_hints: dict[str, dict[str, Any]] = {}
    _trader_mem = find_trader_memory_candidate_from_beliefs(belief, agent, world_turn)
    if _trader_mem:
        _memory_hints["trader"] = _trader_mem
    _food_mem = find_food_memory_candidate_from_beliefs(belief, agent, world_turn)
    if _food_mem:
        _memory_hints["food"] = _food_mem
    _water_mem = find_water_memory_candidate_from_beliefs(belief, agent, world_turn)
    if _water_mem:
        _memory_hints["water"] = _water_mem
    agent["_belief_memory_hints"] = _memory_hints
    target_belief = build_target_belief(
        agent_id=agent_id,
        agent=agent,
        state=state,
        world_turn=world_turn,
        belief_state=belief,
    )

    need_result = evaluate_need_result(ctx, state)
    needs = need_result.scores

    objective_decision = None
    selected_objective = None
    selected_objective_score = None
    plan_unavailable_keys: set[str] = set()
    objective_pairs: list[tuple[Any, Any]] = []
    try:
        objective_ctx = ObjectiveGenerationContext(
            agent_id=agent_id,
            world_turn=world_turn,
            belief_state=belief,
            need_result=need_result,
            active_plan_summary=_build_active_plan_summary(agent),
            personality=agent,
            target_belief=target_belief,
            location_state=ctx.location_state,
        )
        objective_candidates = generate_objectives(objective_ctx)
        objective_decision = choose_objective(
            objective_candidates,
            personality=agent,
        )
        objective_pairs = [(objective_decision.selected, objective_decision.selected_score)] + list(objective_decision.alternatives)

        for candidate_objective, candidate_score in objective_pairs:
            candidate_intent = objective_to_intent(
                candidate_objective,
                candidate_score,
                world_turn=world_turn,
                source_goal=agent.get("global_goal"),
            )
            candidate_plan = build_plan(ctx, candidate_intent, state, world_turn, need_result=need_result)
            if _objective_plan_is_meaningful(candidate_objective, candidate_plan):
                selected_objective = candidate_objective
                selected_objective_score = candidate_score
                intent = candidate_intent
                plan = candidate_plan
                break
            plan_unavailable_keys.add(candidate_objective.key)

        if selected_objective is None:
            selected_objective = Objective(
                key="IDLE",
                source="fallback",
                urgency=0.1,
                expected_value=0.1,
                risk=0.0,
                time_cost=0.0,
                resource_cost=0.0,
                confidence=1.0,
                goal_alignment=0.1,
                memory_confidence=0.5,
                reasons=("Нет выполнимых целей",),
                source_refs=("fallback",),
                metadata={"is_blocking": False, "allows_wait": True},
            )
            selected_objective_score = ObjectiveScore(
                objective_key="IDLE",
                raw_score=0.0,
                final_score=0.0,
                factors=(),
                penalties=(),
                decision="selected",
            )
            intent = objective_to_intent(
                selected_objective,
                selected_objective_score,
                world_turn=world_turn,
                source_goal=agent.get("global_goal"),
            )
            plan = build_plan(ctx, intent, state, world_turn, need_result=need_result)
            objective_pairs = objective_pairs + [(selected_objective, selected_objective_score)]

        if plan_unavailable_keys:
            objective_pairs = [
                (
                    _obj,
                    ObjectiveScore(
                        objective_key=_score.objective_key,
                        raw_score=_score.raw_score,
                        final_score=_score.final_score,
                        factors=_score.factors,
                        penalties=_score.penalties,
                        decision=(
                            "selected"
                            if _obj.key == selected_objective.key
                            else "rejected"
                        ),
                    ),
                )
                for _obj, _score in objective_pairs
            ]
    except Exception:
        intent = select_intent(ctx, needs, world_turn, need_result=need_result)
        plan = build_plan(ctx, intent, state, world_turn, need_result=need_result)

    # Compute needs dict once (reused below)
    _needs_dict = asdict(needs)
    _selected_objective_key = (
        selected_objective.key
        if selected_objective is not None
        else (
            _fallback_objective_key_for_intent(
                intent,
                agent=agent,
                state=state,
                world_turn=world_turn,
            )
        )
    )
    _selected_objective_score = (
        round(float(selected_objective_score.final_score), 3)
        if selected_objective_score is not None
        else ((intent.metadata or {}).get("objective_score") if isinstance(intent.metadata, dict) else None)
    )

    _selected_objective_metadata = (
        selected_objective.metadata
        if selected_objective is not None and isinstance(selected_objective.metadata, dict)
        else {}
    )
    _selected_combat_ready = _selected_objective_metadata.get("combat_ready")
    _selected_not_attacking_reasons = list(_selected_objective_metadata.get("not_attacking_reasons") or [])
    _intent_metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
    _plan_step_0_payload = plan.steps[0].payload if plan.steps and isinstance(plan.steps[0].payload, dict) else {}
    _plan_fallback_active = bool(
        _intent_metadata.get("fallback_reason")
        or _plan_step_0_payload.get("fallback_reason")
    )
    _plan_fallback = {
        "active": _plan_fallback_active,
        "from_objective_key": _intent_metadata.get("fallback_from_objective_key") or _plan_step_0_payload.get("fallback_from_objective_key"),
        "from_intent": _intent_metadata.get("fallback_from_intent") or _plan_step_0_payload.get("fallback_from_intent"),
        "to_intent": _intent_metadata.get("fallback_to_intent") or _plan_step_0_payload.get("fallback_to_intent") or plan.intent_kind,
        "reason": _intent_metadata.get("fallback_reason") or _plan_step_0_payload.get("fallback_reason"),
        "blocked_category": _intent_metadata.get("blocked_resupply_category") or _plan_step_0_payload.get("blocked_resupply_category"),
        "agent_money": _intent_metadata.get("agent_money") or _plan_step_0_payload.get("agent_money"),
        "material_threshold": _intent_metadata.get("material_threshold") or _plan_step_0_payload.get("material_threshold"),
    } if _plan_fallback_active else None

    # Store context for observability / debug
    agent.pop("_v2_context", None)
    agent["brain_v3_context"] = {
        "need_scores": _needs_dict,
        "intent_kind": intent.kind,
        "intent_score": round(intent.score, 3),
        "intent_reason": intent.reason,
        "adapter_intent": {
            "kind": intent.kind,
            "score": round(intent.score, 3),
            "reason": intent.reason,
        },
        "plan_intent": plan.intent_kind,
        "plan_steps": len(plan.steps),
        "plan_confidence": round(plan.confidence, 3),
        "plan_step_0": plan.steps[0].kind if plan.steps else None,
        "plan_fallback": _plan_fallback,
        "objective_key": _selected_objective_key,
        "objective_score": _selected_objective_score,
        "objective_reason": "; ".join(selected_objective.reasons) if selected_objective and selected_objective.reasons else (intent.reason or None),
        "objective_switch_decision": objective_decision.switch_decision if objective_decision else None,
        "global_goal": agent.get("global_goal"),
        "support_objective_for": _selected_objective_metadata.get("support_objective_for"),
        "combat_ready": _selected_combat_ready,
        "not_attacking_reasons": _selected_not_attacking_reasons,
        "target_visible_now": _selected_objective_metadata.get("target_visible_now"),
        "target_co_located": _selected_objective_metadata.get("target_co_located"),
        "target_strength": _selected_objective_metadata.get("target_strength"),
        "hunter_preparation": (
            {
                "active": not bool(_selected_objective_metadata.get("equipment_advantaged", True)),
                **_selected_objective_metadata["equipment_advantage"],
            }
            if isinstance(_selected_objective_metadata.get("equipment_advantage"), dict)
            else None
        ),
        "hunt_target_belief": {
            "target_id": target_belief.target_id,
            "is_known": target_belief.is_known,
            "is_alive": target_belief.is_alive,
            "last_known_location_id": target_belief.last_known_location_id,
            "location_confidence": round(float(target_belief.location_confidence), 3),
            "best_location_id": target_belief.best_location_id,
            "best_location_confidence": round(float(target_belief.best_location_confidence), 3),
            "last_seen_turn": target_belief.last_seen_turn,
            "visible_now": target_belief.visible_now,
            "co_located": target_belief.co_located,
            "equipment_known": target_belief.equipment_known,
            "combat_strength": target_belief.combat_strength,
            "combat_strength_confidence": round(float(target_belief.combat_strength_confidence), 3),
            "possible_locations": [
                {
                    "location_id": hypothesis.location_id,
                    "probability": round(float(hypothesis.probability), 3),
                    "confidence": round(float(hypothesis.confidence), 3),
                    "freshness": round(float(hypothesis.freshness), 3),
                    "reason": hypothesis.reason,
                    "source_refs": list(hypothesis.source_refs),
                }
                for hypothesis in target_belief.possible_locations
            ],
            "likely_routes": [
                {
                    "from_location_id": route.from_location_id,
                    "to_location_id": route.to_location_id,
                    "confidence": round(float(route.confidence), 3),
                    "freshness": round(float(route.freshness), 3),
                    "reason": route.reason,
                    "source_refs": list(route.source_refs),
                }
                for route in target_belief.likely_routes
            ],
            "exhausted_locations": list(target_belief.exhausted_locations),
            "lead_count": int(target_belief.lead_count),
            "route_hints": list(target_belief.route_hints),
            "source_refs": list(target_belief.source_refs),
        } if target_belief.target_id else None,
    }

    # Update current_goal from objective first, with intent fallback.
    if _selected_objective_key:
        agent["current_goal"] = _OBJECTIVE_TO_GOAL.get(
            str(_selected_objective_key),
            _INTENT_TO_GOAL.get(intent.kind, agent.get("current_goal", "idle")),
        )
    else:
        agent["current_goal"] = _INTENT_TO_GOAL.get(intent.kind, agent.get("current_goal", "idle"))

    # Write a decision memory entry when the intent kind changes.
    # We skip writing when intent is the same as the last decision entry to avoid
    # flooding the log with identical entries every tick.
    # Only look at entries that are themselves decision records; other
    # decision entries (e.g. wait_in_shelter, seek_item, …) do not carry an
    # objective/intent fields and would cause the dedup guard to fail every tick.
    _prev_decision_key = next(
        (
            _v3_details(m).get("objective_key")
            or _v3_details(m).get("adapter_intent_kind")
            or _v3_details(m).get("intent_kind")
            for m in _v3_records_desc(agent)
            if _v3_memory_type(m) == "decision"
            and _v3_action_kind(m) in {"v2_decision", "objective_decision"}
        ),
        None,
    )
    _current_decision_key = str(_selected_objective_key or intent.kind)
    if _prev_decision_key != _current_decision_key:
        _top_needs = sorted(
            ((k, v) for k, v in _needs_dict.items() if v > 0.05),
            key=lambda x: x[1],
            reverse=True,
        )[:3]
        _needs_str = (
            ", ".join(f"{k}: {round(v * 100)}%" for k, v in _top_needs)
            or "нет"
        )
        _step0_kind = plan.steps[0].kind if plan.steps else "wait"
        _objective_label = str(_selected_objective_key or "UNKNOWN")
        _objective_reason = "; ".join(selected_objective.reasons) if selected_objective and selected_objective.reasons else (intent.reason or "")
        _add_memory(
            agent, world_turn, state, "decision",
            f"🧠 Цель {_objective_label}",
            {
                "action_kind": "objective_decision",
                "objective_key": _objective_label,
                "objective_score": _selected_objective_score if _selected_objective_score is not None else round(intent.score, 3),
                "objective_source": selected_objective.source if selected_objective is not None else "legacy_intent",
                "objective_reason": _objective_reason,
                "adapter_intent_kind": intent.kind,
                "adapter_intent_score": round(intent.score, 3),
                "global_goal": agent.get("global_goal"),
                "support_objective_for": _selected_objective_metadata.get("support_objective_for"),
                "target_visible_now": _selected_objective_metadata.get("target_visible_now"),
                "target_co_located": _selected_objective_metadata.get("target_co_located"),
                "combat_ready": _selected_combat_ready,
                "not_attacking_reasons": _selected_not_attacking_reasons,
                # Legacy compatibility fields.
                "intent_kind": intent.kind,
                "intent_score": round(intent.score, 3),
                "plan_step": _step0_kind,
                "plan_steps_count": len(plan.steps),
            },
            summary=(
                f"Выбрана цель «{_objective_label}» ({round(float(_selected_objective_score or intent.score) * 100)}%)."
                + (f" {_objective_reason}." if _objective_reason else "")
                + f" Адаптер intent: {intent.kind}."
                + (
                    f" Не атакую: {', '.join(_selected_not_attacking_reasons)}."
                    if _selected_not_attacking_reasons
                    else ""
                )
                + f" Топ потребности: {_needs_str}"
            ),
        )

    # PR 3: collect concrete memory-backed lookup usages from planner first.
    _planner_memory_used = list(agent.get("_memory_used_decision", []))
    _memory_used_payload: list[dict] = _build_objective_memory_used_payload(
        selected_objective=selected_objective,
        planner_memory_used=_planner_memory_used,
        belief=belief,
    )

    _selected_objective_trace = None
    _objective_scores_trace = None
    _alternatives_trace = None
    if selected_objective is not None:
        _selected_objective_trace = {
            "key": selected_objective.key,
            "score": round(float(selected_objective_score.final_score), 3) if selected_objective_score else 0.0,
            "source": selected_objective.source,
            "reason": "; ".join(selected_objective.reasons) if selected_objective.reasons else "",
        }
        _objective_scores_trace = []
        for _obj, _score in objective_pairs[:5]:
            _reason = "; ".join(_obj.reasons) if _obj.reasons else ""
            if _obj.key in plan_unavailable_keys and _obj.key != selected_objective.key:
                _reason = f"{_reason}; plan_unavailable" if _reason else "plan_unavailable"
            _objective_scores_trace.append({
                "key": _obj.key,
                "score": round(float(_score.final_score), 3),
                "decision": _score.decision or ("selected" if _obj.key == selected_objective.key else "rejected"),
                "reason": _reason,
            })
        _alternatives_trace = [
            {
                "key": _obj.key,
                "score": round(float(_score.final_score), 3),
                "decision": _score.decision or "rejected",
                "reason": (
                    f"{'; '.join(_obj.reasons)}; plan_unavailable"
                    if _obj.reasons and _obj.key in plan_unavailable_keys
                    else ("plan_unavailable" if _obj.key in plan_unavailable_keys else ("; ".join(_obj.reasons) if _obj.reasons else ""))
                ),
            }
            for _obj, _score in [pair for pair in objective_pairs if pair[0].key != selected_objective.key][:5]
        ]

    runtime_active_plan = None
    if is_v3_monitored_bot(agent):
        plan_decision = objective_decision
        if plan_decision is None and selected_objective is not None and selected_objective_score is not None:
            plan_decision = ObjectiveDecision(
                selected=selected_objective,
                selected_score=selected_objective_score,
                alternatives=(),
            )
        if plan_decision is not None:
            composed_plan = plan
            if selected_objective is not None:
                composed_steps = compose_active_plan_steps(
                    objective_key=selected_objective.key,
                    base_plan=plan,
                    agent=agent,
                    state=state,
                    world_turn=world_turn,
                )
                composed_plan = Plan(
                    intent_kind=plan.intent_kind,
                    steps=composed_steps,
                    current_step_index=0,
                    interruptible=plan.interruptible,
                    confidence=plan.confidence,
                    created_turn=plan.created_turn,
                    expires_turn=plan.expires_turn,
                )
            runtime_active_plan = create_active_plan(
                objective_decision=plan_decision,
                world_turn=world_turn,
                plan=composed_plan,
            )
            save_active_plan(agent, runtime_active_plan)
            agent["action_queue"] = []

    write_npc_brain_v3_decision_trace(
        agent,
        world_turn=world_turn,
        intent_kind=intent.kind,
        intent_score=float(intent.score),
        reason=intent.reason,
        state=state,
        need_result=need_result,
        memory_used=_memory_used_payload if _memory_used_payload else None,
        active_objective=_selected_objective_trace,
        objective_scores=_objective_scores_trace,
        alternatives=_alternatives_trace,
        active_plan_runtime=_active_plan_trace_payload(runtime_active_plan) if runtime_active_plan is not None else None,
    )

    if runtime_active_plan is not None:
        _write_active_plan_trace_event(
            agent,
            world_turn=world_turn,
            state=state,
            event="active_plan_created",
            active_plan=runtime_active_plan,
            summary=(
                f"ActivePlan {runtime_active_plan.objective_key}: создан "
                f"с {len(runtime_active_plan.steps)} шаг(ами)."
            ),
        )
        _write_active_plan_memory_event(
            agent,
            world_turn=world_turn,
            state=state,
            action_kind="active_plan_created",
            active_plan=runtime_active_plan,
            add_memory=_add_memory,
        )
        result = _start_or_continue_active_plan_step(
            agent_id,
            agent,
            runtime_active_plan,
            state,
            world_turn,
            add_memory=_add_memory,
        )
    else:
        result = execute_plan_step(ctx, plan, state, world_turn)
    # Transient PR3 hint/debug fields should not leak between ticks.
    agent.pop("_belief_memory_hints", None)
    agent.pop("_memory_used_decision", None)
    return result


def _kill_stalker_has_personal_evidence(agent: Dict[str, Any], target_id: str) -> bool:
    """True when agent memory contains personal kill evidence for target_id."""
    for rec in _v3_records_desc(agent):
        details = _v3_details(rec)
        evidence_target = str(details.get("target_id") or details.get("target") or "")
        if evidence_target != str(target_id):
            continue
        if _v3_action_kind(rec) in {"target_death_confirmed", "hunt_target_killed"}:
            return True
    return False


def _kill_stalker_has_direct_confirmation(agent: Dict[str, Any], target_id: str) -> bool:
    """True when agent directly observed target death confirmation."""
    for rec in _v3_records_desc(agent):
        if _v3_action_kind(rec) != "target_death_confirmed":
            continue
        details = _v3_details(rec)
        if str(details.get("target_id") or "") != str(target_id):
            continue
        if bool(details.get("directly_observed")) is True:
            return True
    return False


def _recent_combat_with_target(
    agent_id: str,
    agent: Dict[str, Any],
    target_id: str,
    state: Dict[str, Any],
    world_turn: int,
    *,
    max_turn_delta: int = 1_000_000,
) -> bool:
    """True when agent recently engaged target in combat according to memory or state."""
    target_id_s = str(target_id)
    for rec in _v3_records_desc(agent):
        rec_turn = _v3_turn(rec)
        if world_turn - rec_turn > max_turn_delta:
            break
        action_kind = _v3_action_kind(rec)
        details = _v3_details(rec)
        rec_target = str(details.get("target_id") or details.get("target") or "")
        if action_kind in {"combat_initiated", "combat_shoot", "hunt_target_killed", "target_death_confirmed"} and rec_target == target_id_s:
            return True

    for combat in (state.get("combat_interactions") or {}).values():
        if not isinstance(combat, dict):
            continue
        participants = combat.get("participants")
        if not isinstance(participants, dict):
            continue
        if agent_id not in participants or target_id_s not in participants:
            continue
        ended_turn = combat.get("ended_turn")
        started_turn = combat.get("started_turn")
        if isinstance(ended_turn, (int, float)) and world_turn - int(ended_turn) <= max_turn_delta:
            return True
        if isinstance(started_turn, (int, float)) and world_turn - int(started_turn) <= max_turn_delta:
            return True
        if not combat.get("ended"):
            return True
    return False



def _ensure_exit_zone_mode(
    agent: Dict[str, Any],
    *,
    reason: str,
    world_turn: int,
) -> Dict[str, Any]:
    exit_mode = agent.get("exit_zone_mode")
    if isinstance(exit_mode, dict):
        if not bool(exit_mode.get("active")):
            exit_mode["active"] = True
            if "started_turn" not in exit_mode:
                exit_mode["started_turn"] = int(world_turn)
        exit_mode["reason"] = str(reason)
        return exit_mode
    exit_mode = {
        "active": True,
        "started_turn": int(world_turn),
        "reason": str(reason),
    }
    agent["exit_zone_mode"] = exit_mode
    return exit_mode


def _mark_kill_stalker_goal_achieved(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    target_id: str,
    *,
    confirmation_source: str,
) -> None:
    """Idempotently mark kill_stalker goal achieved and cleanup stale hunt runtime."""
    if bool(agent.get("global_goal_achieved")):
        return

    target_agent = state.get("agents", {}).get(target_id, {})
    target_name = target_agent.get("name", target_id) if isinstance(target_agent, dict) else target_id

    agent["global_goal_achieved"] = True
    _ensure_exit_zone_mode(
        agent,
        reason="global_goal_completed",
        world_turn=world_turn,
    )
    goal_summary = f"Я выполнил задание — устранил «{target_name}». Пора покидать Зону!"
    goal_common = {
        "goal": "kill_stalker",
        "global_goal": "kill_stalker",
        "target_id": target_id,
        "confirmation_source": confirmation_source,
    }
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        f"⚔️ Цель достигнута: «{target_name}» устранён!",
        {
            "action_kind": "goal_achieved",
            **goal_common,
            "legacy_action_kind": "global_goal_completed",
        },
        summary=goal_summary,
    )
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        f"⚔️ Цель достигнута: «{target_name}» устранён!",
        {
            "action_kind": "global_goal_completed",
            **goal_common,
        },
        summary=goal_summary,
    )

    active_plan = get_active_plan(agent)
    if active_plan is not None and str(getattr(active_plan, "objective_key", "")) != "LEAVE_ZONE":
        active_plan.abort("goal_achieved", world_turn)
        save_active_plan(agent, active_plan)
        clear_active_plan(agent)
        sched = agent.get("scheduled_action")
        if isinstance(sched, dict) and sched.get("active_plan_id") == active_plan.id:
            agent["scheduled_action"] = None
            agent["action_queue"] = []

    invalidate_brain(
        agent,
        _cow_runtime(),
        reason="global_goal_achieved",
        priority="urgent",
        world_turn=world_turn,
    )


def _check_global_goal_completion(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> None:
    """Detect and record global goal achievement. Sets agent['global_goal_achieved']=True."""
    from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES as _SECRET_TYPES
    global_goal = agent.get("global_goal")
    if global_goal == "get_rich":
        from app.games.zone_stalkers.decision.needs import _agent_liquid_wealth  # noqa: PLC0415

        wealth = _agent_liquid_wealth(agent)
        target = agent.get("wealth_goal_target", GET_RICH_COMPLETION_MIN)
        if wealth >= target:
            agent["global_goal_achieved"] = True
            _ensure_exit_zone_mode(
                agent,
                reason="global_goal_completed",
                world_turn=world_turn,
            )
            _add_memory(
                agent, world_turn, state, "observation",
                "💰 Цель достигнута: разбогател!",
                {
                    "action_kind": "global_goal_completed",
                    "global_goal": "get_rich",
                    "wealth_goal_target": target,
                    "liquid_wealth": wealth,
                },
                summary=(
                    f"Я достиг своей цели «get_rich»: ликвидное богатство {wealth} ≥ {target}. "
                    "Пора покидать Зону."
                ),
            )
    elif global_goal == "unravel_zone_mystery":
        has_doc = any(
            i.get("type") in _SECRET_TYPES
            for i in agent.get("inventory", [])
        )
        if has_doc:
            agent["global_goal_achieved"] = True
            _ensure_exit_zone_mode(
                agent,
                reason="global_goal_completed",
                world_turn=world_turn,
            )
            _add_memory(
                agent, world_turn, state, "observation",
                "🔍 Цель достигнута: тайна раскрыта!",
                {"action_kind": "goal_achieved", "goal": "unravel_zone_mystery"},
                summary="Я нашёл секретный документ и раскрыл тайну Зоны. Пора покидать!",
            )
    elif global_goal == "kill_stalker":
        target_id = str(agent.get("kill_target_id") or "")
        if target_id:
            target_agent = state.get("agents", {}).get(target_id)
            target_dead_in_state = (
                isinstance(target_agent, dict)
                and not bool(target_agent.get("is_alive", True))
            )
            if not target_dead_in_state:
                return

            direct_confirmation_exists = _kill_stalker_has_direct_confirmation(agent, target_id)
            personal_kill_exists = _kill_stalker_has_personal_evidence(agent, target_id)
            recently_engaged_this_target = _recent_combat_with_target(
                agent_id,
                agent,
                target_id,
                state,
                world_turn,
            )
            if not (direct_confirmation_exists or personal_kill_exists or recently_engaged_this_target):
                return

            if direct_confirmation_exists:
                source = "direct_confirmation"
            elif personal_kill_exists:
                source = "personal_kill_evidence"
            else:
                source = "state_confirmed_after_recent_combat"

            has_target_death_confirmation = any(
                _v3_action_kind(rec) == "target_death_confirmed"
                and str(_v3_details(rec).get("target_id") or "") == target_id
                for rec in _v3_records_desc(agent)
            )
            if not has_target_death_confirmation:
                target_name = target_agent.get("name", target_id) if isinstance(target_agent, dict) else target_id
                _add_memory(
                    agent,
                    world_turn,
                    state,
                    "observation",
                    f"✅ Подтверждена ликвидация цели «{target_name}»",
                    {
                        "action_kind": "target_death_confirmed",
                        "target_id": target_id,
                        "target_name": target_name,
                        "confirmation_source": source,
                        "directly_observed": False,
                        "corpse_location_id": str((target_agent or {}).get("location_id") or ""),
                        "location_id": str(agent.get("location_id") or ""),
                    },
                    summary=f"Смерть цели «{target_name}» подтверждена по состоянию мира и боевым данным.",
                )

            _mark_kill_stalker_goal_achieved(
                agent_id,
                agent,
                state,
                world_turn,
                target_id,
                confirmation_source=source,
            )



def _bot_route_to_exit(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Route agent toward the nearest exit_zone location (by travel time)."""
    locations = state.get("locations", {})
    loc_id = agent.get("location_id", "")
    _map_revision = int(state.get("map_revision", 0))
    _best_exit: Optional[str] = None
    try:
        from app.games.zone_stalkers.pathfinding_cache import get_cached as _cache_get
        _cached_exit = _cache_get(
            map_revision=_map_revision,
            from_loc_id=loc_id,
            query_kind="nearest_exit_location",
        )
        if _cached_exit:
            _best_exit = str(_cached_exit)
    except Exception:
        _best_exit = None

    if _best_exit is None:
        # Dijkstra to nearest exit_zone
        _heap: List = [(0, 0, loc_id)]
        _dist: Dict[str, int] = {}
        _best_time: Optional[int] = None
        while _heap:
            _cur_min, _cur_hops, _cur_id = heapq.heappop(_heap)
            if _cur_id in _dist:
                continue
            _dist[_cur_id] = _cur_min
            if _best_time is not None and _cur_min > _best_time:
                break
            if _cur_id != loc_id and locations.get(_cur_id, {}).get("exit_zone"):
                if _best_time is None or _cur_min < _best_time:
                    _best_time = _cur_min
                    _best_exit = _cur_id
                continue  # don't expand from exit
            for _conn in locations.get(_cur_id, {}).get("connections", []):
                if _conn.get("closed"):
                    continue
                _nxt = _conn["to"]
                if _nxt in _dist:
                    continue
                _edge_min = _conn.get("travel_time", 12) * MINUTES_PER_TURN
                _nxt_min = _cur_min + _edge_min
                if _best_time is None or _nxt_min <= _best_time:
                    heapq.heappush(_heap, (_nxt_min, _cur_hops + 1, _nxt))
        try:
            from app.games.zone_stalkers.pathfinding_cache import set_cached as _cache_set
            _cache_set(
                map_revision=_map_revision,
                from_loc_id=loc_id,
                query_kind="nearest_exit_location",
                value=_best_exit or "",
            )
        except Exception:
            pass

    if _best_exit is None:
        # No exit found — log once
        _last_kind = next(
            (_v3_action_kind(m) for m in _v3_records_desc(agent)
             if _v3_memory_type(m) == "decision"),
            None,
        )
        if _last_kind != "no_exit_found":
            _add_memory(
                agent, world_turn, state, "decision",
                "🚪 Нет выхода из Зоны",
                {"action_kind": "no_exit_found"},
                summary="Я хочу покинуть Зону, но не могу найти выход",
            )
        return []

    # Write "heading to exit" decision once
    _last_kind = next(
        (_v3_action_kind(m) for m in _v3_records_desc(agent)
         if _v3_memory_type(m) == "decision"),
        None,
    )
    if _last_kind not in ("heading_to_exit", "travel_arrived"):
        _exit_name = locations.get(_best_exit, {}).get("name", _best_exit)
        _add_memory(
            agent, world_turn, state, "decision",
            "🚪 Иду к выходу из Зоны",
            {"action_kind": "heading_to_exit",
             "exit_id": _best_exit, "exit_name": _exit_name},
            summary=f"Я решил покинуть Зону через «{_exit_name}», потому что выполнил свою цель",
        )
    return _bot_schedule_travel(agent_id, agent, _best_exit, state, world_turn)


def _execute_leave_zone(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Agent has reached an exit_zone location after achieving their goal. Mark as left."""
    agent = _runtime_agent(agent_id, agent)
    loc_id = agent.get("location_id", "")
    locations = state.get("locations", {})
    loc = locations.get(loc_id, {})
    exit_name = loc.get("name", loc_id)
    agent["has_left_zone"] = True
    exit_mode = agent.get("exit_zone_mode")
    if isinstance(exit_mode, dict):
        exit_mode["active"] = False
        exit_mode["completed_turn"] = world_turn
        exit_mode.setdefault("reason", "left_zone")
    else:
        agent["exit_zone_mode"] = {
            "active": False,
            "started_turn": world_turn,
            "completed_turn": world_turn,
            "reason": "left_zone",
        }
    try:
        from app.games.zone_stalkers.economy.debts import (  # noqa: PLC0415
            DEBT_STATUS_DEBTOR_LEFT_ZONE,
            DEBT_STATUS_ESCAPED,
            freeze_debtor_accounts,
        )
        debt_escape_leave = bool(agent.get("debt_escape_pending")) or bool(agent.get("_debt_escape_triggered"))
        freeze_status = DEBT_STATUS_ESCAPED if debt_escape_leave else DEBT_STATUS_DEBTOR_LEFT_ZONE
        freeze_reason = "debt_escape" if debt_escape_leave else "left_zone"
        if debt_escape_leave:
            agent["debt_escape_pending"] = False
            agent["escaped_due_to_debt"] = True
            agent["debt_escape_completed"] = True
        _frozen_events = freeze_debtor_accounts(
            state=state,
            debtor_id=agent_id,
            world_turn=world_turn,
            status=freeze_status,
            reason=freeze_reason,
        )
    except Exception:
        _frozen_events = []
    active_plan = get_active_plan(agent)
    if active_plan is not None:
        active_plan.abort("left_zone", world_turn)
        save_active_plan(agent, active_plan)
        _write_active_plan_trace_event(
            agent,
            world_turn=world_turn,
            state=state,
            event="active_plan_completed",
            active_plan=active_plan,
            reason="left_zone",
            summary=f"ActivePlan {active_plan.objective_key}: завершён при выходе из Зоны.",
        )
        _write_active_plan_memory_event(
            agent,
            world_turn=world_turn,
            state=state,
            action_kind="active_plan_completed",
            active_plan=active_plan,
            add_memory=_add_memory,
            reason="left_zone",
        )
        clear_active_plan(agent)
    agent["scheduled_action"] = None
    agent["action_queue"] = []
    # Remove agent from the exit location's agent list
    loc_agents = _runtime_mutable_location_agents(state, loc_id)
    if agent_id in loc_agents:
        loc_agents.remove(agent_id)
    _add_memory(
        agent, world_turn, state, "observation",
        "🚪 Покинул Зону",
        {"action_kind": "left_zone", "exit_location": loc_id, "exit_name": exit_name},
        summary=f"Я покинул Зону через «{exit_name}»",
    )
    return [
        *list(_frozen_events),
        {"event_type": "agent_left_zone", "payload": {"agent_id": agent_id, "exit_location": loc_id}},
    ]

# ─── Backwards-compatibility aliases (legacy → NPC Brain v3 migration) ───────
#
# The v1 cascade functions (_run_bot_action, _run_bot_action_inner,
# _bot_pursue_goal, _describe_bot_decision_tree) have been removed and
# replaced by the v2 decision pipeline. These shims preserve the
# public names so that existing tests and external code continue to work.

def _run_bot_action(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Backwards-compat alias → ``_run_npc_brain_v3_decision``."""
    return _run_npc_brain_v3_decision(agent_id, agent, state, world_turn)


def _run_bot_decision_v2(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper retained for older tests/callers."""
    return _run_npc_brain_v3_decision(agent_id, agent, state, world_turn)


def _run_bot_decision_v2_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper retained for older tests/callers."""
    return _run_npc_brain_v3_decision_inner(agent_id, agent, state, world_turn)


def _run_bot_action_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Backwards-compat: delegates to _bot_pursue_goal for goal-specific v1 behavior.

    This function is called by tests that expect v1-style memory entries
    (seek_item, wander, wait_at_trader, etc.).  Using _bot_pursue_goal
    rather than _run_npc_brain_v3_decision_inner ensures those memory entries
    are written correctly.
    """
    import random as _random
    global_goal = agent.get("global_goal", "get_rich")
    loc_id = agent.get("location_id", "")
    loc = state.get("locations", {}).get(loc_id, {})
    rng = _random.Random(agent_id + str(world_turn))
    return _bot_pursue_goal(agent_id, agent, global_goal, loc_id, loc, state, world_turn, rng)


def _describe_bot_decision_tree(
    agent: Dict[str, Any],
    events: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Backwards-compat stub returning a minimal decision tree structure."""
    from app.games.zone_stalkers.decision.context_builder import build_agent_context
    from app.games.zone_stalkers.decision.needs import evaluate_need_result
    from app.games.zone_stalkers.decision.intents import select_intent
    world_turn = state.get("world_turn", 0)
    agent_id = agent.get("id") or next(
        (aid for aid, a in state.get("agents", {}).items() if a is agent), "unknown"
    )
    try:
        ctx = build_agent_context(agent_id, agent, state)
        need_result = evaluate_need_result(ctx, state)
        needs = need_result.scores
        intent = select_intent(ctx, needs, world_turn, need_result=need_result)
        goal = intent.source_goal or intent.kind
        action = intent.kind
        reason = intent.reason or ""
    except Exception:
        goal = agent.get("global_goal", "unknown")
        action = "unknown"
        reason = "explain failed"
    return {
        "goal": goal,
        "chosen": {"action": action, "reason": reason},
        "layers": [
            {"name": "СНАРЯЖЕНИЕ", "skipped": bool(agent.get("equipment", {}).get("weapon"))},
            {"name": "ЦЕЛЬ", "skipped": False},
        ],
    }


def _describe_bot_decision(
    agent: Dict[str, Any],
    events: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Backwards-compat stub → delegates to _describe_bot_decision_tree."""
    tree = _describe_bot_decision_tree(agent, events, state)
    return {"goal": tree["goal"], "action": tree["chosen"]["action"],
            "reason": tree["chosen"]["reason"]}


def _bot_pursue_goal(
    agent_id: str,
    agent: Dict[str, Any],
    global_goal: str,
    loc_id: str,
    loc: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    rng: Any,
) -> List[Dict[str, Any]]:
    """Backwards-compat implementation of the v1 _bot_pursue_goal.

    Handles goal-specific behaviors with the exact memory entries that
    existing tests expect:
    - kill_stalker: combat initiation, intel gathering, trader travel
    - unravel_zone_mystery: doc seeking, stalker asking, trader waiting
    - get_rich / others: delegate to v2
    """
    if global_goal == "kill_stalker":
        return _compat_pursue_kill_stalker(agent_id, agent, loc_id, state, world_turn)
    if global_goal == "unravel_zone_mystery":
        return _compat_pursue_unravel(agent_id, agent, loc_id, state, world_turn)
    if global_goal == "get_rich":
        return _compat_pursue_get_rich(agent_id, agent, loc_id, loc, state, world_turn, rng)
    # Default: delegate to NPC Brain v3
    return _run_npc_brain_v3_decision_inner(agent_id, agent, state, world_turn)


def _compat_pursue_kill_stalker(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """v1-compat kill_stalker logic: combat initiation, intel, trader travel."""
    target_id: Optional[str] = agent.get("kill_target_id")
    if not target_id:
        _runtime_set_action_used(agent, True)
        return []

    agents = state.get("agents", {})
    target = agents.get(target_id, {})
    target_loc = target.get("location_id") if target else None
    target_name = target.get("name", target_id) if target else target_id

    # 1. Target at same location → initiate combat
    if target_loc == loc_id and target.get("is_alive", True):
        return _compat_initiate_combat(agent_id, agent, target_id, target, loc_id, state, world_turn)

    # 2. Ask co-located stalkers about target
    new_intel = _bot_ask_colocated_stalkers_about_agent(
        agent_id, agent, target_id, target_name, state, world_turn
    )
    if new_intel:
        _runtime_set_action_used(agent, True)
        # If we learned the location, schedule travel there
        intel_loc = _find_hunt_intel_location(agent, target_id, state)
        if intel_loc and intel_loc != loc_id:
            return _bot_schedule_travel(agent_id, agent, intel_loc, state, world_turn)
        return []

    # 3. Already have hunt intel → travel to known location
    hunt_loc = _find_hunt_intel_location(agent, target_id, state)
    if hunt_loc and hunt_loc != loc_id:
        _add_memory(
            agent, world_turn, state, "decision",
            f"🎯 Еду к цели в {hunt_loc}",
            {"action_kind": "hunt_travel", "destination": hunt_loc, "target_id": target_id},
            summary=f"Отправляюсь в {hunt_loc} за целью",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, hunt_loc, state, world_turn)

    # 4. At trader location → try to buy intel; if broke → wait
    trader = _find_trader_at_location(loc_id, state) or next(
        (t for t in state.get('traders', {}).values() if t.get('location_id') == loc_id and t.get('is_alive', True)),
        None)
    if trader:
        # Anti-spam: if last decision was hunt_wait_at_trader, don't repeat
        _hunt_last_dec = next(
            (_v3_action_kind(m) for m in _v3_records_desc(agent)
             if _v3_memory_type(m) == "decision"),
            None,
        )
        if _hunt_last_dec == "hunt_wait_at_trader":
            _runtime_set_action_used(agent, True)
            return []
        bought = _bot_buy_hunt_intel_from_trader(
            agent_id, agent, target_id, target_name, state, world_turn
        )
        _runtime_set_action_used(agent, True)
        if bought:
            return []
        # Broke or intel already bought → wait
        _add_memory(
            agent, world_turn, state, "decision",
            "⏳ Жду у торговца (охота за целью)",
            {"action_kind": "hunt_wait_at_trader", "location_id": loc_id},
            summary="Жду у торговца, чтобы получить информацию о цели",
        )
        return []

    # 5. No intel, no trader at current location → travel to nearest trader
    nearest_trader_loc = _find_nearest_trader_location(loc_id, state)
    nearest_trader_id = None
    if nearest_trader_loc and nearest_trader_loc != loc_id:
        _add_memory(
            agent, world_turn, state, "decision",
            "🏪 Еду к торговцу за информацией о цели",
            {"action_kind": "hunt_wait_at_trader", "destination": nearest_trader_loc},
            summary="Еду к торговцу за информацией о цели",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, nearest_trader_loc, state, world_turn)

    # 6. No intel, no trader anywhere → wait
    _runtime_set_action_used(agent, True)
    return []


def _compat_initiate_combat(
    agent_id: str,
    agent: Dict[str, Any],
    target_id: str,
    target: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Create a combat interaction and write combat_initiated memory/event."""
    import uuid
    cid = f"combat_{loc_id}_{world_turn}_{uuid.uuid4().hex[:6]}"
    state.setdefault("combat_interactions", {})[cid] = {
        "id": cid,
        "location_id": loc_id,
        "started_turn": world_turn,
        "ended": False,
        "ended_turn": None,
        "participants": {
            agent_id: {
                "motive": "победить",
                "enemies": [target_id],
                "friends": [],
                "fled": False,
                "fled_to": None,
            },
            target_id: {
                "motive": "выжить",
                "enemies": [agent_id],
                "friends": [],
                "fled": False,
                "fled_to": None,
            },
        },
    }
    target_name = target.get("name", target_id)
    _add_memory(
        agent, world_turn, state, "decision",
        f"⚔️ Атакую цель «{target_name}»",
        {"action_kind": "combat_initiated", "target_id": target_id, "combat_id": cid},
        summary=f"Начинаю боевое взаимодействие с «{target_name}»",
    )
    invalidate_brain(
        agent,
        _cow_runtime(),
        reason="combat_started",
        priority="urgent",
        world_turn=world_turn,
    )
    invalidate_brain(
        target,
        _cow_runtime(),
        reason="combat_started",
        priority="urgent",
        world_turn=world_turn,
    )
    _runtime_set_action_used(agent, True)
    return [{"event_type": "combat_initiated",
             "payload": {"agent_id": agent_id, "target_id": target_id,
                         "combat_id": cid, "location_id": loc_id}}]


def _compat_pursue_unravel(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """v1-compat unravel_zone_mystery: seek documents, ask stalkers, wait at trader."""
    from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
    doc_types = frozenset(SECRET_DOCUMENT_ITEM_TYPES)

    # 1. Pick up doc from ground if present
    pickup_evs = _bot_pickup_item_from_ground(agent_id, agent, doc_types, state, world_turn)
    if pickup_evs:
        return pickup_evs

    # 2. Check memory for known doc location
    known_loc = _find_item_memory_location(agent, doc_types, state)
    if known_loc and known_loc != loc_id:
        _add_memory(
            agent, world_turn, state, "decision",
            f"🔍 Еду за документом в {known_loc}",
            {"action_kind": "seek_item", "item_category": "secret_document",
             "destination": known_loc},
            summary=f"Еду в {known_loc} за секретным документом",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, known_loc, state, world_turn)

    # 3. Ask co-located stalkers about docs
    new_intel_loc = _bot_ask_colocated_stalkers_about_item(
        agent_id, agent, doc_types, "secret_document", state, world_turn
    )
    if new_intel_loc and new_intel_loc != loc_id:
        _add_memory(
            agent, world_turn, state, "decision",
            f"🔍 Еду за документом в {new_intel_loc} (по информации сталкеров)",
            {"action_kind": "seek_item", "item_category": "secret_document",
             "destination": new_intel_loc},
            summary=f"Еду в {new_intel_loc} за секретным документом",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, new_intel_loc, state, world_turn)

    # 4. If at trader → wait (anti-spam)
    trader = _find_trader_at_location(loc_id, state) or next(
        (t for t in state.get('traders', {}).values() if t.get('location_id') == loc_id and t.get('is_alive', True)),
        None)
    if trader:
        last_wait = next(
            (_v3_action_kind(m) for m in _v3_records_desc(agent)
             if _v3_memory_type(m) == "decision"),
            None,
        )
        if last_wait != "wait_at_trader":
            _add_memory(
                agent, world_turn, state, "decision",
                "⏳ Жду у торговца (документы)",
                {"action_kind": "wait_at_trader", "location_id": loc_id},
                summary="Жду у торговца новостей о документах",
            )
        _runtime_set_action_used(agent, True)
        return []

    # 5. Travel to nearest trader
    nearest_trader_loc = _find_nearest_trader_location(loc_id, state)
    nearest_trader_id = None
    if nearest_trader_loc and nearest_trader_loc != loc_id:
        _add_memory(
            agent, world_turn, state, "decision",
            f"🏪 Еду к торговцу за информацией о документах",
            {"action_kind": "wait_at_trader", "destination": nearest_trader_loc},
            summary="Еду к торговцу за информацией о документах",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, nearest_trader_loc, state, world_turn)

    # 6. Wander toward dungeon/x_lab if available
    locations = state.get("locations", {})
    search_terrains = frozenset({"x_lab", "dungeon", "industrial", "military_buildings"})
    best_loc = None
    for conn in loc.get("connections", []) if False else []:  # conn iteration placeholder
        pass
    reachable = _dijkstra_reachable_locations(
        loc_id, locations, max_minutes=6 * 60 * MINUTES_PER_TURN,
        map_revision=int(state.get("map_revision", 0)),
    )
    for rlt, dist in sorted(reachable.items(), key=lambda x: x[1]):
        if rlt == loc_id:
            continue
        rl = locations.get(rlt, {})
        if rl.get("terrain_type") in search_terrains:
            best_loc = rlt
            break
    if best_loc:
        _add_memory(
            agent, world_turn, state, "decision",
            f"🔍 Блуждаю в поисках документов",
            {"action_kind": "wander", "destination": best_loc},
            summary="Иду искать документы на объектах",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, best_loc, state, world_turn)

    # 7. Wander to any neighbor
    conns = locations.get(loc_id, {}).get("connections", [])
    if conns:
        next_loc = rng.choice([c["to"] for c in conns]) if False else conns[0]["to"]
        _add_memory(
            agent, world_turn, state, "decision",
            "🔍 Блуждаю в поисках документов",
            {"action_kind": "wander", "destination": next_loc},
            summary="Иду наугад в поисках документов",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, next_loc, state, world_turn)

    _runtime_set_action_used(agent, True)
    return []


def _compat_pursue_get_rich(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    loc: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    rng: Any,
) -> List[Dict[str, Any]]:
    """v1-compat get_rich logic: explore anomaly, write move_for_anomaly memory."""
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    art_types = frozenset(ARTIFACT_TYPES.keys())

    # Gate: if agent is below material_threshold, set current_goal based on equipment needs
    material_threshold = agent.get("material_threshold", 0)
    money = agent.get("money", 0)
    if material_threshold > 0 and money < material_threshold:
        equipment = agent.get("equipment", {})
        if equipment.get("weapon") is None:
            agent["current_goal"] = "get_weapon"
        elif equipment.get("armor") is None:
            agent["current_goal"] = "get_armor"
        else:
            agent["current_goal"] = "gather_resources"
    # Check if agent has artifacts to sell
    has_artifacts = any(i.get("type") in art_types for i in agent.get("inventory", []))
    if has_artifacts:
        # Find trader
        nearest_trader_loc = _find_nearest_trader_location(loc_id, state)
        if nearest_trader_loc:
            if nearest_trader_loc == loc_id:
                trader = _find_trader_at_location(loc_id, state)
                if trader:
                    return _bot_sell_to_trader(agent_id, agent, trader, state, world_turn)
            else:
                from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART_COMPAT
                _art_count_compat = sum(1 for i in agent.get("inventory", []) if i.get("type") in frozenset(_ART_COMPAT.keys()))
                _add_memory(
                    agent, world_turn, state, "decision",
                    "🎁 Иду продавать артефакты",
                    {"action_kind": "sell_artifacts",
                     "artifacts_count": _art_count_compat,
                     "destination": nearest_trader_loc},
                    summary=f"Иду к торговцу в {nearest_trader_loc} продавать {_art_count_compat} артефакт(ов)",
                )
                _runtime_set_action_used(agent, True)
                return _bot_schedule_travel(agent_id, agent, nearest_trader_loc, state, world_turn)

    # Find best anomaly location
    locations = state.get("locations", {})
    confirmed_empty = _confirmed_empty_locations(agent)

    # Current location
    if loc.get("anomaly_activity", 0) > 0 and loc_id not in confirmed_empty:
        _add_memory(
            agent, world_turn, state, "decision",
            "⚡ Исследую аномалию здесь",
            {"action_kind": "explore_decision", "location_id": loc_id},
            summary="Начинаю исследовать аномалию в текущей локации",
        )
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location",
            "target_id": loc_id,
            "turns_remaining": EXPLORE_DURATION_TURNS,
            "turns_total": EXPLORE_DURATION_TURNS,
            "started_turn": world_turn,
            "ends_turn": world_turn + EXPLORE_DURATION_TURNS,
            "revision": 1,
            "interruptible": True,
        }
        _runtime_set_action_used(agent, True)
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    # Find best reachable anomaly location by anomaly_activity score
    reachable = _dijkstra_reachable_locations(
        loc_id, locations, max_minutes=6 * 60 * MINUTES_PER_TURN,
        map_revision=int(state.get("map_revision", 0)),
    )
    best_loc_id = None
    best_score = -1.0
    for cand_id, travel_min in reachable.items():
        if cand_id in confirmed_empty:
            continue
        cand = locations.get(cand_id, {})
        activity = cand.get("anomaly_activity", 0)
        if activity <= 0:
            continue
        score = _score_location(cand, "artifacts")
        if score > best_score:
            best_score = score
            best_loc_id = cand_id
    if best_loc_id and best_loc_id != loc_id:
        travel_minutes = reachable.get(best_loc_id, 0)
        _add_memory(
            agent, world_turn, state, "decision",
            f"⚡ Еду к аномалии в {best_loc_id}",
            {"action_kind": "move_for_anomaly", "destination": best_loc_id,
             "travel_minutes": travel_minutes},
            summary=f"Еду искать артефакты в {best_loc_id}",
        )
        _runtime_set_action_used(agent, True)
        return _bot_schedule_travel(agent_id, agent, best_loc_id, state, world_turn)

    _runtime_set_action_used(agent, True)
    return []
