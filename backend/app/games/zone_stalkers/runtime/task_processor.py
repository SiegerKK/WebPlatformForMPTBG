"""
Task processor for Zone Stalkers event-driven actions (CPU PR3).

Dispatches due scheduled tasks to the appropriate handlers.
"""
from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.runtime.scheduler import pop_due_tasks, schedule_task
from app.games.zone_stalkers.needs.lazy_needs import (
    get_need,
    _SOFT_THRESHOLD,
    _CRITICAL_SLEEPINESS_THRESHOLD,
)
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER,
    HP_DAMAGE_PER_HOUR_CRITICAL_THIRST,
    SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL,
    HUNGER_INCREASE_PER_SLEEP_INTERVAL,
    THIRST_INCREASE_PER_SLEEP_INTERVAL,
)

# Maps action types to semantic task kinds.
ACTION_TYPE_TO_TASK_KIND: dict[str, str] = {
    "travel": "travel_arrival",
    "sleep": "sleep_complete",
    "explore_anomaly_location": "explore_complete",
    "wait": "wait_complete",
    "trade": "trade_complete",
}

# All task kinds that represent action completion (including backward-compat kind).
_ACTION_COMPLETION_KINDS: frozenset[str] = frozenset(ACTION_TYPE_TO_TASK_KIND.values()) | {"scheduled_action_complete"}

# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_agent(state: dict[str, Any], runtime: Any, agent_id: str) -> dict[str, Any] | None:
    """Try runtime.agent(agent_id) first, then fall back to state."""
    if runtime is not None:
        try:
            return runtime.agent(agent_id)
        except Exception:
            pass
    return state.get("agents", {}).get(agent_id)


def _set_agent_field(
    state: dict[str, Any],
    runtime: Any,
    agent_id: str,
    key: str,
    value: Any,
) -> None:
    """Set an agent field via runtime or direct state mutation."""
    if runtime is not None:
        try:
            runtime.set_agent_field(agent_id, key, value)
            return
        except Exception:
            pass
    agent = state.get("agents", {}).get(agent_id)
    if agent is not None:
        agent[key] = value


def _mark_dirty(runtime: Any, agent_id: str) -> None:
    """Mark agent as dirty via runtime if available."""
    if runtime is not None:
        try:
            runtime.mark_agent_dirty(agent_id)
        except Exception:
            pass


# ── Public helpers ────────────────────────────────────────────────────────────

def interrupt_action(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    runtime: Any,
    world_turn: int,
    reason: str = "interrupted",
) -> bool:
    """
    Interrupt an agent's scheduled_action if interruptible.

    Returns True if interrupted, False otherwise.
    Bumps revision before clearing so stale completion tasks are ignored.
    """
    sched = agent.get("scheduled_action")
    if not isinstance(sched, dict):
        return False
    if not sched.get("interruptible", True):
        return False
    # Bump revision before clearing so any outstanding completion tasks become stale.
    new_revision = int(sched.get("revision", 0)) + 1
    sched["revision"] = new_revision
    _set_agent_field(state, runtime, agent_id, "scheduled_action", None)
    _mark_dirty(runtime, agent_id)
    return True


# ── Task handlers ─────────────────────────────────────────────────────────────

def _handle_need_threshold_crossed(
    task: dict[str, Any],
    state: dict[str, Any],
    runtime: Any,
    world_turn: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    agent_id = str(task.get("agent_id") or "")
    if not agent_id:
        return events

    agent = _get_agent(state, runtime, agent_id)
    if agent is None:
        return events
    if not agent.get("is_alive", True):
        return events
    if agent.get("has_left_zone"):
        return events

    # Stale revision check
    needs_state = agent.get("needs_state")
    if not isinstance(needs_state, dict):
        return events
    current_revision = int(needs_state.get("revision", 0))
    task_revision = int(task.get("needs_revision", -1))
    if task_revision != current_revision:
        return events

    need_key = str(task.get("need") or "")
    threshold = str(task.get("threshold") or "")
    if need_key == "hunger":
        threshold_value = CRITICAL_HUNGER_THRESHOLD if threshold == "critical" else _SOFT_THRESHOLD
    elif need_key == "thirst":
        threshold_value = CRITICAL_THIRST_THRESHOLD if threshold == "critical" else _SOFT_THRESHOLD
    elif need_key == "sleepiness":
        threshold_value = _CRITICAL_SLEEPINESS_THRESHOLD if threshold == "critical" else _SOFT_THRESHOLD
    else:
        threshold_value = _SOFT_THRESHOLD

    current_need = get_need(agent, need_key, world_turn)
    if current_need < threshold_value:
        # Threshold no longer crossed
        return events

    events.append({
        "event_type": "need_threshold_crossed",
        "payload": {
            "agent_id": agent_id,
            "need": need_key,
            "threshold": threshold,
            "value": current_need,
        },
    })

    # Critical hunger/thirst: interrupt travel action and schedule damage task
    if threshold == "critical" and need_key in ("hunger", "thirst"):
        # Re-fetch agent for latest scheduled_action
        agent = _get_agent(state, runtime, agent_id) or agent
        interrupt_action(agent_id, agent, state, runtime, world_turn, reason=f"critical_{need_key}")
        # Schedule ongoing damage every 60 turns
        schedule_task(
            state,
            runtime,
            int(world_turn) + 60,
            {
                "kind": "need_damage",
                "agent_id": agent_id,
                "need": need_key,
                "needs_revision": current_revision,
            },
        )

    return events


def _handle_need_damage(
    task: dict[str, Any],
    state: dict[str, Any],
    runtime: Any,
    world_turn: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    agent_id = str(task.get("agent_id") or "")
    if not agent_id:
        return events

    agent = _get_agent(state, runtime, agent_id)
    if agent is None:
        return events
    if not agent.get("is_alive", True):
        return events

    # Stale revision check
    needs_state = agent.get("needs_state")
    if not isinstance(needs_state, dict):
        return events
    current_revision = int(needs_state.get("revision", 0))
    task_revision = int(task.get("needs_revision", -1))
    if task_revision != current_revision:
        return events

    need_key = str(task.get("need") or "")
    # Only hunger/thirst cause HP damage
    if need_key not in ("hunger", "thirst"):
        return events

    current_need = get_need(agent, need_key, world_turn)
    threshold_value = CRITICAL_HUNGER_THRESHOLD if need_key == "hunger" else CRITICAL_THIRST_THRESHOLD
    if current_need < threshold_value:
        return events

    # Apply damage
    if need_key == "hunger":
        damage = HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER
    else:
        damage = HP_DAMAGE_PER_HOUR_CRITICAL_THIRST

    current_hp = int(agent.get("hp", 0))
    new_hp = max(0, current_hp - damage)
    _set_agent_field(state, runtime, agent_id, "hp", new_hp)
    _mark_dirty(runtime, agent_id)

    events.append({
        "event_type": "critical_need_damage",
        "payload": {
            "agent_id": agent_id,
            "need": need_key,
            "damage": damage,
            "hp": new_hp,
        },
    })

    # Re-fetch agent for updated HP
    agent = _get_agent(state, runtime, agent_id) or agent
    if agent.get("is_alive", True) and int(agent.get("hp", 0)) > 0:
        schedule_task(
            state,
            runtime,
            int(world_turn) + 60,
            {
                "kind": "need_damage",
                "agent_id": agent_id,
                "need": need_key,
                "needs_revision": current_revision,
            },
        )

    return events


def _handle_sleep_tick(
    task: dict[str, Any],
    state: dict[str, Any],
    runtime: Any,
    world_turn: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    agent_id = str(task.get("agent_id") or "")
    if not agent_id:
        return events

    agent = _get_agent(state, runtime, agent_id)
    if agent is None:
        return events
    if not agent.get("is_alive", True):
        return events

    sched = agent.get("scheduled_action")
    if not isinstance(sched, dict):
        return events
    if sched.get("type") != "sleep":
        return events

    task_revision = int(task.get("scheduled_action_revision", -1))
    sched_revision = int(sched.get("revision", 0))
    if task_revision != sched_revision:
        return events

    # Apply sleep interval effects
    needs_state = agent.get("needs_state")
    if isinstance(needs_state, dict):
        sleepiness = get_need(agent, "sleepiness", world_turn)
        hunger = get_need(agent, "hunger", world_turn)
        thirst = get_need(agent, "thirst", world_turn)

        new_sleepiness = max(0.0, sleepiness - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL)
        new_hunger = min(100.0, hunger + HUNGER_INCREASE_PER_SLEEP_INTERVAL)
        new_thirst = min(100.0, thirst + THIRST_INCREASE_PER_SLEEP_INTERVAL)

        from app.games.zone_stalkers.needs.lazy_needs import set_need
        set_need(agent, "sleepiness", new_sleepiness, world_turn)
        set_need(agent, "hunger", new_hunger, world_turn)
        set_need(agent, "thirst", new_thirst, world_turn)
        _set_agent_field(state, runtime, agent_id, "needs_state", agent.get("needs_state"))
    else:
        sleepiness = float(agent.get("sleepiness", 0))
        hunger = float(agent.get("hunger", 0))
        thirst = float(agent.get("thirst", 0))
        new_sleepiness = max(0.0, sleepiness - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL)
        new_hunger = min(100.0, hunger + HUNGER_INCREASE_PER_SLEEP_INTERVAL)
        new_thirst = min(100.0, thirst + THIRST_INCREASE_PER_SLEEP_INTERVAL)
        _set_agent_field(state, runtime, agent_id, "sleepiness", new_sleepiness)
        _set_agent_field(state, runtime, agent_id, "hunger", new_hunger)
        _set_agent_field(state, runtime, agent_id, "thirst", new_thirst)

    _mark_dirty(runtime, agent_id)

    # Update sleep intervals applied counter
    new_sched = dict(sched)
    new_sched["sleep_intervals_applied"] = int(sched.get("sleep_intervals_applied", 0)) + 1
    _set_agent_field(state, runtime, agent_id, "scheduled_action", new_sched)

    events.append({
        "event_type": "sleep_interval_applied",
        "payload": {
            "agent_id": agent_id,
            "sleepiness": new_sleepiness,
            "intervals_applied": new_sched["sleep_intervals_applied"],
        },
    })

    # Early wake if sleepiness reaches 0
    if new_sleepiness <= 0:
        _set_agent_field(state, runtime, agent_id, "scheduled_action", None)
        _mark_dirty(runtime, agent_id)
        events.append({
            "event_type": "sleep_completed_early",
            "payload": {"agent_id": agent_id},
        })

    return events


# ── Main dispatcher ───────────────────────────────────────────────────────────

def process_due_tasks(
    state: dict[str, Any],
    runtime: Any,
    world_turn: int,
    profiler: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, set[int]]]:
    """
    Pop and process all tasks due at world_turn.

    Returns:
        (events, due_action_completions)
        due_action_completions: dict[agent_id → set[revision]]
    """
    events: list[dict[str, Any]] = []
    due_action_completions: dict[str, set[int]] = {}

    due_tasks = pop_due_tasks(state, runtime, world_turn)

    for task in due_tasks:
        kind = str(task.get("kind") or "")

        if kind in _ACTION_COMPLETION_KINDS:
            task_agent_id = str(task.get("agent_id") or "")
            if task_agent_id:
                task_revision = int(task.get("scheduled_action_revision", -1))
                due_action_completions.setdefault(task_agent_id, set()).add(task_revision)

        elif kind == "need_threshold_crossed":
            evs = _handle_need_threshold_crossed(task, state, runtime, world_turn)
            events.extend(evs)

        elif kind == "need_damage":
            evs = _handle_need_damage(task, state, runtime, world_turn)
            events.extend(evs)

        elif kind == "sleep_tick":
            evs = _handle_sleep_tick(task, state, runtime, world_turn)
            events.extend(evs)

        else:
            # Unknown task kind
            if profiler is not None:
                try:
                    profiler.inc("unknown_scheduled_task_kind")
                except Exception:
                    try:
                        profiler.set_counter("unknown_scheduled_task_kind", 1)
                    except Exception:
                        pass
            events.append({
                "event_type": "debug_unknown_task_kind",
                "payload": {"kind": kind, "task": task},
            })

    return events, due_action_completions
