from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.immediate_needs import evaluate_immediate_needs
from tests.decision.conftest import make_agent, make_minimal_state


def _eval(agent: dict) -> list:
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    return evaluate_immediate_needs(ctx, state)


def test_thirst_critical_selects_drink_now_with_inventory_item() -> None:
    agent = make_agent(thirst=90, inventory=[{"id": "w1", "type": "water", "value": 30}])
    needs = _eval(agent)
    drink = next(n for n in needs if n.key == "drink_now" and n.trigger_context == "survival")
    assert drink.selected_item_type == "water"
    assert drink.urgency >= 0.9


def test_hunger_critical_prefers_cheapest_food_item() -> None:
    agent = make_agent(
        hunger=86,
        inventory=[
            {"id": "g", "type": "glucose", "value": 120},
            {"id": "b", "type": "bread", "value": 20},
        ],
    )
    needs = _eval(agent)
    eat = next(n for n in needs if n.key == "eat_now" and n.trigger_context == "survival")
    assert eat.selected_item_type == "bread"


def test_high_thirst_but_not_critical_creates_rest_preparation_need() -> None:
    agent = make_agent(thirst=72)
    needs = _eval(agent)
    drink = next(n for n in needs if n.key == "drink_now")
    assert drink.trigger_context == "rest_preparation"
    assert 0.70 <= drink.urgency <= 0.79
