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
import copy
import random
from typing import Any, Dict, List, Tuple


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

    # 2. Degrade survival needs and apply critical penalties
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

    # 4. Advance world time
    world_hour = state.get("world_hour", 6)
    world_day = state.get("world_day", 1)
    world_hour += 1
    if world_hour >= 24:
        world_hour = 0
        world_day += 1
        events.append({"event_type": "day_changed", "payload": {"world_day": world_day}})
    state["world_hour"] = world_hour
    state["world_day"] = world_day
    state["world_turn"] = world_turn + 1

    # 5. Reset action_used for next turn
    for agent in state.get("agents", {}).values():
        if agent.get("is_alive", True):
            agent["action_used"] = False

    # Check game-over
    if state["world_turn"] > state.get("max_turns", 50):
        state["game_over"] = True
        events.append({"event_type": "game_over", "payload": {"reason": "max_turns_reached"}})

    events.append({
        "event_type": "world_turn_advanced",
        "payload": {
            "world_turn": state["world_turn"],
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
        # Still in progress — emit progress event
        events.append({
            "event_type": f"{action_type}_in_progress",
            "payload": {
                "agent_id": agent_id,
                "turns_remaining": turns_remaining,
                "action_type": action_type,
            },
        })
        return events

    # Action complete — resolve effects
    agent["scheduled_action"] = None

    if action_type == "travel":
        route = sched.get("route", [])
        destination = sched.get("target_id")
        if destination and destination in state.get("locations", {}):
            # Move agent along the whole route (just teleport to destination for simplicity)
            old_loc = agent["location_id"]
            # Remove from old location
            old_loc_data = state["locations"].get(old_loc, {})
            if agent_id in old_loc_data.get("agents", []):
                old_loc_data["agents"].remove(agent_id)
            # Add to destination
            agent["location_id"] = destination
            new_loc_data = state["locations"].get(destination, {})
            if agent_id not in new_loc_data.get("agents", []):
                new_loc_data.setdefault("agents", []).append(agent_id)
            # Apply anomaly damage for each hop
            from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
            total_dmg = 0
            for hop_loc_id in route:
                hop_loc = state["locations"].get(hop_loc_id, {})
                for anom in hop_loc.get("anomalies", []):
                    anom_info = ANOMALY_TYPES.get(anom["type"], {})
                    dmg = anom_info.get("damage", 0) // 4  # quarter damage while passing
                    total_dmg += dmg
            if total_dmg > 0:
                agent["hp"] = max(0, agent["hp"] - total_dmg)
            events.append({
                "event_type": "travel_completed",
                "payload": {
                    "agent_id": agent_id,
                    "from": old_loc,
                    "to": destination,
                    "route": route,
                    "anomaly_damage": total_dmg,
                },
            })
            # Write memory
            _add_memory(agent, world_turn, state, "travel",
                        f"Travelled to {state['locations'][destination].get('name', destination)}",
                        f"Arrived after a long journey through the Zone.",
                        {"damage_taken": total_dmg})
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
    """Resolve exploration: chance for loot, anomaly encounter, or discovery."""
    from app.games.zone_stalkers.rules.world_rules import _EXPLORE_LOOT_CHANCE
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    events: List[Dict[str, Any]] = []

    loc_type = loc.get("type", "wild_area")
    danger_level = loc.get("danger_level", 2)
    loot_chance = _EXPLORE_LOOT_CHANCE.get(loc_type, 0.3)

    # Seed using agent id for reproducibility variance
    rng = random.Random(agent_id + str(world_turn))
    roll = rng.random()

    found_items = []
    found_artifacts = []
    notes = []

    if roll < loot_chance:
        # Found something!
        roll2 = rng.random()
        if loc.get("anomalies") and roll2 < 0.4:
            # Found an artifact near an anomaly
            art_type = rng.choice(list(ARTIFACT_TYPES.keys()))
            art_info = ARTIFACT_TYPES[art_type]
            art_item = {
                "id": f"art_{agent_id}_{world_turn}",
                "type": art_type,
                "name": art_info["name"],
                "value": art_info["value"],
            }
            agent.setdefault("inventory", []).append(art_item)
            found_artifacts.append(art_item)
            notes.append(f"Found {art_info['name']} near an anomaly cluster.")
            events.append({
                "event_type": "exploration_found_artifact",
                "payload": {"agent_id": agent_id, "artifact": art_item},
            })
        else:
            # Found a regular item
            consumable_types = [k for k, v in ITEM_TYPES.items() if v["type"] in ("medical", "consumable", "ammo")]
            if consumable_types:
                item_type = rng.choice(consumable_types)
                item_info = ITEM_TYPES[item_type]
                item = {
                    "id": f"item_{agent_id}_{world_turn}",
                    "type": item_type,
                    "name": item_info["name"],
                    "weight": item_info.get("weight", 0),
                    "value": item_info.get("value", 0),
                }
                agent.setdefault("inventory", []).append(item)
                found_items.append(item)
                notes.append(f"Found {item_info['name']} in the rubble.")
                events.append({
                    "event_type": "exploration_found_item",
                    "payload": {"agent_id": agent_id, "item": item},
                })
    else:
        notes.append("Searched the area but found nothing of value.")

    # Possible anomaly encounter during exploration
    if loc.get("anomalies") and rng.random() < 0.15 * danger_level / 5:
        from app.games.zone_stalkers.balance.anomalies import ANOMALY_TYPES
        anom = rng.choice(loc["anomalies"])
        anom_info = ANOMALY_TYPES.get(anom["type"], {})
        dmg = anom_info.get("damage", 10)
        agent["hp"] = max(0, agent["hp"] - dmg)
        notes.append(f"Triggered a {anom_info.get('name', 'anomaly')} — took {dmg} damage!")
        events.append({
            "event_type": "anomaly_damage",
            "payload": {
                "agent_id": agent_id,
                "anomaly_type": anom["type"],
                "damage": dmg,
                "hp_remaining": agent["hp"],
            },
        })
        if agent["hp"] <= 0:
            agent["is_alive"] = False
            events.append({"event_type": "agent_died", "payload": {"agent_id": agent_id, "cause": "anomaly_exploration"}})

    summary = " ".join(notes) if notes else f"Explored {loc.get('name', loc_id)}."
    events.insert(0, {
        "event_type": "exploration_completed",
        "payload": {
            "agent_id": agent_id,
            "location_id": loc_id,
            "location_name": loc.get("name", loc_id),
            "found_items": found_items,
            "found_artifacts": found_artifacts,
        },
    })

    _add_memory(agent, world_turn, state, "explore",
                f"Explored {loc.get('name', loc_id)}",
                summary,
                {"items_found": len(found_items), "artifacts_found": len(found_artifacts)})
    return events


def _resolve_sleep(agent: Dict[str, Any], sched: Dict[str, Any], world_turn: int, state: Dict[str, Any]) -> None:
    """Apply sleep healing effects."""
    hours = sched.get("turns_total", 6)
    # Heal HP (15 per hour, max 100)
    hp_regen = min(15 * hours, agent["max_hp"] - agent["hp"])
    agent["hp"] = min(agent["max_hp"], agent["hp"] + hp_regen)
    # Reduce radiation (5 per hour)
    rad_reduce = 5 * hours
    agent["radiation"] = max(0, agent.get("radiation", 0) - rad_reduce)
    # Restore stamina
    agent["stamina"] = min(100, agent.get("stamina", 100) + 20 * hours)
    # Reset sleepiness
    agent["sleepiness"] = 0
    _add_memory(agent, world_turn, state, "sleep",
                f"Slept for {hours} hours",
                f"Rested well. HP +{hp_regen}, Radiation -{rad_reduce}.",
                {"hp_gained": hp_regen, "radiation_reduced": rad_reduce})


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
        "type": memory_type,
        "title": title,
        "summary": summary,
        "effects": effects,
    }
    agent.setdefault("memory", []).append(memory_entry)
    # Keep only the last 50 memory entries
    if len(agent["memory"]) > 50:
        agent["memory"] = agent["memory"][-50:]


# ─────────────────────────────────────────────────────────────────
# Bot decisions
# ─────────────────────────────────────────────────────────────────

# Import centralised item-type sets from balance data (single source of truth)
from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES


def _bot_consume(
    agent_id: str,
    agent: Dict[str, Any],
    item: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply a consumable item to agent and remove it from inventory. Returns events."""
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    from app.games.zone_stalkers.rules.world_rules import _apply_item_effects
    item_info = ITEM_TYPES.get(item["type"], {})
    effects = item_info.get("effects", {})
    _apply_item_effects(agent, effects)
    agent["inventory"] = [i for i in agent.get("inventory", []) if i["id"] != item["id"]]
    agent["action_used"] = True
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
    Make a prioritised decision for a bot-controlled stalker agent and apply it.

    Priority order (GDD §11):
      P1 – Heal if HP critical
      P2 – Eat/drink if hunger/thirst critical
      P3 – Sleep if sleepy and in safe location
      P4 – Pick up artifacts at current location
      P5 – Schedule exploration or move to adjacent location
    """
    events: List[Dict[str, Any]] = []
    loc_id = agent.get("location_id")
    locations = state.get("locations", {})
    loc = locations.get(loc_id, {})
    rng = random.Random(agent_id + str(world_turn))
    inventory = agent.get("inventory", [])

    # P1 — Heal if HP ≤ 30
    if agent.get("hp", 100) <= 30:
        heal_item = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
        if heal_item:
            return _bot_consume(agent_id, agent, heal_item)
        # No healing item: stay put
        agent["action_used"] = True
        return events

    # P2 — Eat if hunger ≥ 70
    if agent.get("hunger", 0) >= 70:
        food = next((i for i in inventory if i["type"] in FOOD_ITEM_TYPES), None)
        if food:
            return _bot_consume(agent_id, agent, food)

    # P2b — Drink if thirst ≥ 70
    if agent.get("thirst", 0) >= 70:
        nourishment = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
        if nourishment:
            return _bot_consume(agent_id, agent, nourishment)

    # P3 — Sleep if sleepiness ≥ 75 and in a safe/resting location
    if agent.get("sleepiness", 0) >= 75 and loc.get("type") in ("safe_hub", "ruins"):
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": 6,
            "turns_total": 6,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        events.append({
            "event_type": "sleep_started",
            "payload": {"agent_id": agent_id, "hours": 6},
        })
        return events

    # P4 — Pick up artifact if available
    artifacts = loc.get("artifacts", [])
    if artifacts:
        art = artifacts[0]
        loc["artifacts"] = artifacts[1:]
        agent.setdefault("inventory", []).append(art)
        agent["action_used"] = True
        events.append({
            "event_type": "artifact_picked_up",
            "payload": {"agent_id": agent_id, "artifact_id": art["id"], "artifact_type": art["type"]},
        })
        return events

    # P5a — Schedule exploration with 30% probability
    if rng.random() < 0.30:
        agent["scheduled_action"] = {
            "type": "explore",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        events.append({
            "event_type": "exploration_started",
            "payload": {"agent_id": agent_id, "location_id": loc_id},
        })
        return events

    # P5b — Move to adjacent location
    connections = loc.get("connections", [])
    if connections and rng.random() < 0.70:
        conn = rng.choice(connections)
        target_loc_id = conn["to"]
        if agent_id in loc.get("agents", []):
            loc["agents"].remove(agent_id)
        agent["location_id"] = target_loc_id
        new_loc = locations.get(target_loc_id, {})
        if agent_id not in new_loc.get("agents", []):
            new_loc.setdefault("agents", []).append(agent_id)
        agent["action_used"] = True
        events.append({
            "event_type": "agent_moved",
            "payload": {"agent_id": agent_id, "from": loc_id, "to": target_loc_id, "bot": True},
        })
        return events

    agent["action_used"] = True
    return events
