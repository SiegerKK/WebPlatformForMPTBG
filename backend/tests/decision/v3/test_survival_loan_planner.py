from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.models.plan import (
    STEP_CONSUME_ITEM,
    STEP_REQUEST_LOAN,
    STEP_REPAY_DEBT,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import _plan_get_rich, _plan_heal_or_flee, _plan_repay_debt, _plan_resupply, _plan_seek_consumable
from app.games.zone_stalkers.economy.debts import advance_survival_credit
from tests.decision.conftest import make_agent, make_state_with_trader


def _prepare_state(agent: dict) -> dict:
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["id"] = "trader_1"
    state["traders"]["trader_1"]["money"] = 0
    state["traders"]["trader_1"]["accounts_receivable"] = 0
    return state


def test_poor_thirsty_agent_at_trader_gets_loan_plan_not_sell_plan() -> None:
    agent = make_agent(
        money=20,
        thirst=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_water", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]
    assert STEP_TRADE_SELL_ITEM not in kinds


def test_poor_hungry_agent_at_trader_gets_loan_plan_not_sell_plan() -> None:
    agent = make_agent(
        money=10,
        hunger=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_food", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]
    assert STEP_TRADE_SELL_ITEM not in kinds


def test_poor_injured_agent_at_trader_gets_loan_buy_heal_consume_plan() -> None:
    agent = make_agent(
        hp=40,
        money=5,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_heal_or_flee(ctx, Intent(kind="heal_self", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]


def test_agent_with_safe_sellable_item_sells_before_taking_loan() -> None:
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[{"id": "artifact_1", "type": "soul", "name": "Soul", "value": 2000}],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_water", score=1.0), state, 100, need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM


def test_generic_resupply_for_survival_drink_can_use_loan() -> None:
    """resupply with a thirsty penniless agent at a trader resolves via survival drink loan.

    Since PR debt/credit rules: survival credits are allowed for food/drink/medical.
    _plan_seek_consumable with Intent(kind='resupply') for such an agent MUST produce
    a loan plan (request_loan -> trade_buy_item -> consume_item).
    """
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(
        ctx,
        Intent(kind="resupply", score=1.0),
        state,
        100,
        need_result,
    )
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]
    assert plan.steps[0].payload["item_category"] in {"drink", "food", "medical"}


def test_no_survival_loan_for_ammo_resupply() -> None:
    """_plan_resupply for an agent that needs ammo must NOT add STEP_REQUEST_LOAN.

    Agents with a weapon but no ammo use _plan_resupply which only produces
    a direct STEP_TRADE_BUY_ITEM (or travel) plan - no survival credit.
    """
    # Default inventory includes 2 food + 2 drink, so food/drink needs are satisfied.
    # has_armor=True so armor is covered. has_ammo=False triggers ammo resupply.
    agent = make_agent(
        money=0,
        has_weapon=True,
        has_armor=True,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_resupply(
        ctx,
        Intent(kind="resupply", score=1.0),
        state,
        100,
        None,
    )
    assert plan is None or all(step.kind != STEP_REQUEST_LOAN for step in plan.steps)


def test_no_survival_loan_for_weapon_resupply() -> None:
    """_plan_resupply for an agent that needs a weapon must NOT add STEP_REQUEST_LOAN."""
    # Default inventory: food/drink covered. has_armor=True covers armor.
    # has_weapon=False triggers weapon resupply path.
    agent = make_agent(
        money=0,
        has_weapon=False,
        has_armor=True,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_resupply(
        ctx,
        Intent(kind="resupply", score=1.0),
        state,
        100,
        None,
    )
    assert plan is None or all(step.kind != STEP_REQUEST_LOAN for step in plan.steps)


def test_no_survival_loan_for_armor_resupply() -> None:
    """_plan_resupply for an agent that needs armor must NOT add STEP_REQUEST_LOAN."""
    # Default inventory: food/drink covered. has_armor=False triggers armor path.
    agent = make_agent(
        money=0,
        has_weapon=True,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_resupply(
        ctx,
        Intent(kind="resupply", score=1.0),
        state,
        100,
        None,
    )
    assert plan is None or all(step.kind != STEP_REQUEST_LOAN for step in plan.steps)


def test_no_survival_loan_for_get_rich() -> None:
    """_plan_get_rich must never include STEP_REQUEST_LOAN."""
    agent = make_agent(money=0, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_get_rich(
        ctx,
        Intent(kind="get_rich", score=0.5),
        state,
        100,
        None,
    )
    assert plan is None or all(step.kind != STEP_REQUEST_LOAN for step in plan.steps)




def test_repay_debt_plan_created_for_colocated_creditor() -> None:
    agent = make_agent(money=500, thirst=20, hunger=20, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _prepare_state(agent)
    advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=400,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    plan = _plan_repay_debt(build_agent_context("bot1", agent, state), Intent(kind="repay_debt", score=1.0), state, 100, evaluate_need_result(build_agent_context("bot1", agent, state), state))
    assert plan is not None
    assert plan.steps[-1].kind == STEP_REPAY_DEBT


def test_repay_debt_plan_travels_to_creditor_when_not_colocated() -> None:
    agent = make_agent(money=500, thirst=20, hunger=20, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _prepare_state(agent)
    state["traders"]["trader_1"]["location_id"] = "loc_b"
    state.setdefault("locations", {})["loc_b"] = {"id": "loc_b", "name": "B", "terrain_type": "buildings", "connections": [], "items": [], "agents": ["trader_1"]}
    advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=300,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_repay_debt(ctx, Intent(kind="repay_debt", score=1.0), state, 100, evaluate_need_result(ctx, state))
    assert plan is not None
    assert len(plan.steps) >= 2
    assert plan.steps[0].kind == "travel_to_location"
    assert plan.steps[-1].kind == STEP_REPAY_DEBT
