"""perception — helpers for agent perception state.

``is_perception_suppressed`` determines whether an agent is currently in
a state (sleeping, unconscious, downed) that prevents direct observation
of co-located agents or targets.
"""
from __future__ import annotations

from typing import Any


def is_perception_suppressed(agent: dict[str, Any]) -> bool:
    """Return True if the agent cannot directly perceive co-located entities.

    An agent's direct perception is suppressed when it is:
    - Dead or has left the zone.
    - Currently executing a sleep action (``scheduled_action.type == "sleep"``
      or ``scheduled_action.kind/action_type == "sleep_for_hours"``).
    - In the middle of an active plan step with kind ``sleep_for_hours``.
    """
    if not agent.get("is_alive", True):
        return True
    if agent.get("has_left_zone"):
        return True

    # Check scheduled_action
    scheduled = agent.get("scheduled_action")
    if isinstance(scheduled, dict):
        action_type = str(
            scheduled.get("type")
            or scheduled.get("kind")
            or scheduled.get("action_type")
            or ""
        )
        if action_type in {"sleep", "sleep_for_hours"}:
            return True

    # Check active_plan_v3 current step kind
    active_plan_raw = agent.get("active_plan_v3")
    if isinstance(active_plan_raw, dict):
        steps = active_plan_raw.get("steps")
        current_step_index = int(active_plan_raw.get("current_step_index", 0) or 0)
        if isinstance(steps, list) and current_step_index < len(steps):
            current_step = steps[current_step_index]
            if isinstance(current_step, dict):
                step_kind = str(current_step.get("kind") or "")
                if step_kind == "sleep_for_hours":
                    return True

    return False
