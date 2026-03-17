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
from typing import Any, Dict, List, Optional, Tuple


# 1 game turn = 1 real minute
MINUTES_PER_TURN = 1

# Derived constants (update MINUTES_PER_TURN above to rescale the whole system)
_HOUR_IN_TURNS = 60 // MINUTES_PER_TURN       # turns needed to pass 1 in-game hour
EXPLORE_DURATION_TURNS = 30 // MINUTES_PER_TURN  # turns needed for a 30-min exploration
DEFAULT_SLEEP_HOURS = 6                         # default hours of sleep when no 'hours' key is present in sched

# Agent memory cap — oldest entries are dropped when this limit is exceeded.
MAX_AGENT_MEMORY = 2000

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
            agent["hunger"] = min(100, agent.get("hunger", 0) + 3)
            agent["thirst"] = min(100, agent.get("thirst", 0) + 5)
            agent["sleepiness"] = min(100, agent.get("sleepiness", 0) + 4)
            # Critical thirst causes HP damage faster than hunger
            if agent.get("thirst", 0) >= 80:
                agent["hp"] = max(0, agent["hp"] - 2)
            if agent.get("hunger", 0) >= 80:
                agent["hp"] = max(0, agent["hp"] - 1)
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

    # 3. AI bot agent decisions (bots without a scheduled action)
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
        bot_evs = _run_bot_action(agent_id, agent, state, world_turn)
        events.extend(bot_evs)

    # 3b. Per-turn location observations for every alive stalker agent.
    # Writes a new observation entry only when content has changed since the last
    # entry of the same category (deduplication prevents memory flooding).
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):  # departed agents are no longer at any location
            continue
        if agent.get("archetype") != "stalker_agent":
            continue
        loc_id = agent.get("location_id")
        if loc_id:
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
    entities enter or leave the agent's location.  Deduplication ensures a new entry
    is only appended when the observed content has changed since the last entry of the
    same category.
    """
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
    if stalker_names and stalker_names != _last_obs_content(agent, "stalkers", loc_id):
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
    if mutant_names and mutant_names != _last_obs_content(agent, "mutants", loc_id):
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
    if item_types and item_types != _last_obs_content(agent, "items", loc_id):
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
    # knows this turn so we don't write duplicate entries.
    already_known: set = set()
    for mem in agent.get("memory", []):
        if mem.get("world_turn") != world_turn:
            continue
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

            # Skip if already known from this turn (don't re-write).
            if (other_id, obs_loc) in already_known:
                continue  # keep scanning — other locations may still be new

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

    # Build set of (source_agent_id, loc_id) pairs already known this turn.
    already_known: set = set()
    for mem in agent.get("memory", []):
        if mem.get("world_turn") != world_turn:
            continue
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
            if (other_id, obs_loc) in already_known:
                continue
            if obs_loc in seen_locs_this_stalker:
                continue

            obs_turn = mem.get("world_turn", 0)
            _obs_time_label = _turn_to_time_label(obs_turn)
            obs_loc_name = state.get("locations", {}).get(obs_loc, {}).get("name", obs_loc)
            current_loc_name = state.get("locations", {}).get(loc_id, {}).get("name", loc_id)

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


def _run_bot_action(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """
    Make a goal-directed decision for a bot-controlled stalker agent.

    Decision layers:
      EMERGENCY – Heal / eat / drink (always overrides goal logic)
      SURVIVAL  – Sleep when exhausted
      EQUIPMENT – Initial equipment acquisition (no weapon/armor/ammo)
      GOAL      – If wealth < material_threshold: gather resources
                  If wealth >= material_threshold: try equipment upgrade
                  Then: pursue global_goal
    """
    prev_goal = agent.get("current_goal")
    events = _run_bot_action_inner(agent_id, agent, state, world_turn)
    new_goal = agent.get("current_goal")
    # Emit a bot_decision event whenever the bot's current_goal changes so that
    # debug_advance_turns can detect when a meaningful decision occurred.
    if new_goal and new_goal != prev_goal:
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


def _run_bot_action_inner(
    agent_id: str,
    agent: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    loc_id = agent.get("location_id")
    locations = state.get("locations", {})
    loc = locations.get(loc_id, {})
    rng = random.Random(agent_id + str(world_turn))
    inventory = agent.get("inventory", [])

    # ── EMISSION ESCAPE: Flee dangerous terrain when emission is active or imminent ──
    # "Imminent" is now determined by whether the agent received an
    # ``emission_imminent`` observation memory that is newer than any
    # ``emission_ended`` observation (i.e. the warning hasn't been superseded).
    # Fallback: if emission is already active the bot also flees regardless of memory.
    _emission_active = state.get("emission_active", False)
    _on_dangerous_terrain = loc.get("terrain_type", "") in _EMISSION_DANGEROUS_TERRAIN
    _emission_warned = False
    if not _emission_active:
        # Check agent memory for a live emission_imminent observation
        _last_ended_turn: int = 0
        _last_imminent_turn: int = 0
        for _mem in agent.get("memory", []):
            if _mem.get("type") != "observation":
                continue
            _mem_kind = _mem.get("effects", {}).get("action_kind")
            _mem_turn = _mem.get("world_turn", 0)
            if _mem_kind == "emission_ended" and _mem_turn > _last_ended_turn:
                _last_ended_turn = _mem_turn
            elif _mem_kind == "emission_imminent" and _mem_turn > _last_imminent_turn:
                _last_imminent_turn = _mem_turn
        _emission_warned = _last_imminent_turn > _last_ended_turn
    if _on_dangerous_terrain and (_emission_active or _emission_warned):
        # ── Dijkstra to find the FASTEST (min travel-time) safe location ─────────
        # Priority queue: (minutes, hops, loc_id).  Explored once per node.
        _dijk_heap: List = [(0, 0, loc_id)]
        _dijk_dist: Dict[str, int] = {}     # loc_id → min minutes to reach
        _safe_candidates: List = []          # list of (minutes, hops, loc_id)
        _best_minutes: Optional[int] = None

        while _dijk_heap:
            _cur_min, _cur_hops, _cur_id = heapq.heappop(_dijk_heap)
            if _cur_id in _dijk_dist:
                continue
            _dijk_dist[_cur_id] = _cur_min
            # Once current cost exceeds best, no better candidates exist
            if _best_minutes is not None and _cur_min > _best_minutes:
                break
            # Check if this is a safe location (skip start node)
            if _cur_id != loc_id:
                _nxt_terrain = locations.get(_cur_id, {}).get("terrain_type", "")
                if _nxt_terrain not in _EMISSION_DANGEROUS_TERRAIN:
                    _safe_candidates.append((_cur_min, _cur_hops, _cur_id))
                    if _best_minutes is None:
                        _best_minutes = _cur_min
                    continue  # Don't expand from safe terrain
            # Expand neighbors
            for _conn in locations.get(_cur_id, {}).get("connections", []):
                if _conn.get("closed"):
                    continue
                _nxt = _conn["to"]
                if _nxt in _dijk_dist:
                    continue
                _edge_min = _conn.get("travel_time", 12) * MINUTES_PER_TURN
                _nxt_min = _cur_min + _edge_min
                if _best_minutes is None or _nxt_min <= _best_minutes:
                    heapq.heappush(_dijk_heap, (_nxt_min, _cur_hops + 1, _nxt))

        if _safe_candidates:
            # Pick deterministically: smallest ID among ties at minimum travel-time
            target = min(c[2] for c in _safe_candidates)
            hops = next(c[1] for c in _safe_candidates if c[2] == target)
            minutes_to_shelter = _best_minutes if _best_minutes is not None else hops * 12
            _shelter_name = locations.get(target, {}).get("name", target)
            _emission_reason = "идёт выброс" if _emission_active else "скоро будет выброс"
            agent["current_goal"] = "flee_emission"
            _add_memory(
                agent, world_turn, state, "decision",
                "⚡ Бегу от выброса!",
                {
                    "action_kind": "flee_emission",
                    "target_id": target,
                    "target_name": _shelter_name,
                    "hops_to_shelter": hops,
                    "minutes_to_shelter": minutes_to_shelter,
                },
                summary=f"Я решил бежать к укрытию «{_shelter_name}» (~{minutes_to_shelter} мин), потому что {_emission_reason}",
            )
            return _bot_schedule_travel(agent_id, agent, target, state, world_turn, emergency_flee=True)
        else:
            # Trapped on dangerous terrain — no safe location reachable via open connections.
            # Log once so the agent's memory reflects the grim situation.
            _last_trapped_kind = None
            for _sm in reversed(agent.get("memory", [])):
                if _sm.get("type") == "decision":
                    _last_trapped_kind = _sm.get("effects", {}).get("action_kind")
                    break
            if _last_trapped_kind != "trapped_on_dangerous_terrain":
                _add_memory(
                    agent, world_turn, state, "decision",
                    "☠️ Нет пути к укрытию",
                    {
                        "action_kind": "trapped_on_dangerous_terrain",
                        "current_location": agent.get("location_id"),
                        "current_terrain": loc.get("terrain_type", "unknown"),
                    },
                    summary=f"Нет пути к укрытию — застрял на опасной местности, {'идёт выброс' if _emission_active else 'скоро будет выброс'}",
                )
            return []

    # ── EMISSION SHELTER: Stay put when emission is active or imminent ────────
    # Second-highest priority (after fleeing dangerous terrain, before any pending
    # tasks or new decisions).  If the agent is already on safe terrain but knows
    # an emission is coming (or is ongoing), it must NOT do anything — doing so
    # risks arriving on dangerous terrain when the emission fires.  The agent
    # simply waits until it sees an ``emission_ended`` observation.
    # Emission is an "urgent trigger" that overrides all pending arrival tasks.
    if (_emission_active or _emission_warned) and not _on_dangerous_terrain:
        # Only write the decision memory once; avoid flooding the log.
        _last_decision_kind = None
        for _sm in reversed(agent.get("memory", [])):
            if _sm.get("type") == "decision":
                _last_decision_kind = _sm.get("effects", {}).get("action_kind")
                break
        if _last_decision_kind != "wait_in_shelter":
            _add_memory(
                agent, world_turn, state, "decision",
                "🛡️ Жду в укрытии",
                {
                    "action_kind": "wait_in_shelter",
                    "current_location": agent.get("location_id"),
                    "current_terrain": loc.get("terrain_type", "unknown"),
                },
                summary=f"Я решил оставаться в укрытии и ждать, потому что {'идёт выброс' if _emission_active else 'скоро начнётся выброс'}",
            )
        return []

    # ── GLOBAL GOAL: detect achievement and leave zone ─────────────────────────
    if not agent.get("has_left_zone") and agent.get("is_alive", True):
        if not agent.get("global_goal_achieved"):
            _check_global_goal_completion(agent_id, agent, state, world_turn)
        if agent.get("global_goal_achieved"):
            if loc.get("exit_zone"):
                return _execute_leave_zone(agent_id, agent, state, world_turn)
            return _bot_route_to_exit(agent_id, agent, state, world_turn)

    # ── ARRIVAL COMMITMENT: pick up the item we travelled here for ────────────
    # When the most recent decision was a seek_item whose destination is the
    # current location, immediately attempt to pick up the sought item before
    # re-evaluating priorities.  This prevents a higher-priority Need from
    # redirecting the agent on the very tick it arrives, without ever collecting
    # what it came for.
    _arrival_events = _bot_pickup_on_arrival(agent_id, agent, state, world_turn)
    if _arrival_events:
        return _arrival_events

    # ── EMERGENCY: Heal ────────────────────────────────────────────────────────
    if agent.get("hp", 100) <= 30:
        heal_item = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
        if heal_item:
            return _bot_consume(agent_id, agent, heal_item, world_turn, state, "consume_heal")
        # No heal item — try to buy from a nearby trader
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(
                agent_id, agent, HEAL_ITEM_TYPES, state, world_turn,
                purchase_reason=f"критически низкое HP ({agent.get('hp', 0)}%)",
            )
            if bought:
                return bought
        elif trader_loc is not None and _can_afford_cheapest(agent, HEAL_ITEM_TYPES):
            trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду к торговцу за аптечкой (экстренно)",
                {"action_kind": "seek_item", "item_category": "medical",
                 "destination": trader_loc, "emergency": True,
                 "hp": agent.get("hp", 0)},
                summary=f"Я решил идти к торговцу в «{trader_loc_name}» за аптечкой, потому что HP {agent.get('hp', 0)}% — критически мало",
            )
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)
        # No trader reachable (or can't afford) — flee to low-anomaly neighbor
        safe_neighbors = [
            c["to"] for c in loc.get("connections", [])
            if not c.get("closed")
            and locations.get(c["to"], {}).get("anomaly_activity", 5) <= 3
        ]
        if safe_neighbors:
            return _bot_schedule_travel(
                agent_id, agent, rng.choice(safe_neighbors), state, world_turn
            )

    # ── EMERGENCY: Eat ────────────────────────────────────────────────────────
    if agent.get("hunger", 0) >= 70:
        food = next((i for i in inventory if i["type"] in FOOD_ITEM_TYPES), None)
        if food:
            return _bot_consume(agent_id, agent, food, world_turn, state, "consume_food")
        # No food — try to buy from a nearby trader
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, FOOD_ITEM_TYPES, state, world_turn,
                                           purchase_reason=f"сильный голод ({agent.get('hunger', 0)}%)")
            if bought:
                return bought
        elif trader_loc is not None and _can_afford_cheapest(agent, FOOD_ITEM_TYPES):
            trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду к торговцу за едой (экстренно)",
                {"action_kind": "seek_item", "item_category": "food",
                 "destination": trader_loc, "emergency": True,
                 "hunger": agent.get("hunger", 0)},
                summary=f"Я решил идти к торговцу в «{trader_loc_name}» за едой, потому что голод {agent.get('hunger', 0)}% — срочно нужна еда",
            )
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── EMERGENCY: Drink ──────────────────────────────────────────────────────
    if agent.get("thirst", 0) >= 70:
        drink = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
        if drink:
            return _bot_consume(agent_id, agent, drink, world_turn, state, "consume_drink")
        # No drink — try to buy from a nearby trader
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, DRINK_ITEM_TYPES, state, world_turn,
                                           purchase_reason=f"сильная жажда ({agent.get('thirst', 0)}%)")
            if bought:
                return bought
        elif trader_loc is not None and _can_afford_cheapest(agent, DRINK_ITEM_TYPES):
            trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду к торговцу за водой (экстренно)",
                {"action_kind": "seek_item", "item_category": "drink",
                 "destination": trader_loc, "emergency": True,
                 "thirst": agent.get("thirst", 0)},
                summary=f"Я решил идти к торговцу в «{trader_loc_name}» за водой, потому что жажда {agent.get('thirst', 0)}% — срочно нужна вода",
            )
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── ARRIVAL COMMITMENT: sell artifacts at the trader we travelled to ──────
    # If the agent's most recent decision was to travel to a trader specifically
    # to sell artifacts (action_kind == "sell_at_trader"), complete that task
    # now before evaluating other needs.  This ensures the agent finishes what
    # it came for instead of being side-tracked by e.g. equipment purchases.
    # Life-threatening emergencies above still override this (they run first).
    _sell_arrival_evs = _bot_sell_on_arrival(agent_id, agent, state, world_turn)
    if _sell_arrival_evs:
        return _sell_arrival_evs

    # ── EQUIPMENT MAINTENANCE ─────────────────────────────────────────────────
    # High-priority: ensure the agent is always armed, armored and has basic
    # supplies.  For each need the cascade is:
    #   1) Equip/use from inventory
    #   2) Pick up from the ground at the current location
    #   3) Travel to a location remembered as having that item (memory)
    #   4) Buy from a nearby trader / travel to the nearest one
    #      *** Step (4) is skipped when wealth < material_threshold.  Buying
    #          equipment is a last resort; a broke agent should gather resources
    #          first and only purchase once it has passed the wealth gate. ***
    equipment = agent.setdefault("equipment", {})
    _equip_wealth = _agent_wealth(agent)
    _equip_threshold = agent.get("material_threshold", DEFAULT_MATERIAL_THRESHOLD)
    _can_buy_equipment = _equip_wealth >= _equip_threshold

    # Need 1 — Weapon ────────────────────────────────────────────────────────
    if not equipment.get("weapon"):
        # a) equip from inventory
        evs = _bot_equip_from_inventory(agent_id, agent, WEAPON_ITEM_TYPES, "weapon", state, world_turn)
        if evs:
            return evs
        # b) pick up from ground
        evs = _bot_pickup_item_from_ground(agent_id, agent, WEAPON_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, WEAPON_ITEM_TYPES, "weapon")
        # c) travel to remembered item location
        mem_loc = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            agent["current_goal"] = "get_weapon"
            _add_memory(
                agent, world_turn, state, "decision",
                "Ищу оружие по памяти",
                {"action_kind": "seek_item", "item_category": "weapon", "destination": mem_loc},
                summary=f"Я решил идти искать оружие в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет оружия и помню, где видел",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
        # d) buy from trader or travel to one (only when wealth >= threshold)
        if _can_buy_equipment:
            trader_loc = _find_nearest_trader_location(loc_id, state)
            if trader_loc == loc_id:
                bought = _bot_buy_from_trader(agent_id, agent, WEAPON_ITEM_TYPES, state, world_turn,
                                              purchase_reason="нет оружия")
                if bought:
                    return bought
            elif trader_loc is not None and _can_afford_cheapest(agent, WEAPON_ITEM_TYPES):
                agent["current_goal"] = "get_weapon"
                _add_memory(
                    agent, world_turn, state, "decision",
                    "Иду к торговцу за оружием",
                    {"action_kind": "buy_item", "item_category": "weapon", "destination": trader_loc},
                    summary=f"Я решил идти к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}» за оружием, потому что нет оружия в снаряжении",
                )
                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # Need 2 — Armor ─────────────────────────────────────────────────────────
    if not equipment.get("armor"):
        evs = _bot_equip_from_inventory(agent_id, agent, ARMOR_ITEM_TYPES, "armor", state, world_turn)
        if evs:
            return evs
        evs = _bot_pickup_item_from_ground(agent_id, agent, ARMOR_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, ARMOR_ITEM_TYPES, "armor")
        mem_loc = _find_item_memory_location(agent, ARMOR_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            agent["current_goal"] = "get_armor"
            _add_memory(
                agent, world_turn, state, "decision",
                "Ищу броню по памяти",
                {"action_kind": "seek_item", "item_category": "armor", "destination": mem_loc},
                summary=f"Я решил идти искать броню в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет брони и помню, где видел",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
        # d) buy from trader or travel to one (only when wealth >= threshold)
        if _can_buy_equipment:
            trader_loc = _find_nearest_trader_location(loc_id, state)
            if trader_loc == loc_id:
                bought = _bot_buy_from_trader(agent_id, agent, ARMOR_ITEM_TYPES, state, world_turn,
                                              purchase_reason="нет брони")
                if bought:
                    return bought
            elif trader_loc is not None and _can_afford_cheapest(agent, ARMOR_ITEM_TYPES):
                agent["current_goal"] = "get_armor"
                _add_memory(
                    agent, world_turn, state, "decision",
                    "Иду к торговцу за бронёй",
                    {"action_kind": "buy_item", "item_category": "armor", "destination": trader_loc},
                    summary=f"Я решил идти к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}» за бронёй, потому что нет брони в снаряжении",
                )
                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # Need 3 — Ammo for equipped weapon ───────────────────────────────────────
    _equipped_weapon = equipment.get("weapon")
    if _equipped_weapon:
        _weapon_type = _equipped_weapon.get("type")  # None if missing
        _required_ammo = AMMO_FOR_WEAPON.get(_weapon_type) if _weapon_type else None
        if _required_ammo:
            _required_ammo_set = frozenset({_required_ammo})
            _has_ammo = any(i["type"] == _required_ammo for i in agent.get("inventory", []))
            if not _has_ammo:
                evs = _bot_pickup_item_from_ground(agent_id, agent, _required_ammo_set, state, world_turn)
                if evs:
                    return evs
                _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, _required_ammo_set, "ammo")
                mem_loc = _find_item_memory_location(agent, _required_ammo_set, state)
                if mem_loc and mem_loc != loc_id:
                    agent["current_goal"] = "get_ammo"
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "Ищу патроны по памяти",
                        {"action_kind": "seek_item", "item_category": "ammo",
                         "ammo_type": _required_ammo, "destination": mem_loc},
                        summary=f"Я решил идти искать патроны «{_required_ammo}» в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет боеприпасов и помню, где видел",
                    )
                    return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
                # d) buy from trader or travel to one (only when wealth >= threshold)
                if _can_buy_equipment:
                    trader_loc = _find_nearest_trader_location(loc_id, state)
                    if trader_loc == loc_id:
                        bought = _bot_buy_from_trader(agent_id, agent, _required_ammo_set, state, world_turn,
                                                      purchase_reason=f"нет патронов для {_weapon_type}")
                        if bought:
                            return bought
                    elif trader_loc is not None and _can_afford_cheapest(agent, _required_ammo_set):
                        agent["current_goal"] = "get_ammo"
                        _add_memory(
                            agent, world_turn, state, "decision",
                            "Иду к торговцу за патронами",
                            {"action_kind": "buy_item", "item_category": "ammo",
                             "ammo_type": _required_ammo, "destination": trader_loc},
                            summary=f"Я решил идти к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}» за патронами «{_required_ammo}», потому что нет боеприпасов",
                        )
                        return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # Need 4 — Medicine reserve (pick up from ground, travel to observed location, or buy locally) ──
    _has_heal = any(i["type"] in HEAL_ITEM_TYPES for i in agent.get("inventory", []))
    if not _has_heal:
        evs = _bot_pickup_item_from_ground(agent_id, agent, HEAL_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        # Travel to a location where healing items were observed (observation memory)
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, HEAL_ITEM_TYPES, "medical")
        mem_loc = _find_item_memory_location(agent, HEAL_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду за аптечкой по памяти",
                {"action_kind": "seek_item", "item_category": "medical", "destination": mem_loc},
                summary=f"Я решил идти за медикаментами в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет медикаментов в инвентаре",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
        # Only buy locally — don't travel just for medicine stockpile
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, HEAL_ITEM_TYPES, state, world_turn,
                                           purchase_reason="создаю запас медикаментов")
            if bought:
                return bought

    # Need 5 — Food reserve (pick up from ground, travel to observed location, or buy locally) ──
    _has_food = any(i["type"] in FOOD_ITEM_TYPES for i in agent.get("inventory", []))
    if not _has_food and agent.get("hunger", 0) > 30:
        evs = _bot_pickup_item_from_ground(agent_id, agent, FOOD_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        # Travel to a location where food was observed (observation memory)
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, FOOD_ITEM_TYPES, "food")
        mem_loc = _find_item_memory_location(agent, FOOD_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду за едой по памяти",
                {"action_kind": "seek_item", "item_category": "food", "destination": mem_loc},
                summary=f"Я решил идти за едой в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет еды в инвентаре",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, FOOD_ITEM_TYPES, state, world_turn,
                                           purchase_reason="создаю запас еды")
            if bought:
                return bought

    # Need 6 — Water/drink reserve (pick up from ground, travel to observed location, or buy locally) ──
    _has_drink = any(i["type"] in DRINK_ITEM_TYPES for i in agent.get("inventory", []))
    if not _has_drink and agent.get("thirst", 0) > 30:
        evs = _bot_pickup_item_from_ground(agent_id, agent, DRINK_ITEM_TYPES, state, world_turn)
        if evs:
            return evs
        # Travel to a location where drinks were observed (observation memory)
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, DRINK_ITEM_TYPES, "drink")
        mem_loc = _find_item_memory_location(agent, DRINK_ITEM_TYPES, state)
        if mem_loc and mem_loc != loc_id:
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду за водой по памяти",
                {"action_kind": "seek_item", "item_category": "drink", "destination": mem_loc},
                summary=f"Я решил идти за водой в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}», потому что нет воды в инвентаре",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, DRINK_ITEM_TYPES, state, world_turn,
                                           purchase_reason="создаю запас воды")
            if bought:
                return bought

    # ── SURVIVAL: Sleep ───────────────────────────────────────────────────────
    if agent.get("sleepiness", 0) >= 75:
        _sleep_hours = 6
        _add_memory(
            agent, world_turn, state, "decision",
            "Ложусь спать",
            {"action_kind": "sleep_decision",
             "sleepiness": agent.get("sleepiness", 0), "hours": _sleep_hours},
            summary=f"Я решил поспать {_sleep_hours} часов, потому что сонливость достигла {agent.get('sleepiness', 0)}%",
        )
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": _sleep_hours * _HOUR_IN_TURNS,
            "turns_total": _sleep_hours * _HOUR_IN_TURNS,
            "hours": _sleep_hours,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        events.append({"event_type": "sleep_started", "payload": {"agent_id": agent_id, "hours": _sleep_hours}})
        return events

    # ── TRADING OPPORTUNITY ────────────────────────────────────────────────────
    # If the agent is carrying artifacts, try to sell them:
    #   a) Trader is at current location → sell immediately
    #   b) No local trader → travel to nearest one (all agents, not just get_rich)
    artifacts_held = _agent_artifacts_in_inventory(agent)
    if artifacts_held:
        trader_here = _find_trader_at_location(loc_id, state)
        if trader_here:
            sell_evs = _bot_sell_to_trader(agent_id, agent, trader_here, state, world_turn)
            if sell_evs:
                return sell_evs
        else:
            trader_loc = _find_nearest_trader_location(loc_id, state)
            if trader_loc and trader_loc != loc_id:
                agent["current_goal"] = "sell_artifacts"
                trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
                # Resolve the trader's name for personalised memories
                trader_obj = _find_trader_at_location(trader_loc, state)
                trader_name = trader_obj.get("name", "торговец") if trader_obj else "торговец"

                # Step 4 — plan which artifacts to sell (only when carrying more than one)
                if len(artifacts_held) > 1:
                    art_types = ", ".join(a.get("type", "?") for a in artifacts_held)
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "Планирую продать артефакты",
                        {"action_kind": "plan_sell",
                         "artifact_types": [a.get("type") for a in artifacts_held]},
                        summary=f"Я решил продать артефакты ({len(artifacts_held)} шт.), потому что несу несколько и нужно выбрать что продать",
                    )

                # Step 5 — record the nearest trader found via BFS
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Ближайший торговец: {trader_name}",
                    {"action_kind": "nearest_trader_found",
                     "trader_location": trader_loc, "trader_name": trader_name,
                     "artifacts_count": len(artifacts_held)},
                    summary=f"Нашёл ближайшего торговца — {trader_name} в «{trader_loc_name}» — для продажи {len(artifacts_held)} артефактов",
                )

                # Step 6 — commit to navigating toward the trader
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Иду к торговцу {trader_name}",
                    {"action_kind": "sell_at_trader", "destination": trader_loc,
                     "artifacts_count": len(artifacts_held)},
                    summary=f"Я решил идти к торговцу {trader_name} в «{trader_loc_name}» продавать {len(artifacts_held)} артефактов",
                )

                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── GOAL SELECTION ─────────────────────────────────────────────────────────
    wealth = _agent_wealth(agent)
    threshold = agent.get("material_threshold", DEFAULT_MATERIAL_THRESHOLD)
    global_goal = agent.get("global_goal", "get_rich")

    # All agents accumulate resources until they reach material_threshold first,
    # then switch to equipment upgrade → global goal pursuit.
    if wealth >= threshold:
        # Phase 2a: Equipment upgrade (wealth is sufficient)
        # Before pursuing the global goal, check whether better-matching equipment
        # is available.  Upgrade attempts only fire when the agent already has
        # basic equipment (no point upgrading an empty slot — handled by Phase 1).
        upgrade_evs = _bot_try_upgrade_equipment(
            agent_id, agent, loc_id, state, world_turn
        )
        if upgrade_evs:
            return upgrade_evs
        # Phase 2b: Pursue global goal
        agent["current_goal"] = f"goal_{global_goal}"
        return _bot_pursue_goal(agent_id, agent, global_goal, loc_id, loc, state, world_turn, rng)
    else:
        # Phase 1: Accumulate resources before pursuing global goal
        agent["current_goal"] = "gather_resources"
        return _bot_gather_resources(agent_id, agent, loc_id, loc, state, world_turn, rng)


def _bot_gather_resources(
    agent_id: str,
    agent: Dict[str, Any],
    loc_id: str,
    loc: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """
    Resource-gathering mode: pick up artifacts, explore high-anomaly areas, move to loot-rich locations.
    """
    locations = state.get("locations", {})
    confirmed_empty = _confirmed_empty_locations(agent)

    # G2 — Explore if anomalies are present (must explore to obtain artifacts)
    if loc.get("anomaly_activity", 0) > 0 and loc_id not in confirmed_empty:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую аномальную зону",
            {"action_kind": "explore_decision", "location_id": loc_id,
             "anomaly_activity": loc.get("anomaly_activity", 0)},
            summary=f"Я решил исследовать аномальную зону «{loc.get('name', loc_id)}» (аномальность {loc.get('anomaly_activity', 0)}) для поиска артефактов",
        )
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location",
            "turns_remaining": EXPLORE_DURATION_TURNS,
            "turns_total": EXPLORE_DURATION_TURNS,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    # G3 — Dijkstra search for the best fresh (not confirmed-empty) anomaly location within
    # skill-based radius expressed in travel-minutes.  Uses the same formula as
    # _bot_pursue_goal (Phase 2b) so Phase-1 stalkers are not limited to immediate neighbours.
    _max_gather_search_min = (4 + int(agent.get("skill_stalker", 1))) * _ANOMALY_SEARCH_MINUTES_PER_HOP
    reachable = _dijkstra_reachable_locations(loc_id, locations, max_minutes=_max_gather_search_min)

    def _gather_candidate_score(lid: str, travel_min: float) -> float:
        _agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
        _loc_risk = locations.get(lid, {}).get("anomaly_activity", 0) / 10.0
        _risk_penalty = abs(_loc_risk - _agent_risk) * _ANOMALY_RISK_MISMATCH_PENALTY
        return _score_location(locations.get(lid, {}), "artifacts") - travel_min * _ANOMALY_DISTANCE_PENALTY_PER_MIN - _risk_penalty + rng.random() * _ANOMALY_SCORE_NOISE

    fresh_gather_candidates = [
        (lid, travel_min) for lid, travel_min in reachable.items()
        if locations.get(lid, {}).get("anomaly_activity", 0) > 0
        and lid not in confirmed_empty
    ]
    if fresh_gather_candidates:
        best_lid, best_travel_min = max(fresh_gather_candidates, key=lambda t: _gather_candidate_score(*t))
        best_nb_name = locations.get(best_lid, {}).get("name", best_lid)
        _add_memory(
            agent, world_turn, state, "decision",
            "Двигаюсь к непроверенной аномальной зоне",
            {"action_kind": "move_for_resources", "destination": best_lid,
             "destination_name": best_nb_name,
             "anomaly_activity": locations.get(best_lid, {}).get("anomaly_activity", 0),
             "travel_minutes": round(best_travel_min)},
            summary=f"Я решил идти к непроверенной аномальной зоне «{best_nb_name}» (~{round(best_travel_min)} мин), потому что там можно найти артефакты",
        )
        return _bot_schedule_travel(agent_id, agent, best_lid, state, world_turn)

    # G4 — Fallback: explore current location only if not confirmed empty
    if loc_id not in confirmed_empty and rng.random() < 0.40:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            {"action_kind": "explore_decision", "location_id": loc_id},
            summary=f"Я решил исследовать текущую локацию «{loc.get('name', loc_id)}», потому что нет подходящих аномальных соседей",
        )
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location",
            "turns_remaining": EXPLORE_DURATION_TURNS,
            "turns_total": EXPLORE_DURATION_TURNS,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    agent["action_used"] = True
    return []


def _bot_pursue_goal(
    agent_id: str,
    agent: Dict[str, Any],
    global_goal: str,
    loc_id: str,
    loc: Dict[str, Any],
    state: Dict[str, Any],
    world_turn: int,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """
    Goal-directed mode: behave according to the NPC's global_goal.
    """
    locations = state.get("locations", {})
    connections = [c for c in loc.get("connections", []) if not c.get("closed")]

    if global_goal == "get_rich":
        # ── Explore current location to find artifacts (must go through explore) ──────
        # Artifacts can only be obtained through the explore action, not picked up directly.
        # Stalkers do NOT have omniscient knowledge of which locations have artifacts —
        # they can only explore anomaly zones and learn from memory.
        confirmed_empty = _confirmed_empty_locations(agent)
        if loc.get("anomaly_activity", 0) > 0 and loc_id not in confirmed_empty:
            loc_name = loc.get("name", loc_id)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Исследую «{loc_name}»",
                {"action_kind": "explore_decision", "location_id": loc_id,
                 "anomaly_activity": loc.get("anomaly_activity", 0)},
                summary=f"Я решил исследовать «{loc_name}» в поисках артефактов, потому что стремлюсь разбогатеть",
            )
            agent["scheduled_action"] = {
                "type": "explore_anomaly_location", "turns_remaining": EXPLORE_DURATION_TURNS,
                "turns_total": EXPLORE_DURATION_TURNS,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "exploration_started",
                     "payload": {"agent_id": agent_id, "location_id": loc_id}}]

        # Current location confirmed empty or no anomaly activity here.
        # Dijkstra radius is skill-based: (4 + skill_stalker) × default hop minutes.
        _max_anomaly_search_min = (4 + int(agent.get("skill_stalker", 1))) * _ANOMALY_SEARCH_MINUTES_PER_HOP
        reachable = _dijkstra_reachable_locations(loc_id, locations, max_minutes=_max_anomaly_search_min)

        def _anomaly_candidate_score(lid: str, travel_min: float) -> float:
            _agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
            _loc_risk = locations.get(lid, {}).get("anomaly_activity", 0) / 10.0
            _risk_penalty = abs(_loc_risk - _agent_risk) * _ANOMALY_RISK_MISMATCH_PENALTY
            return _score_location(locations.get(lid, {}), "artifacts") - travel_min * _ANOMALY_DISTANCE_PENALTY_PER_MIN - _risk_penalty + rng.random() * _ANOMALY_SCORE_NOISE

        fresh_candidates = [
            (lid, travel_min) for lid, travel_min in reachable.items()
            if locations.get(lid, {}).get("anomaly_activity", 0) > 0
            and lid not in confirmed_empty
        ]
        if fresh_candidates:
            best_lid, best_travel_min = max(fresh_candidates, key=lambda t: _anomaly_candidate_score(*t))
            best_name = locations.get(best_lid, {}).get("name", best_lid)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Иду в непроверенную аномальную зону «{best_name}»",
                {"action_kind": "move_for_anomaly", "destination": best_lid,
                 "destination_name": best_name,
                 "anomaly_activity": locations.get(best_lid, {}).get("anomaly_activity", 0),
                 "travel_minutes": round(best_travel_min)},
                summary=f"Я решил идти в непроверенную аномальную зону «{best_name}» (~{round(best_travel_min)} мин) для сбора артефактов",
            )
            return _bot_schedule_travel(agent_id, agent, best_lid, state, world_turn)

        # All anomaly locations within the search radius are confirmed empty.
        # If we are already at an anomaly location, stay here and wait for the
        # next emission to respawn artifacts — no point in oscillating between
        # confirmed-empty spots.
        if loc.get("anomaly_activity", 0) > 0:
            _add_memory(
                agent, world_turn, state, "decision",
                "Жду пополнения артефактов на месте",
                {"action_kind": "wait_for_artifacts", "location_id": loc_id},
                summary=f"Я решил остаться и ждать пополнения артефактов в «{loc.get('name', loc_id)}», потому что все известные аномальные зоны уже исследованы",
            )
            agent["action_used"] = True
            return []

        # Not at an anomaly location — go to the best known one to wait.
        all_anomaly_candidates = [
            (lid, travel_min) for lid, travel_min in reachable.items()
            if locations.get(lid, {}).get("anomaly_activity", 0) > 0
        ]
        if all_anomaly_candidates:
            best_lid, best_travel_min = max(all_anomaly_candidates, key=lambda t: _anomaly_candidate_score(*t))
            best_name = locations.get(best_lid, {}).get("name", best_lid)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Все аномальные зоны изучены — иду в «{best_name}» ждать",
                {"action_kind": "move_for_anomaly", "destination": best_lid,
                 "destination_name": best_name,
                 "travel_minutes": round(best_travel_min)},
                summary=f"Я решил идти в «{best_name}» ждать пополнения артефактов, потому что все известные аномальные зоны уже пусты",
            )
            return _bot_schedule_travel(agent_id, agent, best_lid, state, world_turn)

        # No anomaly locations within search radius — move toward the neighbour with highest anomaly activity.
        if connections:
            best = max(connections,
                       key=lambda c: locations.get(c["to"], {}).get("anomaly_activity", 0))
            best_name = locations.get(best["to"], {}).get("name", best["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду в зону с высокой аномальностью",
                {"action_kind": "move_for_anomaly", "destination": best["to"],
                 "destination_name": best_name},
                summary=f"Я решил идти в «{best_name}» — ближайшую доступную зону с высокой аномальностью",
            )
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    if global_goal == "unravel_zone_mystery":
        # ── Разгадать тайну Зоны: найти секретные документы ──────────────────
        # Even if the agent already carries some documents it keeps searching for
        # more — there is no "done" state.  The fact that it has docs is visible
        # in inventory; no separate idle decision is needed.

        # Step 1: Pick up secret documents if they're on the ground right here.
        pickup_evs = _bot_pickup_item_from_ground(agent_id, agent, SECRET_DOCUMENT_ITEM_TYPES, state, world_turn)
        if pickup_evs:
            return pickup_evs

        # Step 2: Check memory for a known location of secret documents.
        _maybe_record_item_not_found(agent, world_turn, state, loc_id, loc, SECRET_DOCUMENT_ITEM_TYPES, "secret_document")
        mem_loc = _find_item_memory_location(agent, SECRET_DOCUMENT_ITEM_TYPES, state)
        if mem_loc:
            mem_loc_name = locations.get(mem_loc, {}).get("name", mem_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                f"🗺️ Иду за секретными документами в «{mem_loc_name}»",
                {"action_kind": "seek_item", "item_category": "secret_document", "destination": mem_loc},
                summary=f"Я решил идти за секретными документами в «{mem_loc_name}», потому что помню, что видел их там",
            )
            return _bot_schedule_travel(agent_id, agent, mem_loc, state, world_turn)

        # Step 3: Ask co-located stalkers about secret documents.
        intel_loc = _bot_ask_colocated_stalkers_about_item(
            agent_id, agent, SECRET_DOCUMENT_ITEM_TYPES, "секретные документы", state, world_turn
        )
        if intel_loc:
            intel_loc_name = locations.get(intel_loc, {}).get("name", intel_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                f"🗺️ Иду за секретными документами (по наводке) в «{intel_loc_name}»",
                {"action_kind": "seek_item", "item_category": "secret_document", "destination": intel_loc},
                summary=f"Я решил идти за секретными документами в «{intel_loc_name}» по наводке от другого сталкера",
            )
            return _bot_schedule_travel(agent_id, agent, intel_loc, state, world_turn)

        # Step 4: No leads, no co-located stalkers to ask.
        # Strategy: go to the nearest trader and wait there for a non-trader
        # stalker to appear; when one shows up, Step 4 will handle asking them.
        # This avoids spamming the decision log with meaningless wander entries.
        _SECRET_DOC_TERRAIN = frozenset({"dungeon", "x_lab", "scientific_bunker", "military_buildings"})

        trader_loc = _find_nearest_trader_location(loc_id, state)

        # Case A: there's a trader somewhere — go to it (or wait there).
        if trader_loc is not None:
            if trader_loc != loc_id:
                # Not yet at trader — travel there.
                trader_loc_name = locations.get(trader_loc, {}).get("name", trader_loc)
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"🏪 Иду к торговцу в «{trader_loc_name}» в поисках информации",
                    {"action_kind": "wait_at_trader", "destination": trader_loc},
                    summary=f"Я решил идти к торговцу в «{trader_loc_name}» в поисках информации о секретных документах",
                )
                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)
            else:
                # Already at trader — idle and wait for a non-trader stalker to appear.
                # Use anti-spam: only write the decision once (while still waiting).
                _last_unravel_decision = None
                for _sm in reversed(agent.get("memory", [])):
                    if _sm.get("type") == "decision":
                        _last_unravel_decision = _sm.get("effects", {}).get("action_kind")
                        break
                if _last_unravel_decision != "wait_at_trader":
                    trader_loc_name = locations.get(trader_loc, {}).get("name", trader_loc)
                    _add_memory(
                        agent, world_turn, state, "decision",
                        f"⏳ Жду у торговца в «{trader_loc_name}» — ищу сталкера с информацией",
                        {"action_kind": "wait_at_trader", "location_id": trader_loc},
                        summary=f"Я решил ждать у торговца в «{trader_loc_name}» — жду сталкера с информацией о секретных документах",
                    )
                agent["action_used"] = True
                return []

        # Case B: no trader reachable — prefer locations with interesting terrain
        # (dungeons, labs) where documents are most likely to be found.
        interesting_connections = [
            c for c in connections
            if locations.get(c["to"], {}).get("terrain_type", "") in _SECRET_DOC_TERRAIN
        ]
        if interesting_connections:
            conn = rng.choice(interesting_connections)
            conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                f"🔍 Ищу секретные документы в «{conn_name}»",
                {"action_kind": "seek_item", "item_category": "secret_document", "destination": conn["to"]},
                summary=f"Я решил идти искать секретные документы в «{conn_name}», потому что нет торговца и нет наводок",
            )
            return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)

        # No leads, no trader, no interesting terrain — wander randomly.
        if connections:
            conn = rng.choice(connections)
            conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                "❓ Брожу в поисках секретных документов",
                {"action_kind": "wander", "destination": conn["to"]},
                summary=f"Я решил случайно двигаться в «{conn_name}» в поисках секретных документов",
            )
            return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)

        agent["action_used"] = True
        return []

    if global_goal == "kill_stalker":
        # ── Устранить сталкера: найти цель и ликвидировать ───────────────────
        # Behaviour:
        #  Step 1 — If the target is at the current location: initiate combat interaction.
        #  Step 2 — If agent has fresh intel about target location: travel there
        #           and search the base location + its direct neighbours one by
        #           one.  Mark the area as exhausted once all neighbours are done.
        #  Step 3 — Ask co-located stalkers if they have seen the target.
        #  Step 4 — No intel: go to nearest trader, wait for a co-located stalker.

        target_id = agent.get("kill_target_id")
        if not target_id:
            # No target set — nothing to do.
            agent["action_used"] = True
            return []

        target = state.get("agents", {}).get(target_id, {})
        target_name = target.get("name", target_id) if target else target_id

        # Step 1: target is here right now — initiate combat interaction
        if target and target.get("location_id") == loc_id and target.get("is_alive", True):
            loc_name = loc.get("name", loc_id)
            # Check if a combat interaction already exists at this location
            existing_combat_id = None
            for _cid, _ci in state.get("combat_interactions", {}).items():
                if (_ci.get("location_id") == loc_id
                        and not _ci.get("ended", False)
                        and (agent_id in _ci.get("participants", {})
                             or target_id in _ci.get("participants", {}))):
                    existing_combat_id = _cid
                    break
            if existing_combat_id is None:
                new_combat_id = f"combat_{loc_id}_{world_turn}"
                state.setdefault("combat_interactions", {})[new_combat_id] = {
                    "id": new_combat_id,
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
                cid = new_combat_id
            else:
                cid = existing_combat_id
                _combat_obj = state["combat_interactions"][cid]
                if agent_id not in _combat_obj["participants"]:
                    _combat_obj["participants"][agent_id] = {
                        "motive": "победить",
                        "enemies": [target_id],
                        "friends": [],
                        "fled": False,
                        "fled_to": None,
                    }
                if target_id not in _combat_obj["participants"]:
                    _combat_obj["participants"][target_id] = {
                        "motive": "выжить",
                        "enemies": [agent_id],
                        "friends": [],
                        "fled": False,
                        "fled_to": None,
                    }
            _add_memory(
                agent, world_turn, state, "decision",
                f"⚔️ Начинаю боевое взаимодействие с «{target_name}»",
                {
                    "action_kind": "combat_initiated",
                    "combat_id": cid,
                    "target_id": target_id,
                    "target_name": target_name,
                    "location_id": loc_id,
                },
                summary=f"Я обнаружил цель — «{target_name}» — в «{loc_name}» и начал боевое взаимодействие.",
            )
            _tgt_agent = state.get("agents", {}).get(target_id, {})
            if _tgt_agent and _tgt_agent.get("controller", {}).get("kind") == "bot":
                _add_memory(
                    _tgt_agent, world_turn, state, "decision",
                    f"⚔️ Вступаю в боевое взаимодействие",
                    {"action_kind": "combat_joined", "combat_id": cid, "motive": "выжить",
                     "enemies": [agent_id], "location_id": loc_id},
                    summary=f"Я вступил в боевое взаимодействие в «{loc_name}» с мотивом «выжить»",
                )
            agent["action_used"] = True
            return [{"event_type": "combat_initiated",
                     "payload": {"initiator_id": agent_id, "target_id": target_id,
                                 "combat_id": cid, "location_id": loc_id}}]


        # Step 2: work through intel about where the target was last seen.
        intel_loc = _find_hunt_intel_location(agent, target_id, state)
        if intel_loc:
            intel_loc_name = locations.get(intel_loc, {}).get("name", intel_loc)

            # Determine which locations belong to this search area:
            # the intel location itself + its direct open neighbours.
            intel_loc_obj = locations.get(intel_loc, {})
            area_locs = {intel_loc} | {
                c["to"] for c in intel_loc_obj.get("connections", [])
                if not c.get("closed")
            }

            # Find the world_turn of the intel entry so we only count searches
            # performed *after* receiving this specific intel.
            intel_turn = 0
            for mem in reversed(agent.get("memory", [])):
                fx = mem.get("effects", {})
                if (fx.get("action_kind") == "intel_from_stalker"
                        and fx.get("observed") == "agent_location"
                        and fx.get("location_id") == intel_loc
                        and fx.get("target_agent_id") == target_id):
                    intel_turn = mem.get("world_turn", 0)
                    break

            searched = _get_searched_locations_for_target(agent, target_id, since_turn=intel_turn)
            unsearched = area_locs - searched

            if loc_id in unsearched:
                # Mark current location as searched this tick.
                _add_memory(
                    agent, world_turn, state, "observation",
                    f"🔍 Осмотрел «{loc.get('name', loc_id)}» — цели нет",
                    {
                        "action_kind": "hunt_location_searched",
                        "target_id": target_id,
                        "location_id": loc_id,
                    },
                    summary=(
                        f"Я обыскал «{loc.get('name', loc_id)}» в поисках "
                        f"«{target_name}», но не нашёл."
                    ),
                )
                agent["action_used"] = True
                return []

            # Travel to the next unsearched location in the area.
            remaining = area_locs - (searched | {loc_id})
            if remaining:
                next_loc = sorted(remaining)[0]  # deterministic
                next_loc_name = locations.get(next_loc, {}).get("name", next_loc)
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"🗺️ Иду в «{next_loc_name}» — искать «{target_name}»",
                    {
                        "action_kind": "hunt_search",
                        "target_id": target_id,
                        "destination": next_loc,
                    },
                    summary=(
                        f"Я решил обыскать «{next_loc_name}» в рамках поиска "
                        f"«{target_name}» по последней наводке."
                    ),
                )
                return _bot_schedule_travel(agent_id, agent, next_loc, state, world_turn)

            # All area locations searched — mark area as exhausted and seek new intel.
            _add_memory(
                agent, world_turn, state, "observation",
                f"❌ Окрестности «{intel_loc_name}» обысканы — цели нет",
                {
                    "action_kind": "hunt_area_exhausted",
                    "target_id": target_id,
                    "location_id": intel_loc,
                },
                summary=(
                    f"Я обыскал «{intel_loc_name}» и все соседние локации в поисках "
                    f"«{target_name}», но не нашёл. Нужна новая информация."
                ),
            )
            # Fall through to Step 3/4 to get fresh intel.

        # Step 3: Ask co-located stalkers whether they've seen the target.
        new_intel_loc = _bot_ask_colocated_stalkers_about_agent(
            agent_id, agent, target_id, target_name, state, world_turn
        )
        if new_intel_loc:
            new_intel_loc_name = locations.get(new_intel_loc, {}).get("name", new_intel_loc)
            _add_memory(
                agent, world_turn, state, "decision",
                f"🗺️ Иду в «{new_intel_loc_name}» (по наводке) — искать «{target_name}»",
                {
                    "action_kind": "hunt_search",
                    "target_id": target_id,
                    "destination": new_intel_loc,
                },
                summary=(
                    f"Я получил наводку о «{target_name}» и иду в «{new_intel_loc_name}»."
                ),
            )
            return _bot_schedule_travel(agent_id, agent, new_intel_loc, state, world_turn)

        # Step 4: No intel — go to nearest trader and wait for a co-located stalker.
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc is not None:
            if trader_loc != loc_id:
                trader_loc_name = locations.get(trader_loc, {}).get("name", trader_loc)
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"🏪 Иду к торговцу в «{trader_loc_name}» — узнать о «{target_name}»",
                    {"action_kind": "hunt_wait_at_trader", "destination": trader_loc},
                    summary=(
                        f"Я решил идти к торговцу в «{trader_loc_name}» в поисках "
                        f"информации о «{target_name}»."
                    ),
                )
                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)
            else:
                # Already at trader — try to buy intel about the target, then idle.
                bought = _bot_buy_hunt_intel_from_trader(
                    agent_id, agent, target_id, target_name, state, world_turn
                )
                if bought:
                    # Intel purchased — next tick the hunter will use it via Step 2.
                    agent["action_used"] = True
                    return []

                # Could not buy (broke, target dead, already purchased this turn) — anti-spam idle.
                _last_hunt_decision = None
                for _sm in reversed(agent.get("memory", [])):
                    if _sm.get("type") == "decision":
                        _last_hunt_decision = _sm.get("effects", {}).get("action_kind")
                        break
                if _last_hunt_decision != "hunt_wait_at_trader":
                    trader_loc_name = locations.get(trader_loc, {}).get("name", trader_loc)
                    _add_memory(
                        agent, world_turn, state, "decision",
                        f"⏳ Жду у торговца в «{trader_loc_name}» — ищу информацию о «{target_name}»",
                        {"action_kind": "hunt_wait_at_trader", "location_id": trader_loc},
                        summary=(
                            f"Я жду у торговца в «{trader_loc_name}» в надежде узнать "
                            f"что-нибудь о «{target_name}»."
                        ),
                    )
                agent["action_used"] = True
                return []

        # No trader — wander randomly.
        if connections:
            conn = rng.choice(connections)
            conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                f"❓ Ищу «{target_name}»",
                {"action_kind": "hunt_search", "target_id": target_id, "destination": conn["to"]},
                summary=f"Я иду в «{conn_name}» в поисках «{target_name}».",
            )
            return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)

        agent["action_used"] = True
        return []

    # Fallback: wander
    if connections and rng.random() < 0.60:
        conn = rng.choice(connections)
        conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
        _add_memory(
            agent, world_turn, state, "decision",
            "Блуждаю",
            {"action_kind": "wander", "destination": conn["to"]},
            summary=f"Я решил двигаться в «{conn_name}», потому что нет активных задач",
        )
        return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)
    _fallback_confirmed_empty = _confirmed_empty_locations(agent)
    if loc_id not in _fallback_confirmed_empty and rng.random() < 0.30:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            {"action_kind": "explore_decision", "location_id": loc_id},
            summary=f"Я решил исследовать текущую локацию «{loc.get('name', loc_id)}», потому что нет активных задач",
        )
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location", "turns_remaining": EXPLORE_DURATION_TURNS, "turns_total": EXPLORE_DURATION_TURNS,
            "target_id": loc_id, "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    agent["action_used"] = True
    return []


# ─── _describe_bot_decision_tree / _describe_bot_decision ────────────────────

def _describe_bot_decision_tree(
    agent: Dict[str, Any],
    events: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a full decision-tree description for a bot agent.

    Returns:
        {
          "goal": str,
          "chosen": {"action": str, "reason": str},
          "layers": [{"name", "skipped", "action", "reason"}, ...],
        }

    The layers mirror the actual priority order in _run_bot_action_inner.
    """
    hp = agent.get("hp", 100)
    hunger = agent.get("hunger", 0)
    thirst = agent.get("thirst", 0)
    sleepiness = agent.get("sleepiness", 0)
    loc_id = agent.get("location_id", "")
    wealth = _agent_wealth(agent)
    threshold = agent.get("material_threshold", DEFAULT_MATERIAL_THRESHOLD)
    global_goal = agent.get("global_goal", "get_rich")
    artifacts = _agent_artifacts_in_inventory(agent)
    trader_here = _find_trader_at_location(loc_id, state)
    equipment = agent.get("equipment", {})
    inventory = agent.get("inventory", [])

    # ── Build layer list ───────────────────────────────────────────────────────
    layers: List[Dict[str, Any]] = []

    # Layer 1: EMERGENCY: HP критический
    cond1 = hp <= 30
    layers.append({
        "name": "EMERGENCY: HP критический",
        "skipped": not cond1,
        "action": "Лечение/бегство",
        "reason": f"HP = {hp} (порог ≤30)" if cond1 else f"HP = {hp}, выше критического",
    })

    # Layer 2: EMERGENCY: Голод
    cond2 = hunger >= 70
    layers.append({
        "name": "EMERGENCY: Голод",
        "skipped": not cond2,
        "action": "Поесть",
        "reason": f"Голод = {hunger} (порог ≥70)" if cond2 else f"Голод = {hunger}, терпимо",
    })

    # Layer 3: EMERGENCY: Жажда
    cond3 = thirst >= 70
    layers.append({
        "name": "EMERGENCY: Жажда",
        "skipped": not cond3,
        "action": "Попить",
        "reason": f"Жажда = {thirst} (порог ≥70)" if cond3 else f"Жажда = {thirst}, терпимо",
    })

    # Layer 4: СНАРЯЖЕНИЕ: Обслуживание экипировки
    _no_weapon = not equipment.get("weapon")
    _no_armor = not equipment.get("armor")
    _equipped_weapon = equipment.get("weapon")
    _equipped_weapon_type = _equipped_weapon.get("type") if _equipped_weapon else None
    _required_ammo = AMMO_FOR_WEAPON.get(_equipped_weapon_type) if _equipped_weapon_type else None
    _no_ammo = _required_ammo and not any(i["type"] == _required_ammo for i in inventory)
    _no_heal = not any(i["type"] in HEAL_ITEM_TYPES for i in inventory)
    cond4_equip = bool(_no_weapon or _no_armor or _no_ammo)
    if _no_weapon:
        equip_reason = "Нет оружия"
    elif _no_armor:
        equip_reason = "Нет брони"
    elif _no_ammo:
        equip_reason = f"Нет патронов ({_required_ammo})"
    else:
        equip_reason = "Снаряжение в порядке"
    layers.append({
        "name": "СНАРЯЖЕНИЕ: Оружие / броня / патроны",
        "skipped": not cond4_equip,
        "action": "Найти/купить снаряжение",
        "reason": equip_reason,
    })

    # Layer 5: ВЫЖИВАНИЕ: Сон
    cond5 = sleepiness >= 75
    layers.append({
        "name": "ВЫЖИВАНИЕ: Сон",
        "skipped": not cond5,
        "action": "Спать 6ч",
        "reason": f"Усталость = {sleepiness} (порог ≥75)" if cond5 else f"Усталость = {sleepiness}, норма",
    })

    # Layer 6: ТОРГОВЛЯ: Продать артефакты
    cond6 = bool(artifacts) and trader_here is not None
    layers.append({
        "name": "ТОРГОВЛЯ: Продать артефакты",
        "skipped": not cond6,
        "action": "Продать артефакты",
        "reason": (
            f"{len(artifacts)} артефактов, торговец рядом"
            if cond6
            else ("Нет артефактов в инвентаре" if not artifacts else "Нет торговца на локации")
        ),
    })

    # Layer 7: ЦЕЛЬ: Накопить богатство
    cond7 = wealth < threshold
    layers.append({
        "name": "ЦЕЛЬ: Накопить богатство",
        "skipped": not cond7,
        "action": "Собирать ресурсы",
        "reason": f"Богатство {wealth} < порог {threshold}" if cond7 else f"Богатство {wealth} ≥ порог {threshold}",
    })

    # Layer 8: АПГРЕЙД: Улучшение снаряжения
    # Fires when wealth >= threshold: before pursuing the global goal the bot
    # checks whether a better-matching weapon or armor is affordable.
    # The description tree cannot run the full upgrade search here, so we simply
    # mark this layer as active whenever the wealth gate is open.
    cond8 = wealth >= threshold
    layers.append({
        "name": "АПГРЕЙД: Улучшение снаряжения",
        "skipped": not cond8,
        "action": "Купить улучшенное снаряжение",
        "reason": (
            f"Порог {threshold} достигнут — проверяю возможность апгрейда"
            if cond8
            else f"Богатство {wealth} < порог {threshold}, апгрейд недоступен"
        ),
    })

    # Layer 9: ЦЕЛЬ: Глобальная цель
    cond9 = wealth >= threshold
    layers.append({
        "name": "ЦЕЛЬ: Глобальная цель",
        "skipped": not cond9,
        "action": f"Преследование цели «{global_goal}»",
        "reason": (
            f"Богатство {wealth} ≥ порог {threshold}, цель: {global_goal}"
            if cond9
            else f"Богатство {wealth} < порог {threshold}"
        ),
    })

    # ── Determine chosen action (same logic as original _describe_bot_decision) ─
    goal = agent.get("current_goal", "unknown")
    sched = agent.get("scheduled_action")
    action = "Бездействие"
    reason = ""

    if sched:
        t = sched.get("type", "")
        if t == "travel":
            dest_id = sched.get("target_id", "")
            dest_name = state.get("locations", {}).get(dest_id, {}).get("name", dest_id)
            action = f"Движение → {dest_name}"
            turns = sched.get("turns_remaining", 0)
            reason = f"Идти {turns} ходов"
        elif t == "sleep":
            hrs = sched.get("hours", 0)
            action = f"Спать {hrs}ч"
            reason = "Восстановление сил"
        elif t == "explore_anomaly_location":
            loc_id_s = sched.get("target_id", "")
            loc_name = state.get("locations", {}).get(loc_id_s, {}).get("name", loc_id_s)
            action = f"Исследование {loc_name}"
            reason = "Поиск артефактов и ресурсов"
        else:
            action = t

    for ev in events:
        etype = ev.get("event_type", "")
        p = ev.get("payload", {})
        if etype == "item_consumed":
            item_name = p.get("item_type", "предмет")
            action = f"Использовать: {item_name}"
            if p.get("item_type") in ("bandage", "medkit", "stimpack"):
                reason = f"HP критически низкий ({agent.get('hp', 0)})"
            elif p.get("item_type") in ("bread", "sausage", "canned_food"):
                reason = f"Голод {agent.get('hunger', 0)}/100"
            elif p.get("item_type") in ("water", "vodka", "energy_drink"):
                reason = f"Жажда {agent.get('thirst', 0)}/100"
            break
        if etype == "item_equipped":
            slot = p.get("slot", "слот")
            action = f"Экипировать: {p.get('item_type', '?')} → {slot}"
            reason = f"Слот «{slot}» был пуст"
            break
        if etype == "item_picked_up":
            action = f"Подобрать с земли: {p.get('item_type', '?')}"
            reason = "Предмет лежал на земле рядом"
            break
        if etype == "bot_bought_item":
            action = f"Купить: {p.get('item_type', '?')}"
            reason = f"Потрачено {p.get('price', 0)} монет"
            break
        if etype == "artifact_picked_up":
            art = p.get("artifact_type", "артефакт")
            action = f"Подобрать артефакт: {art}"
            reason = "Артефакт лежит на локации"
            break
        if etype in ("trade_sell", "artifacts_sold"):
            action = "Продажа артефактов торговцу"
            money = p.get("money_gained", 0)
            reason = f"Выручка: {money} RU"
            break

    if not reason:
        if hp <= 30:
            reason = f"Критический HP ({hp})"
        elif hunger >= 70:
            reason = f"Голод {hunger}/100"
        elif thirst >= 70:
            reason = f"Жажда {thirst}/100"
        elif cond4_equip:
            reason = equip_reason
        elif sleepiness >= 75:
            reason = f"Усталость {sleepiness}/100"
        elif wealth < threshold:
            reason = f"Богатство {wealth} < порог {threshold}, фаза сбора ресурсов"
        elif goal == "upgrade_equipment":
            reason = f"Порог {threshold} достигнут — улучшаю снаряжение"
        else:
            reason = f"Богатство {wealth} ≥ порог {threshold}, преследование цели «{global_goal}»"

    return {
        "goal": goal,
        "chosen": {"action": action, "reason": reason},
        "layers": layers,
    }


def _describe_bot_decision(
    agent: Dict[str, Any],
    events: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a human-readable description of what a bot agent decided to do.
    Returns a dict: {goal, action, reason}.
    Delegates to _describe_bot_decision_tree internally.
    """
    tree = _describe_bot_decision_tree(agent, events, state)
    return {"goal": tree["goal"], "action": tree["chosen"]["action"], "reason": tree["chosen"]["reason"]}
