"""executors — execute the current PlanStep for an agent.

``execute_plan_step(ctx, plan, state, world_turn)`` performs the concrete
in-world action corresponding to the current step, applying side effects
to the mutable ``agent`` dict and ``state``.

Executor invariants (spec §4.5 / migration plan §9):
    - Executors apply game effects (consume items, travel, trade, heal).
    - Executors do NOT choose strategy, select motives, or pick social intents.
    - Executors write memory entries only for the action just performed.
    - Executors return a list of game events.

During Phase 1–4 migration, most steps delegate to existing tick_rules helpers
to avoid behaviour divergence.  The STEP_LEGACY_SCHEDULED_ACTION kind is a
special passthrough for steps that have no dedicated executor yet.
"""
from __future__ import annotations

from typing import Any

from .models.agent_context import AgentContext
from .models.plan import (
    Plan, PlanStep,
    STEP_TRAVEL_TO_LOCATION,
    STEP_SLEEP_FOR_HOURS,
    STEP_EXPLORE_LOCATION,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_CONSUME_ITEM,
    STEP_EQUIP_ITEM,
    STEP_PICKUP_ITEM,
    STEP_ASK_FOR_INTEL,
    STEP_LOOK_FOR_TRACKS,
    STEP_QUESTION_WITNESSES,
    STEP_SEARCH_TARGET,
    STEP_START_COMBAT,
    STEP_MONITOR_COMBAT,
    STEP_CONFIRM_KILL,
    STEP_LEAVE_ZONE,
    STEP_WAIT,
    STEP_LEGACY_SCHEDULED_ACTION,
    STEP_HEAL_SELF,
)

_SEARCH_EXHAUSTION_THRESHOLD = 3
_SEARCH_LOCATION_COOLDOWN_TURNS = 300


def execute_plan_step(
    ctx: AgentContext,
    plan: Plan,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Execute the current step of ``plan`` and return any emitted events.

    If the step completes this tick, ``plan.advance()`` is called so the
    planner knows to move to the next step next tick.

    Parameters
    ----------
    ctx
        AgentContext (read-only snapshot built this tick).
    plan
        The active Plan; ``plan.current_step`` is executed.
    state
        Mutable world state.
    world_turn
        Current world turn.

    Returns
    -------
    list[dict]
        Game events emitted by this step.
    """
    step = plan.current_step
    if step is None:
        return []

    agent_id = ctx.agent_id
    agent = state["agents"][agent_id]     # mutable reference

    dispatch: dict[str, Any] = {
        STEP_TRAVEL_TO_LOCATION:   _exec_travel,
        STEP_SLEEP_FOR_HOURS:      _exec_sleep,
        STEP_EXPLORE_LOCATION:     _exec_explore,
        STEP_TRADE_BUY_ITEM:       _exec_trade_buy,
        STEP_TRADE_SELL_ITEM:      _exec_trade_sell,
        STEP_CONSUME_ITEM:         _exec_consume,
        STEP_EQUIP_ITEM:           _exec_equip,
        STEP_PICKUP_ITEM:          _exec_pickup,
        STEP_HEAL_SELF:            _exec_consume,   # same as consume
        STEP_ASK_FOR_INTEL:        _exec_ask_for_intel,
        STEP_LOOK_FOR_TRACKS:      _exec_look_for_tracks,
        STEP_QUESTION_WITNESSES:   _exec_question_witnesses,
        STEP_SEARCH_TARGET:        _exec_search_target,
        STEP_START_COMBAT:         _exec_start_combat,
        STEP_MONITOR_COMBAT:       _exec_monitor_combat,
        STEP_CONFIRM_KILL:         _exec_confirm_kill,
        STEP_LEAVE_ZONE:           _exec_leave_zone,
        STEP_WAIT:                 _exec_wait,
        STEP_LEGACY_SCHEDULED_ACTION: _exec_legacy_passthrough,
    }

    executor = dispatch.get(step.kind, _exec_unknown)
    events = executor(agent_id, agent, step, ctx, state, world_turn)

    # If the step is a one-tick action (not a scheduled multi-tick action),
    # advance the plan index immediately.
    if step.kind in (
        STEP_CONSUME_ITEM, STEP_EQUIP_ITEM, STEP_PICKUP_ITEM,
        STEP_HEAL_SELF, STEP_WAIT, STEP_TRADE_BUY_ITEM, STEP_TRADE_SELL_ITEM,
        STEP_ASK_FOR_INTEL, STEP_LOOK_FOR_TRACKS, STEP_QUESTION_WITNESSES,
        STEP_SEARCH_TARGET, STEP_START_COMBAT, STEP_CONFIRM_KILL,
    ):
        plan.advance()
    elif step.kind == STEP_MONITOR_COMBAT and bool(step.payload.get("_monitor_complete")):
        plan.advance()

    return events


# ── Step executors ─────────────────────────────────────────────────────────────

def _exec_travel(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Delegate to the existing _bot_schedule_travel helper."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_schedule_travel, _add_memory
    target_id = step.payload.get("target_id")
    if not target_id:
        return []
    reason = step.payload.get("reason", "")
    emergency_flee = (reason == "flee_emission")

    # Write goal-specific decision memory entries (v1 compat)
    if reason == "flee_emission":
        _write_once_decision(agent, world_turn, state,
            "🚨 Бегу от выброса",
            {"action_kind": "flee_emission", "destination": target_id},
            f"Бегу в безопасное место: {target_id}")
    elif reason in ("heal_self", "emergency_heal", "seek_medical", "buy_heal"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "medical", target_id,
                            emergency=True)
    elif reason in ("seek_food", "emergency_food", "buy_food"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "food", target_id,
                            emergency=True)
    elif reason in ("seek_drink", "emergency_drink", "buy_drink"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "drink", target_id,
                            emergency=True)
    elif reason in ("sell_artifacts", "sell_artifacts_get_rich"):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART_TYPES
        _art_set = frozenset(_ART_TYPES.keys())
        _artifacts_count = sum(1 for i in agent.get("inventory", []) if i.get("type") in _art_set)
        _write_once_decision(agent, world_turn, state,
            "🎁 Иду продавать артефакты",
            {"action_kind": "sell_artifacts", "artifacts_count": _artifacts_count,
             "destination": target_id},
            f"Иду к торговцу в {target_id} продавать {_artifacts_count} артефакт(ов)")
    elif reason == "get_rich_travel_to_anomaly":
        # Write a decision record only when the target anomaly location changes.
        _prev_anomaly_target = next(
            (m.get("effects", {}).get("target_location_id")
             for m in reversed(agent.get("memory", []))
             if m.get("type") == "decision"
             and m.get("effects", {}).get("action_kind") == "anomaly_search_target_chosen"),
            None,
        )
        if _prev_anomaly_target != target_id:
            _target_loc_name = state.get("locations", {}).get(target_id, {}).get("name", target_id)
            _add_memory(
                agent, world_turn, state, "decision",
                f"🗺️ Выбрал локацию для поиска: «{_target_loc_name}»",
                {"action_kind": "anomaly_search_target_chosen",
                 "target_location_id": target_id},
                summary=f"Отправляюсь искать артефакты в «{_target_loc_name}»",
            )

    return _bot_schedule_travel(agent_id, agent, target_id, state, world_turn,
                                emergency_flee=emergency_flee)


def _exec_sleep(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Schedule a sleep action."""
    from app.games.zone_stalkers.rules.tick_rules import DEFAULT_SLEEP_HOURS
    hours = max(1, int(step.payload.get("hours", DEFAULT_SLEEP_HOURS)))
    from app.games.zone_stalkers.rules.tick_rules import MINUTES_PER_TURN
    turns = hours * 60 // MINUTES_PER_TURN
    agent["scheduled_action"] = {
        "type": "sleep",
        "hours": hours,
        "turns_remaining": turns,
        "turns_total": turns,
        "sleep_progress_turns": 0,
        "sleep_intervals_applied": 0,
        "sleep_turns_slept": 0,
    }
    agent["action_used"] = True
    return []


def _exec_explore(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Schedule an exploration action."""
    from app.games.zone_stalkers.rules.tick_rules import EXPLORE_DURATION_TURNS
    target_id = step.payload.get("target_id", agent.get("location_id", ""))
    loc_name = state.get("locations", {}).get(target_id, {}).get("name", target_id)
    _write_once_decision(agent, world_turn, state,
        "🔍 Исследую аномальную зону",
        {"action_kind": "explore_decision", "target_id": target_id},
        f"Решил исследовать локацию «{loc_name}» в поисках артефактов")
    agent["scheduled_action"] = {
        "type": "explore_anomaly_location",
        "target_id": target_id,
        "turns_remaining": EXPLORE_DURATION_TURNS,
        "turns_total": EXPLORE_DURATION_TURNS,
        "started_turn": world_turn,
    }
    agent["action_used"] = True
    return [{"event_type": "exploration_started",
             "payload": {"agent_id": agent_id, "location_id": target_id}}]


def _exec_trade_buy(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Attempt to buy from a co-located trader."""
    from app.games.zone_stalkers.balance.items import (
        HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES,
        WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_ITEM_TYPES,
        AMMO_FOR_WEAPON,
    )
    from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader

    category = step.payload.get("item_category", "medical")

    # Upgrade categories: buy the best upgrade target and immediately equip it.
    if category in ("weapon_upgrade", "armor_upgrade"):
        slot = "weapon" if category == "weapon_upgrade" else "armor"
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import (
            _find_upgrade_target, _bot_equip_from_inventory,
        )
        item_types_for_slot = WEAPON_ITEM_TYPES if slot == "weapon" else ARMOR_ITEM_TYPES
        agent_risk = float(agent.get("risk_tolerance", 0.5))
        agent_money = agent.get("money", 0)
        current = agent.get("equipment", {}).get(slot)
        current_type = current.get("type") if isinstance(current, dict) else None
        upgrade_key = _find_upgrade_target(item_types_for_slot, current_type, agent_risk, agent_money)
        if upgrade_key is None:
            return []
        bought = _bot_buy_from_trader(agent_id, agent, frozenset([upgrade_key]), state, world_turn,
                                      purchase_reason=f"апгрейд {slot}") or []
        if bought:
            equip_evs = _bot_equip_from_inventory(
                agent_id, agent, frozenset([upgrade_key]), slot, state, world_turn,
            )
            return bought + equip_evs
        return []

    # Bug fix: defensive guard — never buy equipment the agent already has equipped.
    eq = agent.get("equipment", {})
    if category in ("weapon", "equipment") and eq.get("weapon") is not None:
        return []   # already armed — nothing to purchase
    if category == "armor" and eq.get("armor") is not None:
        return []   # already armoured — nothing to purchase

    if category == "ammo":
        # Only buy ammo that is compatible with the agent's equipped weapon.
        # Using AMMO_ITEM_TYPES (all ammo) would allow buying wrong-caliber ammo.
        weapon = eq.get("weapon")
        weapon_type = weapon.get("type") if isinstance(weapon, dict) else None
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        if not required_ammo:
            return []   # no weapon or unknown caliber — nothing to buy
        item_types: "frozenset[str]" = frozenset([required_ammo])
    else:
        type_map = {
            "medical": HEAL_ITEM_TYPES,
            "food": FOOD_ITEM_TYPES,
            "drink": DRINK_ITEM_TYPES,
            "weapon": WEAPON_ITEM_TYPES,
            "armor": ARMOR_ITEM_TYPES,
            "equipment": WEAPON_ITEM_TYPES,  # legacy fallback; prefer explicit "weapon"/"armor"
        }
        item_types = type_map.get(category, HEAL_ITEM_TYPES)

    # PR2 survival mode: force cheapest affordable viable item.
    buy_mode = step.payload.get("buy_mode")
    compatible_item_types = step.payload.get("compatible_item_types")
    if isinstance(compatible_item_types, list) and compatible_item_types:
        item_types = frozenset(str(t) for t in compatible_item_types)

    if buy_mode == "survival_cheapest":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES

        affordable = sorted(
            (
                t for t in item_types
                if t in ITEM_TYPES and agent.get("money", 0) >= int(ITEM_TYPES[t].get("value", 0) * 1.5)
            ),
            key=lambda t: (int(ITEM_TYPES[t].get("value", 0) * 1.5), t),
        )
        if not affordable:
            return []
        item_types = frozenset([affordable[0]])
    elif buy_mode == "reserve_basic":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES

        preferred = step.payload.get("preferred_item_types") or []
        preferred_set = frozenset(str(t) for t in preferred if str(t) in item_types)
        candidate_pool = preferred_set or item_types
        affordable = sorted(
            (
                t for t in candidate_pool
                if t in ITEM_TYPES and agent.get("money", 0) >= int(ITEM_TYPES[t].get("value", 0) * 1.5)
            ),
            key=lambda t: (int(ITEM_TYPES[t].get("value", 0) * 1.5), t),
        )
        if not affordable:
            return []
        item_types = frozenset([affordable[0]])

    reason = step.payload.get("reason", f"buy_{category}")
    return _bot_buy_from_trader(agent_id, agent, item_types, state, world_turn,
                                purchase_reason=reason) or []


def _exec_trade_sell(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Sell inventory items at a co-located trader.

    Dispatches based on ``step.payload["item_category"]``:
    - ``"any_sellable"`` — sell non-critical items (artifacts, detectors, spare
      weapons/armor) to raise cash for urgent needs via ``_bot_sell_items_for_cash``.
    - anything else (default) — sell all artifacts via ``_bot_sell_to_trader``.
    """
    from app.games.zone_stalkers.rules.tick_rules import (
        _bot_sell_to_trader,
        _bot_sell_items_for_cash,
        _find_trader_at_location,
    )
    loc_id = agent.get("location_id", "")
    trader = _find_trader_at_location(loc_id, state)
    if trader is None:
        agent["action_used"] = True
        return []
    item_category = step.payload.get("item_category", "artifact")
    if item_category == "any_sellable":
        return _bot_sell_items_for_cash(agent_id, agent, trader, state, world_turn) or []
    return _bot_sell_to_trader(agent_id, agent, trader, state, world_turn) or []


def _exec_consume(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Consume an item from inventory."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_consume
    item_type = step.payload.get("item_type")
    inventory = agent.get("inventory", [])
    item = next((i for i in inventory if i.get("type") == item_type), None)
    if not item:
        return []
    reason_key = step.payload.get("reason", "consume")
    action_kind_map = {
        "emergency_heal": "consume_heal",
        "emergency_food": "consume_food",
        "emergency_drink": "consume_drink",
        "prepare_sleep_food": "consume_food",
        "prepare_sleep_drink": "consume_drink",
        "opportunistic_food": "consume_food",
        "opportunistic_drink": "consume_drink",
        # PR2 explicit reason keys
        "need_food": "consume_food",
        "need_drink": "consume_drink",
        "need_medical": "consume_heal",
    }
    if reason_key in action_kind_map:
        action_kind = action_kind_map[reason_key]
    else:
        # Fallback: derive action_kind from item type category.
        from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES, DRINK_ITEM_TYPES
        if item.get("type") in FOOD_ITEM_TYPES:
            action_kind = "consume_food"
        elif item.get("type") in DRINK_ITEM_TYPES:
            action_kind = "consume_drink"
        else:
            action_kind = "consume_heal"
    return _bot_consume(agent_id, agent, item, world_turn, state, action_kind) or []


def _exec_equip(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Equip an item from inventory."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_equip_from_inventory
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES
    slot = step.payload.get("slot", "weapon")
    item_types = WEAPON_ITEM_TYPES if slot == "weapon" else ARMOR_ITEM_TYPES
    return _bot_equip_from_inventory(agent_id, agent, item_types, slot, state, world_turn) or []


def _exec_pickup(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Pick up an item from the ground."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_item_from_ground
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
    item_types = step.payload.get("item_types", WEAPON_ITEM_TYPES)
    return _bot_pickup_item_from_ground(agent_id, agent, item_types, state, world_turn) or []


def _exec_ask_for_intel(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Ask co-located stalkers for intelligence."""
    from app.games.zone_stalkers.rules.tick_rules import (
        _bot_buy_hunt_intel_from_trader,
        _find_hunt_intel_location,
    )

    target_id = step.payload.get("target_id")
    if target_id:
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_agent
        # _bot_ask_colocated_stalkers_about_agent(agent_id, agent, target_agent_id,
        #   target_agent_name, state, world_turn)
        target = state.get("agents", {}).get(target_id, {})
        target_name = target.get("name", target_id) if target else target_id
        _bot_ask_colocated_stalkers_about_agent(
            agent_id, agent, target_id, target_name, state, world_turn
        )
        # If stalkers had no intel, try trader intel at current location.
        intel_loc = _find_hunt_intel_location(agent, str(target_id), state)
        if not intel_loc:
            _bot_buy_hunt_intel_from_trader(
                agent_id,
                agent,
                str(target_id),
                target_name,
                state,
                world_turn,
            )
        agent["action_used"] = True
    return []


def _exec_question_witnesses(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    return _exec_ask_for_intel(agent_id, agent, step, ctx, state, world_turn)


def _count_target_not_found_failures(
    agent: dict[str, Any],
    *,
    target_id: str,
    location_id: str,
) -> int:
    count = 0
    for memory in agent.get("memory", []):
        if not isinstance(memory, dict):
            continue
        effects = memory.get("effects")
        if not isinstance(effects, dict):
            continue
        if effects.get("action_kind") != "target_not_found":
            continue
        if str(effects.get("target_id") or "") != target_id:
            continue
        if str(effects.get("location_id") or "") != location_id:
            continue
        count += 1
    return count


def _exec_look_for_tracks(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    current_loc = str(agent.get("location_id") or "")
    target_loc = str(target.get("location_id") or "") if isinstance(target, dict) else ""

    if isinstance(target, dict) and target.get("is_alive", True) and target_loc and target_loc != current_loc:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "👣 Обнаружены следы цели",
            {
                "action_kind": "target_route_observed",
                "target_id": target_id,
                "location_id": target_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
            },
            summary="Нашёл свежие следы и предполагаемый маршрут цели.",
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🧭 Цель сместилась по следам",
            {
                "action_kind": "target_moved",
                "target_id": target_id,
                "location_id": target_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
            },
            summary="Следы указывают, куда ушла цель.",
        )
    else:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "👣 Следы цели не найдены",
            {
                "action_kind": "no_tracks_found",
                "target_id": target_id,
                "location_id": current_loc,
            },
            summary="Следов цели в текущей локации не найдено.",
        )
    agent["action_used"] = True
    return []


def _exec_search_target(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    target_loc = target.get("location_id") if isinstance(target, dict) else None
    current_loc = agent.get("location_id")
    if target and target.get("is_alive", True) and target_loc == current_loc:
        target_name = target.get("name", target_id)
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            f"🎯 Цель обнаружена: «{target_name}»",
            {
                "action_kind": "target_seen",
                "target_id": target_id,
                "target_name": target_name,
                "location_id": current_loc,
                "hp": target.get("hp"),
            },
            summary=f"Вижу цель «{target_name}» в текущей локации.",
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "📍 Последнее известное местоположение цели обновлено",
            {
                "action_kind": "target_last_known_location",
                "target_id": target_id,
                "location_id": current_loc,
            },
            summary="Обновил последнее известное местоположение цели.",
        )
        weapon_type = (target.get("equipment", {}) or {}).get("weapon", {}).get("type") if isinstance(target, dict) else None
        armor_type = (target.get("equipment", {}) or {}).get("armor", {}).get("type") if isinstance(target, dict) else None
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🧪 Оценка боевой силы цели",
            {
                "action_kind": "target_combat_strength_observed",
                "target_id": target_id,
                "location_id": current_loc,
                "combat_strength": max(0.1, min(1.0, float(target.get("hp", 100)) / 100.0)),
                "weapon_type": weapon_type,
                "armor_type": armor_type,
            },
            summary="Оценил боевую силу цели по текущему состоянию.",
        )
        if weapon_type or armor_type:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                "🔎 Снаряжение цели замечено",
                {
                    "action_kind": "target_equipment_seen",
                    "target_id": target_id,
                    "location_id": current_loc,
                    "weapon_type": weapon_type,
                    "armor_type": armor_type,
                },
                summary="Зафиксировал видимое снаряжение цели.",
            )
    else:
        expected_loc = step.payload.get("target_location_id") or current_loc
        failed_search_count = _count_target_not_found_failures(
            agent,
            target_id=target_id,
            location_id=str(expected_loc),
        ) + 1
        cooldown_until_turn = (
            world_turn + _SEARCH_LOCATION_COOLDOWN_TURNS
            if failed_search_count >= _SEARCH_EXHAUSTION_THRESHOLD
            else None
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "❓ Цель не найдена в ожидаемой локации",
            {
                "action_kind": "target_not_found",
                "target_id": target_id,
                "location_id": expected_loc,
                "confirmed_empty": True,
                "failed_search_count": failed_search_count,
                "cooldown_until_turn": cooldown_until_turn,
            },
            summary="Цель отсутствует в проверенной локации.",
        )
    agent["action_used"] = True
    return []


def _exec_start_combat(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _compat_initiate_combat, _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []
    target = state.get("agents", {}).get(target_id)
    if not isinstance(target, dict) or not target.get("is_alive", True):
        agent["action_used"] = True
        return []

    current_loc = agent.get("location_id")
    if target.get("location_id") != current_loc:
        target_loc = target.get("location_id")
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🎯 Цель сместилась перед боем",
            {
                "action_kind": "target_moved",
                "target_id": target_id,
                "location_id": current_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
                "confirmed_empty": True,
            },
            summary="Не удалось начать бой: цель сместилась в другую локацию.",
        )
        agent["action_used"] = True
        return []

    target_name = target.get("name", target_id)
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        f"🎯 Цель обнаружена перед боем: «{target_name}»",
        {
            "action_kind": "target_seen",
            "target_id": target_id,
            "target_name": target_name,
            "location_id": current_loc,
            "hp": target.get("hp"),
        },
        summary=f"Подтвердил визуальный контакт с целью «{target_name}».",
    )
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        "📍 Последнее известное местоположение цели обновлено",
        {
            "action_kind": "target_last_known_location",
            "target_id": target_id,
            "location_id": current_loc,
        },
        summary="Перед боем обновил последнее известное местоположение цели.",
    )

    weapon = (agent.get("equipment", {}) or {}).get("weapon")
    if not isinstance(weapon, dict):
        agent["action_used"] = True
        return []

    events = _compat_initiate_combat(
        agent_id,
        agent,
        target_id,
        target,
        current_loc,
        state,
        world_turn,
    )
    return events or []


def _exec_confirm_kill(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    if isinstance(target, dict) and not target.get("is_alive", True):
        target_name = target.get("name", target_id)
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
                "location_id": target.get("location_id"),
                "killer_id": agent_id,
                "cause": "combat",
            },
            summary=f"Подтвердил, что цель «{target_name}» ликвидирована.",
        )
    else:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "❌ Ликвидация цели не подтверждена",
            {
                "action_kind": "hunt_failed",
                "target_id": target_id,
                "reason": "target_still_alive",
            },
            summary="Подтверждение не удалось: цель всё ещё жива.",
        )
    agent["action_used"] = True
    return []


def _exec_monitor_combat(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        step.payload["_monitor_complete"] = True
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    target_alive = bool(target.get("is_alive", True)) if isinstance(target, dict) else False

    combat_active = False
    for combat in (state.get("combat_interactions", {}) or {}).values():
        if not isinstance(combat, dict):
            continue
        if combat.get("ended") or combat.get("ended_turn") is not None:
            continue
        participants = combat.get("participants", {})
        if not isinstance(participants, dict):
            continue
        if agent_id in participants and target_id in participants:
            combat_active = True
            break

    step.payload["_monitor_complete"] = (not combat_active) or (not target_alive)
    agent["action_used"] = True
    return []


def _exec_wait(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Mark action_used without doing anything."""
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    agent["action_used"] = True
    reason = step.payload.get("reason", "")
    if reason == "wait_in_shelter":
        # Merge-in-place: if we already have a wait_in_shelter decision entry,
        # just update its world_turn instead of adding a new entry every tick.
        # This prevents the shelter/v2_decision pair from writing duplicate
        # entries on every emission tick.
        _shelter_entry = next(
            (m for m in reversed(agent.get("memory", []))
             if m.get("type") == "decision"
             and m.get("effects", {}).get("action_kind") == "wait_in_shelter"),
            None,
        )
        if _shelter_entry is not None:
            _shelter_entry["world_turn"] = world_turn
        else:
            _add_memory(agent, world_turn, state, "decision",
                "🏠 Укрываюсь от выброса",
                {"action_kind": "wait_in_shelter"},
                summary="Нахожусь в укрытии — жду окончания выброса")
    elif reason == "trapped_on_dangerous_terrain":
        _write_once_decision(agent, world_turn, state,
            "⚠️ Нет выхода: застрял на опасной местности",
            {"action_kind": "trapped_on_dangerous_terrain"},
            "Все соседние локации тоже опасны — укрыться негде")
    return []


def _exec_legacy_passthrough(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Pass through to legacy tick_rules logic (no-op at plan level)."""
    # The actual decision is handled by the legacy cascade in tick_rules.
    # This step simply signals "let the old code decide" — no side effects here.
    return []


def _exec_unknown(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Fallback executor for unrecognised step kinds."""
    agent["action_used"] = True
    return []


def _exec_leave_zone(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _execute_leave_zone, _bot_route_to_exit

    loc_id = agent.get("location_id", "")
    loc = (state.get("locations", {}) or {}).get(loc_id, {})
    if not bool(loc.get("exit_zone")):
        return _bot_route_to_exit(agent_id, agent, state, world_turn)
    return _execute_leave_zone(agent_id, agent, state, world_turn)


# ── Memory-write helpers used by executors ────────────────────────────────────

def _write_once_decision(
    agent: dict[str, Any],
    world_turn: int,
    state: dict[str, Any],
    title: str,
    effects: dict[str, Any],
    summary: str,
) -> None:
    """Write a decision memory entry but only if one with the same action_kind is not
    already the most recent decision (anti-spam)."""
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    action_kind = effects.get("action_kind")
    for mem in reversed(agent.get("memory", [])):
        if mem.get("type") == "decision":
            if mem.get("effects", {}).get("action_kind") == action_kind:
                return  # already last decision is the same kind
            break  # last decision is different — safe to write
    _add_memory(agent, world_turn, state, "decision", title, effects, summary=summary)


def _write_once_by_dest(
    agent: dict[str, Any],
    world_turn: int,
    state: dict[str, Any],
    action_kind: str,
    item_category: str,
    destination: str,
    emergency: bool = False,
) -> None:
    """Write a seek_item decision memory (anti-spam by destination + category)."""
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    for mem in agent.get("memory", []):
        if mem.get("type") != "decision":
            continue
        fx = mem.get("effects", {})
        if (fx.get("action_kind") == action_kind
                and fx.get("destination") == destination
                and fx.get("item_category") == item_category):
            return  # already recorded
    effects: dict[str, Any] = {
        "action_kind": action_kind,
        "item_category": item_category,
        "destination": destination,
    }
    if emergency:
        effects["emergency"] = True
    _add_memory(agent, world_turn, state, "decision",
                f"🔍 Ищу {item_category} в {destination}",
                effects,
                summary=f"Отправляюсь искать предметы категории {item_category}")
