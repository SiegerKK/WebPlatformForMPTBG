from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.balance.items import (
    AMMO_FOR_WEAPON,
    ARMOR_ITEM_TYPES,
    DRINK_ITEM_TYPES,
    FOOD_ITEM_TYPES,
    HEAL_ITEM_TYPES,
    ITEM_TYPES,
    WEAPON_ITEM_TYPES,
)

from .constants import DESIRED_AMMO_COUNT
from .models.agent_context import AgentContext
from .models.item_need import ItemNeed

# Weapon urgency scores: boosted for kill_stalker agents because a hunter
# without a weapon cannot pursue their primary global goal at all.
_WEAPON_URGENCY_NORMAL: float = 0.65
_WEAPON_URGENCY_HUNT: float = 0.80


def evaluate_item_needs(ctx: AgentContext, state: dict[str, Any]) -> list[ItemNeed]:
    agent = ctx.self_state
    equipment = agent.get("equipment", {})
    inventory = agent.get("inventory", [])
    risk_tolerance = float(agent.get("risk_tolerance", 0.5))
    agent_money = int(agent.get("money", 0))
    global_goal: str = agent.get("global_goal", "get_rich")

    desired_food = _desired_supply_count(risk_tolerance, 1, 3)
    desired_drink = _desired_supply_count(risk_tolerance, 1, 3)
    desired_medicine = _desired_supply_count(risk_tolerance, 2, 4)

    food_count = sum(1 for i in inventory if i.get("type") in FOOD_ITEM_TYPES)
    drink_count = sum(1 for i in inventory if i.get("type") in DRINK_ITEM_TYPES)
    medicine_count = sum(1 for i in inventory if i.get("type") in HEAL_ITEM_TYPES)

    # Fixed urgency scores for stock needs — not scaled by hunger/thirst/hp.
    # The immediate_needs pipeline already handles critical survival drives;
    # item_needs tracks *missing stock* regardless of current stat levels.
    _food_urgency = 0.55
    _drink_urgency = 0.55
    _medicine_urgency = 0.45

    needs: list[ItemNeed] = [
        _build_stock_need("food", desired_food, food_count, _food_urgency, FOOD_ITEM_TYPES, priority=30,
                          reason="Недостаточный запас еды", agent_money=agent_money),
        _build_stock_need("drink", desired_drink, drink_count, _drink_urgency, DRINK_ITEM_TYPES, priority=20,
                          reason="Недостаточный запас воды", agent_money=agent_money),
        _build_stock_need("medicine", desired_medicine, medicine_count, _medicine_urgency, HEAL_ITEM_TYPES, priority=60,
                          reason="Недостаточный запас медикаментов", agent_money=agent_money),
    ]

    has_weapon = equipment.get("weapon") is not None
    has_armor = equipment.get("armor") is not None

    # Phase-1 gate: non-hunter agents should not travel to a trader to buy
    # equipment while their wealth is below their material_threshold (Phase 1).
    # Only kill_stalker hunters bypass this gate because a weapon is critical
    # for their primary goal.
    material_threshold: int = agent.get("material_threshold", 0)
    _in_phase1 = (
        global_goal != "kill_stalker"
        and material_threshold > 0
        and agent_money < material_threshold
    )

    weapon_min_price = _min_buy_price(WEAPON_ITEM_TYPES)
    weapon_missing = 0 if has_weapon else 1
    # For hunters (kill_stalker goal) a missing weapon is critical — boost urgency
    # so that the resupply drive clearly dominates get_rich or unravel drives.
    # Phase-1 gate: suppress weapon urgency for non-hunter agents in Phase 1
    # so they gather resources instead of seeking a trader.
    weapon_urgency = (
        (_WEAPON_URGENCY_HUNT if global_goal == "kill_stalker" else _WEAPON_URGENCY_NORMAL)
        if weapon_missing else 0.0
    )
    weapon_reason = ("Нет оружия (критично для охоты)" if global_goal == "kill_stalker"
                     else "Нет оружия") if weapon_missing else ""
    needs.append(
        ItemNeed(
            key="weapon",
            desired_count=1,
            current_count=0 if not has_weapon else 1,
            missing_count=weapon_missing,
            urgency=weapon_urgency,
            compatible_item_types=WEAPON_ITEM_TYPES,
            priority=40,
            reason=weapon_reason,
            expected_min_price=weapon_min_price,
            affordability_hint=_affordability_hint(agent_money, weapon_min_price) if weapon_missing else None,
        )
    )

    armor_min_price = _min_buy_price(ARMOR_ITEM_TYPES)
    armor_missing = 0 if has_armor else 1
    _armor_urgency = (0.70 if armor_missing else 0.0)
    needs.append(
        ItemNeed(
            key="armor",
            desired_count=1,
            current_count=0 if not has_armor else 1,
            missing_count=armor_missing,
            urgency=_armor_urgency,
            compatible_item_types=ARMOR_ITEM_TYPES,
            priority=35,
            reason="Нет брони" if armor_missing else "",
            expected_min_price=armor_min_price,
            affordability_hint=_affordability_hint(agent_money, armor_min_price) if armor_missing else None,
        )
    )

    ammo_types: frozenset[str] = frozenset()
    ammo_count = 0
    ammo_urgency = 0.0
    if has_weapon:
        weapon = equipment.get("weapon")
        weapon_type = weapon.get("type") if isinstance(weapon, dict) else None
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        if required_ammo:
            ammo_types = frozenset([required_ammo])
            ammo_count = sum(1 for i in inventory if i.get("type") == required_ammo)
            missing = max(0, DESIRED_AMMO_COUNT - ammo_count)
            ammo_urgency = 0.60 * (missing / max(1, DESIRED_AMMO_COUNT))
    ammo_min_price = _min_buy_price(ammo_types) if ammo_types else None
    needs.append(
        ItemNeed(
            key="ammo",
            desired_count=DESIRED_AMMO_COUNT,
            current_count=ammo_count,
            missing_count=max(0, DESIRED_AMMO_COUNT - ammo_count) if ammo_types else 0,
            urgency=ammo_urgency,
            compatible_item_types=ammo_types,
            priority=50,
            reason="Недостаточно патронов" if ammo_urgency > 0 else "",
            expected_min_price=ammo_min_price,
            affordability_hint=_affordability_hint(agent_money, ammo_min_price) if ammo_urgency > 0 else None,
        )
    )

    needs.append(
        ItemNeed(
            key="upgrade",
            desired_count=0,
            current_count=0,
            missing_count=0,
            urgency=0.0,
            priority=200,
            reason="Upgrade debug-only в PR2",
        )
    )

    return needs


def choose_dominant_item_need(item_needs: list[ItemNeed]) -> ItemNeed | None:
    candidates = [n for n in item_needs if n.urgency > 0 and n.key != "upgrade"]
    if not candidates:
        return None
    candidates.sort(key=lambda n: (-n.urgency, n.priority, n.key))
    return candidates[0]


def _desired_supply_count(risk_tolerance: float, min_count: int, max_count: int) -> int:
    return min_count + round((1.0 - risk_tolerance) * (max_count - min_count))


def _build_stock_need(
    key: str,
    desired: int,
    current: int,
    urgency_when_missing: float,
    item_types: frozenset[str],
    *,
    priority: int,
    reason: str,
    agent_money: int = 0,
) -> ItemNeed:
    missing = max(0, desired - current)
    min_price = _min_buy_price(item_types)
    return ItemNeed(
        key=key,
        desired_count=desired,
        current_count=current,
        missing_count=missing,
        urgency=urgency_when_missing if missing > 0 else 0.0,
        compatible_item_types=item_types,
        priority=priority,
        reason=reason if missing > 0 else "",
        expected_min_price=min_price,
        affordability_hint=_affordability_hint(agent_money, min_price) if missing > 0 else None,
    )


def _min_buy_price(item_types: frozenset[str]) -> int | None:
    if not item_types:
        return None
    prices = [int(ITEM_TYPES[t].get("value", 0) * 1.5) for t in item_types if t in ITEM_TYPES]
    return min(prices) if prices else None


def _affordability_hint(agent_money: int, expected_min_price: int | None) -> str:
    """Return 'affordable', 'unaffordable', or 'unknown' based on agent money vs min price."""
    if expected_min_price is None:
        return "unknown"
    return "affordable" if agent_money >= expected_min_price else "unaffordable"
