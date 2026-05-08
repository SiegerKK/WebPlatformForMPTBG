from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.item_needs import choose_dominant_item_need, evaluate_item_needs
from tests.decision.conftest import make_agent, make_minimal_state


def _eval(agent: dict) -> list:
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    return evaluate_item_needs(ctx, state)


def test_no_weapon_creates_weapon_need_with_expected_urgency() -> None:
    agent = make_agent(has_weapon=False, has_armor=True, has_ammo=False)
    needs = _eval(agent)
    weapon = next(n for n in needs if n.key == "weapon")
    assert weapon.urgency == 0.65
    assert weapon.missing_count == 1


def test_armor_present_sets_armor_need_to_zero() -> None:
    agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
    needs = _eval(agent)
    armor = next(n for n in needs if n.key == "armor")
    assert armor.urgency == 0.0


def test_dominant_item_need_uses_score_before_priority() -> None:
    agent = make_agent(has_weapon=False, has_armor=False, has_ammo=False)
    needs = _eval(agent)
    dominant = choose_dominant_item_need(needs)
    assert dominant is not None
    assert dominant.key == "armor"  # 0.70 > 0.65
