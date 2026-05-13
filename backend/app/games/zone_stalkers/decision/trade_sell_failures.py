from __future__ import annotations

from typing import Any

# no_sellable_items is included so that an agent with an empty sellable
# inventory applies a cooldown rather than re-queueing the step every tick.
BLOCKING_TRADE_SELL_FAILURE_REASONS: frozenset[str] = frozenset(
    {"no_trader_at_location", "no_items_sold", "trader_no_money", "no_sellable_items"}
)


def has_recent_trade_sell_failure_for_agent(
    agent: dict[str, Any] | None,
    *,
    trader_id: str | None,
    location_id: str | None,
    item_types: set[str],
    world_turn: int,
) -> bool:
    """Return True when an active trade-sell cooldown matches trader/location/items."""
    if not isinstance(agent, dict):
        return False
    memory_v3 = agent.get("memory_v3") or {}
    records = memory_v3.get("records") or {}

    target_trader_id = str(trader_id or "")
    target_location_id = str(location_id or "")
    target_items = {str(item) for item in item_types if item}

    for record in records.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("kind") or "") != "trade_sell_failed":
            continue

        details = record.get("details")
        if not isinstance(details, dict):
            details = {}
        reason = str(details.get("reason") or "").strip()
        if reason not in BLOCKING_TRADE_SELL_FAILURE_REASONS:
            continue
        cooldown_until_turn = int(details.get("cooldown_until_turn") or 0)
        if cooldown_until_turn <= world_turn:
            continue

        rec_trader_id = str(details.get("trader_id") or "")
        rec_location_id = str(details.get("location_id") or record.get("location_id") or "")
        same_trader = bool(target_trader_id) and bool(rec_trader_id) and rec_trader_id == target_trader_id
        same_location = bool(target_location_id) and bool(rec_location_id) and rec_location_id == target_location_id

        # no_sellable_items is inventory-scoped; block regardless of trader/location.
        if reason == "no_sellable_items":
            from app.games.zone_stalkers.decision.executors import has_sellable_inventory  # noqa: PLC0415

            if has_sellable_inventory(agent, item_category="any_sellable"):
                continue
            return True

        if not (same_trader or same_location):
            continue

        rec_items_raw = details.get("item_types")
        if not isinstance(rec_items_raw, list):
            rec_items_raw = record.get("item_types") or []
        rec_items = {str(item) for item in rec_items_raw if item}

        if target_items:
            if rec_items:
                if target_items.isdisjoint(rec_items):
                    continue
            else:
                # no_trader_at_location is location/trader scoped and may be emitted
                # without explicit item_types, so keep the cooldown blocking locally.
                if reason != "no_trader_at_location":
                    continue

        return True
    return False
