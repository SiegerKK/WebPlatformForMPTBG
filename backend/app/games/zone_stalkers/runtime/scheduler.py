from __future__ import annotations

from typing import Any


def _turn_key(turn: Any) -> str:
    # Keep JSON-compatible string keys in state["scheduled_tasks"].
    return str(turn)


def schedule_task(state: dict[str, Any], runtime: Any, turn: int, task: dict[str, Any]) -> None:
    tasks = state.setdefault("scheduled_tasks", {})
    bucket = tasks.setdefault(_turn_key(turn), [])
    bucket.append(dict(task))
    if runtime is not None:
        try:
            runtime.mark_state_dirty("scheduled_tasks")
        except Exception:
            pass


def pop_due_tasks(state: dict[str, Any], runtime: Any, world_turn: int) -> list[dict[str, Any]]:
    tasks = state.setdefault("scheduled_tasks", {})
    due = tasks.pop(_turn_key(world_turn), [])
    if due and runtime is not None:
        try:
            runtime.mark_state_dirty("scheduled_tasks")
        except Exception:
            pass
    return [task for task in due if isinstance(task, dict)]


def cleanup_old_tasks(
    state: dict[str, Any],
    runtime: Any,
    current_turn: int,
    max_age: int = 1000,
) -> None:
    tasks = state.setdefault("scheduled_tasks", {})
    if not isinstance(tasks, dict):
        state["scheduled_tasks"] = {}
        return
    min_turn = int(current_turn) - int(max_age)
    to_drop = []
    for key in tasks.keys():
        try:
            if int(key) < min_turn:
                to_drop.append(key)
        except Exception:
            continue
    if not to_drop:
        return
    for key in to_drop:
        tasks.pop(key, None)
    if runtime is not None:
        try:
            runtime.mark_state_dirty("scheduled_tasks")
        except Exception:
            pass
