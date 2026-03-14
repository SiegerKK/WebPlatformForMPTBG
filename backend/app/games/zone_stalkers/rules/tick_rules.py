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

# Emission (Выброс) mechanic constants
# 1 game day = 1440 turns (1 turn = 1 minute)
_EMISSION_MIN_INTERVAL_TURNS = 1440   # earliest next emission: 1 game day after last
_EMISSION_MAX_INTERVAL_TURNS = 2880   # latest next emission: 2 game days after last
_EMISSION_MIN_DURATION_TURNS = 5      # shortest emission: 5 game minutes
_EMISSION_MAX_DURATION_TURNS = 10     # longest emission: 10 game minutes
_EMISSION_WARNING_TURNS = 30          # NPC starts fleeing this many turns before emission
# Terrain types where stalkers are killed by an emission
_EMISSION_DANGEROUS_TERRAIN: frozenset = frozenset({"plain", "hills"})

# Anomaly search parameters for the get_rich NPC goal path.
# Search radius is skill-based: 1 + agent["skill_survival"] hops (e.g. 2 for level-1 survival).
_ANOMALY_DISTANCE_PENALTY = 5.0   # Score reduction per hop of distance
_ANOMALY_SCORE_NOISE = 0.5        # Small random jitter to break ties between equal-scoring locations


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
                events.append({
                    "event_type": "agent_died",
                    "payload": {"agent_id": agent_id, "cause": "starvation_or_thirst"},
                })


    # 2b. Emission (Выброс) mechanic — replaces the old midnight artifact spawn.
    # The emission spawns artifacts in anomaly locations, kills stalkers on open
    # terrain (plains / hills), and notifies all alive stalkers via memory.
    _emission_rng = random.Random(str(state.get("seed", 0)) + str(world_turn))

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
                events.append({"event_type": "agent_died", "payload": {"agent_id": agent_id, "cause": "travel_anomaly"}})

    elif action_type == "explore":
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
            # Location is genuinely empty — record as confirmed empty to block future tries
            _add_memory(
                agent, world_turn, state, "decision",
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
) -> None:
    memory_entry = {
        "world_turn": world_turn,
        "world_day": state.get("world_day", 1),
        "world_hour": state.get("world_hour", 0),
        "world_minute": state.get("world_minute", 0),
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
from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES
from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES

# Set of artifact type keys (used to identify artifacts in inventory)
_ARTIFACT_ITEM_TYPES: frozenset = frozenset(ARTIFACT_TYPES.keys())


# ─────────────────────────────────────────────────────────────────
# NPC planning helpers
# ─────────────────────────────────────────────────────────────────

def _agent_wealth(agent: Dict[str, Any]) -> int:
    """Sum of money + total inventory item values."""
    inv_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    return agent.get("money", 0) + inv_value


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
        "world_day": state.get("world_day", 1),
        "world_hour": state.get("world_hour", 0),
        "world_minute": state.get("world_minute", 0),
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


def _bot_buy_from_trader(
    agent_id: str,
    agent: Dict[str, Any],
    item_types: "frozenset[str]",
    state: Dict[str, Any],
    world_turn: int,
) -> List[Dict[str, Any]]:
    """Buy the cheapest available item from a trader at the agent's current location.

    Implements an infinite-stock stub: the trader does not need to have the
    item in their inventory — they always can supply it.  The agent is
    charged 150 % of the item's base value.

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

    # Pick the cheapest buyable item the agent can afford
    candidates = sorted(
        ((k, ITEM_TYPES[k].get("value", 100)) for k in item_types if k in ITEM_TYPES),
        key=lambda x: x[1],
    )
    for item_type, base_value in candidates:
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
        _add_memory(
            agent, world_turn, state, "action",
            f"Купил «{item_name}» у торговца",
            f"Купил «{item_name}» у {trader_name} за {buy_price} монет.",
            {"action_kind": "trade_buy", "item_type": item_type, "price": buy_price},
        )
        return [{
            "event_type": "bot_bought_item",
            "payload": {
                "agent_id": agent_id, "trader_id": trader["id"],
                "item_type": item_type, "price": buy_price,
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
      GOAL      – If wealth < material_threshold: gather resources
                  Else: pursue global_goal
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

    # ── EMISSION ESCAPE: Flee dangerous terrain before emission ───────────────
    # If emission is active OR imminent (within warning window), and the agent is
    # on open terrain (plains / hills), immediately travel to a safer location.
    _emission_active = state.get("emission_active", False)
    _emission_scheduled = state.get("emission_scheduled_turn")
    _on_dangerous_terrain = loc.get("terrain_type", "") in _EMISSION_DANGEROUS_TERRAIN
    _emission_imminent = (
        _emission_scheduled is not None
        and not _emission_active
        and 0 < (_emission_scheduled - world_turn) <= _EMISSION_WARNING_TURNS
    )
    if _on_dangerous_terrain and (_emission_active or _emission_imminent):
        # Find a connected location that is NOT on dangerous terrain
        safe_escape_targets = [
            c["to"] for c in loc.get("connections", [])
            if not c.get("closed")
            and locations.get(c["to"], {}).get("terrain_type", "") not in _EMISSION_DANGEROUS_TERRAIN
        ]
        if safe_escape_targets:
            target = rng.choice(safe_escape_targets)
            agent["current_goal"] = "flee_emission"
            reason = "активный выброс" if _emission_active else "скорый выброс"
            _add_memory(
                agent, world_turn, state, "decision",
                "⚡ Бегу от выброса!",
                f"Нахожусь на открытой местности во время выброса ({reason}). Бегу в укрытие.",
                {"action_kind": "flee_emission", "target_id": target},
            )
            return _bot_schedule_travel(agent_id, agent, target, state, world_turn)

    # ── EMERGENCY: Heal ────────────────────────────────────────────────────────
    if agent.get("hp", 100) <= 30:
        heal_item = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
        if heal_item:
            return _bot_consume(agent_id, agent, heal_item, world_turn, state, "consume_heal")
        # No heal item — try to buy from a nearby trader
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, HEAL_ITEM_TYPES, state, world_turn)
            if bought:
                return bought
        elif trader_loc is not None:
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)
        # No trader reachable — flee to low-anomaly neighbor
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
            bought = _bot_buy_from_trader(agent_id, agent, FOOD_ITEM_TYPES, state, world_turn)
            if bought:
                return bought
        elif trader_loc is not None:
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── EMERGENCY: Drink ──────────────────────────────────────────────────────
    if agent.get("thirst", 0) >= 70:
        drink = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
        if drink:
            return _bot_consume(agent_id, agent, drink, world_turn, state, "consume_drink")
        # No drink — try to buy from a nearby trader
        trader_loc = _find_nearest_trader_location(loc_id, state)
        if trader_loc == loc_id:
            bought = _bot_buy_from_trader(agent_id, agent, DRINK_ITEM_TYPES, state, world_turn)
            if bought:
                return bought
        elif trader_loc is not None:
            return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── SURVIVAL: Sleep ───────────────────────────────────────────────────────
    if agent.get("sleepiness", 0) >= 75:
        _sleep_hours = 6
        _add_memory(
            agent, world_turn, state, "decision",
            "Ложусь спать",
            f"Сильная усталость (сонливость {agent.get('sleepiness', 0)}%). Ложусь спать на {_sleep_hours} ч.",
            {"action_kind": "sleep_decision", "sleepiness": agent.get("sleepiness", 0), "hours": _sleep_hours},
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
    #   b) No local trader but goal is get_rich → travel to nearest trader
    artifacts_held = _agent_artifacts_in_inventory(agent)
    if artifacts_held:
        trader_here = _find_trader_at_location(loc_id, state)
        if trader_here:
            sell_evs = _bot_sell_to_trader(agent_id, agent, trader_here, state, world_turn)
            if sell_evs:
                return sell_evs
        elif agent.get("global_goal") == "get_rich":
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
                    )

                # Step 5 — record the nearest trader found via BFS
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Ближайший торговец: {trader_name}",
                    f"Ближайший торговец: {trader_name} в {trader_loc_name}.",
                    {"trader_location": trader_loc, "trader_name": trader_name,
                     "artifacts_count": len(artifacts_held)},
                )

                # Step 6 — commit to navigating toward the trader
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Иду к торговцу {trader_name}",
                    f"Иду к торговцу {trader_name}.",
                    {"destination": trader_loc},
                )

                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── GOAL SELECTION ─────────────────────────────────────────────────────────
    wealth = _agent_wealth(agent)
    threshold = agent.get("material_threshold", 1000)
    global_goal = agent.get("global_goal", "survive")

    # get_rich agents always follow the goal-directed pursuit path — the goal
    # itself handles both artifact gathering and selling, so the generic
    # resource-accumulation phase would only slow them down.
    if global_goal == "get_rich" or wealth >= threshold:
        # Phase 2: Pursue global goal
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

    # G2 — Explore if anomalies are present (must explore to obtain artifacts)
    if loc.get("anomaly_activity", 0) > 0:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую аномальную зону",
            f"В «{loc.get('name', loc_id)}» есть аномалии — возможно найду артефакты.",
            {"action_kind": "explore_decision", "location_id": loc_id},
        )
        agent["scheduled_action"] = {
            "type": "explore",
            "turns_remaining": EXPLORE_DURATION_TURNS,
            "turns_total": EXPLORE_DURATION_TURNS,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    # G3 — Move toward a more loot-rich adjacent location (higher anomaly_activity)
    connections = [c for c in loc.get("connections", []) if not c.get("closed")]
    if connections:
        # Prefer neighbors with higher anomaly_activity
        def loc_score(conn):
            nb = locations.get(conn["to"], {})
            return nb.get("anomaly_activity", 0) * 2
        best = max(connections, key=loc_score)
        if rng.random() < 0.70:
            best_nb_name = locations.get(best["to"], {}).get("name", best["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                "Двигаюсь за ресурсами",
                f"Иду в «{best_nb_name}» — там выше аномальная активность и больше лута.",
                {"action_kind": "move_for_resources", "destination": best["to"]},
            )
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    # G4 — Fallback: explore current location anyway
    if rng.random() < 0.40:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            f"Некуда идти — исследую «{loc.get('name', loc_id)}» в надежде найти что-нибудь ценное.",
            {"action_kind": "explore_decision", "location_id": loc_id},
        )
        agent["scheduled_action"] = {
            "type": "explore",
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

    if global_goal == "survive":
        # Stay in safe locations; sleep more; top up supplies
        if loc.get("anomaly_activity", 0) > 5 and connections:
            safe_conn = [c for c in connections
                         if locations.get(c["to"], {}).get("anomaly_activity", 5) <= 3]
            if safe_conn:
                chosen = rng.choice(safe_conn)
                safe_name = locations.get(chosen["to"], {}).get("name", chosen["to"])
                _add_memory(
                    agent, world_turn, state, "decision",
                    "Ухожу в безопасную зону",
                    f"Аномальная активность слишком высокая. Ухожу в «{safe_name}».",
                    {"action_kind": "move_to_safety", "destination": chosen["to"]},
                )
                return _bot_schedule_travel(agent_id, agent, chosen["to"], state, world_turn)
        if agent.get("sleepiness", 0) >= 40:
            _sleep_hours = 4
            _add_memory(
                agent, world_turn, state, "decision",
                "Ложусь спать",
                f"Устал (сонливость {agent.get('sleepiness', 0)}%). Ложусь поспать на {_sleep_hours} ч.",
                {"action_kind": "sleep_decision", "sleepiness": agent.get("sleepiness", 0), "hours": _sleep_hours},
            )
            agent["scheduled_action"] = {
                "type": "sleep", "turns_remaining": _sleep_hours * _HOUR_IN_TURNS,
                "turns_total": _sleep_hours * _HOUR_IN_TURNS, "hours": _sleep_hours,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "sleep_started", "payload": {"agent_id": agent_id, "hours": _sleep_hours}}]

    elif global_goal == "get_rich":
        # ── Explore current location to find artifacts (must go through explore) ──────
        # Artifacts can only be obtained through the explore action, not picked up directly.
        # Stalkers do NOT have omniscient knowledge of which locations have artifacts —
        # they can only explore anomaly zones and learn from memory.
        if loc.get("anomaly_activity", 0) > 0:
            # Build confirmed-empty set to avoid re-exploring fruitless spots
            confirmed_empty_here: frozenset = frozenset(
                mem.get("effects", {}).get("location_id")
                for mem in agent.get("memory", [])
                if mem.get("effects", {}).get("action_kind") == "explore_confirmed_empty"
                and mem.get("effects", {}).get("location_id")
            )
            if loc_id not in confirmed_empty_here:
                loc_name = loc.get("name", loc_id)
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Исследую «{loc_name}»",
                    f"На локации «{loc_name}» есть аномальная активность — нужно провести разведку чтобы найти артефакты.",
                    {"action_kind": "explore_decision", "location_id": loc_id},
                )
                agent["scheduled_action"] = {
                    "type": "explore", "turns_remaining": EXPLORE_DURATION_TURNS,
                    "turns_total": EXPLORE_DURATION_TURNS,
                    "target_id": loc_id, "started_turn": world_turn,
                }
                agent["action_used"] = True
                return [{"event_type": "exploration_started",
                         "payload": {"agent_id": agent_id, "location_id": loc_id}}]

        # Current location has no anomaly activity or already confirmed empty.
        # Build the set of locations the agent has confirmed as empty through exploration.
        confirmed_empty: frozenset = frozenset(
            mem.get("effects", {}).get("location_id")
            for mem in agent.get("memory", [])
            if mem.get("effects", {}).get("action_kind") == "explore_confirmed_empty"
            and mem.get("effects", {}).get("location_id")
        )

        # If current location has anomalies AND activity > 0 AND is not yet confirmed empty → explore it.
        if loc.get("anomaly_activity", 0) > 0 and loc_id not in confirmed_empty:
            _add_memory(
                agent, world_turn, state, "decision",
                "Исследую зону в ожидании артефактов",
                f"Артефактов нигде нет. Исследую «{loc.get('name', loc_id)}» в надежде найти что-нибудь.",
                {"action_kind": "explore_decision", "location_id": loc_id},
            )
            agent["scheduled_action"] = {
                "type": "explore", "turns_remaining": EXPLORE_DURATION_TURNS,
                "turns_total": EXPLORE_DURATION_TURNS,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "exploration_started",
                     "payload": {"agent_id": agent_id, "location_id": loc_id}}]

        # Current location confirmed empty or no anomaly activity here.
        # BFS radius is skill-based: 1 + skill_survival hops (e.g. 2 for level-1 survival).
        _max_anomaly_search_hops = 1 + int(agent.get("skill_survival", 1))
        reachable = _bfs_reachable_locations(loc_id, locations, max_hops=_max_anomaly_search_hops)

        def _anomaly_candidate_score(lid: str, dist: int) -> float:
            return _score_location(locations.get(lid, {}), "artifacts") - dist * _ANOMALY_DISTANCE_PENALTY + rng.random() * _ANOMALY_SCORE_NOISE

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
            )
            return _bot_schedule_travel(agent_id, agent, best_lid, state, world_turn)

        # All anomaly locations within the search radius are confirmed empty — go to the best one to wait for midnight spawns.
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
            )
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    elif global_goal == "explore_zone":
        # Visit as many unique locations as possible
        visited = {mem.get("effects", {}).get("to_loc") for mem in agent.get("memory", [])
                   if mem.get("type") == "travel"}
        visited.add(loc_id)
        unvisited = [c for c in connections if c["to"] not in visited]
        if unvisited:
            target = rng.choice(unvisited)
            target_name = locations.get(target["to"], {}).get("name", target["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду исследовать новое место",
                f"Хочу посетить как можно больше локаций. Иду в «{target_name}».",
                {"action_kind": "explore_new_location", "destination": target["to"]},
            )
            return _bot_schedule_travel(agent_id, agent, target["to"], state, world_turn)
        # Explore current location if not recently explored
        if rng.random() < 0.50:
            _add_memory(
                agent, world_turn, state, "decision",
                "Исследую текущую локацию",
                f"Все соседние локации уже посещены. Исследую «{loc.get('name', loc_id)}».",
                {"action_kind": "explore_decision", "location_id": loc_id},
            )
            agent["scheduled_action"] = {
                "type": "explore", "turns_remaining": EXPLORE_DURATION_TURNS, "turns_total": EXPLORE_DURATION_TURNS,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "exploration_started",
                     "payload": {"agent_id": agent_id, "location_id": loc_id}}]
        if connections:
            rand_conn = rng.choice(connections)
            rand_name = locations.get(rand_conn["to"], {}).get("name", rand_conn["to"])
            _add_memory(
                agent, world_turn, state, "decision",
                "Иду в случайном направлении",
                f"Иду в «{rand_name}» — наугад, продолжаю исследование зоны.",
                {"action_kind": "wander", "destination": rand_conn["to"]},
            )
            return _bot_schedule_travel(agent_id, agent, rand_conn["to"], state, world_turn)

    elif global_goal == "serve_faction":
        # Patrol: move toward locations where faction members are present
        faction = agent.get("faction", "loner")
        faction_locs = []
        for cid, c_loc in locations.items():
            for aid in c_loc.get("agents", []):
                other = state.get("agents", {}).get(aid, {})
                if other.get("faction") == faction and aid != agent_id:
                    faction_locs.append(cid)
                    break
        # Move toward closest faction location
        if faction_locs and connections:
            target_conn = next(
                (c for c in connections if c["to"] in faction_locs),
                rng.choice(connections) if connections else None
            )
            if target_conn:
                target_name = locations.get(target_conn["to"], {}).get("name", target_conn["to"])
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Патрулирую с фракцией «{faction}»",
                    f"Иду в «{target_name}» — там члены моей фракции.",
                    {"action_kind": "faction_patrol", "destination": target_conn["to"], "faction": faction},
                )
                return _bot_schedule_travel(agent_id, agent, target_conn["to"], state, world_turn)

    # Fallback: wander
    if connections and rng.random() < 0.60:
        conn = rng.choice(connections)
        conn_name = locations.get(conn["to"], {}).get("name", conn["to"])
        _add_memory(
            agent, world_turn, state, "decision",
            "Блуждаю",
            f"Нет активной задачи. Иду в «{conn_name}» наугад.",
            {"action_kind": "wander", "destination": conn["to"]},
        )
        return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)
    if rng.random() < 0.30:
        _add_memory(
            agent, world_turn, state, "decision",
            "Исследую текущую локацию",
            f"Нет активной задачи. Исследую «{loc.get('name', loc_id)}».",
            {"action_kind": "explore_decision", "location_id": loc_id},
        )
        agent["scheduled_action"] = {
            "type": "explore", "turns_remaining": EXPLORE_DURATION_TURNS, "turns_total": EXPLORE_DURATION_TURNS,
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

    The 7 layers mirror the actual priority order in _run_bot_action_inner.
    """
    hp = agent.get("hp", 100)
    hunger = agent.get("hunger", 0)
    thirst = agent.get("thirst", 0)
    sleepiness = agent.get("sleepiness", 0)
    loc_id = agent.get("location_id", "")
    wealth = _agent_wealth(agent)
    threshold = agent.get("material_threshold", 1000)
    global_goal = agent.get("global_goal", "survive")
    artifacts = _agent_artifacts_in_inventory(agent)
    trader_here = _find_trader_at_location(loc_id, state)

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

    # Layer 4: ВЫЖИВАНИЕ: Сон
    cond4 = sleepiness >= 75
    layers.append({
        "name": "ВЫЖИВАНИЕ: Сон",
        "skipped": not cond4,
        "action": "Спать 6ч",
        "reason": f"Усталость = {sleepiness} (порог ≥75)" if cond4 else f"Усталость = {sleepiness}, норма",
    })

    # Layer 5: ТОРГОВЛЯ: Продать артефакты
    cond5 = bool(artifacts) and trader_here is not None
    layers.append({
        "name": "ТОРГОВЛЯ: Продать артефакты",
        "skipped": not cond5,
        "action": "Продать артефакты",
        "reason": (
            f"{len(artifacts)} артефактов, торговец рядом"
            if cond5
            else ("Нет артефактов в инвентаре" if not artifacts else "Нет торговца на локации")
        ),
    })

    # Layer 6: ЦЕЛЬ: Накопить богатство
    cond6 = wealth < threshold
    layers.append({
        "name": "ЦЕЛЬ: Накопить богатство",
        "skipped": not cond6,
        "action": "Собирать ресурсы",
        "reason": f"Богатство {wealth} < порог {threshold}" if cond6 else f"Богатство {wealth} ≥ порог {threshold}",
    })

    # Layer 7: ЦЕЛЬ: Глобальная цель
    cond7 = wealth >= threshold
    layers.append({
        "name": "ЦЕЛЬ: Глобальная цель",
        "skipped": not cond7,
        "action": f"Преследование цели «{global_goal}»",
        "reason": (
            f"Богатство {wealth} ≥ порог {threshold}, цель: {global_goal}"
            if cond7
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
        elif t == "explore":
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
            if p.get("item_type") in ("bandage", "medkit"):
                reason = f"HP критически низкий ({agent.get('hp', 0)})"
            elif p.get("item_type") in ("bread", "sausage", "canned_food"):
                reason = f"Голод {agent.get('hunger', 0)}/100"
            elif p.get("item_type") in ("water", "vodka"):
                reason = f"Жажда {agent.get('thirst', 0)}/100"
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
        elif sleepiness >= 75:
            reason = f"Усталость {sleepiness}/100"
        elif wealth < threshold:
            reason = f"Богатство {wealth} < порог {threshold}, фаза сбора ресурсов"
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
