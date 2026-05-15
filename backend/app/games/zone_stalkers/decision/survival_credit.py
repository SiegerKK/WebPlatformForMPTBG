from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .liquidity import evaluate_affordability


@dataclass(frozen=True)
class SurvivalPurchaseQuote:
    category: str
    item_type: str
    item_name: str
    required_price: int
    current_money: int
    principal_needed: int
    compatible_item_types: tuple[str, ...]


def quote_survival_purchase(
    *,
    agent: dict[str, Any],
    category: str,
    compatible_item_types: set[str] | None = None,
) -> SurvivalPurchaseQuote | None:
    afford = evaluate_affordability(
        agent=agent,
        trader={},
        category=category,
        compatible_item_types=compatible_item_types,
    )
    if afford.required_price is None or afford.cheapest_viable_item_type is None:
        return None

    required_price = int(afford.required_price)
    current_money = int(agent.get("money") or 0)
    principal_needed = max(0, required_price - current_money)
    item_type = str(afford.cheapest_viable_item_type)
    item_name = str(afford.cheapest_viable_item_name or item_type)
    compatible = (
        tuple(sorted(str(item_type_name) for item_type_name in compatible_item_types))
        if compatible_item_types
        else (item_type,)
    )
    return SurvivalPurchaseQuote(
        category=str(category),
        item_type=item_type,
        item_name=item_name,
        required_price=required_price,
        current_money=current_money,
        principal_needed=principal_needed,
        compatible_item_types=compatible,
    )
