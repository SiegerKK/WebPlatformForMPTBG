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
    STEP_WAIT,
    STEP_LEGACY_SCHEDULED_ACTION,
    STEP_HEAL_SELF,
)


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
        STEP_ASK_FOR_INTEL,
    ):
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
    hours = step.payload.get("hours", DEFAULT_SLEEP_HOURS)
    from app.games.zone_stalkers.rules.tick_rules import MINUTES_PER_TURN
    turns = hours * 60 // MINUTES_PER_TURN
    agent["scheduled_action"] = {
        "type": "sleep",
        "hours": hours,
        "turns_remaining": turns,
        "turns_total": turns,
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
    }
    action_kind = action_kind_map.get(reason_key, "consume_heal")
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
