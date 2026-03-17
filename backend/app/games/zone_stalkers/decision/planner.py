"""planner — build a short Plan from a selected Intent.

``build_plan(ctx, intent, world_turn)`` translates a dominant Intent into a
short ordered sequence of PlanSteps.

Design principles (Phase 4):
- Plans are SHORT (1–3 steps max for MVP).
- Plans are rebuilt when the intent changes or expires.
- The first step maps directly to a ``scheduled_action`` via ``bridges.py``.
- Plans are tagged with ``expires_turn`` when the intent is time-bounded.

Supported intents (Phase 4):
    travel-based:    INTENT_GET_RICH, INTENT_HUNT_TARGET, INTENT_SEARCH_INFORMATION,
                     INTENT_LEAVE_ZONE, INTENT_SELL_ARTIFACTS, INTENT_UPGRADE_EQUIPMENT
    immediate:       INTENT_HEAL_SELF, INTENT_SEEK_FOOD, INTENT_SEEK_WATER, INTENT_REST,
                     INTENT_RESUPPLY
    environmental:   INTENT_FLEE_EMISSION, INTENT_WAIT_IN_SHELTER
    fallback:        INTENT_IDLE
"""
from __future__ import annotations

from typing import Any, Optional

from .models.agent_context import AgentContext
from .models.intent import (
    Intent,
    INTENT_ESCAPE_DANGER,
    INTENT_FLEE_EMISSION,
    INTENT_WAIT_IN_SHELTER,
    INTENT_HEAL_SELF,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_REST,
    INTENT_RESUPPLY,
    INTENT_SELL_ARTIFACTS,
    INTENT_TRADE,
    INTENT_GET_RICH,
    INTENT_HUNT_TARGET,
    INTENT_SEARCH_INFORMATION,
    INTENT_LEAVE_ZONE,
    INTENT_UPGRADE_EQUIPMENT,
    INTENT_EXPLORE,
    INTENT_IDLE,
    INTENT_FOLLOW_GROUP_PLAN,
    INTENT_ASSIST_ALLY,
)
from .models.plan import (
    Plan, PlanStep,
    STEP_TRAVEL_TO_LOCATION,
    STEP_SLEEP_FOR_HOURS,
    STEP_EXPLORE_LOCATION,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_CONSUME_ITEM,
    STEP_ASK_FOR_INTEL,
    STEP_WAIT,
    STEP_LEGACY_SCHEDULED_ACTION,
)


def build_plan(
    ctx: AgentContext,
    intent: Intent,
    state: dict[str, Any],
    world_turn: int,
) -> Plan:
    """Build a short Plan for the given Intent.

    Parameters
    ----------
    ctx
        AgentContext for this agent.
    intent
        The selected dominant Intent.
    state
        The full world state dict (read-only).
    world_turn
        Current world turn.

    Returns
    -------
    Plan
        A Plan with at least one step.  Falls back to a single STEP_WAIT
        if no concrete steps can be determined.
    """
    kind = intent.kind
    agent = ctx.self_state

    builder_map = {
        INTENT_FLEE_EMISSION:       _plan_flee_emission,
        INTENT_WAIT_IN_SHELTER:     _plan_wait_in_shelter,
        INTENT_ESCAPE_DANGER:       _plan_heal_or_flee,
        INTENT_HEAL_SELF:           _plan_heal_or_flee,
        INTENT_SEEK_FOOD:           _plan_seek_consumable,
        INTENT_SEEK_WATER:          _plan_seek_consumable,
        INTENT_REST:                _plan_rest,
        INTENT_RESUPPLY:            _plan_resupply,
        INTENT_SELL_ARTIFACTS:      _plan_sell_artifacts,
        INTENT_TRADE:               _plan_sell_artifacts,
        INTENT_GET_RICH:            _plan_get_rich,
        INTENT_HUNT_TARGET:         _plan_hunt_target,
        INTENT_SEARCH_INFORMATION:  _plan_search_information,
        INTENT_LEAVE_ZONE:          _plan_leave_zone,
        INTENT_UPGRADE_EQUIPMENT:   _plan_upgrade_equipment,
        INTENT_EXPLORE:             _plan_explore,
        INTENT_FOLLOW_GROUP_PLAN:   _plan_follow_group,
        INTENT_ASSIST_ALLY:         _plan_assist_ally,
    }

    builder = builder_map.get(kind)
    if builder is not None:
        plan = builder(ctx, intent, state, world_turn)
        if plan is not None:
            return plan

    # Fallback idle plan
    return _idle_plan(intent, world_turn)


# ── Plan builders ─────────────────────────────────────────────────────────────

def _plan_flee_emission(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    target_loc = intent.target_location_id
    if not target_loc:
        # Try to find nearest safe location from ctx if not set on intent
        target_loc = _nearest_safe_location(ctx, state)
    if not target_loc:
        return None
    step = PlanStep(
        kind=STEP_TRAVEL_TO_LOCATION,
        payload={"target_id": target_loc, "reason": "flee_emission"},
        interruptible=False,
        expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state),
    )
    return Plan(
        intent_kind=intent.kind,
        steps=[step],
        interruptible=False,
        confidence=0.9,
        created_turn=world_turn,
    )


def _plan_wait_in_shelter(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    step = PlanStep(
        kind=STEP_WAIT,
        payload={"reason": "wait_in_shelter"},
        interruptible=False,
        expected_duration_ticks=1,
    )
    return Plan(
        intent_kind=intent.kind,
        steps=[step],
        interruptible=False,
        confidence=1.0,
        created_turn=world_turn,
    )


def _plan_heal_or_flee(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    agent = ctx.self_state
    inventory = agent.get("inventory", [])

    from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
    heal_item = next((i for i in inventory if i.get("type") in HEAL_ITEM_TYPES), None)
    if heal_item:
        step = PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": heal_item.get("type"), "reason": "emergency_heal"},
            interruptible=False,
            expected_duration_ticks=1,
        )
        return Plan(
            intent_kind=intent.kind, steps=[step], interruptible=False,
            confidence=1.0, created_turn=world_turn,
        )

    # No heal item — travel to nearest trader
    trader_loc = _nearest_trader_location(ctx, state)
    if trader_loc and trader_loc != ctx.self_state.get("location_id"):
        steps = [
            PlanStep(
                kind=STEP_TRAVEL_TO_LOCATION,
                payload={"target_id": trader_loc, "reason": "buy_heal"},
                interruptible=True,
                expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
            ),
            PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload={"item_category": "medical"},
                interruptible=False,
                expected_duration_ticks=1,
            ),
        ]
        return Plan(
            intent_kind=intent.kind, steps=steps, interruptible=True,
            confidence=0.7, created_turn=world_turn,
        )
    return None


def _plan_seek_consumable(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES, DRINK_ITEM_TYPES
    agent = ctx.self_state
    inventory = agent.get("inventory", [])
    is_food = intent.kind == INTENT_SEEK_FOOD
    item_types = FOOD_ITEM_TYPES if is_food else DRINK_ITEM_TYPES
    category = "food" if is_food else "drink"
    item = next((i for i in inventory if i.get("type") in item_types), None)
    if item:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                kind=STEP_CONSUME_ITEM,
                payload={"item_type": item.get("type"), "reason": f"emergency_{category}"},
                interruptible=False,
                expected_duration_ticks=1,
            )],
            interruptible=False, confidence=1.0, created_turn=world_turn,
        )
    trader_loc = _nearest_trader_location(ctx, state)
    if trader_loc and trader_loc != agent.get("location_id"):
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": f"buy_{category}"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category},
                         interruptible=False),
            ],
            confidence=0.7, created_turn=world_turn,
        )
    return None


def _plan_rest(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    from app.games.zone_stalkers.rules.tick_rules import DEFAULT_SLEEP_HOURS
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(
            kind=STEP_SLEEP_FOR_HOURS,
            payload={"hours": DEFAULT_SLEEP_HOURS},
            interruptible=True,
            expected_duration_ticks=DEFAULT_SLEEP_HOURS * 60,
        )],
        confidence=1.0, created_turn=world_turn,
    )


def _plan_resupply(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = ctx.self_state.get("location_id")
    if trader_loc and trader_loc != agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": "resupply"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": "equipment"},
                         interruptible=False),
            ],
            confidence=0.6, created_turn=world_turn,
        )
    if trader_loc == agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": "equipment"},
                            interruptible=False)],
            confidence=0.8, created_turn=world_turn,
        )
    return None


def _plan_sell_artifacts(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = ctx.self_state.get("location_id")
    if trader_loc == agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(STEP_TRADE_SELL_ITEM, {"item_category": "artifact"},
                            interruptible=False)],
            confidence=1.0, created_turn=world_turn,
        )
    if trader_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": "sell_artifacts"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_SELL_ITEM, {"item_category": "artifact"},
                         interruptible=False),
            ],
            confidence=0.8, created_turn=world_turn,
        )
    return None


def _plan_get_rich(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    # Sell artifacts first if we have them and a trader is nearby
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    artifact_types = frozenset(ARTIFACT_TYPES.keys())
    inventory = ctx.self_state.get("inventory", [])
    has_artifacts = any(i.get("type") in artifact_types for i in inventory)
    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = ctx.self_state.get("location_id")

    if has_artifacts and trader_loc:
        if trader_loc == agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(STEP_TRADE_SELL_ITEM, {"item_category": "artifact"})],
                confidence=1.0, created_turn=world_turn,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": "sell_artifacts_get_rich"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_SELL_ITEM, {"item_category": "artifact"}),
            ],
            confidence=0.7, created_turn=world_turn,
        )

    # Otherwise explore — delegate to legacy logic via a wrapper step
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "get_rich_explore"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_hunt_target(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    target_loc = intent.target_location_id
    if target_loc and target_loc != ctx.self_state.get("location_id"):
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": target_loc, "reason": "hunt_target"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state)),
            ],
            confidence=0.6, created_turn=world_turn,
        )
    # At target location or no location known — ask for intel
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_ASK_FOR_INTEL,
                        {"target_id": intent.target_id, "reason": "hunt_intel"},
                        expected_duration_ticks=1)],
        confidence=0.4, created_turn=world_turn,
    )


def _plan_search_information(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "search_information"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_leave_zone(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    # Find exit location
    exit_loc = _find_exit_location(ctx, state)
    agent_loc = ctx.self_state.get("location_id")
    if exit_loc and exit_loc != agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(STEP_TRAVEL_TO_LOCATION,
                            {"target_id": exit_loc, "reason": "leave_zone"},
                            expected_duration_ticks=_estimate_travel_ticks(ctx, exit_loc, state))],
            interruptible=False, confidence=0.9, created_turn=world_turn,
        )
    return None


def _plan_upgrade_equipment(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "upgrade_equipment"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_explore(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Optional[Plan]:
    loc_id = ctx.self_state.get("location_id", "")
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_EXPLORE_LOCATION,
                        {"target_id": loc_id, "reason": "explore"},
                        expected_duration_ticks=30)],
        confidence=0.8, created_turn=world_turn,
    )


def _plan_follow_group(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "follow_group_plan"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_assist_ally(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int
) -> Plan:
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "assist_ally"},
                        expected_duration_ticks=1)],
        confidence=0.4, created_turn=world_turn,
    )


def _idle_plan(intent: Intent, world_turn: int) -> Plan:
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_WAIT, {"reason": "idle"}, expected_duration_ticks=1)],
        confidence=1.0, created_turn=world_turn,
    )


# ── Location helpers ──────────────────────────────────────────────────────────

def _nearest_trader_location(
    ctx: AgentContext,
    state: dict[str, Any],
) -> Optional[str]:
    """Return the location_id of the nearest known trader, or None.

    Note: delegates to the legacy tick_rules helper during the migration period.
    Phase 5+ can promote this to a standalone utility in a shared module.
    """
    from app.games.zone_stalkers.rules.tick_rules import _find_nearest_trader_location
    agent_loc = ctx.self_state.get("location_id", "")
    return _find_nearest_trader_location(agent_loc, state)


def _nearest_safe_location(
    ctx: AgentContext,
    state: dict[str, Any],
) -> Optional[str]:
    """Return the location_id of the nearest location safe from emission."""
    _DANGEROUS = frozenset({"plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge"})
    loc_id = ctx.self_state.get("location_id", "")
    locations = state.get("locations", {})
    for conn in locations.get(loc_id, {}).get("connections", []):
        if conn.get("closed"):
            continue
        target = conn["to"]
        if locations.get(target, {}).get("terrain_type", "") not in _DANGEROUS:
            return target
    return None


def _find_exit_location(
    ctx: AgentContext,
    state: dict[str, Any],
) -> Optional[str]:
    """Find the nearest location with exit_zone=True."""
    locations = state.get("locations", {})
    loc_id = ctx.self_state.get("location_id", "")
    # BFS
    from collections import deque
    queue: deque[str] = deque([loc_id])
    visited: set[str] = {loc_id}
    while queue:
        current = queue.popleft()
        if locations.get(current, {}).get("exit_zone"):
            return current
        for conn in locations.get(current, {}).get("connections", []):
            nxt = conn["to"]
            if nxt not in visited and not conn.get("closed"):
                visited.add(nxt)
                queue.append(nxt)
    return None


def _estimate_travel_ticks(
    ctx: AgentContext,
    target_loc: str,
    state: dict[str, Any],
) -> int:
    """Estimate travel time in ticks (minutes) using Dijkstra."""
    from app.games.zone_stalkers.rules.tick_rules import _dijkstra_reachable_locations
    loc_id = ctx.self_state.get("location_id", "")
    reachable = _dijkstra_reachable_locations(
        loc_id, state.get("locations", {}), max_minutes=9999
    )
    return int(reachable.get(target_loc, 12))
