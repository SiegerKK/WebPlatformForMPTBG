from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
from app.games.zone_stalkers.balance.items import (
    DRINK_ITEM_TYPES,
    FOOD_ITEM_TYPES,
    HEAL_ITEM_TYPES,
    ITEM_TYPES,
)

from .models.affordability import AffordabilityResult, LiquidityOption
from .models.immediate_need import ImmediateNeed
from .models.item_need import ItemNeed


def find_cheapest_viable_trader_item(
    *,
    trader: dict[str, Any],
    category: str,
    compatible_item_types: set[str] | None,
) -> dict[str, Any] | None:
    del trader  # current economy uses infinite trader stock from item catalogue
    candidate_types = _candidate_types_for_category(category, compatible_item_types)
    candidates = [
        {
            "item_type": t,
            "item_name": ITEM_TYPES[t].get("name", t),
            "base_value": int(ITEM_TYPES[t].get("value", 0)),
            "buy_price": int(ITEM_TYPES[t].get("value", 0) * 1.5),
        }
        for t in candidate_types
        if t in ITEM_TYPES
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: (c["buy_price"], c["item_type"]))[0]


def evaluate_affordability(
    *,
    agent: dict[str, Any],
    trader: dict[str, Any],
    category: str,
    compatible_item_types: set[str] | None = None,
) -> AffordabilityResult:
    cheapest = find_cheapest_viable_trader_item(
        trader=trader,
        category=category,
        compatible_item_types=compatible_item_types,
    )
    money = int(agent.get("money", 0))
    if not cheapest:
        return AffordabilityResult(
            can_buy_now=False,
            required_price=None,
            current_money=money,
            money_missing=0,
            cheapest_viable_item_type=None,
            cheapest_viable_item_name=None,
            reason="no_viable_item",
        )

    required = int(cheapest["buy_price"])
    missing = max(0, required - money)
    return AffordabilityResult(
        can_buy_now=money >= required,
        required_price=required,
        current_money=money,
        money_missing=missing,
        cheapest_viable_item_type=str(cheapest["item_type"]),
        cheapest_viable_item_name=str(cheapest["item_name"]),
        reason="affordable" if money >= required else "insufficient_funds",
    )


def find_liquidity_options(
    *,
    agent: dict[str, Any],
    immediate_needs: list[ImmediateNeed],
    item_needs: list[ItemNeed],
) -> list[LiquidityOption]:
    inventory = agent.get("inventory", [])

    critical_thirst = any(n.key == "drink_now" and n.trigger_context == "survival" for n in immediate_needs)
    critical_hunger = any(n.key == "eat_now" and n.trigger_context == "survival" for n in immediate_needs)
    critical_heal = any(n.key == "heal_now" and n.trigger_context in ("survival", "healing") for n in immediate_needs)

    food_count = sum(1 for i in inventory if i.get("type") in FOOD_ITEM_TYPES)
    drink_count = sum(1 for i in inventory if i.get("type") in DRINK_ITEM_TYPES)
    heal_count = sum(1 for i in inventory if i.get("type") in HEAL_ITEM_TYPES)

    desired_food = _need_desired(item_needs, "food", fallback=1)
    desired_drink = _need_desired(item_needs, "drink", fallback=1)
    desired_med = _need_desired(item_needs, "medicine", fallback=2)

    options: list[LiquidityOption] = []
    artifact_types = frozenset(ARTIFACT_TYPES.keys())

    for item in inventory:
        item_type = item.get("type", "")
        item_name = ITEM_TYPES.get(item_type, {}).get("name", item_type)
        sell_value = _sell_value(item)

        if item_type in artifact_types:
            options.append(LiquidityOption(
                item_id=str(item.get("id", "")),
                item_type=item_type,
                item_name=item_name,
                estimated_sell_value=sell_value,
                safety="safe",
                reason="Артефакт безопасно продать для ликвидности",
            ))
            continue

        if item_type in DRINK_ITEM_TYPES:
            if critical_thirst and drink_count <= 1:
                continue
            safety = "safe" if drink_count > desired_drink else "risky"
            options.append(LiquidityOption(str(item.get("id", "")), item_type, item_name, sell_value, safety,
                                           "Лишняя вода относительно целевого запаса"))
            continue

        if item_type in FOOD_ITEM_TYPES:
            if critical_hunger and food_count <= 1:
                continue
            safety = "safe" if food_count > desired_food else "risky"
            options.append(LiquidityOption(str(item.get("id", "")), item_type, item_name, sell_value, safety,
                                           "Лишняя еда относительно целевого запаса"))
            continue

        if item_type in HEAL_ITEM_TYPES:
            if critical_heal and heal_count <= 1:
                continue
            safety = "safe" if heal_count > desired_med else "emergency_only"
            options.append(LiquidityOption(str(item.get("id", "")), item_type, item_name, sell_value, safety,
                                           "Лишняя медицина или экстренная ликвидность"))
            continue

        base_type = ITEM_TYPES.get(item_type, {}).get("type")
        if base_type in ("detector",):
            options.append(LiquidityOption(str(item.get("id", "")), item_type, item_name, sell_value, "safe",
                                           "Доп. снаряжение можно продать"))
        elif base_type in ("weapon", "armor"):
            options.append(LiquidityOption(str(item.get("id", "")), item_type, item_name, sell_value, "risky",
                                           "Запасное снаряжение можно продать"))

    options.sort(key=lambda o: ({"safe": 0, "risky": 1, "emergency_only": 2, "forbidden": 3}.get(o.safety, 9), -o.estimated_sell_value, o.item_type))
    return options


def _candidate_types_for_category(category: str, compatible_item_types: set[str] | None) -> set[str]:
    if compatible_item_types:
        return set(compatible_item_types)
    mapping: dict[str, frozenset[str]] = {
        "food": FOOD_ITEM_TYPES,
        "drink": DRINK_ITEM_TYPES,
        "medical": HEAL_ITEM_TYPES,
    }
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_ITEM_TYPES

    mapping.update({
        "weapon": WEAPON_ITEM_TYPES,
        "armor": ARMOR_ITEM_TYPES,
        "ammo": AMMO_ITEM_TYPES,
    })
    return set(mapping.get(category, HEAL_ITEM_TYPES))


def _need_desired(item_needs: list[ItemNeed], key: str, fallback: int) -> int:
    match = next((n for n in item_needs if n.key == key), None)
    return int(match.desired_count) if match else fallback


def _sell_value(item: dict[str, Any]) -> int:
    base_value = int(item.get("value", ITEM_TYPES.get(item.get("type", ""), {}).get("value", 0)))
    return max(1, base_value)
