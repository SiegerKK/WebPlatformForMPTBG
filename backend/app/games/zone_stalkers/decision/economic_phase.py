from __future__ import annotations

from typing import Any


def get_economic_phase(agent: dict[str, Any]) -> str:
    """Return current economic phase for autonomous bot planning.

    Uses liquid wealth (money + inventory value) to stay consistent with the
    wealth gate semantics in ``needs.py``.
    """
    money = int(agent.get("money", 0))
    inventory_value = sum(int(i.get("value", 0)) for i in agent.get("inventory", []))
    liquid_wealth = money + inventory_value
    material_threshold = int(agent.get("material_threshold", 0))
    if material_threshold > 0 and liquid_wealth < material_threshold:
        return "material_accumulation"
    return "goal_execution"


def is_phase1(agent: dict[str, Any]) -> bool:
    return get_economic_phase(agent) == "material_accumulation"


def is_item_need_actionable(agent: dict[str, Any], item_need_key: str) -> tuple[bool, str | None]:
    """Return whether item need can create immediate purchase pressure."""
    key = str(item_need_key or "").strip().lower()
    if not is_phase1(agent):
        return True, None

    goal = str(agent.get("global_goal") or "get_rich")
    if key in {"food", "drink", "medicine"}:
        return True, None

    if goal == "kill_stalker":
        if key in {"weapon", "ammo", "medicine"}:
            return True, None
        if key in {"armor", "upgrade"}:
            return False, "phase1_material_gate"
        return True, None

    if key in {"weapon", "armor", "ammo", "upgrade"}:
        return False, "phase1_material_gate"
    return True, None
