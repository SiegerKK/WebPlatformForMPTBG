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
    from app.games.zone_stalkers.rules.tick_rules import _bot_schedule_travel
    target_id = step.payload.get("target_id")
    if not target_id:
        return []
    return _bot_schedule_travel(agent_id, agent, target_id, state, world_turn)


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
    )
    from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader

    category = step.payload.get("item_category", "medical")
    type_map = {
        "medical": HEAL_ITEM_TYPES,
        "food": FOOD_ITEM_TYPES,
        "drink": DRINK_ITEM_TYPES,
        "weapon": WEAPON_ITEM_TYPES,
        "armor": ARMOR_ITEM_TYPES,
        "ammo": AMMO_ITEM_TYPES,
        "equipment": WEAPON_ITEM_TYPES,  # simplified
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
    """Sell artifacts at a co-located trader."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_sell_to_trader
    loc_id = agent.get("location_id", "")
    return _bot_sell_to_trader(agent_id, agent, loc_id, state, world_turn) or []


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
        _bot_ask_colocated_stalkers_about_agent(agent_id, agent, target_id, state, world_turn)
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
    agent["action_used"] = True
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
