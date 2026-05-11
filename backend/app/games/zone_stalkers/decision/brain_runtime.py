"""Brain runtime helpers: invalidation, caching and priority helpers."""
from __future__ import annotations

from typing import Any

_PRIORITY_ORDER: tuple[str, ...] = ("low", "normal", "high", "urgent")
_PRIORITY_RANK: dict[str, int] = {name: idx for idx, name in enumerate(_PRIORITY_ORDER)}


def normalize_priority(priority: str | None) -> str:
    if not priority:
        return "normal"
    value = str(priority).strip().lower()
    return value if value in _PRIORITY_RANK else "normal"


def max_priority(left: str | None, right: str | None) -> str:
    l_val = normalize_priority(left)
    r_val = normalize_priority(right)
    return l_val if _PRIORITY_RANK[l_val] >= _PRIORITY_RANK[r_val] else r_val


def promote_priority(priority: str | None) -> str:
    value = normalize_priority(priority)
    idx = _PRIORITY_RANK[value]
    if idx >= len(_PRIORITY_ORDER) - 1:
        return value
    return _PRIORITY_ORDER[idx + 1]


def ensure_brain_runtime(agent: dict[str, Any], world_turn: int) -> dict[str, Any]:
    br = agent.get("brain_runtime")
    if not isinstance(br, dict):
        br = {}
        agent["brain_runtime"] = br

    defaults = {
        "last_decision_turn": None,
        "valid_until_turn": int(world_turn),
        "decision_revision": 0,
        "last_objective_key": None,
        "last_intent_kind": None,
        "last_plan_key": None,
        "invalidated": False,
        "invalidators": [],
        "queued": False,
        "queued_turn": None,
        "queued_priority": None,
        "last_skip_reason": None,
    }
    for key, value in defaults.items():
        if key not in br:
            br[key] = value

    if not isinstance(br.get("invalidators"), list):
        br["invalidators"] = []
    br["queued_priority"] = normalize_priority(br.get("queued_priority")) if br.get("queued_priority") else None
    return br


def invalidate_brain(
    agent: dict[str, Any],
    runtime: Any,
    *,
    reason: str,
    priority: str = "normal",
    world_turn: int | None = None,
) -> None:
    br = ensure_brain_runtime(agent, int(world_turn or 0))
    br["invalidated"] = True
    inv_priority = normalize_priority(priority)
    invalidators = br.setdefault("invalidators", [])
    invalidators.append(
        {
            "reason": str(reason),
            "priority": inv_priority,
            "world_turn": int(world_turn) if world_turn is not None else None,
        }
    )
    if len(invalidators) > 20:
        del invalidators[:-20]
    br["queued_priority"] = max_priority(br.get("queued_priority"), inv_priority)

    try:
        agent_id = agent.get("id")
        if runtime is not None and agent_id and hasattr(runtime, "mark_agent_dirty"):
            runtime.mark_agent_dirty(str(agent_id))
    except Exception:
        pass


def clear_brain_invalidators(agent: dict[str, Any]) -> None:
    br = ensure_brain_runtime(agent, 0)
    br["invalidated"] = False
    br["invalidators"] = []


def highest_invalidator_priority(agent: dict[str, Any]) -> str:
    br = ensure_brain_runtime(agent, 0)
    best = "low"
    for inv in br.get("invalidators", []):
        if not isinstance(inv, dict):
            continue
        best = max_priority(best, inv.get("priority"))
    return best


def latest_invalidator_reason(agent: dict[str, Any]) -> str | None:
    br = ensure_brain_runtime(agent, 0)
    invalidators = br.get("invalidators", [])
    if not invalidators:
        return None
    last = invalidators[-1]
    if not isinstance(last, dict):
        return None
    reason = last.get("reason")
    return str(reason) if reason else None


def should_run_brain(agent: dict[str, Any], world_turn: int) -> tuple[bool, str]:
    br = ensure_brain_runtime(agent, world_turn)

    if not agent.get("is_alive", True):
        return False, "dead"

    if agent.get("has_left_zone"):
        return False, "left_zone"

    if br.get("invalidated"):
        return True, "invalidated"

    if world_turn >= int(br.get("valid_until_turn") or 0):
        return True, "expired"

    if not agent.get("active_plan_v3") and not agent.get("scheduled_action"):
        return True, "no_plan_or_action"

    return False, "cached_until_valid"
