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

from .economic_phase import is_item_need_actionable
from .item_needs import choose_dominant_item_need, evaluate_item_needs
from .liquidity import evaluate_affordability, find_liquidity_options
from .survival_credit import quote_survival_purchase
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
    INTENT_REPAY_DEBT,
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
    STEP_LOOT_CORPSE,
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
    STEP_REQUEST_LOAN,
    STEP_REPAY_DEBT,
)
from app.games.zone_stalkers.economy.debts import (
    DEBT_REPAYMENT_MIN_PAYMENT,
    choose_debt_repayment_amount,
    ensure_debt_ledger,
    SURVIVAL_LOAN_DAILY_INTEREST_RATE,
    SURVIVAL_LOAN_DUE_TURNS,
    SURVIVAL_LOAN_PURPOSE_BY_CATEGORY,
    can_request_survival_loan,
)

_MIN_NONCRITICAL_CONSUME_THRESHOLD_FOOD = 50
_MIN_NONCRITICAL_CONSUME_THRESHOLD_DRINK = 40


_FALLBACK_PAYLOAD_KEYS: tuple[str, ...] = (
    "fallback_from_intent",
    "fallback_from_objective_key",
    "fallback_to_intent",
    "fallback_reason",
    "blocked_resupply_category",
    "agent_money",
    "material_threshold",
)


def _build_get_rich_fallback_intent(
    *,
    agent: dict[str, Any],
    source_intent: Intent,
    world_turn: int,
    fallback_reason: str,
    blocked_resupply_category: str | None,
    reason: str,
) -> Intent:
    source_metadata = source_intent.metadata if isinstance(source_intent.metadata, dict) else {}
    return Intent(
        kind=INTENT_GET_RICH,
        score=0.5,
        source_goal="get_rich",
        reason=reason,
        created_turn=world_turn,
        metadata={
            "fallback_from_intent": source_intent.kind,
            "fallback_from_objective_key": source_metadata.get("objective_key"),
            "fallback_to_intent": INTENT_GET_RICH,
            "fallback_reason": fallback_reason,
            "blocked_resupply_category": blocked_resupply_category,
            "agent_money": int(agent.get("money") or 0),
            "material_threshold": int(agent.get("material_threshold") or 0),
        },
    )


def _apply_plan_fallback_payload(payload: dict[str, Any], intent: Intent) -> None:
    metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
    for key in _FALLBACK_PAYLOAD_KEYS:
        value = metadata.get(key)
        if value is not None:
            payload[key] = value
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
        INTENT_REPAY_DEBT:        _plan_repay_debt,
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


def _is_location_exhausted_for_money(
    agent: dict[str, Any],
    *,
    location_id: str,
    objective_key: str,
    world_turn: int,
) -> bool:
    location_cooldowns = agent.get("location_search_cooldowns")
    if isinstance(location_cooldowns, dict):
        cooldown_until = location_cooldowns.get(str(location_id))
        if isinstance(cooldown_until, (int, float)) and int(cooldown_until) > int(world_turn):
            return True
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records", {}) if isinstance(memory_v3, dict) else {}
    for rec in records.values():
        if not isinstance(rec, dict):
            continue
        details = rec.get("details")
        if not isinstance(details, dict):
            details = {}
        action_kind = str(details.get("action_kind") or rec.get("kind") or "")
        if action_kind != "anomaly_search_exhausted":
            continue
        rec_location_id = str(details.get("location_id") or rec.get("location_id") or "")
        if rec_location_id != location_id:
            continue
        rec_objective_key = str(details.get("objective_key") or "")
        if rec_objective_key and rec_objective_key != objective_key:
            continue
        cooldown_until = details.get("cooldown_until_turn")
        if isinstance(cooldown_until, (int, float)) and int(cooldown_until) > world_turn:
            return True
    return False


def _has_active_support_exhaustion(
    agent: dict[str, Any],
    *,
    location_id: str | None,
    target_id: str | None,
    objective_key: str,
    world_turn: int,
    action_kinds: set[str],
) -> bool:
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records", {}) if isinstance(memory_v3, dict) else {}
    location_id_str = str(location_id or "")
    target_id_str = str(target_id or "")
    objective_key_str = str(objective_key or "")
    for rec in records.values():
        if not isinstance(rec, dict):
            continue
        details = rec.get("details")
        if not isinstance(details, dict):
            details = {}
        action_kind = str(details.get("action_kind") or rec.get("kind") or "")
        if action_kind not in action_kinds:
            continue
        rec_location_id = str(details.get("location_id") or rec.get("location_id") or "")
        if location_id_str and rec_location_id and rec_location_id != location_id_str:
            continue
        rec_target_id = str(details.get("target_id") or "")
        if target_id_str and rec_target_id and rec_target_id != target_id_str:
            continue
        rec_objective_key = str(details.get("objective_key") or "")
        if rec_objective_key and rec_objective_key != objective_key_str:
            continue
        cooldown_until = details.get("cooldown_until_turn")
        if isinstance(cooldown_until, (int, float)) and int(cooldown_until) <= world_turn:
            continue
        if action_kind == "anomaly_search_exhausted" and objective_key_str in {"GET_MONEY_FOR_RESUPPLY", "FIND_ARTIFACTS"}:
            return True
        if action_kind in {"support_source_exhausted", "witness_source_exhausted"} and objective_key_str in {
            "GATHER_INTEL",
            "LOCATE_TARGET",
            "VERIFY_LEAD",
            "TRACK_TARGET",
            "GET_MONEY_FOR_RESUPPLY",
        }:
            return True
    return False


def _is_support_source_exhausted(
    agent: dict[str, Any],
    *,
    location_id: str | None,
    target_id: str | None,
    objective_key: str,
    world_turn: int,
) -> bool:
    return _has_active_support_exhaustion(
        agent,
        location_id=location_id,
        target_id=target_id,
        objective_key=objective_key,
        world_turn=world_turn,
        action_kinds={"anomaly_search_exhausted", "support_source_exhausted", "witness_source_exhausted"},
    )


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
    pending_plan = _plan_pending_survival_purchase(
        agent=agent,
        intent=intent,
        category="medical",
        world_turn=world_turn,
    )
    if pending_plan is not None:
        return pending_plan
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

    loot_step = _build_local_corpse_loot_step(
        agent=agent,
        state=state,
        preferred_categories={"medical"},
        take_money=True,
    )
    if loot_step is not None:
        return Plan(
            intent_kind=intent.kind,
            steps=[loot_step],
            interruptible=False,
            confidence=0.95,
            created_turn=world_turn,
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
        # Nothing to sell at all — try survival credit from trader.
        trader_npc = _find_trader_npc_at_location(trader_loc, state)
        quote = quote_survival_purchase(agent=agent, category="medical")
        if trader_npc is not None and quote is not None and quote.principal_needed > 0:
            ok_loan, _ = can_request_survival_loan(
                state=state,
                debtor=agent,
                creditor=trader_npc,
                creditor_type="trader",
                item_category="medical",
                required_price=quote.required_price,
                world_turn=world_turn,
            )
            if ok_loan:
                loan_step_dict = _build_survival_loan_payload(
                    agent=agent,
                    trader_npc=trader_npc,
                    item_category="medical",
                    required_price=quote.required_price,
                    amount=quote.principal_needed,
                    item_type=quote.item_type,
                    world_turn=world_turn,
                )
                episode_id = str(loan_step_dict.get("survival_episode_id") or "")
                return Plan(
                    intent_kind=intent.kind,
                    steps=[
                        PlanStep(
                            kind=STEP_REQUEST_LOAN,
                            payload=loan_step_dict,
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                        PlanStep(
                            kind=STEP_TRADE_BUY_ITEM,
                            payload=_build_survival_buy_payload(
                                category="medical",
                                quote=quote,
                                reason="buy_medical_heal_loan",
                                survival_episode_id=episode_id,
                            ),
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                        PlanStep(
                            kind=STEP_CONSUME_ITEM,
                            payload={
                                "item_type": quote.item_type,
                                "reason": "emergency_heal",
                                "survival_episode_id": episode_id,
                                "survival_episode_category": "medical",
                            },
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                    ],
                    interruptible=False, confidence=0.8, created_turn=world_turn,
                )
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
                trader_npc = _find_trader_npc_at_location(trader_loc, state)
                quote = quote_survival_purchase(agent=agent, category="medical")
                if trader_npc is not None and quote is not None and quote.principal_needed > 0:
                    ok_loan, _ = can_request_survival_loan(
                        state=state,
                        debtor=agent,
                        creditor=trader_npc,
                        creditor_type="trader",
                        item_category="medical",
                        required_price=quote.required_price,
                        world_turn=world_turn,
                    )
                    if ok_loan:
                        loan_step = _build_survival_loan_payload(
                            agent=agent,
                            trader_npc=trader_npc,
                            item_category="medical",
                            required_price=quote.required_price,
                            amount=quote.principal_needed,
                            item_type=quote.item_type,
                            world_turn=world_turn,
                        )
                        episode_id = str(loan_step.get("survival_episode_id") or "")
                        return Plan(
                            intent_kind=intent.kind,
                            steps=[
                                PlanStep(
                                    kind=STEP_TRAVEL_TO_LOCATION,
                                    payload={"target_id": trader_loc, "reason": "buy_heal"},
                                    interruptible=True,
                                    expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
                                ),
                                PlanStep(
                                    kind=STEP_REQUEST_LOAN,
                                    payload=loan_step,
                                    interruptible=False,
                                    expected_duration_ticks=1,
                                ),
                                PlanStep(
                                    kind=STEP_TRADE_BUY_ITEM,
                                    payload=_build_survival_buy_payload(
                                        category="medical",
                                        quote=quote,
                                        reason="buy_medical_heal_loan",
                                        survival_episode_id=episode_id,
                                    ),
                                    interruptible=False,
                                    expected_duration_ticks=1,
                                ),
                                PlanStep(
                                    kind=STEP_CONSUME_ITEM,
                                    payload={
                                        "item_type": quote.item_type,
                                        "reason": "emergency_heal",
                                        "survival_episode_id": episode_id,
                                        "survival_episode_category": "medical",
                                    },
                                    interruptible=False,
                                    expected_duration_ticks=1,
                                ),
                            ],
                            interruptible=True,
                            confidence=0.75,
                            created_turn=world_turn,
                        )
                # Cannot afford and no safe sell / no loan.
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
    is_food = intent.kind == INTENT_SEEK_FOOD
    category = "food" if is_food else "drink"
    pending_plan = _plan_pending_survival_purchase(
        agent=agent,
        intent=intent,
        category=category,
        world_turn=world_turn,
    )
    if pending_plan is not None:
        return pending_plan
    inventory = agent.get("inventory", [])
    item_types = FOOD_ITEM_TYPES if is_food else DRINK_ITEM_TYPES

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

    loot_step = _build_local_corpse_loot_step(
        agent=agent,
        state=state,
        preferred_categories={category},
        take_money=True,
    )
    if loot_step is not None:
        return Plan(
            intent_kind=intent.kind,
            steps=[loot_step],
            interruptible=False,
            confidence=0.95,
            created_turn=world_turn,
        )

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
                consume_type = afford.cheapest_viable_item_type
                return Plan(
                    intent_kind=intent.kind,
                    steps=[
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
                        PlanStep(
                            STEP_CONSUME_ITEM,
                            {
                                "item_type": consume_type,
                                "reason": f"emergency_{category}",
                            },
                            interruptible=False,
                        ),
                    ],
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
                    PlanStep(
                        STEP_CONSUME_ITEM,
                        {
                            "item_type": afford.cheapest_viable_item_type,
                            "reason": f"emergency_{category}",
                        },
                        interruptible=False,
                    ),
                ]
                return Plan(intent_kind=intent.kind, steps=steps, interruptible=False, confidence=1.0, created_turn=world_turn)

            # No sellable item — try survival credit from the trader.
            trader_npc = _find_trader_npc_at_location(trader_loc, state)
            quote = quote_survival_purchase(
                agent=agent,
                category=category,
                compatible_item_types=compatible_types,
            )
            if trader_npc is not None and quote is not None and quote.principal_needed > 0:
                ok_loan, _ = can_request_survival_loan(
                    state=state,
                    debtor=agent,
                    creditor=trader_npc,
                    creditor_type="trader",
                    item_category=category,
                    required_price=quote.required_price,
                    world_turn=world_turn,
                )
                if ok_loan:
                    loan_step_dict = _build_survival_loan_payload(
                        agent=agent,
                        trader_npc=trader_npc,
                        item_category=category,
                        required_price=quote.required_price,
                        amount=quote.principal_needed,
                        item_type=quote.item_type,
                        world_turn=world_turn,
                    )
                    episode_id = str(loan_step_dict.get("survival_episode_id") or "")
                    steps = [
                        PlanStep(
                            STEP_REQUEST_LOAN,
                            loan_step_dict,
                            interruptible=False,
                        ),
                        PlanStep(
                            STEP_TRADE_BUY_ITEM,
                            _build_survival_buy_payload(
                                category=category,
                                quote=quote,
                                reason=f"buy_{category}_survival",
                                survival_episode_id=episode_id,
                            ),
                            interruptible=False,
                        ),
                        PlanStep(
                            STEP_CONSUME_ITEM,
                            {
                                "item_type": quote.item_type,
                                "reason": f"emergency_{category}",
                                "survival_episode_id": episode_id,
                                "survival_episode_category": category,
                            },
                            interruptible=False,
                        ),
                    ]
                    return Plan(
                        intent_kind=intent.kind,
                        steps=steps,
                        interruptible=False,
                        confidence=0.8,
                        created_turn=world_turn,
                    )

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
                    trader_npc = _find_trader_npc_at_location(trader_loc, state)
                    quote = quote_survival_purchase(
                        agent=agent,
                        category=category,
                        compatible_item_types=compatible_types,
                    )
                    if trader_npc is not None and quote is not None and quote.principal_needed > 0:
                        ok_loan, _ = can_request_survival_loan(
                            state=state,
                            debtor=agent,
                            creditor=trader_npc,
                            creditor_type="trader",
                            item_category=category,
                            required_price=quote.required_price,
                            world_turn=world_turn,
                        )
                        if ok_loan:
                            loan_step = _build_survival_loan_payload(
                                agent=agent,
                                trader_npc=trader_npc,
                                item_category=category,
                                required_price=quote.required_price,
                                amount=quote.principal_needed,
                                item_type=quote.item_type,
                                world_turn=world_turn,
                            )
                            episode_id = str(loan_step.get("survival_episode_id") or "")
                            steps = [
                                PlanStep(STEP_TRAVEL_TO_LOCATION,
                                         {"target_id": trader_loc, "reason": f"buy_{category}_survival"},
                                         expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state)),
                                PlanStep(STEP_REQUEST_LOAN, loan_step, interruptible=False),
                                PlanStep(STEP_TRADE_BUY_ITEM,
                                         _build_survival_buy_payload(
                                             category=category,
                                             quote=quote,
                                             reason=f"buy_{category}_survival",
                                             survival_episode_id=episode_id,
                                         ),
                                         interruptible=False),
                                PlanStep(STEP_CONSUME_ITEM,
                                         {
                                             "item_type": quote.item_type,
                                             "reason": f"emergency_{category}",
                                             "survival_episode_id": episode_id,
                                             "survival_episode_category": category,
                                         },
                                         interruptible=False),
                            ]
                            return Plan(intent_kind=intent.kind, steps=steps, confidence=0.6, created_turn=world_turn)
                    # Cannot afford, cannot sell, no loan — fallback to gather money.
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
                    PlanStep(
                        STEP_CONSUME_ITEM,
                        {
                            "item_type": remote_afford.cheapest_viable_item_type,
                            "reason": f"emergency_{category}",
                        },
                        interruptible=False,
                    ),
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
                PlanStep(
                    STEP_CONSUME_ITEM,
                    {
                        "item_type": remote_afford.cheapest_viable_item_type,
                        "reason": f"emergency_{category}",
                    },
                    interruptible=False,
                ),
            ]
            return Plan(intent_kind=intent.kind, steps=steps, confidence=0.7, created_turn=world_turn)

    if trader_loc and trader_loc == agent_loc:
        compatible_types_for_afford = set(item_types)
        afford_soft = evaluate_affordability(
            agent=agent,
            trader={},
            category=category,
            compatible_item_types=compatible_types_for_afford,
        )
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
        if afford_soft.can_buy_now:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    STEP_TRADE_BUY_ITEM,
                    buy_payload,
                    interruptible=False,
                )],
                interruptible=False, confidence=1.0, created_turn=world_turn,
            )
        # Cannot afford — try selling, then loan for survival categories
        liquidity_opts_soft = find_liquidity_options(
            agent=agent,
            immediate_needs=list(need_result.immediate_needs) if need_result else [],
            item_needs=list(need_result.item_needs) if need_result else evaluate_item_needs(ctx, state),
        )
        sellable_soft = next((o for o in liquidity_opts_soft if o.safety == "safe"), None)
        if sellable_soft is not None:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(STEP_TRADE_SELL_ITEM,
                             {"item_category": "any_sellable", "reason": f"fund_{category}",
                              "required_price": afford_soft.required_price},
                             interruptible=False),
                    PlanStep(STEP_TRADE_BUY_ITEM, buy_payload, interruptible=False),
                ],
                interruptible=False, confidence=0.85, created_turn=world_turn,
            )
        # No sellable — try survival credit only for immediate survival needs
        if category in ("food", "drink", "medical"):
            trader_npc_soft = _find_trader_npc_at_location(trader_loc, state)
            quote_soft = quote_survival_purchase(
                agent=agent,
                category=category,
                compatible_item_types=compatible_types_for_afford,
            )
            if trader_npc_soft is not None and quote_soft is not None and quote_soft.principal_needed > 0:
                ok_loan_soft, _ = can_request_survival_loan(
                    state=state,
                    debtor=agent,
                    creditor=trader_npc_soft,
                    creditor_type="trader",
                    item_category=category,
                    required_price=quote_soft.required_price,
                    world_turn=world_turn,
                )
                if ok_loan_soft:
                    loan_step_soft = _build_survival_loan_payload(
                        agent=agent,
                        trader_npc=trader_npc_soft,
                        item_category=category,
                        required_price=quote_soft.required_price,
                        amount=quote_soft.principal_needed,
                        item_type=quote_soft.item_type,
                        world_turn=world_turn,
                    )
                    episode_id = str(loan_step_soft.get("survival_episode_id") or "")
                    steps_soft = [
                        PlanStep(STEP_REQUEST_LOAN, loan_step_soft, interruptible=False),
                        PlanStep(
                            STEP_TRADE_BUY_ITEM,
                            _build_survival_buy_payload(
                                category=category,
                                quote=quote_soft,
                                reason=f"buy_{category}_survival_credit",
                                survival_episode_id=episode_id,
                            ),
                            interruptible=False,
                        ),
                    ]
                    steps_soft.append(PlanStep(
                        STEP_CONSUME_ITEM,
                        {
                            "item_type": quote_soft.item_type,
                            "reason": f"need_{category}",
                            "survival_episode_id": episode_id,
                            "survival_episode_category": category,
                        },
                        interruptible=False,
                    ))
                    return Plan(
                        intent_kind=intent.kind,
                        steps=steps_soft,
                        interruptible=False, confidence=0.75, created_turn=world_turn,
                    )
        # Cannot afford and no loan/sell path → fall through to get_rich
        get_rich_soft_intent = _build_get_rich_fallback_intent(
            agent=agent,
            source_intent=intent,
            world_turn=world_turn,
            fallback_reason="soft_restore_unaffordable",
            blocked_resupply_category=category,
            reason=f"Нет денег на {category} у трейдера. Перехожу к сбору ресурсов.",
        )
        return _plan_get_rich(ctx, get_rich_soft_intent, state, world_turn, need_result)

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

    # No trader path: use remembered source from BeliefState hints as safe fallback.
    hint_key = "food" if is_food else "water"
    mem_hint = _memory_hint(agent, hint_key)
    mem_loc = mem_hint.get("location_id") if isinstance(mem_hint, dict) else None
    if mem_loc:
        _record_memory_used(agent, mem_hint, used_for=("find_food" if is_food else "find_water"))
        if mem_loc == agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    STEP_EXPLORE_LOCATION,
                    {"reason": f"check_{category}_source_memory"},
                    interruptible=True,
                    expected_duration_ticks=1,
                )],
                confidence=0.55,
                created_turn=world_turn,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_TRAVEL_TO_LOCATION,
                {"target_id": mem_loc, "reason": f"find_{category}_from_memory"},
                expected_duration_ticks=_estimate_travel_ticks(ctx, mem_loc, state),
            )],
            confidence=0.55,
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


def _build_local_corpse_loot_step(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    preferred_categories: set[str],
    take_money: bool = True,
) -> PlanStep | None:
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    from app.games.zone_stalkers.balance.items import (
        AMMO_ITEM_TYPES,
        AMMO_FOR_WEAPON,
        ARMOR_ITEM_TYPES,
        DRINK_ITEM_TYPES,
        FOOD_ITEM_TYPES,
        HEAL_ITEM_TYPES,
        WEAPON_ITEM_TYPES,
    )

    location_id = str(agent.get("location_id") or "")
    if not location_id:
        return None
    corpses = (state.get("locations", {}).get(location_id, {}) or {}).get("corpses")
    if not isinstance(corpses, list):
        return None

    preferred = {str(category).strip().lower() for category in preferred_categories}
    weapon = (agent.get("equipment") or {}).get("weapon") or {}
    weapon_type = str(weapon.get("type") or "")
    compatible_ammo = AMMO_FOR_WEAPON.get(weapon_type)

    for corpse in corpses:
        if not isinstance(corpse, dict):
            continue
        if not bool(corpse.get("visible", True)) or not bool(corpse.get("lootable", True)):
            continue
        inventory = corpse.get("inventory")
        if not isinstance(inventory, list):
            inventory = []
        corpse_money = int(corpse.get("money") or 0)
        has_useful = False
        for item in inventory:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if "artifact" in preferred and item_type in ARTIFACT_TYPES:
                has_useful = True
                break
            if "food" in preferred and item_type in FOOD_ITEM_TYPES:
                has_useful = True
                break
            if "drink" in preferred and item_type in DRINK_ITEM_TYPES:
                has_useful = True
                break
            if "medical" in preferred and item_type in HEAL_ITEM_TYPES:
                has_useful = True
                break
            if "weapon" in preferred and item_type in WEAPON_ITEM_TYPES:
                has_useful = True
                break
            if "armor" in preferred and item_type in ARMOR_ITEM_TYPES:
                has_useful = True
                break
            if "ammo" in preferred and item_type in AMMO_ITEM_TYPES:
                if compatible_ammo is None or compatible_ammo == item_type:
                    has_useful = True
                    break
            if "any" in preferred:
                has_useful = True
                break
        if not has_useful and not (take_money and corpse_money > 0):
            continue
        return PlanStep(
            kind=STEP_LOOT_CORPSE,
            payload={
                "corpse_id": str(corpse.get("corpse_id") or ""),
                "location_id": location_id,
                "take_money": take_money,
            },
            interruptible=False,
            expected_duration_ticks=1,
        )
    return None


def _desired_supply_count(risk_tolerance: float, min_count: int, max_count: int) -> int:
    """Desired inventory count for a supply category based on risk tolerance.

    More risk-averse agents (low ``risk_tolerance``) want larger stocks.
    Mirrors the same helper in ``needs.py``.
    """
    return min_count + round((1.0 - risk_tolerance) * (max_count - min_count))


def _resupply_need_key_from_category(category: str | None) -> str | None:
    if category is None:
        return None
    return {
        "food": "food",
        "drink": "drink",
        "medical": "medicine",
        "medicine": "medicine",
        "weapon": "weapon",
        "armor": "armor",
        "ammo": "ammo",
        "upgrade": "upgrade",
    }.get(str(category).strip().lower())


def _plan_prepare_for_hunt(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    """Plan specifically for PREPARE_FOR_HUNT objective: buy the actual missing equipment."""
    from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES

    agent = ctx.self_state
    agent_loc = agent.get("location_id", "")
    meta = intent.metadata if isinstance(intent.metadata, dict) else {}
    required_hunt_equipment = meta.get("required_hunt_equipment") or {}
    missing = list(required_hunt_equipment.get("missing_requirements") or [])
    estimated_money_needed = int(meta.get("estimated_money_needed_for_advantage") or 0)

    # If there are no specific missing requirements, fall back to generic resupply
    if not missing:
        return None

    # Determine buy category from priority: weapon_upgrade > armor_upgrade > ammo > medical
    buy_category: str | None = None
    for req in ("weapon_upgrade", "armor_upgrade", "ammo_resupply", "medicine_resupply"):
        if req in missing:
            if req == "weapon_upgrade":
                buy_category = "weapon_upgrade"
            elif req == "armor_upgrade":
                buy_category = "armor_upgrade"
            elif req == "ammo_resupply":
                buy_category = "ammo"
            elif req == "medicine_resupply":
                buy_category = "medical"
            break

    if buy_category is None:
        return None

    trader_loc = _nearest_trader_location(ctx, state)
    agent_money = int(agent.get("money") or 0)

    # Determine required class for the buy payload
    buy_payload: dict[str, Any] = {
        "item_category": buy_category,
        "reason": "prepare_hunt_equipment",
    }
    if buy_category == "weapon_upgrade":
        min_class = required_hunt_equipment.get("weapon_min_class", "rifle")
        buy_payload["min_weapon_class"] = min_class
        buy_payload["target_weapon_class"] = meta.get("equipment_advantage", {}).get("target_weapon_class") if isinstance(meta.get("equipment_advantage"), dict) else None
    elif buy_category == "armor_upgrade":
        min_class = required_hunt_equipment.get("armor_min_class", "medium")
        buy_payload["min_armor_class"] = min_class
    elif buy_category == "ammo":
        min_count = required_hunt_equipment.get("ammo_min_count", 20)
        buy_payload["buy_mode"] = "hunt_ammo_resupply"
        buy_payload["min_count"] = min_count

    # Check per-item (priority item) affordability rather than full estimated total.
    # This allows buying the priority item (e.g. AK-74) even if total budget for all
    # requirements is not yet met.
    # Per-path affordability check below handles the get_rich fallback.

    # Agent at trader: try buying directly
    if trader_loc and trader_loc == agent_loc:
        afford = evaluate_affordability(
            agent=agent,
            trader={},
            category=buy_category,
            compatible_item_types=set(WEAPON_ITEM_TYPES) if buy_category == "weapon_upgrade" else (
                set(ARMOR_ITEM_TYPES) if buy_category == "armor_upgrade" else None
            ),
        )
        if afford.can_buy_now:
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(
                    STEP_TRADE_BUY_ITEM,
                    buy_payload,
                    interruptible=False,
                )],
                confidence=0.85,
                created_turn=world_turn,
            )
        # Not affordable now → get rich first
        get_rich_intent = _build_get_rich_fallback_intent(
            agent=agent,
            source_intent=intent,
            world_turn=world_turn,
            fallback_reason="hunt_preparation_no_money",
            blocked_resupply_category=buy_category,
            reason=(
                f"Нужна подготовка к охоте: {', '.join(missing)}. "
                "Не хватает денег на покупку снаряжения. Перехожу к добыче денег."
            ),
        )
        return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)

    # Trader not co-located: travel to trader
    if trader_loc and trader_loc != agent_loc:
        afford = evaluate_affordability(
            agent=agent,
            trader={},
            category=buy_category,
            compatible_item_types=set(WEAPON_ITEM_TYPES) if buy_category == "weapon_upgrade" else (
                set(ARMOR_ITEM_TYPES) if buy_category == "armor_upgrade" else None
            ),
        )
        if afford.can_buy_now:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": trader_loc, "reason": "prepare_hunt_equipment"},
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
        # Not affordable: get rich first
        get_rich_intent = _build_get_rich_fallback_intent(
            agent=agent,
            source_intent=intent,
            world_turn=world_turn,
            fallback_reason="hunt_preparation_no_money",
            blocked_resupply_category=buy_category,
            reason=(
                f"Нужна подготовка к охоте: {', '.join(missing)}. "
                "Не хватает денег. Перехожу к добыче денег."
            ),
        )
        return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)

    # No trader known
    get_rich_intent = _build_get_rich_fallback_intent(
        agent=agent,
        source_intent=intent,
        world_turn=world_turn,
        fallback_reason="no_trader_for_hunt_prep",
        blocked_resupply_category=buy_category,
        reason=(
            f"Нужна подготовка к охоте: {', '.join(missing)}, "
            "но трейдер недоступен. Перехожу к добыче денег."
        ),
    )
    return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)


def _plan_resupply(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    """Plan resupply using PR2 ItemNeed dominant-urgency semantics."""
    from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location

    # Hunt-specific preparation branch: target-aware equipment buying
    _intent_meta = intent.metadata if isinstance(intent.metadata, dict) else {}
    if (
        _intent_meta.get("support_objective_for") == "kill_stalker"
        and isinstance(_intent_meta.get("required_hunt_equipment"), dict)
        and (_intent_meta["required_hunt_equipment"].get("missing_requirements") or [])
    ):
        _hunt_plan = _plan_prepare_for_hunt(ctx, intent, state, world_turn, need_result)
        if _hunt_plan is not None:
            return _hunt_plan

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
            preferred_categories = {
                "food" if _buy_category == "food" else "",
                "drink" if _buy_category == "drink" else "",
                "medical" if _buy_category in ("medical", "medicine") else "",
                "weapon" if _buy_category == "weapon" else "",
                "armor" if _buy_category == "armor" else "",
                "ammo" if _buy_category == "ammo" else "",
            } - {""}
            loot_step = _build_local_corpse_loot_step(
                agent=agent,
                state=state,
                preferred_categories=preferred_categories,
                take_money=True,
            )
            if loot_step is not None:
                return Plan(
                    intent_kind=intent.kind,
                    steps=[loot_step],
                    interruptible=False,
                    confidence=0.95,
                    created_turn=world_turn,
                )

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
            _need_key = _resupply_need_key_from_category(_buy_category)
            _is_actionable, _blocked_by = (
                is_item_need_actionable(agent, _need_key) if _need_key else (True, None)
            )

            if not _is_actionable:
                _phase1_fb = _build_get_rich_fallback_intent(
                    agent=agent,
                    source_intent=intent,
                    world_turn=world_turn,
                    fallback_reason="resupply_not_actionable",
                    blocked_resupply_category=_buy_category,
                    reason=(
                        "Пополнение отложено политикой "
                        f"({_blocked_by or 'phase_gate'}). "
                        "Перехожу к сбору артефактов."
                    ),
                )
                return _plan_get_rich(ctx, _phase1_fb, state, world_turn, need_result)

            if trader_loc and trader_loc != agent_loc:
                # Phase-1 gate: non-hunter agents below material_threshold should
                # NOT travel to a remote trader to buy supplies.  They should
                # explore and gather resources (artifacts) instead.
                _p1_money = agent.get("money", 0)
                _p1_threshold = agent.get("material_threshold", 0)
                _p1_goal = agent.get("global_goal", "")
                if (
                    _p1_goal != "kill_stalker"
                    and _p1_threshold > 0
                    and _p1_money < _p1_threshold
                ):
                    _phase1_fb = _build_get_rich_fallback_intent(
                        agent=agent,
                        source_intent=intent,
                        world_turn=world_turn,
                        fallback_reason="phase1_material_threshold_gate",
                        blocked_resupply_category=_buy_category,
                        reason=(
                            "Phase-1: пополнение запасов желательно, "
                            "но поход к трейдеру отложен до достижения порога богатства. "
                            "Перехожу к сбору артефактов."
                        ),
                    )
                    return _plan_get_rich(ctx, _phase1_fb, state, world_turn, need_result)
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

            get_rich_intent = _build_get_rich_fallback_intent(
                agent=agent,
                source_intent=intent,
                world_turn=world_turn,
                fallback_reason="no_trader_known",
                blocked_resupply_category=_buy_category,
                reason=(
                    "Нужен resupply, но покупка сейчас невозможна/нецелесообразна. "
                    "Перехожу к fallback_get_money через поиск артефактов."
                ),
            )
            return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)

        return _plan_resupply_upgrade(ctx, intent, state, world_turn, need_result)

    item_needs = list(need_result.item_needs) if need_result is not None else evaluate_item_needs(ctx, state)
    forced_category_raw = (
        intent.metadata.get("forced_resupply_category")
        if isinstance(intent.metadata, dict)
        else None
    )
    forced_category = str(forced_category_raw).strip().lower() if forced_category_raw else None
    forced_category_aliases = {"medical": "medicine"}
    if forced_category in forced_category_aliases:
        forced_category = forced_category_aliases[forced_category]

    if forced_category is not None:
        dominant = next((need for need in item_needs if need.key == forced_category), None)
        if dominant is None or float(dominant.urgency) <= 0:
            fallback_reason = (
                f"forced_resupply_category={forced_category} недоступна сейчас. "
                "Перехожу к fallback_get_money."
            )
            get_rich_intent = _build_get_rich_fallback_intent(
                agent=agent,
                source_intent=intent,
                world_turn=world_turn,
                fallback_reason="forced_resupply_category_unavailable",
                blocked_resupply_category=forced_category,
                reason=fallback_reason,
            )
            return _plan_get_rich(ctx, get_rich_intent, state, world_turn, need_result)
    else:
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

    preferred_categories = {
        "food" if buy_category == "food" else "",
        "drink" if buy_category == "drink" else "",
        "medical" if buy_category == "medical" else "",
        "weapon" if buy_category == "weapon" else "",
        "armor" if buy_category == "armor" else "",
        "ammo" if buy_category == "ammo" else "",
    } - {""}
    loot_step = _build_local_corpse_loot_step(
        agent=agent,
        state=state,
        preferred_categories=preferred_categories,
        take_money=True,
    )
    if loot_step is not None:
        return Plan(
            intent_kind=intent.kind,
            steps=[loot_step],
            interruptible=False,
            confidence=0.95,
            created_turn=world_turn,
        )

    _dominant_actionable, _dominant_blocked_by = is_item_need_actionable(agent, dominant.key)
    if not _dominant_actionable:
        _phase_fb = _build_get_rich_fallback_intent(
            agent=agent,
            source_intent=intent,
            world_turn=world_turn,
            fallback_reason="resupply_not_actionable",
            blocked_resupply_category=buy_category,
            reason=(
                "Пополнение отложено политикой "
                f"({_dominant_blocked_by or 'phase_gate'}). "
                "Перехожу к сбору артефактов."
            ),
        )
        return _plan_get_rich(ctx, _phase_fb, state, world_turn, need_result)

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
        # Phase-1 gate: non-hunter agents below material_threshold should
        # NOT travel to a remote trader to buy supplies.
        _p1r2_money = agent.get("money", 0)
        _p1r2_threshold = agent.get("material_threshold", 0)
        _p1r2_goal = agent.get("global_goal", "")
        if (
            _p1r2_goal != "kill_stalker"
            and _p1r2_threshold > 0
            and _p1r2_money < _p1r2_threshold
        ):
            _p1r2_intent = _build_get_rich_fallback_intent(
                agent=agent,
                source_intent=intent,
                world_turn=world_turn,
                fallback_reason="phase1_material_threshold_gate",
                blocked_resupply_category=buy_category,
                reason=(
                    "Phase-1: пополнение запасов желательно, "
                    "но поход к трейдеру отложен до достижения порога богатства. "
                    "Перехожу к сбору артефактов."
                ),
            )
            return _plan_get_rich(ctx, _p1r2_intent, state, world_turn, need_result)
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
    get_rich_intent = _build_get_rich_fallback_intent(
        agent=agent,
        source_intent=intent,
        world_turn=world_turn,
        fallback_reason=(
            "no_trader_known"
            if not trader_loc
            else "resupply_unaffordable_no_liquidity"
        ),
        blocked_resupply_category=buy_category,
        reason=(
            "Нужен resupply, но покупка сейчас невозможна/нецелесообразна. "
            "Перехожу к fallback_get_money через поиск артефактов."
        ),
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
    if not _has_sellable_inventory(ctx.self_state, item_category="artifact"):
        return None
    trader_loc = _nearest_unblocked_trader_location_for_artifacts(
        ctx,
        state,
        world_turn=world_turn,
        used_for="sell_artifacts",
    )
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
    from app.games.zone_stalkers.rules.tick_rules import (
        _confirmed_empty_locations,
        _dijkstra_reachable_locations,
        _score_location,
    )
    agent = ctx.self_state
    has_artifacts = _has_sellable_inventory(agent, item_category="artifact")
    trader_loc = _nearest_unblocked_trader_location_for_artifacts(
        ctx,
        state,
        world_turn=world_turn,
        used_for="get_rich_sell_artifacts",
    )
    agent_loc = agent.get("location_id", "")
    fallback_metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
    fallback_reason = fallback_metadata.get("fallback_reason") if isinstance(fallback_metadata.get("fallback_reason"), str) else None
    objective_key = str((intent.metadata or {}).get("objective_key") or "FIND_ARTIFACTS")

    if objective_key == "GET_MONEY_FOR_RESUPPLY":
        loot_step = _build_local_corpse_loot_step(
            agent=agent,
            state=state,
            preferred_categories={"artifact"},
            take_money=True,
        )
        if loot_step is not None:
            return Plan(
                intent_kind=intent.kind,
                steps=[loot_step],
                confidence=0.9,
                created_turn=world_turn,
            )

    # 1. Sell artifacts
    if has_artifacts and trader_loc:
        sell_payload: dict[str, Any] = {"item_category": "artifact"}
        _apply_plan_fallback_payload(sell_payload, intent)
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
        _apply_plan_fallback_payload(travel_payload, intent)
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
    if (
        loc.get("anomaly_activity", 0) > 0
        and agent_loc not in confirmed_empty
        and not _is_location_exhausted_for_money(
            agent,
            location_id=str(agent_loc),
            objective_key=objective_key,
            world_turn=world_turn,
        )
    ):
        explore_payload: dict[str, Any] = {
            "target_id": agent_loc,
            "reason": "get_rich_explore_here",
        }
        _apply_plan_fallback_payload(explore_payload, intent)
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
        if _is_location_exhausted_for_money(
            agent,
            location_id=str(cand_id),
            objective_key=objective_key,
            world_turn=world_turn,
        ):
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
        _apply_plan_fallback_payload(travel_payload, intent)
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
        steps=[PlanStep(STEP_WAIT, {"reason": "get_rich_sources_exhausted"})],
        confidence=0.3, created_turn=world_turn,
    )


def _plan_hunt_target(
    ctx: AgentContext, intent: Intent, state: dict[str, Any], world_turn: int,
    need_result: NeedEvaluationResult | None = None
) -> Optional[Plan]:
    objective_key = str((intent.metadata or {}).get("objective_key") or "")
    target_id = intent.target_id or ctx.self_state.get("kill_target_id")
    target_loc = intent.target_location_id
    agent_loc = ctx.self_state.get("location_id")

    exhaustion_location_id = target_loc or agent_loc
    if objective_key and _is_support_source_exhausted(
        ctx.self_state,
        location_id=exhaustion_location_id,
        target_id=target_id,
        objective_key=objective_key,
        world_turn=world_turn,
    ):
        if objective_key in {"GATHER_INTEL", "LOCATE_TARGET", "VERIFY_LEAD", "TRACK_TARGET"}:
            return _build_hunt_expand_search_plan(
                ctx,
                intent,
                state,
                world_turn,
                target_id=target_id,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(STEP_WAIT, {"reason": "support_source_exhausted_preplan"})],
            confidence=0.3,
            created_turn=world_turn,
        )

    if objective_key == "ENGAGE_TARGET":
        if target_loc and target_loc != agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": target_loc, "reason": "engage_target"},
                        expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state),
                    ),
                    PlanStep(
                        STEP_START_COMBAT,
                        {"target_id": target_id, "reason": "engage_target"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                    PlanStep(
                        STEP_MONITOR_COMBAT,
                        {"target_id": target_id, "reason": "engage_target"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                    PlanStep(
                        STEP_CONFIRM_KILL,
                        {"target_id": target_id, "reason": "confirm_after_engage"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                ],
                confidence=0.8,
                created_turn=world_turn,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_START_COMBAT,
                    {"target_id": target_id, "reason": "engage_target"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ),
                PlanStep(
                    STEP_MONITOR_COMBAT,
                    {"target_id": target_id, "reason": "engage_target"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ),
                PlanStep(
                    STEP_CONFIRM_KILL,
                    {"target_id": target_id, "reason": "confirm_after_engage"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.85,
            created_turn=world_turn,
        )

    if objective_key == "VERIFY_LEAD":
        verify_steps: list[PlanStep] = []
        if target_loc and target_loc != agent_loc:
            verify_steps.append(
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": target_loc, "reason": "verify_lead"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state),
                )
            )
        verify_steps.extend([
            PlanStep(
                STEP_SEARCH_TARGET,
                {"target_id": target_id, "target_location_id": target_loc or agent_loc, "reason": "verify_lead"},
                expected_duration_ticks=1,
            ),
            PlanStep(
                STEP_LOOK_FOR_TRACKS,
                {"target_id": target_id, "target_location_id": target_loc or agent_loc, "reason": "verify_lead"},
                expected_duration_ticks=1,
            ),
            PlanStep(
                STEP_QUESTION_WITNESSES,
                {"target_id": target_id, "reason": "verify_lead"},
                expected_duration_ticks=1,
            ),
        ])
        return Plan(
            intent_kind=intent.kind,
            steps=verify_steps,
            confidence=0.76,
            created_turn=world_turn,
        )

    if objective_key == "TRACK_TARGET":
        if target_loc and target_loc != agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": target_loc, "reason": "track_target"},
                        expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state),
                    ),
                    PlanStep(
                        STEP_SEARCH_TARGET,
                        {"target_id": target_id, "target_location_id": target_loc, "reason": "track_target"},
                        expected_duration_ticks=1,
                    ),
                    PlanStep(
                        STEP_LOOK_FOR_TRACKS,
                        {"target_id": target_id, "target_location_id": target_loc, "reason": "track_target"},
                        expected_duration_ticks=1,
                    ),
                ],
                confidence=0.75,
                created_turn=world_turn,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_SEARCH_TARGET,
                    {"target_id": target_id, "target_location_id": agent_loc, "reason": "track_target"},
                    expected_duration_ticks=1,
                ),
                PlanStep(
                    STEP_LOOK_FOR_TRACKS,
                    {"target_id": target_id, "target_location_id": agent_loc, "reason": "track_target"},
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.7,
            created_turn=world_turn,
        )

    if objective_key == "CONFIRM_KILL":
        if target_loc and target_loc != agent_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": target_loc, "reason": "confirm_kill"},
                        expected_duration_ticks=_estimate_travel_ticks(ctx, target_loc, state),
                    ),
                    PlanStep(
                        STEP_CONFIRM_KILL,
                        {"target_id": target_id, "reason": "confirm_kill"},
                        interruptible=False,
                        expected_duration_ticks=1,
                    ),
                ],
                confidence=0.8,
                created_turn=world_turn,
            )
        if not target_loc:
            return Plan(
                intent_kind=intent.kind,
                steps=[
                    PlanStep(
                        STEP_QUESTION_WITNESSES,
                        {"target_id": target_id, "reason": "confirm_kill_location_unknown"},
                        expected_duration_ticks=1,
                    )
                ],
                confidence=0.6,
                created_turn=world_turn,
            )
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_CONFIRM_KILL,
                    {"target_id": target_id, "reason": "confirm_kill"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.85,
            created_turn=world_turn,
        )

    # If target is actually co-located right now, engage immediately regardless of objective_key.
    if target_id:
        _live_target = state.get("agents", {}).get(target_id)
        if (
            isinstance(_live_target, dict)
            and _live_target.get("is_alive", True)
            and _live_target.get("location_id") == agent_loc
        ):
            weapon = (ctx.self_state.get("equipment", {}) or {}).get("weapon")
            if isinstance(weapon, dict):
                return Plan(
                    intent_kind=intent.kind,
                    steps=[
                        PlanStep(
                            STEP_START_COMBAT,
                            {"target_id": target_id, "reason": "hunt_target_co_located"},
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                        PlanStep(
                            STEP_MONITOR_COMBAT,
                            {"target_id": target_id, "reason": "hunt_target_co_located"},
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                        PlanStep(
                            STEP_CONFIRM_KILL,
                            {"target_id": target_id, "reason": "confirm_after_hunt"},
                            interruptible=False,
                            expected_duration_ticks=1,
                        ),
                    ],
                    confidence=0.85,
                    created_turn=world_turn,
                )

    # GATHER_INTEL / LOCATE_TARGET fallback hunt behavior.
    trader_loc = _nearest_trader_location(ctx, state, used_for="find_trader")
    if trader_loc and trader_loc != agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": trader_loc, "reason": "hunt_visit_trader"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
                ),
                PlanStep(
                    STEP_ASK_FOR_INTEL,
                    {"target_id": target_id, "reason": "hunt_buy_or_request_intel"},
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.65,
            created_turn=world_turn,
        )
    if _location_has_live_trader(state, str(agent_loc or "")):
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_ASK_FOR_INTEL,
                    {"target_id": target_id, "reason": "hunt_buy_or_request_intel"},
                    expected_duration_ticks=1,
                )
            ],
            confidence=0.55,
            created_turn=world_turn,
        )
    return _build_hunt_expand_search_plan(
        ctx,
        intent,
        state,
        world_turn,
        target_id=target_id,
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
            steps=[
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": exit_loc, "reason": "leave_zone"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, exit_loc, state),
                ),
                PlanStep(
                    STEP_LEAVE_ZONE,
                    {"target_id": exit_loc, "reason": "leave_zone"},
                    interruptible=False,
                    expected_duration_ticks=1,
                ),
            ],
            interruptible=False, confidence=0.9, created_turn=world_turn,
        )
    if exit_loc and exit_loc == agent_loc:
        return Plan(
            intent_kind=intent.kind,
            steps=[PlanStep(
                STEP_LEAVE_ZONE,
                {"target_id": exit_loc, "reason": "leave_zone"},
                interruptible=False,
                expected_duration_ticks=1,
            )],
            interruptible=False,
            confidence=1.0,
            created_turn=world_turn,
        )
    return Plan(
        intent_kind=intent.kind,
        steps=[PlanStep(STEP_WAIT, {"reason": "leave_zone_no_exit"})],
        interruptible=False,
        confidence=0.2,
        created_turn=world_turn,
    )


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

def _record_memory_used(
    agent: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    used_for: str,
) -> None:
    """Store transient memory usage for brain_trace in this tick."""
    if not candidate:
        return
    memory_id = candidate.get("memory_id") or candidate.get("id")
    if not memory_id:
        return
    payload = {
        "id": memory_id,
        "kind": candidate.get("kind", "memory"),
        "summary": candidate.get("summary", ""),
        "confidence": round(float(candidate.get("confidence", 0.0)), 3),
        "used_for": used_for,
    }
    used_list = agent.setdefault("_memory_used_decision", [])
    if not any(u.get("id") == payload["id"] and u.get("used_for") == payload["used_for"] for u in used_list):
        used_list.append(payload)
    if len(used_list) > 5:
        agent["_memory_used_decision"] = used_list[:5]


def _memory_hint(agent: dict[str, Any], key: str) -> dict[str, Any] | None:
    hints = agent.get("_belief_memory_hints")
    if isinstance(hints, dict):
        value = hints.get(key)
        if isinstance(value, dict):
            return value
    return None


def _nearest_trader_location(
    ctx: AgentContext,
    state: dict[str, Any],
    *,
    used_for: str = "find_trader",
) -> Optional[str]:
    """Return nearest trader location; prefer memory-backed BeliefState hints."""
    agent = ctx.self_state
    hint = _memory_hint(agent, "trader")
    if hint and hint.get("location_id"):
        _record_memory_used(agent, hint, used_for=used_for)
        return str(hint["location_id"])

    from app.games.zone_stalkers.rules.tick_rules import _find_nearest_trader_location
    agent_loc = agent.get("location_id", "")
    return _find_nearest_trader_location(agent_loc, state)


def _location_has_live_trader(state: dict[str, Any], location_id: str | None) -> bool:
    if not location_id:
        return False
    traders = state.get("traders")
    if not isinstance(traders, dict):
        return False
    return any(
        isinstance(raw_trader, dict)
        and raw_trader.get("is_alive", True)
        and str(raw_trader.get("location_id") or "") == str(location_id)
        for raw_trader in traders.values()
    )


def _build_hunt_expand_search_plan(
    ctx: AgentContext,
    intent: Intent,
    state: dict[str, Any],
    world_turn: int,
    *,
    target_id: str | None,
) -> Plan:
    from app.games.zone_stalkers.rules.tick_rules import _dijkstra_reachable_locations

    agent = ctx.self_state
    agent_loc = str(agent.get("location_id") or "")
    reachable = _dijkstra_reachable_locations(agent_loc, state.get("locations", {}), max_minutes=9999)
    search_objective_key = str((intent.metadata or {}).get("objective_key") or "GATHER_INTEL")
    current_trader_exhausted = _has_active_support_exhaustion(
        agent,
        location_id=agent_loc,
        target_id=target_id,
        objective_key=search_objective_key,
        world_turn=world_turn,
        action_kinds={"support_source_exhausted"},
    )

    trader_candidates = sorted(
        {
            str(raw_trader.get("location_id") or "")
            for raw_trader in (state.get("traders") or {}).values()
            if isinstance(raw_trader, dict)
            and raw_trader.get("is_alive", True)
            and str(raw_trader.get("location_id") or "")
            and str(raw_trader.get("location_id") or "") != agent_loc
            and not _has_active_support_exhaustion(
                agent,
                location_id=str(raw_trader.get("location_id") or ""),
                target_id=target_id,
                objective_key=search_objective_key,
                world_turn=world_turn,
                action_kinds={"support_source_exhausted"},
            )
        },
        key=lambda loc: (int(reachable.get(loc, 10**9)), loc),
    )
    if trader_candidates:
        trader_loc = trader_candidates[0]
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": trader_loc, "reason": "hunt_expand_search_trader_hub"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, trader_loc, state),
                ),
                PlanStep(
                    STEP_ASK_FOR_INTEL,
                    {"target_id": target_id, "reason": "hunt_expand_search_trader_hub"},
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.62,
            created_turn=world_turn,
        )

    witness_candidates = [
        loc
        for loc, _distance in sorted(reachable.items(), key=lambda item: (int(item[1]), item[0]))
        if loc != agent_loc
        and not _is_support_source_exhausted(
            agent,
            location_id=str(loc),
            target_id=target_id,
            objective_key=search_objective_key,
            world_turn=world_turn,
        )
    ]
    if witness_candidates:
        dest = str(witness_candidates[0])
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_TRAVEL_TO_LOCATION,
                    {"target_id": dest, "reason": "hunt_expand_search_neighbor"},
                    expected_duration_ticks=_estimate_travel_ticks(ctx, dest, state),
                ),
                PlanStep(
                    STEP_QUESTION_WITNESSES,
                    {"target_id": target_id, "reason": "hunt_expand_search_neighbor"},
                    expected_duration_ticks=1,
                ),
                PlanStep(
                    STEP_LOOK_FOR_TRACKS,
                    {"target_id": target_id, "target_location_id": dest, "reason": "hunt_expand_search_neighbor"},
                    expected_duration_ticks=1,
                ),
            ],
            confidence=0.56,
            created_turn=world_turn,
        )

    if not current_trader_exhausted and _location_has_live_trader(state, agent_loc):
        return Plan(
            intent_kind=intent.kind,
            steps=[
                PlanStep(
                    STEP_ASK_FOR_INTEL,
                    {"target_id": target_id, "reason": "hunt_expand_search_local_trader"},
                    expected_duration_ticks=1,
                )
            ],
            confidence=0.5,
            created_turn=world_turn,
        )

    return Plan(
        intent_kind=intent.kind,
        steps=[
            PlanStep(
                STEP_LOOK_FOR_TRACKS,
                {"target_id": target_id, "target_location_id": agent_loc, "reason": "hunt_expand_search_tracks"},
                expected_duration_ticks=1,
            )
        ],
        confidence=0.4,
        created_turn=world_turn,
    )


def _artifact_item_types(agent: dict[str, Any]) -> set[str]:
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES

    artifact_types = frozenset(ARTIFACT_TYPES.keys())
    return {
        str(item.get("type") or "")
        for item in (agent.get("inventory") or [])
        if item.get("type") in artifact_types
    }


def _nearest_unblocked_trader_location_for_artifacts(
    ctx: AgentContext,
    state: dict[str, Any],
    *,
    world_turn: int,
    used_for: str,
) -> Optional[str]:
    from app.games.zone_stalkers.decision.trade_sell_failures import has_recent_trade_sell_failure_for_agent

    agent = ctx.self_state
    agent_loc = str(agent.get("location_id") or "")
    artifact_item_types = _artifact_item_types(agent)
    if not artifact_item_types:
        return _nearest_trader_location(ctx, state, used_for=used_for)

    traders = state.get("traders")
    if not isinstance(traders, dict) or not traders:
        return _nearest_trader_location(ctx, state, used_for=used_for)

    unblocked_locations: set[str] = set()
    for trader_key, raw_trader in traders.items():
        if not isinstance(raw_trader, dict):
            continue
        if raw_trader.get("is_alive", True) is False:
            continue
        location_id = str(raw_trader.get("location_id") or "")
        if not location_id:
            continue
        trader_id = str(raw_trader.get("agent_id") or raw_trader.get("id") or trader_key or "")
        if has_recent_trade_sell_failure_for_agent(
            agent,
            trader_id=trader_id,
            location_id=location_id,
            item_types=artifact_item_types,
            world_turn=world_turn,
        ):
            continue
        unblocked_locations.add(location_id)

    if not unblocked_locations:
        return None
    if agent_loc in unblocked_locations:
        return agent_loc

    return min(
        unblocked_locations,
        key=lambda location_id: (_estimate_travel_ticks(ctx, location_id, state), location_id),
    )


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



def _plan_repay_debt(
    ctx: AgentContext,
    intent: Intent,
    state: dict[str, Any],
    world_turn: int,
    need_result: NeedEvaluationResult | None = None,
) -> Plan | None:
    agent = ctx.self_state
    agent_id = str(agent.get("id") or ctx.agent_id or "")
    if not agent_id:
        return None

    if need_result is not None and any(
        need.key in {"drink_now", "eat_now", "heal_now"} and float(need.urgency) >= 0.8
        for need in need_result.immediate_needs
    ):
        return None

    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    account_ids = (ledger.get("by_debtor") or {}).get(agent_id, []) or []
    accounts = []
    for account_id in account_ids:
        account = (ledger.get("accounts") or {}).get(str(account_id))
        if not isinstance(account, dict):
            continue
        if str(account.get("status") or "") != "active":
            continue
        if int(account.get("outstanding_total") or 0) <= 0:
            continue
        accounts.append(account)
    if not accounts:
        return None

    accounts.sort(key=lambda a: (
        int(a.get("next_due_turn") or world_turn + 999999),
        -int(a.get("outstanding_total") or 0),
    ))
    account = accounts[0]
    creditor_id = str(account.get("creditor_id") or "")
    if not creditor_id:
        return None

    creditors = state.get("traders") if str(account.get("creditor_type") or "") == "trader" else state.get("agents")
    creditor = (creditors or {}).get(creditor_id) if isinstance(creditors, dict) else None
    if not isinstance(creditor, dict):
        return None

    if choose_debt_repayment_amount(
        debtor=agent,
        account=account,
        world_turn=world_turn,
        critical_needs=False,
    ) < DEBT_REPAYMENT_MIN_PAYMENT:
        return None

    agent_loc = str(agent.get("location_id") or "")
    creditor_loc = str(creditor.get("location_id") or "")
    steps: list[PlanStep] = []
    if creditor_loc and creditor_loc != agent_loc:
        steps.append(PlanStep(
            kind=STEP_TRAVEL_TO_LOCATION,
            payload={"target_id": creditor_loc, "reason": "repay_debt"},
            interruptible=True,
        ))

    steps.append(PlanStep(
        kind=STEP_REPAY_DEBT,
        payload={
            "creditor_id": creditor_id,
            "creditor_type": str(account.get("creditor_type") or "trader"),
            "account_id": str(account.get("id") or ""),
            "reason": "reduce_debt_before_rollover",
            "allow_partial": True,
        },
        interruptible=False,
    ))
    return Plan(intent_kind=intent.kind, steps=steps, confidence=0.82, created_turn=world_turn)


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
    """Compatibility wrapper for planner sell checks."""
    return _has_sellable_inventory(agent, item_category="any_sellable")


def _has_sellable_inventory(agent: dict[str, Any], *, item_category: str) -> bool:
    from app.games.zone_stalkers.decision.executors import has_sellable_inventory  # noqa: PLC0415

    return has_sellable_inventory(agent, item_category=item_category)


def _plan_pending_survival_purchase(
    *,
    agent: dict[str, Any],
    intent: Intent,
    category: str,
    world_turn: int,
) -> Optional[Plan]:
    pending = agent.get("pending_survival_purchase")
    if not isinstance(pending, dict):
        return None
    if str(pending.get("category") or "") != str(category):
        return None
    if int(pending.get("expires_turn") or 0) < int(world_turn):
        return None
    required_price = int(pending.get("required_price") or 0)
    if int(agent.get("money") or 0) < required_price:
        return None
    item_type = str(pending.get("expected_item_type") or "")
    if not item_type:
        return None

    survival_episode_id = str(pending.get("survival_episode_id") or "")
    buy_payload = {
        "item_category": category,
        "reason": f"buy_{category}_survival_pending",
        "buy_mode": "survival_cheapest",
        "required_price": required_price,
        "expected_item_type": item_type,
        "previous_step_was_survival_credit": True,
        "survival_episode_id": survival_episode_id,
        "survival_episode_category": category,
    }
    return Plan(
        intent_kind=intent.kind,
        steps=[
            PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload=buy_payload,
                interruptible=False,
                expected_duration_ticks=1,
            ),
            PlanStep(
                kind=STEP_CONSUME_ITEM,
                payload={
                    "item_type": item_type,
                    "reason": f"emergency_{category}",
                    "survival_episode_id": survival_episode_id,
                    "survival_episode_category": category,
                },
                interruptible=False,
                expected_duration_ticks=1,
            ),
        ],
        interruptible=False,
        confidence=0.95,
        created_turn=world_turn,
    )


def _build_survival_buy_payload(
    *,
    category: str,
    quote: Any,
    reason: str,
    survival_episode_id: str | None = None,
) -> dict[str, Any]:
    return {
        "item_category": category,
        "reason": reason,
        "buy_mode": "survival_cheapest",
        "compatible_item_types": list(quote.compatible_item_types),
        "required_price": int(quote.required_price),
        "expected_item_type": quote.item_type,
        "previous_step_was_survival_credit": True,
        "survival_episode_id": str(survival_episode_id or ""),
        "survival_episode_category": category,
    }



def _build_survival_loan_payload(
    *,
    agent: dict[str, Any],
    trader_npc: dict[str, Any],
    item_category: str,
    required_price: int,
    amount: int,
    item_type: str,
    world_turn: int,
    survival_episode_id: str | None = None,
) -> dict[str, Any]:
    trader_id = str(trader_npc.get("id") or "")
    principal_needed = max(0, int(required_price) - int(agent.get("money") or 0))
    corrected_amount = max(int(amount), principal_needed)
    episode_id = str(
        survival_episode_id
        or f"survival_{item_category}_{int(world_turn)}_{str(agent.get('id') or agent.get('name') or 'agent')}"
    )
    return {
        "creditor_id": trader_id,
        "creditor_type": "trader",
        "amount": corrected_amount,
        "purpose": SURVIVAL_LOAN_PURPOSE_BY_CATEGORY.get(item_category, "survival_generic"),
        "item_category": item_category,
        "required_price": int(required_price),
        "principal_needed": principal_needed,
        "survival_credit_quote_item_type": item_type,
        "survival_credit_sized_to_purchase": True,
        "daily_interest_rate": SURVIVAL_LOAN_DAILY_INTEREST_RATE,
        "due_turns": SURVIVAL_LOAN_DUE_TURNS,
        "reason": f"survival_credit_{item_category}",
        "location_id": str(agent.get("location_id") or ""),
        "survival_episode_id": episode_id,
        "survival_episode_category": item_category,
        "expected_item_type": item_type,
    }

def _find_trader_npc_at_location(
    loc_id: str,
    state: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Return the first trader NPC dict at *loc_id*, or None."""
    from app.games.zone_stalkers.rules.tick_rules import _find_trader_at_location
    trader = _find_trader_at_location(loc_id, state)
    if not isinstance(trader, dict):
        return None
    if not trader.get("id"):
        for trader_id, trader_obj in (state.get("traders") or {}).items():
            if trader_obj is trader:
                trader["id"] = str(trader_id)
                break
    return trader
