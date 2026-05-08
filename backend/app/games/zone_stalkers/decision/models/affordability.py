from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AffordabilityResult:
    can_buy_now: bool
    required_price: int | None
    current_money: int
    money_missing: int
    cheapest_viable_item_type: str | None
    cheapest_viable_item_name: str | None
    reason: str


@dataclass(frozen=True)
class LiquidityOption:
    item_id: str
    item_type: str
    item_name: str
    estimated_sell_value: int
    safety: str
    reason: str
