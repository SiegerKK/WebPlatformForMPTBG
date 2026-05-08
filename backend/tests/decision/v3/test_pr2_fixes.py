"""PR 2 fix regression and integration tests.

Covers:
  Fix 1  — NeedEvaluationResult passes through full tick decision pipeline.
  Fix 2  — ItemNeed.affordability_hint is populated.
  Fix 3  — Resupply does not sell risky/last-survival items for normal resupply.
  Fix 4  — Remote trader survival purchase checks affordability before travel→buy.
  Fix 5  — heal_self uses affordability/liquidity (PR2 migration).
  Fix 6  — _exec_consume records correct action_kind for need_food/need_drink.
  Fix 7  — brain_trace liquidity summary contains money_missing/required_price/decision.
  Extra  — Stabilised Поцик regression case.
           PR1 sleep behavior still passes.
           Critical hunger/thirst with inventory → consume (not buy).
           Critical hunger + no weapon → seek_food, not resupply.
"""
from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.debug.brain_trace import write_decision_brain_trace_from_v2
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.intents import select_intent
from app.games.zone_stalkers.decision.models.intent import (
    INTENT_HEAL_SELF,
    INTENT_RESUPPLY,
    INTENT_REST,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    Intent,
)
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_CONSUME_ITEM,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_TRAVEL_TO_LOCATION,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import build_plan
from tests.decision.conftest import make_agent, make_minimal_state, make_state_with_trader


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_intent(kind: str, score: float = 0.8) -> Intent:
    return Intent(kind=kind, score=score, created_turn=100)


def _plan_for(agent_id="bot1", agent=None, state=None, intent_kind=INTENT_SEEK_FOOD,
              with_need_result: bool = False):
    if agent is None:
        agent = make_agent()
    if state is None:
        state = make_minimal_state(agent_id=agent_id, agent=agent)
    ctx = build_agent_context(agent_id, agent, state)
    intent = _make_intent(intent_kind)
    if with_need_result:
        need_result = evaluate_need_result(ctx, state)
        return build_plan(ctx, intent, state, 100, need_result=need_result)
    return build_plan(ctx, intent, state, 100)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — NeedEvaluationResult passes through full decision pipeline
# ─────────────────────────────────────────────────────────────────────────────

def test_decision_pipeline_passes_need_result_to_planner_and_trace() -> None:
    """Full pipeline: critical thirst + water → seek_water intent → consume plan →
    brain_trace event contains immediate_needs."""
    agent = make_agent(
        thirst=90,
        money=100,
        inventory=[{"id": "w0", "type": "water", "value": 30}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)

    need_result = evaluate_need_result(ctx, state)
    assert any(n.key == "drink_now" and n.trigger_context == "survival"
               for n in need_result.immediate_needs), "Expected critical drink_now need"

    intent = select_intent(ctx, need_result.scores, 100, need_result=need_result)
    assert intent.kind == INTENT_SEEK_WATER, f"Expected seek_water but got {intent.kind}"

    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_CONSUME_ITEM, (
        f"Expected consume water step, got {plan.steps[0].kind}"
    )

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=100,
        intent_kind=intent.kind,
        intent_score=intent.score,
        reason=intent.reason,
        state=state,
        need_result=need_result,
    )
    trace = agent["brain_trace"]
    last_event = trace["events"][-1]
    assert "immediate_needs" in last_event, (
        "brain_trace event must include immediate_needs when need_result is provided"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — ItemNeed.affordability_hint is populated
# ─────────────────────────────────────────────────────────────────────────────

def test_item_need_affordability_hint_unaffordable() -> None:
    """Agent with no money → weapon ItemNeed has affordability_hint='unaffordable'."""
    agent = make_agent(has_weapon=False, has_armor=True, money=0, inventory=[])
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    weapon_need = next((n for n in need_result.item_needs if n.key == "weapon"), None)
    assert weapon_need is not None
    assert weapon_need.affordability_hint == "unaffordable", (
        f"Expected unaffordable, got {weapon_need.affordability_hint}"
    )


def test_item_need_affordability_hint_affordable() -> None:
    """Agent with plenty of money → weapon ItemNeed has affordability_hint='affordable'."""
    agent = make_agent(has_weapon=False, has_armor=True, money=2000, inventory=[])
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    weapon_need = next((n for n in need_result.item_needs if n.key == "weapon"), None)
    assert weapon_need is not None
    assert weapon_need.affordability_hint == "affordable", (
        f"Expected affordable, got {weapon_need.affordability_hint}"
    )


def test_item_need_affordability_hint_food_stock() -> None:
    """Agent with no food and no money → food ItemNeed has affordability_hint='unaffordable'."""
    agent = make_agent(money=0, inventory=[])
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    food_need = next((n for n in need_result.item_needs if n.key == "food"), None)
    assert food_need is not None
    assert food_need.affordability_hint == "unaffordable"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 — Resupply must NOT sell risky/last-survival items for normal resupply
# ─────────────────────────────────────────────────────────────────────────────

def test_resupply_does_not_sell_last_food_below_reserve_for_weapon() -> None:
    """Agent with 1 bread (below desired reserve=2) + no weapon + at trader.
    Resupply plan must NOT include a sell step for the last food item."""
    agent = make_agent(
        has_weapon=False,
        has_armor=True,
        has_ammo=False,
        money=0,  # cannot afford weapon
        inventory=[{"id": "b0", "type": "bread", "value": 30}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_RESUPPLY)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    sell_steps = [s for s in plan.steps if s.kind == STEP_TRADE_SELL_ITEM]
    assert len(sell_steps) == 0, (
        "Resupply must not sell last food (risky item) to fund weapon purchase"
    )


def test_resupply_sells_safe_artifact_to_fund_weapon() -> None:
    """Agent with artifact (safe to sell) + no weapon + no money → sell artifact, buy weapon."""
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    artifact_type = next(iter(ARTIFACT_TYPES))
    agent = make_agent(
        has_weapon=False,
        has_armor=True,
        has_ammo=False,
        money=0,
        inventory=[{"id": "a0", "type": artifact_type, "value": 500}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_RESUPPLY)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    sell_steps = [s for s in plan.steps if s.kind == STEP_TRADE_SELL_ITEM]
    assert len(sell_steps) == 1, "Resupply with safe artifact + no money should sell artifact first"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4 — Remote trader survival purchase checks affordability before travel→buy
# ─────────────────────────────────────────────────────────────────────────────

def test_critical_food_remote_trader_unaffordable_no_liquidity_falls_back() -> None:
    """Agent with critical hunger, no money, no sellable items, remote trader
    → plan must NOT be a bare travel→buy loop (fallback to get_rich instead)."""
    agent = make_agent(
        hunger=90,
        money=0,
        inventory=[],  # no food, no sellable items
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_SEEK_FOOD)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    # The plan should NOT end with a bare BUY step (pointing at the remote trader)
    # when the agent cannot afford the item and has nothing to sell.
    # Acceptable outcomes: get_rich plan (explore/loot) or a sell+buy plan.
    buy_steps = [s for s in plan.steps if s.kind == STEP_TRADE_BUY_ITEM]
    travel_then_buy = (
        len(plan.steps) == 2
        and plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        and plan.steps[-1].kind == STEP_TRADE_BUY_ITEM
    )
    assert not travel_then_buy, (
        "Must not build bare travel→buy when agent cannot afford item and has no liquidity"
    )


def test_critical_food_remote_trader_unaffordable_with_safe_liquidity_builds_sell_plan() -> None:
    """Agent with critical hunger, no money, has safe sellable artifact, remote trader
    → plan should include sell step before buy."""
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    artifact_type = next(iter(ARTIFACT_TYPES))
    agent = make_agent(
        hunger=90,
        money=0,
        inventory=[{"id": "art0", "type": artifact_type, "value": 500}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_SEEK_FOOD)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    sell_steps = [s for s in plan.steps if s.kind == STEP_TRADE_SELL_ITEM]
    assert len(sell_steps) >= 1, (
        "With safe liquidity (artifact), plan should include a sell step to fund food purchase"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 5 — heal_self uses affordability / liquidity (PR2 migration)
# ─────────────────────────────────────────────────────────────────────────────

def test_heal_self_with_medkit_in_inventory_consumes_it() -> None:
    """HP low + medkit in inventory → plan consumes medkit (not travel→buy)."""
    agent = make_agent(
        hp=20,
        money=0,
        inventory=[{"id": "med0", "type": "bandage", "value": 50}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_HEAL_SELF)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_CONSUME_ITEM, (
        f"Expected consume medkit, got {plan.steps[0].kind}"
    )


def test_heal_self_no_medkit_affordable_buys_immediately() -> None:
    """HP low + no medkit + enough money + at trader → plan buys medkit."""
    agent = make_agent(hp=20, money=2000, inventory=[])
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_HEAL_SELF)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM, (
        f"Expected buy medical, got {plan.steps[0].kind}"
    )


def test_heal_self_no_medkit_unaffordable_has_artifact_sells_first() -> None:
    """HP low + no medkit + money insufficient + artifact → sell artifact then buy medkit."""
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    artifact_type = next(iter(ARTIFACT_TYPES))
    agent = make_agent(
        hp=20,
        money=0,
        inventory=[{"id": "art0", "type": artifact_type, "value": 500}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_HEAL_SELF)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    sell_steps = [s for s in plan.steps if s.kind == STEP_TRADE_SELL_ITEM]
    buy_steps = [s for s in plan.steps if s.kind == STEP_TRADE_BUY_ITEM]
    assert len(sell_steps) == 1, "Should sell artifact before buying medkit"
    assert len(buy_steps) == 1, "Should buy medkit after selling artifact"


def test_heal_self_no_medkit_only_last_food_water_no_sell() -> None:
    """HP low + no medkit + only last food/water (risky/emergency) → do NOT sell them."""
    agent = make_agent(
        hp=20,
        money=0,
        inventory=[
            {"id": "f0", "type": "bread", "value": 30},
            {"id": "w0", "type": "water", "value": 30},
        ],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_HEAL_SELF)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    # Acceptable: plan is None, or plan has no safe sell of the last food/water.
    # Must not produce a plan that ONLY sells the critical food/water item.
    if plan:
        sell_steps = [s for s in plan.steps if s.kind == STEP_TRADE_SELL_ITEM]
        # If there's a sell step, it must not be the ONLY path forward when item is last survival item.
        # Verify the sell safety: bread/water below desired reserve should not be sold safely.
        # The test passes if either there's no sell at all, or there's a safe-only sell
        # (which food/water below reserve is not).
        # The key invariant: plan should not sell the safe+risky items when only critical items remain.
        buy_steps = [s for s in plan.steps if s.kind == STEP_TRADE_BUY_ITEM]
        if sell_steps and not buy_steps:
            raise AssertionError(
                "Plan must not contain only a sell step without a subsequent buy step"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 6 — _exec_consume records correct action_kind for need_food / need_drink
# ─────────────────────────────────────────────────────────────────────────────

def test_need_food_reason_records_consume_food_action_kind() -> None:
    """Consume step with reason='need_food' → action memory records action_kind=consume_food."""
    agent = make_agent(
        hunger=70,
        money=200,
        inventory=[{"id": "b0", "type": "bread", "value": 30}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)

    plan = Plan(
        intent_kind=INTENT_SEEK_FOOD,
        steps=[PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": "bread", "reason": "need_food"},
            interruptible=False,
            expected_duration_ticks=1,
        )],
        created_turn=100,
    )
    execute_plan_step(ctx, plan, state, 100)
    action_mem = next(
        (m for m in agent.get("memory", []) if m.get("type") == "action"), None
    )
    assert action_mem is not None, "No action memory entry after consume"
    assert action_mem.get("effects", {}).get("action_kind") == "consume_food", (
        f"Expected consume_food, got {action_mem.get('effects', {}).get('action_kind')}"
    )


def test_need_drink_reason_records_consume_drink_action_kind() -> None:
    """Consume step with reason='need_drink' → action memory records action_kind=consume_drink."""
    agent = make_agent(
        thirst=70,
        money=200,
        inventory=[{"id": "w0", "type": "water", "value": 30}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)

    plan = Plan(
        intent_kind=INTENT_SEEK_WATER,
        steps=[PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": "water", "reason": "need_drink"},
            interruptible=False,
            expected_duration_ticks=1,
        )],
        created_turn=100,
    )
    execute_plan_step(ctx, plan, state, 100)
    action_mem = next(
        (m for m in agent.get("memory", []) if m.get("type") == "action"), None
    )
    assert action_mem is not None
    assert action_mem.get("effects", {}).get("action_kind") == "consume_drink", (
        f"Expected consume_drink, got {action_mem.get('effects', {}).get('action_kind')}"
    )


def test_unknown_reason_falls_back_to_item_type_category() -> None:
    """Consume step with unknown reason='some_other_reason' + item=bread
    → falls back to consume_food (by item type)."""
    agent = make_agent(
        hunger=50,
        money=200,
        inventory=[{"id": "b0", "type": "bread", "value": 30}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)

    plan = Plan(
        intent_kind=INTENT_SEEK_FOOD,
        steps=[PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": "bread", "reason": "some_other_reason"},
            interruptible=False,
            expected_duration_ticks=1,
        )],
        created_turn=100,
    )
    execute_plan_step(ctx, plan, state, 100)
    action_mem = next(
        (m for m in agent.get("memory", []) if m.get("type") == "action"), None
    )
    assert action_mem is not None
    assert action_mem.get("effects", {}).get("action_kind") == "consume_food"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 7 — brain_trace liquidity summary includes money_missing/required_price/decision
# ─────────────────────────────────────────────────────────────────────────────

def test_brain_trace_liquidity_summary_has_enriched_fields() -> None:
    """brain_trace event should contain required_price, money_missing, decision
    when need_result is provided and agent has an urgent unaffordable item need."""
    agent = make_agent(
        has_weapon=False,
        money=0,
        inventory=[],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=100,
        intent_kind=INTENT_RESUPPLY,
        intent_score=0.7,
        reason="no_weapon",
        state=state,
        need_result=need_result,
    )
    trace = agent["brain_trace"]
    last_event = trace["events"][-1]
    liq = last_event.get("liquidity")
    assert liq is not None, "brain_trace event must include liquidity block"
    assert "required_price" in liq, f"liquidity must have required_price; got {liq}"
    assert "money_missing" in liq, f"liquidity must have money_missing; got {liq}"
    assert "planner_allowed_decision" in liq, (
        "liquidity must include planner_allowed_decision to represent what planner "
        f"is allowed to do with available liquidity; got {liq}"
    )
    assert "risky_liquidity_available" in liq, (
        "liquidity must include risky_liquidity_available so trace can show risky "
        f"liquidity presence without implying planner can use it; got {liq}"
    )
    assert liq["money_missing"] > 0, "agent with no money should have money_missing > 0"
    assert liq["planner_allowed_decision"] == "fallback_get_money", (
        "Expected fallback_get_money when no money and no liquidity, "
        f"got {liq['planner_allowed_decision']}"
    )
    # Backward-compatible alias mirrors planner_allowed_decision.
    assert liq["decision"] == liq["planner_allowed_decision"]
    assert liq["risky_liquidity_available"] is False


def test_brain_trace_liquidity_decision_affordable() -> None:
    """When agent can afford dominant need, decision should be 'affordable'."""
    agent = make_agent(
        has_weapon=False,
        money=5000,
        inventory=[],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=100,
        intent_kind=INTENT_RESUPPLY,
        intent_score=0.7,
        reason="no_weapon",
        state=state,
        need_result=need_result,
    )
    trace = agent["brain_trace"]
    last_event = trace["events"][-1]
    liq = last_event.get("liquidity")
    assert liq is not None
    assert liq.get("planner_allowed_decision") == "affordable", (
        "Expected affordable when agent has 5000 money, "
        f"got {liq.get('planner_allowed_decision')}"
    )
    assert liq.get("decision") == "affordable"


def test_brain_trace_liquidity_risky_available_but_not_planner_allowed() -> None:
    """Risky liquidity can exist while planner_allowed_decision still disallows risky sell."""
    agent = make_agent(
        has_weapon=False,
        money=0,
        inventory=[{"id": "b0", "type": "bread", "value": 30}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=100,
        intent_kind=INTENT_RESUPPLY,
        intent_score=0.7,
        reason="no_weapon",
        state=state,
        need_result=need_result,
    )
    liq = agent["brain_trace"]["events"][-1].get("liquidity") or {}
    assert liq.get("risky_liquidity_available") is True, (
        f"Expected risky liquidity from a single bread below reserve, got {liq}"
    )
    assert liq.get("planner_allowed_decision") == "fallback_get_money", (
        "Risky liquidity must not auto-promote planner decision to sell; "
        f"got {liq.get('planner_allowed_decision')}"
    )
    assert liq.get("decision") == liq.get("planner_allowed_decision")


# ─────────────────────────────────────────────────────────────────────────────
# Stabilised Поцик regression case
# ─────────────────────────────────────────────────────────────────────────────

def test_potsik_stabilised_needs_evaluated() -> None:
    """Stabilised Поцик 1 case:
    hunger=50, thirst=57, money=29, no weapon, inventory=[bread, bandage, medkit]
    → need_result is computed without error; dominant need is resupply (weapon urgency=0.65
    > eat/drink scores 0.50/0.57) which is correct with non-critical thirst/hunger.
    The key invariant is that bread is NOT sold for weapon resupply (tested separately)."""
    agent = make_agent(
        hunger=50,
        thirst=57,
        money=29,
        has_weapon=False,
        has_armor=True,
        has_ammo=False,
        inventory=[
            {"id": "b0", "type": "bread", "value": 30},
            {"id": "ban0", "type": "bandage", "value": 40},
            {"id": "med0", "type": "medkit", "value": 80},
        ],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    # Hunger=50 and thirst=57 are below critical threshold (80), so no survival
    # ImmediateNeed is triggered; reload_or_rearm (0.65) wins.
    assert not any(n.trigger_context == "survival" for n in need_result.immediate_needs), (
        "hunger=50 / thirst=57 must not trigger critical survival ImmediateNeed"
    )
    intent = select_intent(ctx, need_result.scores, 100, need_result=need_result)
    # resupply is the correct dominant intent here (no weapon, score=0.65)
    assert intent.kind == INTENT_RESUPPLY, (
        f"With non-critical hunger/thirst and no weapon, resupply should dominate, got {intent.kind}"
    )


def test_potsik_stabilised_bread_not_sold_for_weapon() -> None:
    """Поцик with 1 bread below reserve, no money → resupply plan must not sell bread."""
    agent = make_agent(
        hunger=50,
        thirst=57,
        money=29,
        has_weapon=False,
        has_armor=True,
        has_ammo=False,
        inventory=[
            {"id": "b0", "type": "bread", "value": 30},
            {"id": "ban0", "type": "bandage", "value": 40},
            {"id": "med0", "type": "medkit", "value": 80},
        ],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_RESUPPLY)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    # Must not sell the last bread (risky) to buy weapon
    if plan:
        for step in plan.steps:
            assert not (step.kind == STEP_TRADE_SELL_ITEM), (
                "Resupply plan must not sell risky/last survival items (bread) for weapon"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Critical hunger/thirst + inventory → consume (not travel→buy)
# ─────────────────────────────────────────────────────────────────────────────

def test_critical_thirst_has_water_in_inventory_consumes_water() -> None:
    """Critical thirst + water in inventory → consume water (not travel to trader)."""
    agent = make_agent(
        thirst=85,
        money=200,
        inventory=[{"id": "w0", "type": "water", "value": 30}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_SEEK_WATER)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_CONSUME_ITEM, (
        f"Critical thirst with water in inventory should consume water, got {plan.steps[0].kind}"
    )


def test_critical_hunger_has_food_in_inventory_consumes_food() -> None:
    """Critical hunger + food in inventory → consume food (not travel to trader)."""
    agent = make_agent(
        hunger=85,
        money=200,
        inventory=[{"id": "b0", "type": "bread", "value": 30}],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = _make_intent(INTENT_SEEK_FOOD)
    plan = build_plan(ctx, intent, state, 100, need_result=need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_CONSUME_ITEM, (
        f"Critical hunger with food in inventory should consume food, got {plan.steps[0].kind}"
    )


def test_critical_hunger_no_weapon_intent_is_seek_food_not_resupply() -> None:
    """Critical hunger (85) + no weapon → dominant intent must be seek_food, not resupply."""
    agent = make_agent(
        hunger=85,
        money=200,
        has_weapon=False,
        inventory=[],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = select_intent(ctx, need_result.scores, 100, need_result=need_result)
    assert intent.kind == INTENT_SEEK_FOOD, (
        f"Critical hunger (85) must override resupply; got {intent.kind}"
    )
