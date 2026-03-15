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
MAX_AGENT_MEMORY = 500

# Default risk_tolerance used when an agent or item does not specify one.
DEFAULT_RISK_TOLERANCE = 0.5

# Wealth buffer all stalkers must accumulate before pursuing their global goal.
# Valid range for material_threshold is [3000, 10000].
DEFAULT_MATERIAL_THRESHOLD = 3000
MATERIAL_THRESHOLD_MIN = 3000
MATERIAL_THRESHOLD_MAX = 10000

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
_EMISSION_DANGEROUS_TERRAIN: frozenset = frozenset({"plain", "hills"})

# Anomaly search parameters for the get_rich NPC goal path.
# Search radius is skill-based: 4 + agent["skill_stalker"] hops (e.g. 5 for level-1 stalker).
_ANOMALY_DISTANCE_PENALTY = 5.0        # Score reduction per hop of distance
_ANOMALY_SCORE_NOISE = 0.5             # Small random jitter to break ties between equal-scoring locations
_ANOMALY_RISK_MISMATCH_PENALTY = 150.0  # Penalty for full risk-tolerance mismatch; exceeds max base score (10*10=100) so agents strongly prefer zones that match their risk profile


def tick_zone_map(state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Advance the world by one turn.

    Returns (new_state, events_emitted).
    """
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []
    world_turn = state.get("world_turn", 1)

    # 1. Process scheduled actions for each alive stalker agent
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
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
                    f"Погиб от {_death_cause_str}. Голод: {_hunger}%, жажда: {_thirst}%.",
                    {"action_kind": "death", "cause": "starvation_or_thirst",
                     "hunger": _hunger, "thirst": _thirst},
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
                    f"Почувствовал приближение выброса. До начала примерно {_warn_offset} минут. Нужно найти укрытие!",
                    {"action_kind": "emission_imminent",
                     "turns_until": _warn_offset,
                     "emission_scheduled_turn": _emission_scheduled},
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
                    f"Погиб от выброса на открытой местности в «{_em_loc_name}».",
                    {"action_kind": "death", "cause": "emission",
                     "location_id": _em_agent.get("location_id"), "terrain": _em_terrain},
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
                "Начался выброс! Нужно укрыться в безопасном месте.",
                {"action_kind": "emission_started"},
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
                "Выброс закончился. Аномальные зоны могут снова содержать артефакты.",
                {"action_kind": "emission_ended"},
            )

        events.append({
            "event_type": "emission_ended",
            "payload": {
                "world_turn": world_turn,
                "next_emission_turn": state["emission_scheduled_turn"],
            },
        })

    # 3. AI bot agent decisions (bots without a scheduled action)
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("controller", {}).get("kind") != "bot":
            continue
        if agent.get("scheduled_action"):
            continue
        if agent.get("action_used"):
            continue
        bot_evs = _run_bot_action(agent_id, agent, state, world_turn)
        events.extend(bot_evs)

    # 3b. Per-turn location observations for every alive stalker agent.
    # Writes a new observation entry only when content has changed since the last
    # entry of the same category (deduplication prevents memory flooding).
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
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
        if agent.get("is_alive", True):
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
        if action_type in ("explore_anomaly_location", "travel"):
            if _is_emission_threat(agent, state):
                agent["scheduled_action"] = None
                _int_loc_name = state.get("locations", {}).get(
                    agent.get("location_id", ""), {}
                ).get("name", "текущей позиции")
                if action_type == "explore_anomaly_location":
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю исследование из-за выброса",
                        f"Получено предупреждение о выбросе во время исследования «{_int_loc_name}». "
                        "Прерываю разведку аномалии — нужно найти укрытие.",
                        {"action_kind": "exploration_interrupted", "reason": "emission_warning",
                         "location_id": agent.get("location_id")},
                        reason="выброс во время исследования аномалии",
                    )
                else:  # travel
                    _cancelled = sched.get("final_target_id", sched.get("target_id"))
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю движение из-за выброса",
                        f"Получено предупреждение о выбросе во время движения. "
                        f"Нахожусь в «{_int_loc_name}» — прерываю маршрут, нужно найти укрытие.",
                        {"action_kind": "travel_interrupted", "reason": "emission_warning",
                         "location_id": agent.get("location_id"),
                         "cancelled_target": _cancelled},
                        reason="выброс во время движения",
                    )
                # Return without events; the bot decision loop will run this tick
                # (since scheduled_action is now None) and will order a flee/shelter.
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
                if _is_emission_threat(agent, state):
                    _dest_name_em = state.get("locations", {}).get(destination, {}).get("name", destination)
                    _final_name_em = state.get("locations", {}).get(final_target, {}).get("name", final_target)
                    _add_memory(
                        agent, world_turn, state, "decision",
                        "⚡ Прерываю движение из-за выброса",
                        f"Получено предупреждение о выбросе. Остановился в «{_dest_name_em}» "
                        f"вместо продолжения к «{_final_name_em}».",
                        {"action_kind": "travel_interrupted", "reason": "emission_warning",
                         "stopped_at": destination, "cancelled_target": final_target},
                        reason="выброс — остановка на промежуточной точке маршрута",
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
                            f"Переход из «{dest_name_rr}» заблокирован. Нашёл альтернативный путь к «{final_name_rr}».",
                            {"action_kind": "route_changed", "rerouted_at": destination,
                             "final_target": final_target, "new_next_hop": next_hop},
                            reason="переход из следующей точки маршрута заблокирован",
                        )
                    else:
                        # Final target is completely unreachable — cancel travel.
                        final_name = state["locations"].get(final_target, {}).get("name", final_target)
                        dest_name_hop = state["locations"].get(destination, {}).get("name", destination)
                        _add_memory(
                            agent, world_turn, state, "decision",
                            "Смена решения из-за недоступности цели",
                            f"Цель «{final_name}» стала недоступна: маршрут заблокирован в «{dest_name_hop}».",
                            {"action_kind": "goal_cancelled", "cancelled_target": final_target,
                             "blocked_at": destination},
                            reason="цель стала полностью недоступна",
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
                agent["scheduled_action"] = {
                    "type": "travel",
                    "turns_remaining": hop_time,
                    "turns_total": hop_time,
                    "target_id": next_hop,
                    "final_target_id": final_target,
                    "remaining_route": remaining_route[1:],
                    "started_turn": world_turn,
                }
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
                    f"Промежуточная остановка на пути к «{final_name}».",
                    {"action_kind": "travel_hop", "to_loc": destination,
                     "final_target": final_target, "damage_taken": total_dmg},
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
                            f"Добрался до цели путешествия.",
                            {"action_kind": "travel_arrived", "to_loc": destination, "damage_taken": total_dmg})
                # Write observations for what's visible at the final destination
                _write_location_observations(agent_id, agent, destination, state, world_turn)
            if agent["hp"] <= 0:
                agent["is_alive"] = False
                _travel_loc_name = state.get("locations", {}).get(destination, {}).get("name", destination)
                _add_memory(
                    agent, world_turn, state, "observation",
                    "💀 Смерть",
                    f"Погиб от воздействия аномалии во время перехода в «{_travel_loc_name}».",
                    {"action_kind": "death", "cause": "travel_anomaly", "location_id": destination},
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
            f"Поднял артефакт {art_name} с аномалии.",
            {
                "action_kind": "pickup",
                "artifact_id": art["id"],
                "artifact_type": art["type"],
                "artifact_value": art.get("value", 0),
                "location_id": loc_id,
            },
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
        # Did not pick up an artifact — determine why and write appropriate memory
        if not existing_artifacts:
            # Location is genuinely empty — record as confirmed empty to block future tries.
            # Written as an "observation" (not "decision") so the stalker treats it as
            # a factual note about the world state rather than an active choice.
            _add_memory(
                agent, world_turn, state, "observation",
                f"Аномалия в «{loc_name}» пустая",
                f"Тщательно обыскал «{loc_name}» — артефактов нет.",
                {"action_kind": "explore_confirmed_empty", "location_id": loc_id},
            )
        else:
            # Bad luck — artifacts are present but agent missed them
            _add_memory(
                agent, world_turn, state, "decision",
                f"Не нашёл артефакт в «{loc_name}»",
                f"Искал в «{loc_name}» — не повезло, ничего не нашёл.",
                {"action_kind": "explore_miss", "location_id": loc_id},
                reason="разведка аномалии не дала результата",
            )

    # Always record that an exploration action was performed (satisfies memory tests
    # and gives the agent a record of every search attempt regardless of outcome).
    _add_memory(
        agent, world_turn, state, "action",
        f"Исследовал «{loc_name}»",
        f"Провёл разведку в «{loc_name}». Найдено артефактов: {len(found_artifacts)}.",
        {"action_kind": "explore", "location_id": loc_id, "artifacts_found": len(found_artifacts)},
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
                f"Погиб от аномалии во время разведки в «{loc_name}». Тип аномалии: {anomaly_type}.",
                {"action_kind": "death", "cause": "anomaly_exploration",
                 "location_id": loc_id, "anomaly_type": anomaly_type},
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
                f"Хорошо отдохнул. HP +{hp_regen}, радиация -{rad_reduce}.",
                {"action_kind": "sleep", "hp_gained": hp_regen, "radiation_reduced": rad_reduce})


def _add_memory(
    agent: Dict[str, Any],
    world_turn: int,
    state: Dict[str, Any],
    memory_type: str,
    title: str,
    summary: str,
    effects: Dict[str, Any],
    reason: str = "",
) -> None:
    """Append a memory entry to an agent.

    *reason* is only meaningful for ``memory_type == "decision"`` entries.
    When provided it is stored in *effects* under the key ``"почему"`` (the
    conditions that triggered this decision).  The ``summary`` field carries
    the purpose of the decision ("зачем") and is left unmodified.
    For all other memory types *reason* is silently ignored.
    """
    if reason and memory_type == "decision":
        effects = {**effects, "почему": reason}
    memory_entry = {
        "world_turn": world_turn,
        "type": memory_type,
        "title": title,
        "summary": summary,
        "effects": effects,
    }
    mem = agent.setdefault("memory", [])
    mem.append(memory_entry)
    # Keep only the last MAX_AGENT_MEMORY memory entries
    if len(mem) > MAX_AGENT_MEMORY:
        agent["memory"] = mem[-MAX_AGENT_MEMORY:]


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
            f"В локации: {', '.join(stalker_names)}.",
            {"observed": "stalkers", "location_id": loc_id, "names": stalker_names},
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
            f"Замечены мутанты: {', '.join(mutant_names)}.",
            {"observed": "mutants", "location_id": loc_id, "names": mutant_names},
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
            f"На земле: {', '.join(item_types)} (всего {len(item_types)} шт.).",
            {"observed": "items", "location_id": loc_id, "item_types": item_types},
        )


# ─────────────────────────────────────────────────────────────────
# Bot decisions
# ─────────────────────────────────────────────────────────────────

# Import centralised item-type sets from balance data (single source of truth)
from app.games.zone_stalkers.balance.items import (
    HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES,
    WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_ITEM_TYPES, AMMO_FOR_WEAPON,
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
    summary: str,
    effects: Dict[str, Any],
) -> None:
    """Append a memory entry to a trader NPC (same structure as agent memory)."""
    entry = {
        "world_turn": world_turn,
        "type": memory_type,
        "title": title,
        "summary": summary,
        "effects": effects,
    }
    trader.setdefault("memory", []).append(entry)
    if len(trader["memory"]) > 50:
        trader["memory"] = trader["memory"][-50:]


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
        f"Продал {len(sold_items)} артефактов на {sell_price_total} денег. "
        f"Торговец: {trader_name} в {loc_name}.",
        {"action_kind": "trade_sell", "money_gained": sell_price_total,
         "items_sold": [i["type"] for i in sold_items], "trader_id": trader["id"]},
    )

    # ── Trader memory ─────────────────────────────────────────────
    stalker_name = agent.get("name", agent_id)
    _add_trader_memory(
        trader, world_turn, state, "trade_buy",
        f"Купил {item_names} у сталкера {stalker_name}",
        f"Потратил {sell_price_total} RU, купив {len(sold_items)} артефакт(ов) у {stalker_name} "
        f"в локации {loc_name}.",
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
) -> List[Dict[str, Any]]:
    """Schedule hop-by-hop travel for a bot toward target_loc_id. Returns events."""
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
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": hop_time,
        "turns_total": hop_time,
        "target_id": first_hop,
        "final_target_id": target_loc_id,
        "remaining_route": route[1:],
        "started_turn": world_turn,
    }
    agent["action_used"] = True
    return [{
        "event_type": "agent_travel_started",
        "payload": {"agent_id": agent_id, "destination": target_loc_id, "turns": hop_time, "bot": True},
    }]


def _select_item_by_risk_tolerance(
    item_types: "frozenset[str]",
    agent_risk: float,
) -> "tuple[str, float] | None":
    """Return the (item_key, buy_price) whose ``risk_tolerance`` is closest to
    *agent_risk* among all items in *item_types* that exist in the catalogue.

    Ties are broken by preferring a lower base value (cheaper item wins).
    Returns ``None`` when *item_types* is empty or no matching entries exist.
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES

    best_key: "str | None" = None
    best_dist = float("inf")
    best_value = float("inf")

    for k in item_types:
        info = ITEM_TYPES.get(k)
        if info is None:
            continue
        dist = abs(info.get("risk_tolerance", DEFAULT_RISK_TOLERANCE) - agent_risk)
        value = info.get("value", 0)
        if dist < best_dist or (dist == best_dist and value < best_value):
            best_key = k
            best_dist = dist
            best_value = value

    if best_key is None:
        return None
    return best_key, best_value


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

    # Build candidate list sorted by closeness of risk_tolerance to agent's own,
    # with ties broken by lower value (cheaper preferred).
    candidates = sorted(
        (
            (k, ITEM_TYPES[k].get("value", 0), ITEM_TYPES[k].get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
            for k in item_types if k in ITEM_TYPES
        ),
        key=lambda x: (abs(x[2] - agent_risk), x[1]),
    )

    for item_type, base_value, item_risk in candidates:
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
        # Decision memory: explain the risk-tolerance match and the reason for purchase
        _add_memory(
            agent, world_turn, state, "decision",
            f"Решил купить «{item_name}»",
            (
                f"Выбор основан на склонности к риску: моя толерантность к риску "
                f"{agent_risk:.2f}, предмет «{item_name}» имеет {item_risk:.2f}. "
                f"Расхождение {abs(item_risk - agent_risk):.2f} — ближайшее среди доступных. "
                f"Куплю у торговца {trader_name} за {buy_price} монет."
            ),
            {
                "action_kind": "trade_decision",
                "item_type": item_type,
                "agent_risk_tolerance": agent_risk,
                "item_risk_tolerance": item_risk,
                "price": buy_price,
            },
            reason=purchase_reason,
        )
        # Action memory: record the completed purchase
        _add_memory(
            agent, world_turn, state, "action",
            f"Купил «{item_name}» у торговца",
            f"Купил «{item_name}» у {trader_name} за {buy_price} монет.",
            {"action_kind": "trade_buy", "item_type": item_type, "price": buy_price},
        )
        # Trader memory: record the sale from the trader's perspective
        agent_name = agent.get("name", agent_id)
        loc_name = state.get("locations", {}).get(agent.get("location_id", ""), {}).get("name", "?")
        _add_trader_memory(
            trader, world_turn, state, "trade_sale",
            f"Продал «{item_name}» сталкеру {agent_name}",
            f"Продал «{item_name}» сталкеру {agent_name} в «{loc_name}» за {buy_price} монет.",
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
        f"Применил «{item_name}».",
        {"action_kind": action_kind, "item_type": item["type"]},
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
        f"Экипировал «{item_name}» в слот «{slot}».",
        {"action_kind": "equip", "item_type": item["type"], "slot": slot},
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
        pickup_events = _bot_pickup_item_from_ground(agent_id, agent, item_types, state, world_turn)
        if not pickup_events:
            # Item not on the ground.  If this is not a trader location, record
            # the dead end immediately so that any interrupt (emergency, higher-
            # priority need) that fires next cannot cause the agent to loop back
            # here on the next search cycle.  Trader locations are skipped because
            # the agent came to *buy*, not pick up, so "not found on ground" is
            # expected and must not blacklist the trader.
            if _find_trader_at_location(loc_id, state) is None:
                loc = state.get("locations", {}).get(loc_id, {})
                _maybe_record_item_not_found(
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
) -> List[Dict[str, Any]]:
    """Pick up a matching item from the ground at the agent's current location.

    Removes the item from ``location["items"]`` and adds it to the agent's
    inventory.  Returns a non-empty event list on success.

    After a successful pickup, if no items of ``item_types`` remain on the ground
    at this location, an ``item_not_found_here`` observation is written so that
    the agent does not return looking for more of those items until a fresh
    ``observed:items`` entry supersedes the block.
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
        f"Нашёл «{item_name}» на земле в «{loc_name}» и подобрал.",
        {"action_kind": "pickup_ground", "item_type": item["type"], "location_id": loc_id},
    )
    # If no more items of the requested types remain, record that so the agent
    # won't plan another trip here for the same item category.
    remaining = loc.get("items", [])
    if not any(i.get("type") in item_types for i in remaining):
        _add_memory(
            agent, world_turn, state, "observation",
            f"📭 Предметы закончились в «{loc_name}»",
            f"Подобрал последний предмет нужного типа в «{loc_name}». Здесь больше нет.",
            {
                "action_kind": "item_not_found_here",
                "source": "pickup",
                "location_id": loc_id,
                "item_types": sorted(item_types),
            },
        )
    return [{"event_type": "item_picked_up",
             "payload": {"agent_id": agent_id, "item_type": item["type"],
                         "item_id": item["id"], "location_id": loc_id}}]


def _item_not_found_locations(
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
) -> "set[str]":
    """Return loc_ids where the agent previously looked for items of the given types
    but arrived and found nothing (``item_not_found_here`` observation).

    An entry is superseded — and the block lifted — when a *newer* ``observed:items``
    memory for those item types exists at the same location (e.g. a fresh item spawn).
    """
    last_item_obs_turn: Dict[str, int] = {}   # loc_id → most recent observed-items turn
    last_not_found_turn: Dict[str, int] = {}  # loc_id → most recent item_not_found_here turn

    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        effects = mem.get("effects", {})
        turn = mem.get("world_turn", 0)
        loc_id = effects.get("location_id")
        if not loc_id:
            continue

        if effects.get("action_kind") == "item_not_found_here":
            mem_types = frozenset(effects.get("item_types", []))
            if item_types.intersection(mem_types):
                last_not_found_turn[loc_id] = max(last_not_found_turn.get(loc_id, 0), turn)

        elif effects.get("observed") == "items":
            obs_types = set(effects.get("item_types", []))
            if item_types.intersection(obs_types):
                last_item_obs_turn[loc_id] = max(last_item_obs_turn.get(loc_id, 0), turn)

    result: set = set()
    for loc_id, nf_turn in last_not_found_turn.items():
        # Only block if the not_found observation is newer than (or same turn as) the
        # most recent item observation at that location.
        obs_turn = last_item_obs_turn.get(loc_id, 0)
        if nf_turn >= obs_turn:
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
    Duplicate entries for the same location and item category within the same turn are
    suppressed.
    """
    # Only record if the agent explicitly decided to travel here for this item.
    found_seek = False
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") != "decision":
            continue
        effects = mem.get("effects", {})
        if (
            effects.get("action_kind") == "seek_item"
            and effects.get("destination") == loc_id
            and effects.get("item_category") == item_category
        ):
            found_seek = True
            break
    if not found_seek:
        return

    # Suppress duplicates: don't write the same not_found entry twice in the same turn.
    for mem in reversed(agent.get("memory", [])):
        if mem.get("world_turn") != world_turn:
            break
        effects = mem.get("effects", {})
        if (
            effects.get("action_kind") == "item_not_found_here"
            and effects.get("location_id") == loc_id
            and frozenset(effects.get("item_types", [])).intersection(item_types)
        ):
            return  # already recorded this turn

    loc_name = loc.get("name", loc_id)
    _add_memory(
        agent, world_turn, state, "observation",
        f"⚠️ Предмет исчез из «{loc_name}»",
        f"Пришёл в «{loc_name}» за {item_category}, но предмет уже забрали.",
        {
            "action_kind": "item_not_found_here",
            "source": "arrival",
            "location_id": loc_id,
            "item_types": sorted(item_types),
        },
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
    connections) or that the agent has previously visited and found empty
    (recorded via ``_maybe_record_item_not_found``) are excluded.

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
                (
                    f"Накопил достаточно средств. Текущий предмет «{current_name}» "
                    f"(риск {current_rt:.2f}) хуже соответствует моей склонности к риску "
                    f"{agent_risk:.2f}, чем «{upgrade_name}» (риск {upgrade_rt:.2f}). "
                    f"Покупаю апгрейд за {buy_price} монет."
                ),
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
                reason=f"текущий предмет в слоте «{slot}» можно улучшить",
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
                (
                    f"Хочу заменить «{current_name}» на «{upgrade_name}» — "
                    f"лучше соответствует моей склонности к риску ({agent_risk:.2f}). "
                    f"Иду к торговцу {trader_name} в «{trader_loc_name}»."
                ),
                {
                    "action_kind": "upgrade_travel",
                    "slot": slot,
                    "old_item": current_type,
                    "new_item": upgrade_key,
                    "destination": trader_loc,
                },
                reason=f"нужен апгрейд в слоте «{slot}», торговец не здесь",
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
        # Find a connected location that is NOT on dangerous terrain
        safe_escape_targets = [
            c["to"] for c in loc.get("connections", [])
            if not c.get("closed")
            and locations.get(c["to"], {}).get("terrain_type", "") not in _EMISSION_DANGEROUS_TERRAIN
        ]
        if safe_escape_targets:
            target = rng.choice(safe_escape_targets)
            agent["current_goal"] = "flee_emission"
            reason = "активный выброс" if _emission_active else "скоро будет выброс"
            _add_memory(
                agent, world_turn, state, "decision",
                "⚡ Бегу от выброса!",
                f"Нахожусь на открытой местности, {'идёт' if _emission_active else 'скоро начнётся'} выброс. Бегу в укрытие.",
                {"action_kind": "flee_emission", "target_id": target},
                reason=reason,
            )
            return _bot_schedule_travel(agent_id, agent, target, state, world_turn)

    # ── EMISSION SHELTER: Stay put when emission is active or imminent ────────
    # Second-highest priority (after fleeing dangerous terrain, before any pending
    # tasks or new decisions).  If the agent is already on safe terrain but knows
    # an emission is coming (or is ongoing), it must NOT do anything — doing so
    # risks arriving on dangerous terrain when the emission fires.  The agent
    # simply waits until it sees an ``emission_ended`` observation.
    # Emission is an "urgent trigger" that overrides all pending arrival tasks.
    if _emission_active or _emission_warned:
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
                "Получено предупреждение о выбросе. Нахожусь в безопасном месте — жду окончания.",
                {"action_kind": "wait_in_shelter"},
                reason="скоро выброс, нахожусь в безопасном месте",
            )
        return []

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
                f"Критически низкое HP ({agent.get('hp', 0)}%). Нет аптечки в инвентаре. "
                f"Иду к торговцу в «{trader_loc_name}».",
                {"action_kind": "seek_item", "item_category": "medical",
                 "destination": trader_loc, "emergency": True},
                reason=f"HP {agent.get('hp', 0)}% — нужна аптечка срочно",
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
                f"Сильный голод ({agent.get('hunger', 0)}%). Нет еды в инвентаре. "
                f"Иду к торговцу в «{trader_loc_name}».",
                {"action_kind": "seek_item", "item_category": "food",
                 "destination": trader_loc, "emergency": True},
                reason=f"голод {agent.get('hunger', 0)}% — нужна еда срочно",
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
                f"Сильная жажда ({agent.get('thirst', 0)}%). Нет воды в инвентаре. "
                f"Иду к торговцу в «{trader_loc_name}».",
                {"action_kind": "seek_item", "item_category": "drink",
                 "destination": trader_loc, "emergency": True},
                reason=f"жажда {agent.get('thirst', 0)}% — нужна вода срочно",
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
                f"Нет оружия. Помню, что видел его в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                {"action_kind": "seek_item", "item_category": "weapon", "destination": mem_loc},
                reason="нет оружия в снаряжении, но помню где видел",
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
                    f"Нет оружия. Иду к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}».",
                    {"action_kind": "buy_item", "item_category": "weapon", "destination": trader_loc},
                    reason="нет оружия в снаряжении, иду к торговцу",
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
                f"Нет брони. Помню, что видел её в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                {"action_kind": "seek_item", "item_category": "armor", "destination": mem_loc},
                reason="нет брони в снаряжении, но помню где видел",
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
                    f"Нет брони. Иду к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}».",
                    {"action_kind": "buy_item", "item_category": "armor", "destination": trader_loc},
                    reason="нет брони в снаряжении, иду к торговцу",
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
                        f"Нет патронов для оружия. Помню, что видел их в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                        {"action_kind": "seek_item", "item_category": "ammo",
                         "ammo_type": _required_ammo, "destination": mem_loc},
                        reason="нет патронов для оружия, но помню где видел",
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
                            f"Нет патронов для оружия. Иду к торговцу в «{state.get('locations', {}).get(trader_loc, {}).get('name', trader_loc)}».",
                            {"action_kind": "buy_item", "item_category": "ammo",
                             "ammo_type": _required_ammo, "destination": trader_loc},
                            reason="нет патронов для оружия, иду к торговцу",
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
                f"Нет лечебных предметов. Помню, что видел их в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                {"action_kind": "seek_item", "item_category": "medical", "destination": mem_loc},
                reason="в инвентаре нет медикаментов",
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
                f"Нет еды. Помню, что видел её в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                {"action_kind": "seek_item", "item_category": "food", "destination": mem_loc},
                reason="в инвентаре нет еды",
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
                f"Нет воды. Помню, что видел её в «{state.get('locations', {}).get(mem_loc, {}).get('name', mem_loc)}». Иду туда.",
                {"action_kind": "seek_item", "item_category": "drink", "destination": mem_loc},
                reason="в инвентаре нет воды",
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
            f"Сильная усталость (сонливость {agent.get('sleepiness', 0)}%). Ложусь спать на {_sleep_hours} ч.",
            {"action_kind": "sleep_decision", "sleepiness": agent.get("sleepiness", 0), "hours": _sleep_hours},
            reason=f"сонливость достигла {agent.get('sleepiness', 0)}%",
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
                        f"Планирую продать: {art_types}.",
                        {"artifact_types": [a.get("type") for a in artifacts_held]},
                        reason="несу несколько артефактов — выбираю что продать",
                    )

                # Step 5 — record the nearest trader found via BFS
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Ближайший торговец: {trader_name}",
                    f"Ближайший торговец: {trader_name} в {trader_loc_name}.",
                    {"trader_location": trader_loc, "trader_name": trader_name,
                     "artifacts_count": len(artifacts_held)},
                    reason="ищу ближайшего торговца для продажи артефактов",
                )

                # Step 6 — commit to navigating toward the trader
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Иду к торговцу {trader_name}",
                    f"Иду к торговцу {trader_name}.",
                    {"action_kind": "sell_at_trader", "destination": trader_loc},
                    reason="есть артефакты для продажи, торговец не здесь",
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
            f"В «{loc.get('name', loc_id)}» есть аномалии — возможно найду артефакты.",
            {"action_kind": "explore_decision", "location_id": loc_id},
            reason="текущая локация имеет аномальную активность — ищу артефакты",
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

    # G3 — BFS search for the best fresh (not confirmed-empty) anomaly location within
    # skill-based radius.  Uses the same formula as _bot_pursue_goal (Phase 2b) so that
    # Phase-1 stalkers are not limited to just their immediate neighbors.
    _max_gather_search_hops = 4 + int(agent.get("skill_stalker", 1))  # mirrors Phase-2b radius
    reachable = _bfs_reachable_locations(loc_id, locations, max_hops=_max_gather_search_hops)

    def _gather_candidate_score(lid: str, dist: int) -> float:
        _agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
        _loc_risk = locations.get(lid, {}).get("anomaly_activity", 0) / 10.0
        _risk_penalty = abs(_loc_risk - _agent_risk) * _ANOMALY_RISK_MISMATCH_PENALTY
        return _score_location(locations.get(lid, {}), "artifacts") - dist * _ANOMALY_DISTANCE_PENALTY - _risk_penalty + rng.random() * _ANOMALY_SCORE_NOISE

    fresh_gather_candidates = [
        (lid, dist) for lid, dist in reachable.items()
        if locations.get(lid, {}).get("anomaly_activity", 0) > 0
        and lid not in confirmed_empty
    ]
    if fresh_gather_candidates:
        best_lid, best_dist = max(fresh_gather_candidates, key=lambda t: _gather_candidate_score(*t))
        best_nb_name = locations.get(best_lid, {}).get("name", best_lid)
        _add_memory(
            agent, world_turn, state, "decision",
            "Двигаюсь к непроверенной аномальной зоне",
            f"Ищу аномальные зоны для сбора ресурсов. Лучший вариант в радиусе {_max_gather_search_hops} переходов: «{best_nb_name}» (активность {locations.get(best_lid, {}).get('anomaly_activity', 0)}, расстояние {best_dist}).",
            {"action_kind": "move_for_resources", "destination": best_lid},
            reason="ближайшая непроверенная аномальная зона в радиусе поиска",
        )
        return _bot_schedule_travel(agent_id, agent, best_lid, state, world_turn)

    # G4 — Fallback: explore current location only if not confirmed empty
    if loc_id not in confirmed_empty and rng.random() < 0.40:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            f"Некуда идти — исследую «{loc.get('name', loc_id)}» в надежде найти что-нибудь ценное.",
            {"action_kind": "explore_decision", "location_id": loc_id},
            reason="нет аномальных соседей для перемещения — исследую текущую локацию",
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
                f"На локации «{loc_name}» есть аномальная активность — нужно провести разведку чтобы найти артефакты.",
                {"action_kind": "explore_decision", "location_id": loc_id},
                reason="цель get_rich требует поиска артефактов в аномальных зонах",
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
        # BFS radius is skill-based: 4 + skill_stalker hops (e.g. 5 for level-1 stalker).
        _max_anomaly_search_hops = 4 + int(agent.get("skill_stalker", 1))
        reachable = _bfs_reachable_locations(loc_id, locations, max_hops=_max_anomaly_search_hops)

        def _anomaly_candidate_score(lid: str, dist: int) -> float:
            _agent_risk = float(agent.get("risk_tolerance", DEFAULT_RISK_TOLERANCE))
            _loc_risk = locations.get(lid, {}).get("anomaly_activity", 0) / 10.0
            _risk_penalty = abs(_loc_risk - _agent_risk) * _ANOMALY_RISK_MISMATCH_PENALTY
            return _score_location(locations.get(lid, {}), "artifacts") - dist * _ANOMALY_DISTANCE_PENALTY - _risk_penalty + rng.random() * _ANOMALY_SCORE_NOISE

        fresh_candidates = [
            (lid, dist) for lid, dist in reachable.items()
            if locations.get(lid, {}).get("anomaly_activity", 0) > 0
            and lid not in confirmed_empty
        ]
        if fresh_candidates:
            best_lid, best_dist = max(fresh_candidates, key=lambda t: _anomaly_candidate_score(*t))
            best_name = locations.get(best_lid, {}).get("name", best_lid)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Иду в непроверенную аномальную зону «{best_name}»",
                f"Ищу аномальные зоны. Лучший вариант в радиусе {_max_anomaly_search_hops} переходов: «{best_name}» (активность {locations.get(best_lid, {}).get('anomaly_activity', 0)}, расстояние {best_dist}).",
                {"action_kind": "move_for_anomaly", "destination": best_lid},
                reason="ищу аномальные зоны для сбора артефактов в радиусе поиска",
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
                f"Все известные аномальные зоны уже исследованы. Остаюсь в «{loc.get('name', loc_id)}» в ожидании выброса.",
                {"action_kind": "wait_for_artifacts", "location_id": loc_id},
                reason="все известные аномальные зоны в радиусе поиска уже исследованы",
            )
            agent["action_used"] = True
            return []

        # Not at an anomaly location — go to the best known one to wait.
        all_anomaly_candidates = [
            (lid, dist) for lid, dist in reachable.items()
            if locations.get(lid, {}).get("anomaly_activity", 0) > 0
        ]
        if all_anomaly_candidates:
            best_lid, best_dist = max(all_anomaly_candidates, key=lambda t: _anomaly_candidate_score(*t))
            best_name = locations.get(best_lid, {}).get("name", best_lid)
            _add_memory(
                agent, world_turn, state, "decision",
                f"Все аномальные зоны изучены — иду в «{best_name}» ждать",
                f"Все известные аномальные локации в радиусе {_max_anomaly_search_hops} переходов пусты. Иду в «{best_name}» в ожидании новых артефактов.",
                {"action_kind": "move_for_anomaly", "destination": best_lid},
                reason="все аномальные зоны в радиусе пусты — иду ждать выброс",
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
                f"Аномальных зон нет в радиусе {_max_anomaly_search_hops} переходов. Иду в «{best_name}» — там выше аномальная активность.",
                {"action_kind": "move_for_anomaly", "destination": best["to"]},
                reason="аномальных зон в радиусе нет — иду к ближайшей по активности",
            )
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    # Fallback: wander
    if connections and rng.random() < 0.60:
        conn = rng.choice(connections)
        conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
        _add_memory(
            agent, world_turn, state, "decision",
            "Блуждаю",
            f"Нет активной задачи. Иду в «{conn_name}» наугад.",
            {"action_kind": "wander", "destination": conn["to"]},
            reason="нет активной задачи или доступных целей",
        )
        return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)
    _fallback_confirmed_empty = _confirmed_empty_locations(agent)
    if loc_id not in _fallback_confirmed_empty and rng.random() < 0.30:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            f"Нет активной задачи. Исследую «{loc.get('name', loc_id)}».",
            {"action_kind": "explore_decision", "location_id": loc_id},
            reason="нет активной задачи — исследую текущую локацию",
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
