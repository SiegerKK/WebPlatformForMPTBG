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

import math
from typing import Any, Optional

from .item_needs import choose_dominant_item_need, evaluate_item_needs
from .liquidity import evaluate_affordability, find_liquidity_options
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
from .constants import DESIRED_AMMO_COUNT
from .models.immediate_need import ImmediateNeed
from .models.need_evaluation import NeedEvaluationResult
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

_MIN_NONCRITICAL_CONSUME_THRESHOLD_FOOD = 50
_MIN_NONCRITICAL_CONSUME_THRESHOLD_DRINK = 40


def build_plan(
    ctx: AgentContext,
    intent: Intent,
    state: dict[str, Any],
    world_turn: int,
    need_result: NeedEvaluationResult | None = None,
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
        plan = builder(ctx, intent, state, world_turn, need_result)
        if plan is not None:
            return plan

    # Fallback idle plan
    return _idle_plan(intent, world_turn)


# ── Plan builders ─────────────────────────────────────────────────────────────

def _plan_flee_emission(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    target_loc = intent.target_location_id
    if not target_loc:
        # Try to find nearest safe location from ctx if not set on intent
        target_loc = _nearest_safe_location(ctx, state)
    if not target_loc:
        # Trapped: all neighbours are dangerous — wait in place
        step = PlanStep(
            kind=STEP_WAIT,
            payload={"reason": "trapped_on_dangerous_terrain"},
            interruptible=False,
            expected_duration_ticks=1,
        )
        return Plan(
            intent_kind=intent.kind,
            steps=[step],
            interruptible=False,
            confidence=0.5,
            created_turn=world_turn,
        )
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    agent = ctx.self_state
    inventory = agent.get("inventory", [])

    from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES

    # PR2: prefer the item selected by ImmediateNeed.heal_now when available.
    heal_need = _find_immediate_need(need_result, "heal_now") if need_result else None
    if heal_need and heal_need.selected_item_type:
        heal_item = next(
            (i for i in inventory if i.get("type") == heal_need.selected_item_type),
            None,
        )
    else:
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

    # No heal item in inventory — evaluate buy affordability.
    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = ctx.self_state.get("location_id")

    afford = evaluate_affordability(agent=agent, trader={}, category="medical")

    if trader_loc and trader_loc == agent_loc:
        if afford.can_buy_now:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    kind=STEP_TRADE_BUY_ITEM,
                    payload={"item_category": "medical", "reason": "buy_medical_heal",
                             "required_price": afford.required_price},
                    interruptible=False,
                    expected_duration_ticks=1,
                )],
                interruptible=False, confidence=1.0, created_turn=world_turn,
            )

        # Cannot afford — check liquidity; only use safe options (not last food/water).
        liquidity_options = find_liquidity_options(
            agent=agent,
            immediate_needs=list(need_result.immediate_needs) if need_result else [],
            item_needs=list(need_result.item_needs) if need_result else evaluate_item_needs(ctx, state),
        )
        sellable = next((o for o in liquidity_options if o.safety == "safe"), None)
        if sellable:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        kind=STEP_TRADE_SELL_ITEM,
                        payload={"item_category": "any_sellable", "reason": "fund_heal",
                                 "required_price": afford.required_price},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                    PlanStep(
                        kind=STEP_TRADE_BUY_ITEM,
                        payload={"item_category": "medical", "reason": "buy_medical_heal",
                                 "required_price": afford.required_price},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                ],
                interruptible=False, confidence=1.0, created_turn=world_turn,
            )
        # No safe liquidity available — legacy fallback: try any sellable item
        # (e.g. detector, spare weapon/armor). Do not sell food/water
        # (they're risky or emergency_only per liquidity policy).
        # KNOWN LIMITATION (PR2): emergency heal may still use risky liquidity
        # through this fallback path.
        # TODO(future liquidity refinement): replace this with a proper "can I trade-sell this tick?"
        # affordability gate so that risky items (e.g. spare weapon) are also
        # protected when the agent's survival needs change before the sell executes.
        legacy_sellable = next((o for o in liquidity_options if o.safety in ("safe", "risky")), None)
        if legacy_sellable:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        kind=STEP_TRADE_SELL_ITEM,
                        payload={"item_category": "any_sellable", "reason": "fund_heal"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                    PlanStep(
                        kind=STEP_TRADE_BUY_ITEM,
                        payload={"item_category": "medical"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                ],
                interruptible=False, confidence=0.7, created_turn=world_turn,
            )
        # Nothing to sell at all; wait/idle
        return None

    if trader_loc and trader_loc != agent_loc:
        # Check affordability before committing to travel→buy.
        if not afford.can_buy_now:
            liquidity_options = find_liquidity_options(
                agent=agent,
                immediate_needs=list(need_result.immediate_needs) if need_result else [],
                item_needs=list(need_result.item_needs) if need_result else evaluate_item_needs(ctx, state),
            )
            safe_local = next((o for o in liquidity_options if o.safety == "safe"), None)
            if safe_local is None:
                # Cannot afford and nothing safe to sell
                return None
        steps = [
            PlanStep(
                kind=STEP_TRAVEL_TO_LOCATION,
                payload={"target_id": trader_loc, "reason": "buy_heal"},
                interruptible=True,
                expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
            ),
        ]
        if not afford.can_buy_now and _has_sellable_items(agent):
            steps.append(PlanStep(
                kind=STEP_TRADE_SELL_ITEM,
                payload={"item_category": "any_sellable", "reason": "fund_heal"},
                interruptible=False,
                expected_duration_ticks=1,
            ))
        steps.append(PlanStep(
            kind=STEP_TRADE_BUY_ITEM,
            payload={"item_category": "medical"},
            interruptible=False,
            expected_duration_ticks=1,
        ))
        return Plan(
            intent_kind=intent.kind, steps=steps, interruptible=True,
            confidence=0.7, created_turn=world_turn,
        )
    return None


def _plan_seek_consumable(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES, DRINK_ITEM_TYPES

    agent = ctx.self_state
    inventory = agent.get("inventory", [])
    is_food = intent.kind == INTENT_SEEK_FOOD
    item_types = FOOD_ITEM_TYPES if is_food else DRINK_ITEM_TYPES
    category = "food" if is_food else "drink"

    # Legacy compatibility path (used by existing tests / v2 callers):
    # keep opportunistic consume and sell-before-buy behavior from PR1.
    if need_result is None:
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
        agent_loc = agent.get("location_id")
        if trader_loc and trader_loc == agent_loc:
            if agent.get("money", 0) == 0 and _has_sellable_items(agent):
                return Plan(
                    intent_kind=intent.kind,
                    steps=[
                        PlanStep(STEP_TRADE_SELL_ITEM,
                                 {"item_category": "any_sellable", "reason": "fund_consumable"},
                                 interruptible=False),
                        PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category}, interruptible=False),
                    ],
                    interruptible=False, confidence=1.0, created_turn=world_turn,
                )
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category}, interruptible=False)],
                interruptible=False, confidence=1.0, created_turn=world_turn,
            )

        if trader_loc and trader_loc != agent_loc:
            steps = [
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": f"buy_{category}"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
            ]
            _OPPORTUNISTIC_THRESHOLD = 25
            other_types = DRINK_ITEM_TYPES if is_food else FOOD_ITEM_TYPES
            other_attr = "thirst" if is_food else "hunger"
            other_item = next((i for i in inventory if i.get("type") in other_types), None)
            if other_item and agent.get(other_attr, 0) >= _OPPORTUNISTIC_THRESHOLD:
                other_category = "drink" if is_food else "food"
                steps.insert(0, PlanStep(
                    kind=STEP_CONSUME_ITEM,
                    payload={"item_type": other_item.get("type"), "reason": f"opportunistic_{other_category}"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ))
            if agent.get("money", 0) == 0 and _has_sellable_items(agent):
                steps.append(PlanStep(STEP_TRADE_SELL_ITEM,
                                      {"item_category": "any_sellable", "reason": "fund_consumable"},
                                      interruptible=False))
            steps.append(PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category}, interruptible=False))
            return Plan(intent_kind=intent.kind, steps=steps, confidence=0.7, created_turn=world_turn)
        return None

    immediate_key = "eat_now" if is_food else "drink_now"
    immediate_need = _find_immediate_need(need_result, immediate_key)
    selected_item_type = immediate_need.selected_item_type if immediate_need else None
    selected_item_id = immediate_need.selected_item_id if immediate_need else None

    item = None
    if selected_item_type is not None:
        item = next(
            (
                i for i in inventory
                if i.get("type") == selected_item_type
                and (selected_item_id is None or i.get("id") == selected_item_id)
            ),
            None,
        )
    if item is None:
        item = next((i for i in inventory if i.get("type") in item_types), None)

    if item:
        is_critical_consume = bool(
            immediate_need and immediate_need.trigger_context in ("survival", "healing")
        )
        is_rest_preparation = bool(
            immediate_need and immediate_need.trigger_context == "rest_preparation"
        )
        current_need_value = int(agent.get("hunger" if is_food else "thirst", 0))
        soft_threshold = (
            _MIN_NONCRITICAL_CONSUME_THRESHOLD_FOOD
            if is_food
            else _MIN_NONCRITICAL_CONSUME_THRESHOLD_DRINK
        )
        allow_soft_consume = current_need_value >= soft_threshold

        if is_critical_consume or is_rest_preparation or allow_soft_consume:
            reason = (
                f"emergency_{category}"
                if is_critical_consume
                else (f"prepare_sleep_{category}" if is_rest_preparation else f"need_{category}")
            )
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    kind=STEP_CONSUME_ITEM,
                    payload={"item_type": item.get("type"), "reason": reason},
                    interruptible=False,
                    expected_duration_ticks=1,
                )],
                interruptible=False, confidence=1.0, created_turn=world_turn,
            )
        # Non-critical/low need: avoid spending inventory consumables.
        return None

    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = agent.get("location_id")

    # Immediate survival mode: cheapest affordable viable item + liquidity fallback.
    if immediate_need and immediate_need.trigger_context in ("survival", "healing"):
        compatible_types = set(item_types)
        if trader_loc and trader_loc == agent_loc:
            afford = evaluate_affordability(
                agent=agent,
                trader={},
                category=category,
                compatible_item_types=compatible_types,
            )
            if afford.can_buy_now:
                return Plan(
                    intent_kind=intent.kind,
                    steps=[PlanStep(
                        STEP_TRADE_BUY_ITEM,
                        {
                            "item_category": category,
                            "reason": f"buy_{category}_survival",
                            "buy_mode": "survival_cheapest",
                            "compatible_item_types": sorted(compatible_types),
                            "required_price": afford.required_price,
                        },
                        interruptible=False,
                    )],
                    interruptible=False, confidence=1.0, created_turn=world_turn,
                )

            liquidity_options = find_liquidity_options(
                agent=agent,
                immediate_needs=list(need_result.immediate_needs) if need_result else [],
                item_needs=list(need_result.item_needs) if need_result else evaluate_item_needs(ctx, state),
            )
            sellable = next((o for o in liquidity_options if o.safety in ("safe", "emergency_only")), None)
            if sellable:
                steps = [
                    PlanStep(
                        STEP_TRADE_SELL_ITEM,
                        {
                            "item_category": "any_sellable",
                            "reason": f"fund_{category}",
                            "required_price": afford.required_price,
                        },
                        interruptible=False,
                    ),
                    PlanStep(
                        STEP_TRADE_BUY_ITEM,
                        {
                            "item_category": category,
                            "reason": f"buy_{category}_survival",
                            "buy_mode": "survival_cheapest",
                            "compatible_item_types": sorted(compatible_types),
                            "required_price": afford.required_price,
                        },
                        interruptible=False,
                    ),
                ]
                return Plan(intent_kind=intent.kind, steps=steps, interruptible=False, confidence=1.0, created_turn=world_turn)

        if trader_loc and trader_loc != agent_loc:
            # Check affordability before committing to travel→buy.
            # If the agent cannot afford the item AND has no safe local sell option,
            # fallback to get_rich rather than building a pointless travel plan.
            compatible_types = set(item_types)
            remote_afford = evaluate_affordability(
                agent=agent,
                trader={},
                category=category,
                compatible_item_types=compatible_types,
            )
            if not remote_afford.can_buy_now:
                # Try local liquidity first (sell at current location before traveling)
                local_liq = find_liquidity_options(
                    agent=agent,
                    immediate_needs=list(need_result.immediate_needs) if need_result else [],
                    item_needs=list(need_result.item_needs) if need_result else evaluate_item_needs(ctx, state),
                )
                safe_local = next((o for o in local_liq if o.safety in ("safe", "emergency_only")), None)
                if safe_local is None:
                    # Cannot afford and nothing to sell — fallback to gather money
                    get_rich_intent = Intent(
                        kind=INTENT_GET_RICH,
                        score=0.6,
                        source_goal="get_rich",
                        reason=f"Survival {category}: unaffordable and no liquidity — gather money first",
                        created_turn=world_turn,
                    )
                    return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)
                # Has something to sell locally → travel to trader, sell, then buy
                steps = [
                    PlanStep(STEP_TRAVEL_TO_LOCATION,
                             {"target_id": trader_loc, "reason": f"buy_{category}_survival"},
                             expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                    PlanStep(STEP_TRADE_SELL_ITEM,
                             {"item_category": "any_sellable", "reason": f"fund_{category}",
                              "required_price": remote_afford.required_price},
                             interruptible=False),
                    PlanStep(STEP_TRADE_BUY_ITEM,
                             {
                                 "item_category": category,
                                 "reason": f"buy_{category}_survival",
                                 "buy_mode": "survival_cheapest",
                                 "compatible_item_types": sorted(compatible_types),
                             },
                             interruptible=False),
                ]
                return Plan(intent_kind=intent.kind, steps=steps, confidence=0.6, created_turn=world_turn)
            steps = [
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         {"target_id": trader_loc, "reason": f"buy_{category}_survival"},
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_BUY_ITEM,
                         {
                             "item_category": category,
                             "reason": f"buy_{category}_survival",
                             "buy_mode": "survival_cheapest",
                             "compatible_item_types": sorted(compatible_types),
                         },
                         interruptible=False),
            ]
            return Plan(intent_kind=intent.kind, steps=steps, confidence=0.7, created_turn=world_turn)

    if trader_loc and trader_loc == agent_loc:
        buy_payload: dict[str, Any] = {
            "item_category": category,
            "reason": f"buy_{category}_stock",
            **(
                {
                    "buy_mode": "reserve_basic",
                    "preferred_item_types": (
                        ["bread", "canned_food"] if category == "food" else ["water", "purified_water"]
                    ),
                }
                if category in ("food", "drink")
                else {}
            ),
        }
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRADE_BUY_ITEM,
                buy_payload,
                interruptible=False,
            )],
            interruptible=False, confidence=1.0, created_turn=world_turn,
        )

    if trader_loc and trader_loc != agent_loc:
        buy_payload: dict[str, Any] = {
            "item_category": category,
            "reason": f"buy_{category}_stock",
            **(
                {
                    "buy_mode": "reserve_basic",
                    "preferred_item_types": (
                        ["bread", "canned_food"] if category == "food" else ["water", "purified_water"]
                    ),
                }
                if category in ("food", "drink")
                else {}
            ),
        }
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": trader_loc, "reason": f"buy_{category}_stock"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
                ),
                PlanStep(
                    STEP_TRADE_BUY_ITEM,
                    buy_payload,
                    interruptible=False,
                ),
            ],
            confidence=0.7,
            created_turn=world_turn,
        )
    return None


def _build_sleep_preparation_steps(
    ctx: AgentContext,
    world_turn: int,
    need_result: NeedEvaluationResult | None = None,
) -> list[PlanStep]:
    """Return consume steps for food/drink that should precede sleep."""
    from app.games.zone_stalkers.rules.tick_rules import DEFAULT_SLEEP_HOURS

    agent = ctx.self_state
    inventory = agent.get("inventory", [])
    steps: list[PlanStep] = []

    drink_need = _find_immediate_need(need_result, "drink_now", trigger_context="rest_preparation")
    if drink_need and drink_need.selected_item_type:
        drink = next((i for i in inventory if i.get("type") == drink_need.selected_item_type), None)
        if drink:
            steps.append(PlanStep(
                kind=STEP_CONSUME_ITEM,
                payload={"item_type": drink["type"], "reason": "prepare_sleep_drink"},
                interruptible=False,
                expected_duration_ticks=1,
            ))

    food_need = _find_immediate_need(need_result, "eat_now", trigger_context="rest_preparation")
    if food_need and food_need.selected_item_type:
        food = next((i for i in inventory if i.get("type") == food_need.selected_item_type), None)
        if food:
            steps.append(PlanStep(
                kind=STEP_CONSUME_ITEM,
                payload={"item_type": food["type"], "reason": "prepare_sleep_food"},
                interruptible=False,
                expected_duration_ticks=1,
            ))

    # Basic sleep duration policy:
    # linearly map sleepiness 0..100 -> 1..DEFAULT_SLEEP_HOURS.
    sleepiness = max(0, int(agent.get("sleepiness", 0)))
    sleepiness_per_hour = max(1, math.ceil(100 / DEFAULT_SLEEP_HOURS))
    estimated_hours = max(1, math.ceil(sleepiness / sleepiness_per_hour))
    sleep_hours = min(DEFAULT_SLEEP_HOURS, estimated_hours)

    steps.append(PlanStep(
        kind=STEP_SLEEP_FOR_HOURS,
        payload={"hours": sleep_hours},
        interruptible=True,
        expected_duration_ticks=sleep_hours * 60,
    ))
    return steps


def _plan_rest(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Plan:
    if need_result is None:
        from app.games.zone_stalkers.decision.needs import evaluate_need_result
        need_result = evaluate_need_result(ctx, state)
    steps = _build_sleep_preparation_steps(ctx, world_turn, need_result)
    return Plan(
        intent_kind=intent.kind,
        steps=steps,
        confidence=1.0,
        created_turn=world_turn,
    )


def _find_immediate_need(
    need_result: NeedEvaluationResult | None,
    key: str,
    trigger_context: str | None = None,
) -> ImmediateNeed | None:
    if need_result is None:
        return None
    for need in need_result.immediate_needs:
        if need.key != key:
            continue
        if trigger_context is not None and need.trigger_context != trigger_context:
            continue
        return need
    return None


def _desired_supply_count(risk_tolerance: float, min_count: int, max_count: int) -> int:
    """Desired inventory count for a supply category based on risk tolerance.

    More risk-averse agents (low ``risk_tolerance``) want larger stocks.
    Mirrors the same helper in ``needs.py``.
    """
    return min_count + round((1.0 - risk_tolerance) * (max_count - min_count))


def _plan_resupply(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    """Plan resupply using PR2 ItemNeed dominant-urgency semantics."""
    from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location

    agent = ctx.self_state
    agent_loc = agent.get("location_id", "")

    if need_result is None:
        from app.games.zone_stalkers.balance.items import (
            WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_FOR_WEAPON,
            FOOD_ITEM_TYPES, DRINK_ITEM_TYPES, HEAL_ITEM_TYPES,
        )

        eq = agent.get("equipment", {})
        inventory = agent.get("inventory", [])
        risk_tolerance = float(agent.get("risk_tolerance", 0.5))
        desired_food = _desired_supply_count(risk_tolerance, 1, 3)
        desired_drink = _desired_supply_count(risk_tolerance, 1, 3)
        desired_medicine = _desired_supply_count(risk_tolerance, 2, 4)

        food_count = sum(1 for i in inventory if i.get("type") in FOOD_ITEM_TYPES)
        drink_count = sum(1 for i in inventory if i.get("type") in DRINK_ITEM_TYPES)
        medicine_count = sum(1 for i in inventory if i.get("type") in HEAL_ITEM_TYPES)

        has_weapon = eq.get("weapon") is not None
        has_armor = eq.get("armor") is not None

        need_types: Optional["frozenset[str]"] = None
        _buy_category: Optional[str] = None

        if food_count < desired_food:
            need_types = FOOD_ITEM_TYPES
            _buy_category = "food"
        elif drink_count < desired_drink:
            need_types = DRINK_ITEM_TYPES
            _buy_category = "drink"
        elif not has_armor:
            need_types = ARMOR_ITEM_TYPES
            _buy_category = "armor"
        elif not has_weapon:
            need_types = WEAPON_ITEM_TYPES
            _buy_category = "weapon"
        else:
            weapon_type = eq["weapon"].get("type") if isinstance(eq.get("weapon"), dict) else None
            required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
            if required_ammo:
                ammo_count = sum(1 for i in inventory if i.get("type") == required_ammo)
                if ammo_count < DESIRED_AMMO_COUNT:
                    need_types = frozenset([required_ammo])
                    _buy_category = "ammo"
            if need_types is None and medicine_count < desired_medicine:
                need_types = HEAL_ITEM_TYPES
                _buy_category = "medical"

        if need_types is not None:
            mem_loc = _find_item_memory_location(agent, need_types, state)
            if mem_loc and mem_loc != agent_loc:
                return Plan(
                    intent_kind=intent.kind,
                    steps=[PlanStep(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": mem_loc, "reason": "seek_item_from_memory"},
                        expected_duration_ticks=_estimate_travel_ticks(ctx, mem_loc, state),
                    )],
                    confidence=0.85, created_turn=world_turn,
                )

            trader_loc = _nearest_trader_location(ctx, state)
            if trader_loc and trader_loc != agent_loc:
                return Plan(
                    intent_kind=intent.kind,
                    steps=[
                        PlanStep(STEP_TRAVEL_TO_LOCATION,
                                 {"target_id": trader_loc, "reason": "resupply"},
                                 expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                        PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": _buy_category}, interruptible=False),
                    ],
                    confidence=0.6, created_turn=world_turn,
                )
            if trader_loc == agent_loc:
                return Plan(
                    intent_kind=intent.kind,
                    steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": _buy_category}, interruptible=False)],
                    confidence=0.8, created_turn=world_turn,
                )

            get_rich_intent = Intent(
                kind=INTENT_GET_RICH, score=0.5, source_goal="get_rich",
                reason=(
                    "Нужен resupply, но покупка сейчас невозможна/нецелесообразна. "
                    "Перехожу к fallback_get_money через поиск артефактов."
                ),
                created_turn=world_turn,
            )
            return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)

        return _plan_resupply_upgrade(ctx, intent, state, world_turn, need_result)

    item_needs = list(need_result.item_needs) if need_result is not None else evaluate_item_needs(ctx, state)
    dominant = choose_dominant_item_need(item_needs)
    if dominant is None:
        return _plan_resupply_upgrade(ctx, intent, state, world_turn, need_result)

    category_map = {
        "food": "food",
        "drink": "drink",
        "medicine": "medical",
        "weapon": "weapon",
        "armor": "armor",
        "ammo": "ammo",
    }
    buy_category = category_map.get(dominant.key)
    need_types = dominant.compatible_item_types

    if not buy_category:
        return _plan_resupply_upgrade(ctx, intent, state, world_turn, need_result)

    # a) Try memory pickup first
    mem_loc = _find_item_memory_location(agent, need_types, state) if need_types else None
    if mem_loc and mem_loc != agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": mem_loc, "reason": "seek_item_from_memory"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, mem_loc, state),
            )],
            confidence=0.85,
            created_turn=world_turn,
        )

    trader_loc = _nearest_trader_location(ctx, state)
    affordability = evaluate_affordability(
        agent=agent,
        trader={},
        category=buy_category,
        compatible_item_types=set(need_types) if need_types else None,
    )

    if trader_loc == agent_loc:
        buy_payload: dict[str, Any] = {
            "item_category": buy_category,
            "reason": f"buy_{buy_category}_resupply",
            "required_price": affordability.required_price,
        }
        if dominant.key in ("food", "drink") and dominant.missing_count > 0:
            buy_payload["buy_mode"] = "reserve_basic"
            buy_payload["preferred_item_types"] = (
                ["bread", "canned_food"] if dominant.key == "food" else ["water", "purified_water"]
            )
        if affordability.can_buy_now:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    STEP_TRADE_BUY_ITEM,
                    buy_payload,
                    interruptible=False,
                )],
                confidence=0.8,
                created_turn=world_turn,
            )

        liquidity_options = find_liquidity_options(
            agent=agent,
            immediate_needs=list(need_result.immediate_needs) if need_result else [],
            item_needs=item_needs,
        )
        # Only allow safe-to-sell items for normal resupply.
        # Risky items (e.g. last food/water below reserve) must not be sold
        # automatically for weapon/armor/ammo acquisition.
        sellable = next((o for o in liquidity_options if o.safety == "safe"), None)
        if sellable is not None:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_TRADE_SELL_ITEM,
                        {
                            "item_category": "any_sellable",
                            "reason": "fund_resupply",
                            "required_price": affordability.required_price,
                        },
                        interruptible=False,
                    ),
                    PlanStep(
                        STEP_TRADE_BUY_ITEM,
                        buy_payload,
                        interruptible=False,
                    ),
                ],
                confidence=0.75,
                created_turn=world_turn,
            )

    if trader_loc and trader_loc != agent_loc:
        buy_payload: dict[str, Any] = {
            "item_category": buy_category,
            "reason": f"buy_{buy_category}_resupply",
            "required_price": affordability.required_price,
        }
        if dominant.key in ("food", "drink") and dominant.missing_count > 0:
            buy_payload["buy_mode"] = "reserve_basic"
            buy_payload["preferred_item_types"] = (
                ["bread", "canned_food"] if dominant.key == "food" else ["water", "purified_water"]
            )
        steps = [
            PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": trader_loc, "reason": "resupply"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
            )
        ]
        if affordability.can_buy_now:
            steps.append(PlanStep(
                STEP_TRADE_BUY_ITEM,
                buy_payload,
                interruptible=False,
            ))
            return Plan(intent_kind=intent.kind, steps=steps, confidence=0.6, created_turn=world_turn)

    # No trader known or cannot afford and no liquidity.
    get_rich_intent = Intent(
        kind=INTENT_GET_RICH,
        score=0.5,
        source_goal="get_rich",
        reason=(
            "Нужен resupply, но покупка сейчас невозможна/нецелесообразна. "
            "Перехожу к fallback_get_money через поиск артефактов."
        ),
        created_turn=world_turn,
    )
    return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)


def _plan_resupply_upgrade(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    """Plan an equipment upgrade when all basic resupply needs are met.

    Checks weapon first, then armor.  An upgrade is valid when a catalogued
    item of the same slot offers a closer ``risk_tolerance`` match AND has a
    higher base value (higher tier), AND the agent can afford it at trader
    price (base × 1.5).
    """
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES
    from app.games.zone_stalkers.rules.tick_rules import _find_upgrade_target

    agent = ctx.self_state
    eq = agent.get("equipment", {})
    agent_risk = float(agent.get("risk_tolerance", 0.5))
    agent_money = agent.get("money", 0)
    agent_loc = agent.get("location_id", "")

    for slot, item_types, category in [
        ("weapon", WEAPON_ITEM_TYPES, "weapon_upgrade"),
        ("armor", ARMOR_ITEM_TYPES, "armor_upgrade"),
    ]:
        current = eq.get(slot)
        if not isinstance(current, dict):
            continue
        current_type = current.get("type")
        upgrade_key = _find_upgrade_target(item_types, current_type, agent_risk, agent_money)
        if upgrade_key is None:
            continue

        trader_loc = _nearest_trader_location(ctx, state)
        if trader_loc == agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category},
                                interruptible=False)],
                confidence=0.7, created_turn=world_turn,
            )
        if trader_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(STEP_TRAVEL_TO_LOCATION,
                             {"target_id": trader_loc, "reason": f"upgrade_{slot}"},
                             expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                    PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": category},
                             interruptible=False),
                ],
                confidence=0.5, created_turn=world_turn,
            )

    return None


def _plan_sell_artifacts(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    """Build a plan for the get_rich intent.

    Priority:
    1. Sell artifacts if we have them and know a trader.
    2. Explore current location if it has anomaly activity and isn't confirmed empty.
    3. Travel to the best reachable anomaly location.
    4. Wait (no candidates).
    """
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    from app.games.zone_stalkers.rules.tick_rules import (
        _confirmed_empty_locations,
        _dijkstra_reachable_locations,
        _score_location,
    )
    artifact_types = frozenset(ARTIFACT_TYPES.keys())
    agent = ctx.self_state
    inventory = agent.get("inventory", [])
    has_artifacts = any(i.get("type") in artifact_types for i in inventory)
    trader_loc = _nearest_trader_location(ctx, state)
    agent_loc = agent.get("location_id", "")
    fallback_reason = (
        intent.reason
        if isinstance(intent.reason, str) and "fallback_get_money" in intent.reason
        else None
    )

    # 1. Sell artifacts
    if has_artifacts and trader_loc:
        sell_payload: dict[str, Any] = {"item_category": "artifact"}
        if fallback_reason:
            sell_payload["fallback_reason"] = fallback_reason
        if trader_loc == agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(STEP_TRADE_SELL_ITEM, sell_payload)],
                confidence=1.0, created_turn=world_turn,
            )
        travel_payload: dict[str, Any] = {
            "target_id": trader_loc,
            "reason": "sell_artifacts_get_rich",
        }
        if fallback_reason:
            travel_payload["fallback_reason"] = fallback_reason
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(STEP_TRAVEL_TO_LOCATION,
                         travel_payload,
                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                PlanStep(STEP_TRADE_SELL_ITEM, sell_payload),
            ],
            confidence=0.7, created_turn=world_turn,
        )

    # 2. Explore current location if it has anomaly and isn't confirmed empty
    confirmed_empty = _confirmed_empty_locations(agent)
    loc = ctx.location_state
    if loc.get("anomaly_activity", 0) > 0 and agent_loc not in confirmed_empty:
        explore_payload: dict[str, Any] = {
            "target_id": agent_loc,
            "reason": "get_rich_explore_here",
        }
        if fallback_reason:
            explore_payload["fallback_reason"] = fallback_reason
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_EXPLORE_LOCATION,
                explore_payload,
                expected_duration_ticks=30,
            )],
            confidence=0.8, created_turn=world_turn,
        )

    # 3. Travel to the best reachable anomaly location
    # Score is risk-adjusted: agents prefer zones whose anomaly_activity/10 is
    # closest to their risk_tolerance (low-risk prefers quiet zones, high-risk
    # prefers dangerous ones).
    risk_tolerance: float = agent.get("risk_tolerance", 0.5)
    locations = state.get("locations", {})
    reachable = _dijkstra_reachable_locations(agent_loc, locations, max_minutes=9999)
    best_loc: Optional[str] = None
    best_score: float = -2.0  # sentinel below any possible score
    for cand_id in reachable:
        if cand_id == agent_loc:
            continue
        if cand_id in confirmed_empty:
            continue
        cand = locations.get(cand_id, {})
        if cand.get("anomaly_activity", 0) <= 0:
            continue
        # Proximity of normalised anomaly activity to risk tolerance
        score = -abs(cand.get("anomaly_activity", 0) / 10.0 - risk_tolerance)
        if score > best_score:
            best_score = score
            best_loc = cand_id

    if best_loc:
        travel_payload: dict[str, Any] = {
            "target_id": best_loc,
            "reason": "get_rich_travel_to_anomaly",
        }
        if fallback_reason:
            travel_payload["fallback_reason"] = fallback_reason
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                travel_payload,
                expected_duration_ticks=_estimate_travel_ticks(ctx, best_loc, state),
            )],
            confidence=0.6, created_turn=world_turn,
        )

    # 4. No candidates — wait
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_WAIT, {"reason": "get_rich_no_candidates"})],
        confidence=0.3, created_turn=world_turn,
    )


def _plan_hunt_target(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Plan:
    """Build a plan for the search_information intent (unravel_zone_mystery goal).

    Priority:
    1. Memory location with secret documents → travel there.
    2. Ask co-located stalkers for intel → travel to reported location.
    3. Go to nearest trader (wait for info / buy intel).
    4. Go to dungeon or x_lab location.
    5. Wait.
    """
    from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
    from app.games.zone_stalkers.rules.tick_rules import (
        _find_item_memory_location,
        _bot_ask_colocated_stalkers_about_item,
        _find_nearest_trader_location,
        _dijkstra_reachable_locations,
    )
    agent = ctx.self_state
    agent_id = ctx.agent_id
    agent_loc = agent.get("location_id", "")

    # 1. Check memory for a location with secret documents
    mem_loc = _find_item_memory_location(agent, SECRET_DOCUMENT_ITEM_TYPES, state)
    if mem_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": mem_loc, "reason": "search_info_doc_loc"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, mem_loc, state),
            )],
            confidence=0.7, created_turn=world_turn,
        )

    # 2. Ask co-located stalkers for intel about secret documents
    intel_loc = _bot_ask_colocated_stalkers_about_item(
        agent_id, agent, SECRET_DOCUMENT_ITEM_TYPES,
        "секретный документ", state, world_turn,
    )
    if intel_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": intel_loc, "reason": "search_info_intel_from_stalker"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, intel_loc, state),
            )],
            confidence=0.6, created_turn=world_turn,
        )

    # 3. Go to nearest trader (may provide intel or documents for purchase)
    trader_loc = _find_nearest_trader_location(agent_loc, state)
    if trader_loc and trader_loc != agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": trader_loc, "reason": "search_info_visit_trader"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
            )],
            confidence=0.5, created_turn=world_turn,
        )

    # 4. Find dungeon or x_lab location — likely to have secret documents
    locations = state.get("locations", {})
    dungeon_types = {"dungeon", "x_lab"}
    reachable = _dijkstra_reachable_locations(agent_loc, locations, max_minutes=9999)
    dungeon_loc: Optional[str] = None
    for cand_id in reachable:
        if cand_id == agent_loc:
            continue
        if locations.get(cand_id, {}).get("terrain_type") in dungeon_types:
            dungeon_loc = cand_id
            break

    if dungeon_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": dungeon_loc, "reason": "search_info_dungeon"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, dungeon_loc, state),
            )],
            confidence=0.4, created_turn=world_turn,
        )

    # 5. No leads — wait
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_WAIT, {"reason": "search_info_no_target"})],
        confidence=0.2, created_turn=world_turn,
    )


def _plan_leave_zone(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Plan:
    # TODO(Phase 5): implement real equipment upgrade planning when equipment
    # upgrade mechanics are fully defined.  For now, fall back to legacy logic.
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "upgrade_equipment"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_explore(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
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
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Plan:
    # TODO(Phase 7): implement group-following logic.
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_LEGACY_SCHEDULED_ACTION,
                        {"reason": "follow_group_plan"},
                        expected_duration_ticks=1)],
        confidence=0.5, created_turn=world_turn,
    )


def _plan_assist_ally(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Plan:
    # TODO(Phase 6): implement ally-assistance logic.
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

    Delegates to the tick_rules helper during the migration period.
    Phase 5+ can promote this to a standalone utility in a shared module.
    """
    from app.games.zone_stalkers.rules.tick_rules import _find_nearest_trader_location
    agent_loc = ctx.self_state.get("location_id", "")
    return _find_nearest_trader_location(agent_loc, state)


def _nearest_safe_location(
    ctx: AgentContext,
    state: dict[str, Any],
) -> Optional[str]:
    """Return the location_id of the nearest location safe from emission (BFS)."""
    from .constants import EMISSION_DANGEROUS_TERRAIN
    from collections import deque
    loc_id = ctx.self_state.get("location_id", "")
    locations = state.get("locations", {})
    # BFS — safe means terrain_type NOT in EMISSION_DANGEROUS_TERRAIN
    queue: deque[str] = deque([loc_id])
    visited: set[str] = {loc_id}
    while queue:
        current = queue.popleft()
        for conn in locations.get(current, {}).get("connections", []):
            if conn.get("closed"):
                continue
            nxt = conn["to"]
            if nxt in visited:
                continue
            visited.add(nxt)
            if locations.get(nxt, {}).get("terrain_type", "") not in EMISSION_DANGEROUS_TERRAIN:
                return nxt
            queue.append(nxt)
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


def _agent_wealth_from_ctx(ctx: AgentContext) -> int:
    """Return agent wealth (money + inventory item values)."""
    agent = ctx.self_state
    money = agent.get("money", 0)
    item_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    return money + item_value


def _has_sellable_items(agent: dict) -> bool:
    """Return True if the agent has any non-critical inventory item that can be sold.

    Sellable categories (in order of sell priority):
      - Artifacts
      - Detectors
      - Spare weapons (in inventory; equipped weapon is in agent["equipment"])
      - Spare armor   (in inventory; equipped armor  is in agent["equipment"])

    Not sellable:
      - Consumables (food / drink / medical items)
      - Ammo (low value, needed for combat)
      - Secret documents (needed for ``unravel_zone_mystery`` goal)
    """
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _IT
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART

    _art_set = frozenset(_ART.keys())
    _non_sellable_base = frozenset(["medical", "consumable", "ammo", "secret_document"])
    _sellable_base = frozenset(["weapon", "armor", "detector"])

    for item in agent.get("inventory", []):
        t = item.get("type", "")
        if t in _art_set:
            return True
        base = _IT.get(t, {}).get("type", t)
        if base in _non_sellable_base:
            continue
        if base in _sellable_base and item.get("value", _IT.get(t, {}).get("value", 0)) > 0:
            return True
    return False
    # Basic sleep duration policy:
    # linearly map sleepiness 0..100 -> 1..DEFAULT_SLEEP_HOURS.
    sleepiness = max(0, int(agent.get("sleepiness", 0)))
    sleepiness_per_hour = max(1, math.ceil(100 / DEFAULT_SLEEP_HOURS))
    estimated_hours = max(1, math.ceil(sleepiness / sleepiness_per_hour))
    sleep_hours = min(DEFAULT_SLEEP_HOURS, estimated_hours)
