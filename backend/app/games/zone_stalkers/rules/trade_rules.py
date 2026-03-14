"""
Rules for the trade_session context.

Supported commands:
- buy_item(item_id, quantity=1)
- sell_item(item_id, quantity=1)
- end_trade
"""
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult


def validate_trade_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    if state.get("trade_over"):
        return RuleCheckResult(valid=False, error="Trade session is already closed")

    buyer_id = state.get("buyer_id")
    if buyer_id != player_id:
        return RuleCheckResult(valid=False, error="You are not the buyer in this trade session")

    if command_type == "end_trade":
        return RuleCheckResult(valid=True)

    if command_type == "buy_item":
        item_id = payload.get("item_id")
        if not item_id:
            return RuleCheckResult(valid=False, error="item_id is required")
        trader_inv = state.get("trader_inventory", [])
        item = next((i for i in trader_inv if i["id"] == item_id), None)
        if not item:
            return RuleCheckResult(valid=False, error="Item not in trader's inventory")
        if item.get("stock", 1) < 1:
            return RuleCheckResult(valid=False, error="Item out of stock")
        buyer_money = state.get("buyer_money", 0)
        if buyer_money < item.get("value", 0):
            return RuleCheckResult(valid=False, error="Not enough money")
        return RuleCheckResult(valid=True)

    if command_type == "sell_item":
        item_id = payload.get("item_id")
        if not item_id:
            return RuleCheckResult(valid=False, error="item_id is required")
        buyer_inv = state.get("buyer_inventory", [])
        item = next((i for i in buyer_inv if i["id"] == item_id), None)
        if not item:
            return RuleCheckResult(valid=False, error="Item not in your inventory")
        trader_money = state.get("trader_money", 0)
        sell_price = int(item.get("value", 0) * 0.6)  # 60% of base value
        if trader_money < sell_price:
            return RuleCheckResult(valid=False, error="Trader doesn't have enough money")
        return RuleCheckResult(valid=True)

    return RuleCheckResult(valid=False, error=f"Unknown trade command: {command_type}")


def resolve_trade_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import copy
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []

    if command_type == "end_trade":
        state["trade_over"] = True
        events.append({
            "event_type": "trade_ended",
            "payload": {"player_id": player_id},
        })
        return state, events

    if command_type == "buy_item":
        item_id = payload["item_id"]
        trader_inv = state["trader_inventory"]
        item = next((i for i in trader_inv if i["id"] == item_id), None)
        if item:
            price = item.get("value", 0)
            state["buyer_money"] = state.get("buyer_money", 0) - price
            state["trader_money"] = state.get("trader_money", 0) + price
            # Transfer item to buyer
            bought = dict(item)
            bought.pop("stock", None)
            state.setdefault("buyer_inventory", []).append(bought)
            # Reduce stock
            item["stock"] = item.get("stock", 1) - 1
            if item["stock"] <= 0:
                state["trader_inventory"] = [i for i in trader_inv if i["id"] != item_id]
            events.append({
                "event_type": "item_bought",
                "payload": {
                    "player_id": player_id,
                    "item_id": item_id,
                    "item_type": item["type"],
                    "price": price,
                },
            })

    elif command_type == "sell_item":
        item_id = payload["item_id"]
        buyer_inv = state["buyer_inventory"]
        item = next((i for i in buyer_inv if i["id"] == item_id), None)
        if item:
            sell_price = int(item.get("value", 0) * 0.6)
            state["buyer_money"] = state.get("buyer_money", 0) + sell_price
            state["trader_money"] = state.get("trader_money", 0) - sell_price
            state["buyer_inventory"] = [i for i in buyer_inv if i["id"] != item_id]
            # Add to trader's inventory
            sold_item = dict(item)
            sold_item["stock"] = 1
            state.setdefault("trader_inventory", []).append(sold_item)
            events.append({
                "event_type": "item_sold",
                "payload": {
                    "player_id": player_id,
                    "item_id": item_id,
                    "item_type": item["type"],
                    "price": sell_price,
                },
            })

    return state, events
