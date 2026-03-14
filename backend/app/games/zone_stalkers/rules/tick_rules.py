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


# 1 game turn = 1 real minute
MINUTES_PER_TURN = 1


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

    # Check game-over
    if state["world_turn"] > state.get("max_turns", 50):
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
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    events: List[Dict[str, Any]] = []

    loot_chance = 0.4

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
    if loc.get("anomalies") and rng.random() < 0.15 * (loc.get("anomaly_activity", 5) / 10):
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
    hours = sched.get("hours", sched.get("turns_total", 360) // 60)
    # Heal HP (15 per hour, max 100)
    hp_regen = min(15 * hours, agent["max_hp"] - agent["hp"])
    agent["hp"] = min(agent["max_hp"], agent["hp"] + hp_regen)
    # Reduce radiation (5 per hour)
    rad_reduce = 5 * hours
    agent["radiation"] = max(0, agent.get("radiation", 0) - rad_reduce)
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
        "world_minute": state.get("world_minute", 0),
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


def _find_nearest_trader_location(from_loc_id: str, state: Dict[str, Any]) -> Any:
    """
    BFS over the location graph to find the id of the closest location
    that contains a trader.  Returns None if no trader exists in the world.
    """
    trader_locs = set()
    for trader in state.get("traders", {}).values():
        tl = trader.get("location_id")
        if tl:
            trader_locs.add(tl)
    if not trader_locs:
        return None
    if from_loc_id in trader_locs:
        return from_loc_id

    locations = state.get("locations", {})
    visited = {from_loc_id}
    queue = [from_loc_id]
    while queue:
        current = queue.pop(0)
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn.get("to")
            if not nxt or conn.get("closed") or nxt in visited:
                continue
            if nxt in trader_locs:
                return nxt
            visited.add(nxt)
            queue.append(nxt)
    return None


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

    # ── Stalker memory ────────────────────────────────────────────
    item_names = ", ".join(i.get("name", i.get("type", "?")) for i in sold_items)
    trader_name = trader.get("name", trader["id"])
    loc_name = state.get("locations", {}).get(agent.get("location_id", ""), {}).get("name", "?")
    _add_memory(
        agent, world_turn, state, "trade_sell",
        f"Продал {item_names} торговцу {trader_name}",
        f"Заработал {sell_price_total} RU, продав {len(sold_items)} артефакт(ов) торговцу "
        f"{trader_name} в локации {loc_name}.",
        {"money_gained": sell_price_total, "items_sold": [i["type"] for i in sold_items],
         "trader_id": trader["id"]},
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
    """Schedule a travel action for a bot toward target_loc_id. Returns events."""
    from app.games.zone_stalkers.rules.world_rules import _bfs_route, _route_travel_turns
    route = _bfs_route(state["locations"], agent["location_id"], target_loc_id)
    if not route:
        return []
    turns = _route_travel_turns(route, state["locations"], agent["location_id"])
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": turns,
        "turns_total": turns,
        "target_id": target_loc_id,
        "route": route,
        "started_turn": world_turn,
    }
    agent["action_used"] = True
    return [{
        "event_type": "agent_travel_started",
        "payload": {"agent_id": agent_id, "destination": target_loc_id, "turns": turns, "bot": True},
    }]


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
    Make a goal-directed decision for a bot-controlled stalker agent.

    Decision layers:
      EMERGENCY – Heal / eat / drink (always overrides goal logic)
      SURVIVAL  – Sleep when exhausted
      GOAL      – If wealth < material_threshold: gather resources
                  Else: pursue global_goal
    """
    events: List[Dict[str, Any]] = []
    loc_id = agent.get("location_id")
    locations = state.get("locations", {})
    loc = locations.get(loc_id, {})
    rng = random.Random(agent_id + str(world_turn))
    inventory = agent.get("inventory", [])

    # ── EMERGENCY: Heal ────────────────────────────────────────────────────────
    if agent.get("hp", 100) <= 30:
        heal_item = next((i for i in inventory if i["type"] in HEAL_ITEM_TYPES), None)
        if heal_item:
            return _bot_consume(agent_id, agent, heal_item)
        # No healing: move toward a safe location (low anomaly_activity)
        safe_neighbors = [
            c["to"] for c in loc.get("connections", [])
            if not c.get("closed") and
               locations.get(c["to"], {}).get("anomaly_activity", 5) <= 3
        ]
        if safe_neighbors:
            target = rng.choice(safe_neighbors)
            agent["current_goal"] = "flee_to_safety"
            return _bot_schedule_travel(agent_id, agent, target, state, world_turn)
        agent["action_used"] = True
        return events

    # ── EMERGENCY: Eat ────────────────────────────────────────────────────────
    if agent.get("hunger", 0) >= 70:
        food = next((i for i in inventory if i["type"] in FOOD_ITEM_TYPES), None)
        if food:
            return _bot_consume(agent_id, agent, food)

    # ── EMERGENCY: Drink ──────────────────────────────────────────────────────
    if agent.get("thirst", 0) >= 70:
        drink = next((i for i in inventory if i["type"] in DRINK_ITEM_TYPES), None)
        if drink:
            return _bot_consume(agent_id, agent, drink)

    # ── SURVIVAL: Sleep ───────────────────────────────────────────────────────
    if agent.get("sleepiness", 0) >= 75:
        _sleep_hours = 6
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": _sleep_hours * 60,
            "turns_total": _sleep_hours * 60,
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
                items_desc = ", ".join(a.get("name", a.get("type", "?")) for a in artifacts_held)
                trader_loc_name = state.get("locations", {}).get(trader_loc, {}).get("name", trader_loc)
                _add_memory(
                    agent, world_turn, state, "decision",
                    f"Решил продать добычу",
                    f"В инвентаре: {items_desc}. Иду к торговцу в {trader_loc_name}.",
                    {"destination": trader_loc, "artifacts_count": len(artifacts_held)},
                )
                return _bot_schedule_travel(agent_id, agent, trader_loc, state, world_turn)

    # ── GOAL SELECTION ─────────────────────────────────────────────────────────
    wealth = _agent_wealth(agent)
    threshold = agent.get("material_threshold", 1000)
    global_goal = agent.get("global_goal", "survive")

    if wealth < threshold:
        # Phase 1: Accumulate resources before pursuing global goal
        agent["current_goal"] = "gather_resources"
        return _bot_gather_resources(agent_id, agent, loc_id, loc, state, world_turn, rng)
    else:
        # Phase 2: Pursue global goal
        agent["current_goal"] = f"goal_{global_goal}"
        return _bot_pursue_goal(agent_id, agent, global_goal, loc_id, loc, state, world_turn, rng)


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

    # G1 — Pick up artifact at current location
    artifacts = loc.get("artifacts", [])
    if artifacts:
        art = artifacts[0]
        loc["artifacts"] = artifacts[1:]
        agent.setdefault("inventory", []).append(art)
        agent["action_used"] = True
        art_name = art.get("name", art.get("type", "артефакт"))
        loc_name = loc.get("name", loc_id)
        _add_memory(
            agent, world_turn, state, "pickup",
            f"Подобрал {art_name}",
            f"Нашёл и поднял артефакт «{art_name}» в {loc_name}. "
            f"Ценность: {art.get('value', 0)} RU.",
            {"artifact_id": art["id"], "artifact_type": art["type"],
             "artifact_value": art.get("value", 0), "location_id": loc_id},
        )
        return [{"event_type": "artifact_picked_up",
                 "payload": {"agent_id": agent_id, "artifact_id": art["id"], "artifact_type": art["type"]}}]

    # G2 — Explore if anomalies are present (good chance of finding artifacts)
    if loc.get("anomalies") and rng.random() < 0.50:
        agent["scheduled_action"] = {
            "type": "explore",
            "turns_remaining": 30,  # 30 minutes of exploration
            "turns_total": 30,
            "target_id": loc_id,
            "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    # G3 — Move toward a more loot-rich adjacent location (higher anomaly_activity)
    connections = [c for c in loc.get("connections", []) if not c.get("closed")]
    if connections:
        # Prefer neighbors with higher anomaly_activity and artifacts present
        def loc_score(conn):
            nb = locations.get(conn["to"], {})
            return nb.get("anomaly_activity", 0) * 2 + len(nb.get("artifacts", [])) * 3
        best = max(connections, key=loc_score)
        if rng.random() < 0.70:
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    # G4 — Fallback: explore current location anyway
    if rng.random() < 0.40:
        agent["scheduled_action"] = {
            "type": "explore",
            "turns_remaining": 30,
            "turns_total": 30,
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
                return _bot_schedule_travel(agent_id, agent, rng.choice(safe_conn)["to"], state, world_turn)
        if agent.get("sleepiness", 0) >= 40:
            _sleep_hours = 4
            agent["scheduled_action"] = {
                "type": "sleep", "turns_remaining": _sleep_hours * 60,
                "turns_total": _sleep_hours * 60, "hours": _sleep_hours,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "sleep_started", "payload": {"agent_id": agent_id, "hours": _sleep_hours}}]
        # Pick up any nearby artifacts opportunistically
        artifacts = loc.get("artifacts", [])
        if artifacts:
            art = artifacts[0]
            loc["artifacts"] = artifacts[1:]
            agent.setdefault("inventory", []).append(art)
            agent["action_used"] = True
            return [{"event_type": "artifact_picked_up",
                     "payload": {"agent_id": agent_id, "artifact_id": art["id"], "artifact_type": art["type"]}}]

    elif global_goal == "get_rich":
        # Aggressively collect artifacts; move to high-anomaly zones
        artifacts = loc.get("artifacts", [])
        if artifacts:
            art = artifacts[0]
            loc["artifacts"] = artifacts[1:]
            agent.setdefault("inventory", []).append(art)
            agent["action_used"] = True
            return [{"event_type": "artifact_picked_up",
                     "payload": {"agent_id": agent_id, "artifact_id": art["id"], "artifact_type": art["type"]}}]
        if loc.get("anomalies") and rng.random() < 0.65:
            agent["scheduled_action"] = {
                "type": "explore", "turns_remaining": 30, "turns_total": 30,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "exploration_started",
                     "payload": {"agent_id": agent_id, "location_id": loc_id}}]
        if connections:
            best = max(connections,
                       key=lambda c: locations.get(c["to"], {}).get("anomaly_activity", 0))
            return _bot_schedule_travel(agent_id, agent, best["to"], state, world_turn)

    elif global_goal == "explore":
        # Visit as many unique locations as possible
        visited = {mem.get("effects", {}).get("to_loc") for mem in agent.get("memory", [])
                   if mem.get("type") == "travel"}
        visited.add(loc_id)
        unvisited = [c for c in connections if c["to"] not in visited]
        if unvisited:
            target = rng.choice(unvisited)
            return _bot_schedule_travel(agent_id, agent, target["to"], state, world_turn)
        # Explore current location if not recently explored
        if rng.random() < 0.50:
            agent["scheduled_action"] = {
                "type": "explore", "turns_remaining": 30, "turns_total": 30,
                "target_id": loc_id, "started_turn": world_turn,
            }
            agent["action_used"] = True
            return [{"event_type": "exploration_started",
                     "payload": {"agent_id": agent_id, "location_id": loc_id}}]
        if connections:
            return _bot_schedule_travel(agent_id, agent, rng.choice(connections)["to"], state, world_turn)

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
                return _bot_schedule_travel(agent_id, agent, target_conn["to"], state, world_turn)

    # Fallback: wander
    if connections and rng.random() < 0.60:
        conn = rng.choice(connections)
        return _bot_schedule_travel(agent_id, agent, conn["to"], state, world_turn)
    if rng.random() < 0.30:
        agent["scheduled_action"] = {
            "type": "explore", "turns_remaining": 30, "turns_total": 30,
            "target_id": loc_id, "started_turn": world_turn,
        }
        agent["action_used"] = True
        return [{"event_type": "exploration_started",
                 "payload": {"agent_id": agent_id, "location_id": loc_id}}]

    agent["action_used"] = True
    return []
