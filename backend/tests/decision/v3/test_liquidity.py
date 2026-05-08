from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.immediate_needs import evaluate_immediate_needs
from app.games.zone_stalkers.decision.item_needs import evaluate_item_needs
from app.games.zone_stalkers.decision.liquidity import evaluate_affordability, find_liquidity_options
from tests.decision.conftest import make_agent, make_minimal_state


def test_affordability_reports_missing_money_for_food() -> None:
    agent = make_agent(money=10)
    result = evaluate_affordability(agent=agent, trader={}, category="food")
    assert result.can_buy_now is False
    assert result.required_price is not None
    assert result.money_missing > 0


def test_liquidity_blocks_selling_last_water_on_critical_thirst() -> None:
    agent = make_agent(thirst=95, inventory=[{"id": "w1", "type": "water", "value": 30}])
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=item_needs)
    assert all(o.item_type != "water" for o in options)
