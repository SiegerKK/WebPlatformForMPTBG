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
import heapq
import copy
import random
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from app.games.zone_stalkers.decision.debug.brain_trace import (
    ensure_brain_trace_for_tick,
    write_decision_brain_trace_from_v2,
    write_plan_monitor_trace,
)
from app.games.zone_stalkers.decision.plan_monitor import (
    PlanMonitorResult,
    assess_scheduled_action_v3,
    is_v3_monitored_bot,
)
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    HUNGER_INCREASE_PER_HOUR,
    HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER,
    HP_DAMAGE_PER_HOUR_CRITICAL_THIRST,
    SLEEPINESS_INCREASE_PER_HOUR,
    THIRST_INCREASE_PER_HOUR,
)

# 1 game turn = 1 real minute
MINUTES_PER_TURN = 1

# Derived constants (update MINUTES_PER_TURN above to rescale the whole system)
_HOUR_IN_TURNS = 60 // MINUTES_PER_TURN       # turns needed to pass 1 in-game hour
EXPLORE_DURATION_TURNS = 30 // MINUTES_PER_TURN  # turns needed for a 30-min exploration
DEFAULT_SLEEP_HOURS = 6                         # default hours of sleep when no 'hours' key is present in sched

# Agent memory cap — oldest entries are dropped when this limit is exceeded.
MAX_AGENT_MEMORY = 2000
PLAN_MONITOR_MEMORY_DEDUP_TURNS = 10

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
# Used in _run_bot_decision_v2_inner to update current_goal BEFORE writing
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


def tick_zone_map(state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Advance the world by one turn.

    Returns (new_state, events_emitted).
    """
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []
    world_turn = state.get("world_turn", 1)

    for _agent in state.get("agents", {}).values():
        _agent.setdefault("brain_trace", None)
        _agent.setdefault("active_plan_v3", None)
        _agent.setdefault("memory_v3", None)

    # One-time migration: normalize terrain types that were removed in the v3 update
    # (urban → plain, underground → plain) and any other unknown types.
    if not state.get("_terrain_migrated_v3"):
        _valid_v3: frozenset = frozenset({
            "plain", "hills", "slag_heaps", "industrial", "buildings", "military_buildings",
            "hamlet", "farm", "field_camp", "dungeon", "x_lab", "bridge",
            "tunnel", "swamp", "scientific_bunker",
        })
        for loc in state.get("locations", {}).values():
            if loc.get("terrain_type") not in _valid_v3:
                loc["terrain_type"] = "plain"
        state["_terrain_migrated_v3"] = True

    # 1. Process scheduled actions for each alive stalker agent
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):
            continue
        sched = agent.get("scheduled_action")
        if sched:
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
                    _dominant_pressure = None
                    if monitor_result.dominant_pressure is not None and monitor_result.dominant_pressure_value is not None:
                        _dominant_pressure = {
                            "key": monitor_result.dominant_pressure,
                            "value": round(float(monitor_result.dominant_pressure_value), 3),
                        }
                    _summary = (
                        f"Прерываю {sched.get('type')} из-за {monitor_result.reason}."
                    )
                    _signature = {
                        "reason": monitor_result.reason,
                        "scheduled_action_type": sched.get("type"),
                        "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
                    }
                    write_plan_monitor_trace(
                        agent,
                        world_turn=world_turn,
                        decision="abort",
                        reason=monitor_result.reason,
                        summary=_summary,
                        scheduled_action_type=sched.get("type"),
                        dominant_pressure_key=monitor_result.dominant_pressure,
                        dominant_pressure_value=monitor_result.dominant_pressure_value,
                        state=state,
                    )
                    if should_write_plan_monitor_memory_event(
                        agent,
                        world_turn,
                        action_kind="plan_monitor_abort",
                        signature=_signature,
                    ):
                        _add_memory(
                            agent,
                            world_turn,
                            state,
                            "decision",
                            "⚡ PlanMonitor: прерываю активное действие",
                            {
                                "action_kind": "plan_monitor_abort",
                                "reason": monitor_result.reason,
                                "scheduled_action_type": sched.get("type"),
                                "dominant_pressure": _dominant_pressure,
                                "dedup_signature": _signature,
                            },
                            summary=_summary,
                        )
                    events.append({
                        "event_type": "plan_monitor_aborted_action",
                        "payload": {
                            "agent_id": agent_id,
                            "scheduled_action_type": sched.get("type"),
                            "reason": monitor_result.reason,
                            "dominant_pressure": _dominant_pressure
                            or {"key": "unknown", "value": 0.0},
                            "cancelled_target": sched.get("target_id"),
                            "cancelled_final_target": sched.get("final_target_id"),
                            "current_location_id": agent.get("location_id"),
                            "turns_remaining": sched.get("turns_remaining"),
                        },
                    })
                    agent["scheduled_action"] = None
                    if monitor_result.should_clear_action_queue:
                        agent["action_queue"] = []
                    continue
                write_plan_monitor_trace(
                    agent,
                    world_turn=world_turn,
                    decision="continue",
                    reason=monitor_result.reason,
                    summary=f"Продолжаю {sched.get('type')} — {monitor_result.reason}.",
                    scheduled_action_type=sched.get("type"),
                    state=state,
                )
            new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
            events.extend(new_evs)

    # 2. Degrade survival needs and apply critical penalties (once per in-game hour)
    # Determine current minute after advancing (before committing to state)
    _new_minute = (state.get("world_minute", 0) + 1) % 60
    if _new_minute == 0:  # hour boundary reached
        for agent_id, agent in state.get("agents", {}).items():
            if not agent.get("is_alive", True):
                continue
            if agent.get("has_left_zone"):  # departed agents need no hunger/thirst/sleep degradation
                continue
            agent["hunger"] = min(100, agent.get("hunger", 0) + HUNGER_INCREASE_PER_HOUR)
            agent["thirst"] = min(100, agent.get("thirst", 0) + THIRST_INCREASE_PER_HOUR)
            agent["sleepiness"] = min(100, agent.get("sleepiness", 0) + SLEEPINESS_INCREASE_PER_HOUR)
            # Critical thirst causes HP damage faster than hunger
            if agent.get("thirst", 0) >= CRITICAL_THIRST_THRESHOLD:
                agent["hp"] = max(0, agent["hp"] - HP_DAMAGE_PER_HOUR_CRITICAL_THIRST)
            if agent.get("hunger", 0) >= CRITICAL_HUNGER_THRESHOLD:
                agent["hp"] = max(0, agent["hp"] - HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER)
            if agent["hp"] <= 0 and agent.get("is_alive", True):
                agent["is_alive"] = False
                _hunger = agent.get("hunger", 0)
                _thirst = agent.get("thirst", 0)
                _death_cause_str = (
                    "обезвоживание" if _thirst >= _hunger else "голод"
                )
                _add_memory(
                    agent, world_turn, state, "observation",
                    "💀 Смерть",
                    {"action_kind": "death", "cause": "starvation_or_thirst",
                     "hunger": _hunger, "thirst": _thirst},
                    summary=f"Погиб от {_death_cause_str} — голод {_hunger}%, жажда {_thirst}%",
                )
                events.append({
                    "event_type": "agent_died",
                    "payload": {"agent_id": agent_id, "cause": "starvation_or_thirst"},
                })


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
            state["emission_warning_offset"] = _warn_offset
        _warn_offset = state["emission_warning_offset"]
        if _turns_until == _warn_offset:
            state["emission_warning_written_turn"] = world_turn
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
        state["emission_active"] = True
        state["emission_ends_turn"] = world_turn + _emission_duration

        # Spawn artifacts in all anomaly locations during emission start
        for _em_loc_id, _em_loc in state.get("locations", {}).items():
            if _em_loc.get("anomaly_activity", 0) <= 0:
                continue
            _spawn_chance = _em_loc.get("anomaly_activity", 0) / 10.0
            if _emission_rng.random() < _spawn_chance:
                _art_type = _emission_rng.choice(list(ARTIFACT_TYPES.keys()))
                _art_info = ARTIFACT_TYPES[_art_type]
                _art_id = f"art_emission_{_em_loc_id}_{world_turn}"
                _em_loc.setdefault("artifacts", []).append({
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
                _em_agent["is_alive"] = False
                _em_loc_name = _em_agent_loc.get("name", _em_agent.get("location_id", "?"))
                _add_memory(
                    _em_agent, world_turn, state, "observation",
                    "💀 Смерть",
                    {"action_kind": "death", "cause": "emission",
                     "location_id": _em_agent.get("location_id"), "terrain": _em_terrain},
                    summary=f"Погиб от выброса в локации «{_em_loc_name}» (местность: {_TERRAIN_NAME_RU.get(_em_terrain, _em_terrain)})",
                )
                events.append({
                    "event_type": "agent_died",
                    "payload": {"agent_id": _em_agent_id, "cause": "emission"},
                })

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
        state["emission_active"] = False
        # Schedule next emission 1–2 in-game days from now
        _next_emission_delay = _emission_rng.randint(
            _EMISSION_MIN_INTERVAL_TURNS, _EMISSION_MAX_INTERVAL_TURNS
        )
        state["emission_scheduled_turn"] = world_turn + _next_emission_delay
        # Reset warning state so the next cycle gets a fresh random offset.
        state["emission_warning_written_turn"] = None
        state["emission_warning_offset"] = None

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

    # 3. AI bot agent decisions — v2 decision pipeline (Phase 5+)
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("controller", {}).get("kind") != "bot":
            continue
        if agent.get("has_left_zone"):
            continue
        if agent.get("scheduled_action"):
            continue
        if agent.get("action_used"):
            continue
        # Skip bot decisions for agents in active combat (not fled)
        if _agent_in_active_combat(agent_id, state):
            continue

        bot_evs = _run_bot_decision_v2(agent_id, agent, state, world_turn)
        events.extend(bot_evs)

    for _agent in state.get("agents", {}).values():
        if not is_v3_monitored_bot(_agent):
            continue
        ensure_brain_trace_for_tick(_agent, world_turn=world_turn, state=state)

    # 3b. Per-turn location observations for every alive stalker agent.
    # Writes a new observation entry only when content has changed since the last
    # entry of the same category; merges repeated observations via the semantic
    # merge system; marks stale entries before writing new ones.
    from app.games.zone_stalkers.rules.memory_merge import apply_staleness  # noqa: PLC0415
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):  # departed agents are no longer at any location
            continue
        if agent.get("archetype") != "stalker_agent":
            continue
        loc_id = agent.get("location_id")
        if loc_id:
            # Apply staleness decay before writing so that a previously-stale
            # entry is properly re-opened (status→active) when the same
            # observation recurs, rather than being confused with a fresh one.
            apply_staleness(agent.get("memory", []), world_turn)
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
    state["world_minute"] = world_minute
    state["world_hour"] = world_hour
    state["world_day"] = world_day
    state["world_turn"] = world_turn + 1

    # 5. Reset action_used for next turn
    for agent in state.get("agents", {}).values():
        if agent.get("is_alive", True) and not agent.get("has_left_zone"):
            agent["action_used"] = False
        for _k in [k for k in list(agent.keys()) if k.startswith("_v3_")]:
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
    return state, events


# ─────────────────────────────────────────────────────────────────
# Scheduled action processing
# ─────────────────────────────────────────────────────────────────

def _is_emission_threat(agent: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Return True if an active emission or a live emission_imminent warning
    (not yet superseded by emission_ended) is present in the agent's memory."""
    if state.get("emission_active", False):
        return True
    _last_ended: int = 0
    _last_imminent: int = 0
    for _mem in agent.get("memory", []):
        if _mem.get("type") != "observation":
            continue
        _mk = _mem.get("effects", {}).get("action_kind")
        _mt = _mem.get("world_turn", 0)
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
    action_type = sched["type"]
    turns_remaining = sched["turns_remaining"] - 1
    sched["turns_remaining"] = turns_remaining

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
                agent["scheduled_action"] = None
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
    agent["scheduled_action"] = None

    if action_type == "travel":
        # target_id is the IMMEDIATE next hop; final_target_id is the ultimate goal.
        # remaining_route lists hops that come AFTER target_id.
        destination = sched.get("target_id")
        final_target = sched.get("final_target_id", destination)
        remaining_route = sched.get("remaining_route", [])
        if destination and destination in state.get("locations", {}):
            old_loc = agent["location_id"]
            # Move agent to this hop's location
            old_loc_data = state["locations"].get(old_loc, {})
            if agent_id in old_loc_data.get("agents", []):
                old_loc_data["agents"].remove(agent_id)
            agent["location_id"] = destination
            new_loc_data = state["locations"].get(destination, {})
            if agent_id not in new_loc_data.get("agents", []):
                new_loc_data.setdefault("agents", []).append(agent_id)
            # Apply anomaly damage for this single hop
            hop_loc = state["locations"].get(destination, {})
            hop_anomaly_activity = hop_loc.get("anomaly_activity", 0)
            total_dmg = 0
            if hop_anomaly_activity > 0:
                _hop_rng = random.Random(agent_id + str(world_turn) + destination)
                if _hop_rng.random() < hop_anomaly_activity / 20.0:
                    total_dmg = 5 + hop_anomaly_activity
                    agent["hp"] = max(0, agent["hp"] - total_dmg)
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
                }
                # Carry over the emergency_flee flag so that each hop of an
                # emission-flee journey is never interrupted by the emission
                # warning — otherwise the agent would cancel and restart the
                # flee on every hop, creating an infinite loop.
                if sched.get("emergency_flee"):
                    _next_sched["emergency_flee"] = True
                agent["scheduled_action"] = _next_sched
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
                agent["is_alive"] = False
                _travel_loc_name = state.get("locations", {}).get(destination, {}).get("name", destination)
                _add_memory(
                    agent, world_turn, state, "observation",
                    "💀 Смерть",
                    {"action_kind": "death", "cause": "travel_anomaly", "location_id": destination},
                    summary=f"Погиб от урона аномалии при путешествии в локацию «{_travel_loc_name}»",
                )
                events.append({"event_type": "agent_died", "payload": {"agent_id": agent_id, "cause": "travel_anomaly"}})

    elif action_type == "explore_anomaly_location":
        loc_id = agent["location_id"]
        loc = state["locations"].get(loc_id, {})
        result_evs = _resolve_exploration(agent_id, agent, loc, loc_id, state, world_turn)
        events.extend(result_evs)

    elif action_type == "sleep":
        _resolve_sleep(agent, sched, world_turn, state)
        events.append({
            "event_type": "sleep_completed",
            "payload": {
                "agent_id": agent_id,
                "hours_slept": sched.get("turns_total", 6),
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

    # Pop next queued action if available
    queue = agent.get("action_queue", [])
    if queue and not agent.get("scheduled_action"):
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
    """Resolve exploration: pick up an existing artifact (50 % chance) or mark the location.

    New logic (replaces the old loot-table approach):
    - Checks existing artifacts on the location rather than conjuring one from thin air.
    - 50 % success roll.  On success the agent picks up one random artifact.
    - On failure distinguishes between a truly empty location and bad luck.
    """
    from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
    events: List[Dict[str, Any]] = []

    # Seed using agent id + turn for per-agent variance
    rng = random.Random(agent_id + str(world_turn))

    existing_artifacts = loc.get("artifacts", [])
    found_artifacts: List[Dict[str, Any]] = []
    loc_name = loc.get("name", loc_id)

    artifact_found = existing_artifacts and rng.random() < 0.5
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
            agent["is_alive"] = False
            _add_memory(
                agent, world_turn, state, "observation",
                "💀 Смерть",
                {"action_kind": "death", "cause": "anomaly_exploration",
                 "location_id": loc_id, "anomaly_type": anomaly_type},
                summary=f"Погиб от аномалии «{anomaly_type}» при исследовании «{loc.get('name', loc_id)}»",
            )
            events.append({
                "event_type": "agent_died",
                "payload": {"agent_id": agent_id, "cause": "anomaly_exploration"},
            })

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


def _resolve_sleep(agent: Dict[str, Any], sched: Dict[str, Any], world_turn: int, state: Dict[str, Any]) -> None:
    """Apply sleep healing effects.

    ``sched`` must contain either:
    * ``hours``       – preferred; the number of in-game hours slept.
    * ``turns_total`` – fallback; total turns of the sleep action (converted via
                        ``turns_total // _HOUR_IN_TURNS``).
    """
    hours_from_sched = sched.get("hours")
    if hours_from_sched is not None:
        hours = int(hours_from_sched)
    elif sched.get("turns_total"):
        hours = sched["turns_total"] // _HOUR_IN_TURNS
    else:
        hours = DEFAULT_SLEEP_HOURS
    # Heal HP (15 per hour, max 100)
    hp_regen = min(15 * hours, agent["max_hp"] - agent["hp"])
    agent["hp"] = min(agent["max_hp"], agent["hp"] + hp_regen)
    # Reduce radiation (5 per hour)
    rad_reduce = 5 * hours
    agent["radiation"] = max(0, agent.get("radiation", 0) - rad_reduce)
    # Reset sleepiness
    agent["sleepiness"] = 0
    _add_memory(agent, world_turn, state, "action",
                f"Поспал {hours} ч.",
                {"action_kind": "sleep", "hp_gained": hp_regen, "radiation_reduced": rad_reduce},
                summary=f"Поспал {hours} часов: восстановлено {hp_regen} HP, снято {rad_reduce} радиации")


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

    mem = agent.setdefault("memory", [])
    mem.append(memory_entry)
    # Keep only the last MAX_AGENT_MEMORY memory entries
    if len(mem) > MAX_AGENT_MEMORY:
        agent["memory"] = mem[-MAX_AGENT_MEMORY:]


def should_write_plan_monitor_memory_event(
    agent: Dict[str, Any],
    world_turn: int,
    *,
    action_kind: str,
    signature: Dict[str, Any],
    dedup_turns: int = PLAN_MONITOR_MEMORY_DEDUP_TURNS,
) -> bool:
    """Return False when a semantically identical memory exists in the recent window."""
    for mem in reversed(agent.get("memory", [])):
        mem_turn = int(mem.get("world_turn", 0))
        if world_turn - mem_turn > dedup_turns:
            break

        effects = mem.get("effects", {})
        if effects.get("action_kind") != action_kind:
            continue
        if effects.get("dedup_signature") == signature:
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
    for mem in reversed(agent.get("memory", [])):
        fx = mem.get("effects", {})
        if fx.get("action_kind") == "travel_arrived":
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
    agent["action_used"] = True
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
            target["is_alive"] = False
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
                target, world_turn, state, "observation",
                "💀 Убит в бою",
                {"observed": "combat_killed", "combat_id": cid,
                 "killer_id": agent_id, "killer_name": atk_name},
                summary=f"Я был убит «{atk_name}» в бою",
            )
            events.append({
                "event_type": "agent_died",
                "payload": {"agent_id": target_id, "cause": "combat",
                            "killer_id": agent_id, "combat_id": cid},
            })
    agent["action_used"] = True
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
    agent["action_used"] = True
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
) -> Dict[str, float]:
    """Return {loc_id: travel_minutes} for every location reachable from *from_loc_id*
    within *max_minutes* of total travel time via open (non-closed) connections.

    Uses Dijkstra's algorithm so that connections with different ``travel_time`` values
    are compared fairly.  *from_loc_id* itself is not included in the result.
    """
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
    return dist


def _last_obs_content(agent: Dict[str, Any], obs_type: str, loc_id: str) -> Optional[List[str]]:
    """Return the content list from the most recent observation of *obs_type* at *loc_id*.

    Returns ``None`` when no such entry exists yet.  Used for deduplication so that
    identical observations are not written to memory on every turn.
    """
    key = "names" if obs_type in ("stalkers", "mutants") else (
        "artifact_types" if obs_type == "artifacts" else "item_types"
    )
    for entry in reversed(agent.get("memory", [])):
        if entry.get("type") != "observation":
            continue
        fx = entry.get("effects", {})
        if fx.get("observed") == obs_type and fx.get("location_id") == loc_id:
            return fx.get(key)
    return None


def _find_obs_entry(
    agent: Dict[str, Any], obs_type: str, loc_id: str
) -> Optional[Dict[str, Any]]:
    """Return the most recent observation entry for *obs_type* at *loc_id*.

    Unlike :func:`_last_obs_content`, this returns the **mutable entry dict**
    itself so callers can update it in-place (merge logic).  Returns ``None``
    when no such entry exists.
    """
    for entry in reversed(agent.get("memory", [])):
        if entry.get("type") != "observation":
            continue
        fx = entry.get("effects", {})
        if fx.get("observed") == obs_type and fx.get("location_id") == loc_id:
            return entry
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
    memory = agent.get("memory", [])

    # Find the world_turn of the most recent emission_ended observation the agent holds.
    last_emission_ended_turn: int = 0
    for mem in memory:
        if mem.get("effects", {}).get("action_kind") == "emission_ended":
            t = mem.get("world_turn", 0)
            if t > last_emission_ended_turn:
                last_emission_ended_turn = t

    # A confirmed_empty entry is valid only when it was recorded AFTER the last
    # emission end the agent is aware of.
    return frozenset(
        mem.get("effects", {}).get("location_id")
        for mem in memory
        if mem.get("effects", {}).get("action_kind") == "explore_confirmed_empty"
        and mem.get("effects", {}).get("location_id")
        and mem.get("world_turn", 0) > last_emission_ended_turn
    )


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
    from app.games.zone_stalkers.rules.memory_merge import (  # noqa: PLC0415
        find_mergeable_entry,
        update_merged_entry,
    )

    loc = state.get("locations", {}).get(loc_id, {})
    loc_name = loc.get("name", loc_id)

    # ── Stalkers + traders at this location (excluding self) ──────────────────
    stalker_names: List[str] = []
    for aid, ag in state.get("agents", {}).items():
        if (aid != agent_id
                and ag.get("location_id") == loc_id
                and ag.get("is_alive", True)
                and not ag.get("has_left_zone")
                and ag.get("archetype") == "stalker_agent"):
            stalker_names.append(ag.get("name", aid))
    for tid, tr in state.get("traders", {}).items():
        if tr.get("location_id") == loc_id:
            stalker_names.append(tr.get("name", tid))
    stalker_names.sort()

    if stalker_names:
        _stalker_effects_proto = {
            "observed": "stalkers",
            "location_id": loc_id,
            "names": stalker_names,
        }
        _existing = find_mergeable_entry(
            agent.get("memory", []), _stalker_effects_proto, world_turn
        )
        if _existing is not None:
            # Semantic merge: union of previously-seen and currently-visible names.
            _old_names = _existing["effects"].get("names", [])
            _merged = sorted(set(_old_names) | set(stalker_names))
            _existing["effects"]["names"] = _merged
            update_merged_entry(_existing, world_turn)
            if _merged != _old_names:
                # Content changed (new name added) — bump the semantic change timestamp.
                _existing["world_turn"] = world_turn
            _existing["summary"] = (
                f"В локации «{loc_name}» замечены: {', '.join(_merged)}"
            )
        else:
            # No mergeable entry found — create a fresh one.
            # new_obs_aggregate_fields is injected automatically by _add_memory.
            _add_memory(
                agent, world_turn, state, "observation",
                f"Вижу персонажей в «{loc_name}»",
                {"observed": "stalkers", "location_id": loc_id, "names": stalker_names},
                summary=f"В локации «{loc_name}» замечены: {', '.join(stalker_names)}",
            )

    # ── Mutants at this location ──────────────────────────────────────────────
    mutant_names = sorted(
        m.get("name", m.get("type", "?"))
        for m in state.get("mutants", {}).values()
        if m.get("location_id") == loc_id and m.get("is_alive", True)
    )
    if mutant_names:
        _mutant_effects_proto = {
            "observed": "mutants",
            "location_id": loc_id,
            "names": mutant_names,
        }
        _existing_mut = find_mergeable_entry(
            agent.get("memory", []), _mutant_effects_proto, world_turn
        )
        if _existing_mut is not None:
            # Same mutant group visible again — merge.
            _existing_mut["effects"]["names"] = mutant_names
            update_merged_entry(_existing_mut, world_turn)
            _existing_mut["summary"] = (
                f"В локации «{loc_name}» замечены мутанты: {', '.join(mutant_names)}"
            )
        else:
            # Different group (or first sighting, or outside window) — new entry.
            _add_memory(
                agent, world_turn, state, "observation",
                f"Вижу мутантов в «{loc_name}»",
                {"observed": "mutants", "location_id": loc_id, "names": mutant_names},
                summary=f"В локации «{loc_name}» замечены мутанты: {', '.join(mutant_names)}",
            )

    # NOTE: Artifacts are NOT recorded as a location observation on arrival.
    # They can only be discovered and recorded through the explore action.
    # See _resolve_exploration() for the artifact pickup observation.

    # ── Loose items on the ground ─────────────────────────────────────────────
    item_types = sorted(it.get("type", "?") for it in loc.get("items", []))
    if item_types:
        _item_effects_proto = {
            "observed": "items",
            "location_id": loc_id,
            "item_types": item_types,
        }
        _existing_items = find_mergeable_entry(
            agent.get("memory", []), _item_effects_proto, world_turn
        )
        if _existing_items is not None:
            # Replace item list with current ground state; update aggregate fields.
            _old_item_types = _existing_items["effects"].get("item_types", [])
            if item_types != _old_item_types:
                _existing_items["effects"]["item_types"] = item_types
                _existing_items["summary"] = (
                    f"В локации «{loc_name}» на земле: {', '.join(item_types)}"
                )
            update_merged_entry(_existing_items, world_turn)
            if item_types != _old_item_types:
                # Content changed — bump the semantic change timestamp.
                _existing_items["world_turn"] = world_turn
        else:
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
    trader_locs: set = {
        t["location_id"] for t in traders.values() if t.get("is_alive", True)
    }
    if from_loc_id in trader_locs:
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
                return nxt
            queue.append((nxt, dist + 1))
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
    """Append a memory entry to a trader NPC (same structure as agent memory).

    Supports both new-style (6 args, effects_dict only) and legacy-style
    (7 args, summary_str + effects_dict) calling conventions.
    """
    if len(call_args) == 2 and isinstance(call_args[0], str):
        summary: str = call_args[0]
        effects: Dict[str, Any] = call_args[1] if isinstance(call_args[1], dict) else {}
    elif len(call_args) == 1:
        summary = ""
        effects = call_args[0] if isinstance(call_args[0], dict) else {}
    else:
        summary = ""
        effects = {}

    entry: Dict[str, Any] = {
        "world_turn": world_turn,
        "type": memory_type,
        "title": title,
        "effects": effects,
    }
    if summary:
        entry["summary"] = summary
    trader.setdefault("memory", []).append(entry)
    if len(trader["memory"]) > MAX_AGENT_MEMORY:
        trader["memory"] = trader["memory"][-MAX_AGENT_MEMORY:]


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

    sell_price_total = 0
    sold_items = []
    for art in artifacts:
        sell_price = int(art.get("value", 0) * 0.6)  # 60% of base value
        trader_money = trader.get("money", 0)
        if trader_money < sell_price:
            continue  # trader too poor; skip this item
        # Transfer money
        agent["money"] = agent.get("money", 0) + sell_price
        trader["money"] = trader_money - sell_price
        sell_price_total += sell_price
        # Transfer item
        sold_item = dict(art)
        sold_item["stock"] = 1
        trader.setdefault("inventory", []).append(sold_item)
        sold_items.append(art)
        events.append({
            "event_type": "bot_sold_artifact",
            "payload": {
                "agent_id": agent_id,
                "trader_id": trader["id"],
                "item_id": art["id"],
                "item_type": art["type"],
                "price": sell_price,
            },
        })

    if not sold_items:
        return events

    # Remove sold items from inventory
    sold_ids = {i["id"] for i in sold_items}
    agent["inventory"] = [i for i in agent.get("inventory", []) if i["id"] not in sold_ids]
    agent["action_used"] = True

    # ── Stalker memory (Step 7) ───────────────────────────────────
    item_names = ", ".join(i.get("name", i.get("type", "?")) for i in sold_items)
    trader_name = trader.get("name", trader["id"])
    loc_name = state.get("locations", {}).get(agent.get("location_id", ""), {}).get("name", "?")
    _add_memory(
        agent, world_turn, state, "action",
        f"Продал {len(sold_items)} артефактов на {sell_price_total} денег",
        {"action_kind": "trade_sell", "money_gained": sell_price_total,
         "items_sold": [i["type"] for i in sold_items], "trader_id": trader["id"]},
        summary=f"Продал {item_names} торговцу {trader_name} в «{loc_name}» за {sell_price_total} денег",
    )

    # ── Trader memory ─────────────────────────────────────────────
    stalker_name = agent.get("name", agent_id)
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
        agent["action_used"] = True
        return []

    events: List[Dict[str, Any]] = []
    sold_items: List[Dict[str, Any]] = []
    total_earned = 0

    for item in candidates:
        if target_amount is not None and agent.get("money", 0) >= target_amount:
            break
        t = item.get("type", "")
        val = item.get("value", _IT.get(t, {}).get("value", 0))
        sell_price = int(val * _SELL_RATIO)
        if sell_price <= 0:
            continue
        if trader.get("money", 0) < sell_price:
            continue  # trader cannot afford this item
        agent["money"] = agent.get("money", 0) + sell_price
        trader["money"] = trader.get("money", 0) - sell_price
        total_earned += sell_price
        sold_item = dict(item)
        sold_item["stock"] = 1
        trader.setdefault("inventory", []).append(sold_item)
        sold_items.append(item)
        events.append({
            "event_type": "bot_sold_item",
            "payload": {
                "agent_id": agent_id,
                "trader_id": trader.get("id", ""),
                "item_type": t,
                "price": sell_price,
            },
        })

    if not sold_items:
        agent["action_used"] = True
        return []

    # Remove sold items (compare by object identity)
    sold_obj_ids = {id(i) for i in sold_items}
    agent["inventory"] = [i for i in agent.get("inventory", []) if id(i) not in sold_obj_ids]
    agent["action_used"] = True

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
            "trader_id": trader.get("id", ""),
        },
        summary=(
            f"Продал {item_names} торговцу {trader_name} в «{loc_name}» "
            f"за {total_earned} денег, чтобы покрыть критические нужды"
        ),
    )

    stalker_name = agent.get("name", agent_id)
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
    }
    if emergency_flee:
        sched["emergency_flee"] = True
    agent["scheduled_action"] = sched
    agent["action_used"] = True
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
        agent["money"] = agent.get("money", 0) - buy_price
        trader["money"] = trader.get("money", 0) + buy_price
        # Create a fresh item instance and place it in agent inventory
        new_item: Dict[str, Any] = {
            "id": f"{item_type}_{agent_id}_{world_turn}",
            "type": item_type,
            "name": ITEM_TYPES[item_type].get("name", item_type),
            "value": base_value,
        }
        agent.setdefault("inventory", []).append(new_item)
        agent["action_used"] = True
        item_name = ITEM_TYPES[item_type].get("name", item_type)
        trader_name = trader.get("name", trader["id"])
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
                "agent_id": agent_id, "trader_id": trader["id"],
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
    agent["inventory"] = [i for i in agent.get("inventory", []) if i["id"] != item["id"]]
    agent["action_used"] = True
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
    equipment = agent.setdefault("equipment", {})
    # Return old equipment to inventory (if any)
    old_item = equipment.get(slot)
    if old_item:
        agent["inventory"] = [i for i in inventory if i["id"] != item["id"]] + [old_item]
    else:
        agent["inventory"] = [i for i in inventory if i["id"] != item["id"]]
    equipment[slot] = item
    agent["action_used"] = True
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "decision":
            continue
        # We found the latest decision.
        effects = mem.get("effects", {})
        if effects.get("action_kind") != "seek_item":
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "decision":
            continue
        effects = mem.get("effects", {})
        if effects.get("action_kind") != "sell_at_trader":
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
    loc["items"] = [i for i in ground_items if i["id"] != item["id"]]
    agent.setdefault("inventory", []).append(item)
    agent["action_used"] = True
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
    remaining = loc.get("items", [])
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

    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        effects = mem.get("effects", {})
        turn = mem.get("world_turn", 0)
        loc_id = effects.get("location_id")
        if not loc_id:
            continue

        if effects.get("action_kind") in ("item_not_found_here", "item_picked_up_here"):
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "decision":
            continue
        effects = mem.get("effects", {})
        if effects.get("destination") != loc_id or effects.get("item_category") != item_category:
            continue
        ak = effects.get("action_kind")
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "decision":
            continue
        effects = mem.get("effects", {})
        if effects.get("destination") != loc_id or effects.get("item_category") != item_category:
            continue
        ak = effects.get("action_kind")
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("world_turn") != world_turn:
            break
        effects = mem.get("effects", {})
        if (
            effects.get("action_kind") == "item_picked_up_here"
            and effects.get("location_id") == loc_id
            and frozenset(effects.get("item_types", [])).intersection(item_types)
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
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "observation":
            continue
        effects = mem.get("effects", {})
        if effects.get("observed") != "items":
            continue
        loc_id = effects.get("location_id")
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
    for mem in agent.get("memory", []):
        fx = mem.get("effects", {})
        if fx.get("action_kind") == "intel_from_stalker":
            already_known.add((fx.get("source_agent_id"), fx.get("location_id")))

    # Precompute the most recent "resolved" turn per location for the asking agent.
    # Intel whose observation turn is <= the resolved turn is stale and should be skipped.
    resolved_turns: Dict[str, int] = {}
    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        fx = mem.get("effects", {})
        if fx.get("action_kind") not in ("item_not_found_here", "item_picked_up_here"):
            continue
        mem_types = frozenset(fx.get("item_types", []))
        if not item_types.intersection(mem_types):
            continue
        r_loc = fx.get("location_id")
        if r_loc:
            r_turn = mem.get("world_turn", 0)
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
        for mem in reversed(other.get("memory", [])):
            if mem.get("type") != "observation":
                continue
            fx = mem.get("effects", {})
            if fx.get("observed") != "items":
                continue
            obs_loc = fx.get("location_id")
            if not obs_loc:
                continue
            obs_types = frozenset(fx.get("item_types", []))
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
            obs_turn = mem.get("world_turn", 0)
            if obs_turn <= resolved_turns.get(obs_loc, -1):
                continue

            # Compute in-game date/time of the informant's observation so the
            # asking agent remembers *when* the intel was gathered.
            _obs_time_label = _turn_to_time_label(obs_turn)

            obs_loc_name = state.get("locations", {}).get(obs_loc, {}).get("name", obs_loc)
            current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)
            matched_types = sorted(obs_types.intersection(item_types))

            if (other_id, obs_loc) in already_known:
                # Entry already exists — update in-place if this intel is fresher.
                for _em in agent.get("memory", []):
                    _efx = _em.get("effects", {})
                    if (
                        _efx.get("action_kind") == "intel_from_stalker"
                        and _efx.get("source_agent_id") == other_id
                        and _efx.get("location_id") == obs_loc
                        and _efx.get("observed") == "items"
                    ):
                        if obs_turn > _efx.get("obs_world_turn", 0):
                            _efx["obs_world_turn"] = obs_turn
                            _efx["item_types"] = matched_types
                            _em["world_turn"] = world_turn
                            _em["summary"] = (
                                f"{other_name} рассказал, что видел {item_category_name} "
                                f"в «{obs_loc_name}» (в {current_loc_name}). "
                                f"Видел {_obs_time_label}."
                            )
                        break
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
    for mem in agent.get("memory", []):
        fx = mem.get("effects", {})
        if fx.get("action_kind") == "intel_from_stalker" and fx.get("observed") == "agent_location":
            already_known.add((fx.get("source_agent_id"), fx.get("location_id")))

    # Collect exhausted intel locations (base locations whose full neighbourhood
    # has already been searched and target was not found there).
    exhausted_locs: set = set()
    for mem in agent.get("memory", []):
        fx = mem.get("effects", {})
        if (fx.get("action_kind") == "hunt_area_exhausted"
                and fx.get("target_id") == target_agent_id):
            exhausted_locs.add(fx.get("location_id"))

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

        for mem in reversed(other.get("memory", [])):
            if mem.get("type") != "observation":
                continue
            fx = mem.get("effects", {})
            if fx.get("observed") != "stalkers":
                continue
            obs_loc = fx.get("location_id")
            if not obs_loc:
                continue
            names_seen = fx.get("names", [])
            if target_agent_name not in names_seen:
                continue
            if obs_loc in exhausted_locs:
                continue
            if obs_loc in seen_locs_this_stalker:
                continue

            obs_turn = mem.get("world_turn", 0)
            _obs_time_label = _turn_to_time_label(obs_turn)
            obs_loc_name = state.get("locations", {}).get(obs_loc, {}).get("name", obs_loc)
            current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)

            if (other_id, obs_loc) in already_known:
                # Entry already exists — update in-place if this intel is fresher.
                for _em in agent.get("memory", []):
                    _efx = _em.get("effects", {})
                    if (
                        _efx.get("action_kind") == "intel_from_stalker"
                        and _efx.get("source_agent_id") == other_id
                        and _efx.get("location_id") == obs_loc
                        and _efx.get("observed") == "agent_location"
                    ):
                        if obs_turn > _efx.get("obs_world_turn", 0):
                            _efx["obs_world_turn"] = obs_turn
                            _em["world_turn"] = world_turn
                            _em["summary"] = (
                                f"{other_name} рассказал, что видел «{target_agent_name}» "
                                f"в «{obs_loc_name}» (в {current_loc_name}). "
                                f"Видел {_obs_time_label}."
                            )
                        break
                seen_locs_this_stalker.add(obs_loc)
                if first_loc is None:
                    first_loc = obs_loc
                continue

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
    for mem in agent.get("memory", []):
        if mem.get("world_turn") != world_turn:
            continue
        fx = mem.get("effects", {})
        if (fx.get("action_kind") == "intel_from_trader"
                and fx.get("target_agent_id") == target_id):
            return False  # Already bought this turn

    target = state.get("agents", {}).get(target_id)
    if not target or not target.get("is_alive", True):
        return False  # Target is dead or unknown

    target_loc = target.get("location_id")
    if not target_loc:
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
        },
        summary=(
            f"Я купил у торговца «{trader_name}» информацию о местонахождении "
            f"«{target_name}» за {_HUNT_INTEL_PRICE} руб. "
            f"По данным торговца, цель сейчас в «{target_loc_name}»."
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
    for mem in agent.get("memory", []):
        fx = mem.get("effects", {})
        if (fx.get("action_kind") == "hunt_area_exhausted"
                and fx.get("target_id") == target_agent_id):
            exhausted_locs.add(fx.get("location_id"))

    best_loc: Optional[str] = None
    best_turn: int = -1
    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        fx = mem.get("effects", {})
        action_kind = fx.get("action_kind")

        # Source 1: stalker / trader location intel
        if action_kind in ("intel_from_stalker", "intel_from_trader"):
            if fx.get("observed") != "agent_location":
                continue
            if fx.get("target_agent_id") != target_agent_id:
                continue
            obs_loc = fx.get("location_id")
            if not obs_loc or obs_loc in exhausted_locs:
                continue
            t = mem.get("world_turn", 0)
            if t > best_turn:
                best_turn = t
                best_loc = obs_loc

        # Source 2: retreat_observed — the target was seen fleeing to to_location
        elif action_kind == "retreat_observed":
            if fx.get("subject") != target_agent_id:
                continue
            retreat_dest = fx.get("to_location")
            if not retreat_dest or retreat_dest in exhausted_locs:
                continue
            t = mem.get("world_turn", 0)
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
    for mem in agent.get("memory", []):
        if mem.get("world_turn", 0) < since_turn:
            continue
        fx = mem.get("effects", {})
        if (fx.get("action_kind") == "hunt_location_searched"
                and fx.get("target_id") == target_agent_id):
            loc = fx.get("location_id")
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


def _run_bot_decision_v2(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """V2 decision engine entry point — wraps inner function and emits bot_decision event on goal change."""
    prev_goal = agent.get("current_goal")
    events = _run_bot_decision_v2_inner(agent_id, agent, state, world_turn)
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
            agent["action_used"] = True
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
                    agent["action_used"] = True
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
            agent["action_used"] = True
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)

    return None  # nothing to do — run the main pipeline


def _run_bot_decision_v2_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Core V2 decision pipeline: Context → Needs → Intent → Plan → Execute."""
    from app.games.zone_stalkers.decision.context_builder import build_agent_context
    from app.games.zone_stalkers.decision.needs import evaluate_needs
    from app.games.zone_stalkers.decision.intents import select_intent
    from app.games.zone_stalkers.decision.planner import build_plan
    from app.games.zone_stalkers.decision.executors import execute_plan_step

    # ── Commitment logic: handle scheduled arrivals first ─────────────────
    arrival_evs = _bot_pickup_on_arrival(agent_id, agent, state, world_turn)
    if arrival_evs:
        return arrival_evs
    sell_evs = _bot_sell_on_arrival(agent_id, agent, state, world_turn)
    if sell_evs:
        return sell_evs

    # ── Check and handle global goal completion ────────────────────────────
    if not agent.get("has_left_zone") and agent.get("is_alive", True):
        if not agent.get("global_goal_achieved"):
            _check_global_goal_completion(agent_id, agent, state, world_turn)
        if agent.get("global_goal_achieved"):
            loc = state.get("locations", {}).get(agent.get("location_id", ""), {})
            if loc.get("exit_zone"):
                return _execute_leave_zone(agent_id, agent, state, world_turn)
            return _bot_route_to_exit(agent_id, agent, state, world_turn)

    # ── Pre-decision: phase-independent equipment maintenance ─────────────
    # Equip / pick-up / seek from memory happen before the needs pipeline so
    # that Phase-1 resource-gathering is not blocked by reload_or_rearm=1.0.
    _eq_evs = _pre_decision_equipment_maintenance(agent_id, agent, state, world_turn)
    if _eq_evs is not None:
        return _eq_evs

    # ── V2 pipeline ────────────────────────────────────────────────────────
    ctx = build_agent_context(agent_id, agent, state)
    needs = evaluate_needs(ctx, state)
    intent = select_intent(ctx, needs, world_turn)
    plan = build_plan(ctx, intent, state, world_turn)

    # Compute needs dict once (reused below)
    _needs_dict = asdict(needs)

    # Store context for observability / debug
    agent["_v2_context"] = {
        "need_scores": _needs_dict,
        "intent_kind": intent.kind,
        "intent_score": round(intent.score, 3),
        "intent_reason": intent.reason,
        "plan_intent": plan.intent_kind,
        "plan_steps": len(plan.steps),
        "plan_confidence": round(plan.confidence, 3),
        "plan_step_0": plan.steps[0].kind if plan.steps else None,
    }

    # Update current_goal from intent BEFORE writing the decision memory entry
    # so that memory accurately reflects the agent's new goal. (Fix 3)
    agent["current_goal"] = _INTENT_TO_GOAL.get(intent.kind, agent.get("current_goal", "idle"))

    # Write a decision memory entry when the intent kind changes.
    # We skip writing when intent is the same as the last decision entry to avoid
    # flooding the log with identical entries every tick.
    # Only look at entries that are themselves v2_decision records; other
    # decision entries (e.g. wait_in_shelter, seek_item, …) do not carry an
    # intent_kind field and would cause the dedup guard to fail every tick.
    _prev_decision_intent = next(
        (m.get("effects", {}).get("intent_kind")
         for m in reversed(agent.get("memory", []))
         if m.get("type") == "decision"
         and m.get("effects", {}).get("action_kind") == "v2_decision"),
        None,
    )
    if _prev_decision_intent != intent.kind:
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
        _intent_label = _INTENT_LABEL_RU.get(intent.kind, intent.kind)
        _add_memory(
            agent, world_turn, state, "decision",
            f"🧠 {_intent_label}",
            {
                "action_kind": "v2_decision",
                "intent_kind": intent.kind,
                "intent_score": round(intent.score, 3),
                "plan_step": _step0_kind,
                "plan_steps_count": len(plan.steps),
            },
            summary=(
                f"Намерение «{intent.kind}» ({round(intent.score * 100)}%)."
                + (f" {intent.reason}." if intent.reason else "")
                + f" Топ потребности: {_needs_str}"
            ),
        )

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=world_turn,
        intent_kind=intent.kind,
        intent_score=float(intent.score),
        reason=intent.reason,
        state=state,
    )

    return execute_plan_step(ctx, plan, state, world_turn)


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
        wealth = _agent_wealth(agent)
        target = agent.get("wealth_goal_target", GET_RICH_COMPLETION_MIN)
        if wealth >= target:
            agent["global_goal_achieved"] = True
            _add_memory(
                agent, world_turn, state, "observation",
                "💰 Цель достигнута: разбогател!",
                {"action_kind": "goal_achieved", "goal": "get_rich",
                 "wealth": wealth, "target": target},
                summary=f"Я достиг своей цели — разбогател! Моё состояние: {wealth} руб. Пора покидать Зону",
            )
    elif global_goal == "unravel_zone_mystery":
        has_doc = any(
            i.get("type") in _SECRET_TYPES
            for i in agent.get("inventory", [])
        )
        if has_doc:
            agent["global_goal_achieved"] = True
            _add_memory(
                agent, world_turn, state, "observation",
                "🔍 Цель достигнута: тайна раскрыта!",
                {"action_kind": "goal_achieved", "goal": "unravel_zone_mystery"},
                summary="Я нашёл секретный документ и раскрыл тайну Зоны. Пора покидать!",
            )
    elif global_goal == "kill_stalker":
        target_id = agent.get("kill_target_id")
        if target_id:
            target_agent = state.get("agents", {}).get(target_id, {})
            # Goal achieved when the target is confirmed dead in the current game state.
            # Checking is_alive directly (rather than memory) ensures old fake kill-stub
            # entries from previous save states do not falsely complete the goal.
            target_is_dead = not target_agent.get("is_alive", True) if target_agent else False
            if target_is_dead:
                agent["global_goal_achieved"] = True
                target_name = target_agent.get("name", target_id)
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"⚔️ Цель достигнута: «{target_name}» устранён!",
                    {"action_kind": "goal_achieved", "goal": "kill_stalker", "target_id": target_id},
                    summary=f"Я выполнил задание — устранил «{target_name}». Пора покидать Зону!",
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
    # Dijkstra to nearest exit_zone
    _heap: List = [(0, 0, loc_id)]
    _dist: Dict[str, int] = {}
    _best_exit: Optional[str] = None
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

    if _best_exit is None:
        # No exit found — log once
        _last_kind = None
        for _m in reversed(agent.get("memory", [])):
            if _m.get("type") == "decision":
                _last_kind = _m.get("effects", {}).get("action_kind")
                break
        if _last_kind != "no_exit_found":
            _add_memory(
                agent, world_turn, state, "decision",
                "🚪 Нет выхода из Зоны",
                {"action_kind": "no_exit_found"},
                summary="Я хочу покинуть Зону, но не могу найти выход",
            )
        return []

    # Write "heading to exit" decision once
    _last_kind = None
    for _m in reversed(agent.get("memory", [])):
        if _m.get("type") == "decision":
            _last_kind = _m.get("effects", {}).get("action_kind")
            break
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
    loc_id = agent.get("location_id", "")
    locations = state.get("locations", {})
    loc = locations.get(loc_id, {})
    exit_name = loc.get("name", loc_id)
    agent["has_left_zone"] = True
    # Remove agent from the exit location's agent list
    if agent_id in loc.get("agents", []):
        loc["agents"].remove(agent_id)
    _add_memory(
        agent, world_turn, state, "observation",
        "🚪 Покинул Зону",
        {"action_kind": "left_zone", "exit_location": loc_id, "exit_name": exit_name},
        summary=f"Я покинул Зону через «{exit_name}»",
    )
    return [{"event_type": "agent_left_zone",
             "payload": {"agent_id": agent_id, "exit_location": loc_id}}]

# ─── Backwards-compatibility aliases (v1 → v2 migration) ─────────────────────
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
    """Backwards-compat alias → ``_run_bot_decision_v2``."""
    return _run_bot_decision_v2(agent_id, agent, state, world_turn)


def _run_bot_action_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Backwards-compat: delegates to _bot_pursue_goal for goal-specific v1 behavior.

    This function is called by tests that expect v1-style memory entries
    (seek_item, wander, wait_at_trader, etc.).  Using _bot_pursue_goal
    rather than _run_bot_decision_v2_inner ensures those memory entries
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
    from app.games.zone_stalkers.decision.needs import evaluate_needs
    from app.games.zone_stalkers.decision.intents import select_intent
    world_turn = state.get("world_turn", 0)
    agent_id = agent.get("id") or next(
        (aid for aid, a in state.get("agents", {}).items() if a is agent), "unknown"
    )
    try:
        ctx = build_agent_context(agent_id, agent, state)
        needs = evaluate_needs(ctx, state)
        intent = select_intent(ctx, needs, world_turn)
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
    # Default: delegate to v2
    return _run_bot_decision_v2_inner(agent_id, agent, state, world_turn)


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
        agent["action_used"] = True
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
        agent["action_used"] = True
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, hunt_loc, state, world_turn)

    # 4. At trader location → try to buy intel; if broke → wait
    trader = _find_trader_at_location(loc_id, state) or next(
        (t for t in state.get('traders', {}).values() if t.get('location_id') == loc_id and t.get('is_alive', True)),
        None)
    if trader:
        # Anti-spam: if last decision was hunt_wait_at_trader, don't repeat
        for mem in reversed(agent.get("memory", [])):
            if mem.get("type") == "decision":
                if mem.get("effects", {}).get("action_kind") == "hunt_wait_at_trader":
                    agent["action_used"] = True
                    return []
                break  # last decision is different — may write
        bought = _bot_buy_hunt_intel_from_trader(
            agent_id, agent, target_id, target_name, state, world_turn
        )
        agent["action_used"] = True
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, nearest_trader_loc, state, world_turn)

    # 6. No intel, no trader anywhere → wait
    agent["action_used"] = True
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
    agent["action_used"] = True
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
        agent["action_used"] = True
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, new_intel_loc, state, world_turn)

    # 4. If at trader → wait (anti-spam)
    trader = _find_trader_at_location(loc_id, state) or next(
        (t for t in state.get('traders', {}).values() if t.get('location_id') == loc_id and t.get('is_alive', True)),
        None)
    if trader:
        last_wait = None
        for mem in reversed(agent.get("memory", [])):
            if mem.get("type") == "decision":
                last_wait = mem.get("effects", {}).get("action_kind")
                break
        if last_wait != "wait_at_trader":
            _add_memory(
                agent, world_turn, state, "decision",
                "⏳ Жду у торговца (документы)",
                {"action_kind": "wait_at_trader", "location_id": loc_id},
                summary="Жду у торговца новостей о документах",
            )
        agent["action_used"] = True
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, nearest_trader_loc, state, world_turn)

    # 6. Wander toward dungeon/x_lab if available
    locations = state.get("locations", {})
    search_terrains = frozenset({"x_lab", "dungeon", "industrial", "military_buildings"})
    best_loc = None
    for conn in loc.get("connections", []) if False else []:  # conn iteration placeholder
        pass
    reachable = _dijkstra_reachable_locations(loc_id, locations, max_minutes=6 * 60 * MINUTES_PER_TURN)
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
        agent["action_used"] = True
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, next_loc, state, world_turn)

    agent["action_used"] = True
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
                agent["action_used"] = True
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
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    # Find best reachable anomaly location by anomaly_activity score
    reachable = _dijkstra_reachable_locations(loc_id, locations, max_minutes=6 * 60 * MINUTES_PER_TURN)
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
        agent["action_used"] = True
        return _bot_schedule_travel(agent_id, agent, best_loc_id, state, world_turn)

    agent["action_used"] = True
    return []
