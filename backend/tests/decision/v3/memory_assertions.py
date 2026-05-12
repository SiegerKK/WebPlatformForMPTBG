from __future__ import annotations

from typing import Any


def v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        r
        for r in ((agent.get("memory_v3") or {}).get("records") or {}).values()
        if isinstance(r, dict)
    ]


def v3_action_records(agent: dict[str, Any], action_kind: str) -> list[dict[str, Any]]:
    return [
        r
        for r in v3_records(agent)
        if r.get("kind") == action_kind or (r.get("details") or {}).get("action_kind") == action_kind
    ]


def has_v3_action(agent: dict[str, Any], action_kind: str) -> bool:
    return bool(v3_action_records(agent, action_kind))


def has_v3_objective(agent: dict[str, Any], objective_key: str) -> bool:
    return any(
        (
            r.get("kind") == "objective_decision"
            or (r.get("details") or {}).get("action_kind") == "objective_decision"
        )
        and (r.get("details") or {}).get("objective_key") == objective_key
        for r in v3_records(agent)
    )
