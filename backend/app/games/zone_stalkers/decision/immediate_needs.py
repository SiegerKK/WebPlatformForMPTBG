from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.games.zone_stalkers.balance.items import DRINK_ITEM_TYPES, FOOD_ITEM_TYPES, HEAL_ITEM_TYPES, ITEM_TYPES
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    SLEEP_SAFE_HUNGER_THRESHOLD,
    SLEEP_SAFE_THIRST_THRESHOLD,
)

from .models.agent_context import AgentContext
from .models.immediate_need import ImmediateNeed


def evaluate_immediate_needs(ctx: AgentContext, state: dict[str, Any]) -> list[ImmediateNeed]:
    agent = ctx.self_state
    hunger = int(agent.get("hunger", 0))
    thirst = int(agent.get("thirst", 0))
    hp = int(agent.get("hp", 100))

    needs: list[ImmediateNeed] = []

    best_drink = _find_best_inventory_item(agent, DRINK_ITEM_TYPES)
    if thirst >= CRITICAL_THIRST_THRESHOLD:
        needs.append(
            ImmediateNeed(
                key="drink_now",
                urgency=min(1.0, thirst / 100.0),
                current_value=thirst,
                threshold=CRITICAL_THIRST_THRESHOLD,
                trigger_context="survival",
                blocks_intents=frozenset(["rest", "resupply", "get_rich"]),
                available_inventory_item_types=frozenset(i.get("type", "") for i in agent.get("inventory", []) if i.get("type") in DRINK_ITEM_TYPES),
                selected_item_id=best_drink.get("id") if best_drink else None,
                selected_item_type=best_drink.get("type") if best_drink else None,
                reason=f"Жажда {thirst}% критическая",
            )
        )
    elif thirst >= SLEEP_SAFE_THIRST_THRESHOLD:
        needs.append(
            ImmediateNeed(
                key="drink_now",
                urgency=_rest_preparation_urgency(thirst, SLEEP_SAFE_THIRST_THRESHOLD),
                current_value=thirst,
                threshold=SLEEP_SAFE_THIRST_THRESHOLD,
                trigger_context="rest_preparation",
                available_inventory_item_types=frozenset(i.get("type", "") for i in agent.get("inventory", []) if i.get("type") in DRINK_ITEM_TYPES),
                selected_item_id=best_drink.get("id") if best_drink else None,
                selected_item_type=best_drink.get("type") if best_drink else None,
                reason=f"Жажда {thirst}% мешает безопасному сну",
            )
        )

    best_food = _find_best_inventory_item(agent, FOOD_ITEM_TYPES)
    if hunger >= CRITICAL_HUNGER_THRESHOLD:
        needs.append(
            ImmediateNeed(
                key="eat_now",
                urgency=min(1.0, hunger / 100.0),
                current_value=hunger,
                threshold=CRITICAL_HUNGER_THRESHOLD,
                trigger_context="survival",
                blocks_intents=frozenset(["rest", "resupply", "get_rich"]),
                available_inventory_item_types=frozenset(i.get("type", "") for i in agent.get("inventory", []) if i.get("type") in FOOD_ITEM_TYPES),
                selected_item_id=best_food.get("id") if best_food else None,
                selected_item_type=best_food.get("type") if best_food else None,
                reason=f"Голод {hunger}% критический",
            )
        )
    elif hunger >= SLEEP_SAFE_HUNGER_THRESHOLD:
        needs.append(
            ImmediateNeed(
                key="eat_now",
                urgency=_rest_preparation_urgency(hunger, SLEEP_SAFE_HUNGER_THRESHOLD),
                current_value=hunger,
                threshold=SLEEP_SAFE_HUNGER_THRESHOLD,
                trigger_context="rest_preparation",
                available_inventory_item_types=frozenset(i.get("type", "") for i in agent.get("inventory", []) if i.get("type") in FOOD_ITEM_TYPES),
                selected_item_id=best_food.get("id") if best_food else None,
                selected_item_type=best_food.get("type") if best_food else None,
                reason=f"Голод {hunger}% мешает безопасному сну",
            )
        )

    # Matches existing heal_self slope from needs.py: hp<=50 creates pressure.
    heal_urgency = max(0.0, min(1.0, (50 - hp) / 30.0)) if hp > 20 else 1.0
    if hp <= 50:
        best_heal = _find_best_inventory_item(
            agent,
            HEAL_ITEM_TYPES,
            key=lambda item: _heal_value(item),
            reverse=True,
        )
        needs.append(
            ImmediateNeed(
                key="heal_now",
                urgency=heal_urgency,
                current_value=hp,
                threshold=50,
                trigger_context="healing",
                blocks_intents=frozenset(["rest", "resupply", "get_rich"]),
                available_inventory_item_types=frozenset(i.get("type", "") for i in agent.get("inventory", []) if i.get("type") in HEAL_ITEM_TYPES),
                selected_item_id=best_heal.get("id") if best_heal else None,
                selected_item_type=best_heal.get("type") if best_heal else None,
                reason=f"HP {hp} требует лечения",
            )
        )

    return needs


def _rest_preparation_urgency(current: int, threshold: int) -> float:
    # 70..100 -> 0.70..0.79 (per PR2 contract guidance).
    delta = max(0, current - threshold)
    return min(0.79, 0.70 + delta * 0.003)


def _heal_value(item: dict[str, Any]) -> int:
    item_type = item.get("type", "")
    info = ITEM_TYPES.get(item_type, {})
    return int(info.get("effects", {}).get("hp", 0))


def _find_best_inventory_item(
    agent: dict[str, Any],
    item_types: frozenset[str],
    *,
    key: Callable[[dict[str, Any]], Any] | None = None,
    reverse: bool = False,
) -> dict[str, Any] | None:
    inventory = [i for i in agent.get("inventory", []) if i.get("type") in item_types]
    if not inventory:
        return None
    if key is None:
        # Survival default: pick cheapest viable item.
        return sorted(
            inventory,
            key=lambda item: (
                int(item.get("value", ITEM_TYPES.get(item.get("type", ""), {}).get("value", 0))),
                item.get("type", ""),
                item.get("id", ""),
            ),
        )[0]
    return sorted(inventory, key=key, reverse=reverse)[0]
