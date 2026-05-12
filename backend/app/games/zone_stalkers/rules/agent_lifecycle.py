"""agent_lifecycle.py — Canonical agent death helper for Zone Stalkers.

All death paths (emission, starvation, combat, anomaly, etc.) must go through
kill_agent.  This guarantees consistent state invariants after death:

    is_alive == False
    hp == 0
    scheduled_action is None
    action_queue == []
    active_plan_v3 is None  (cleared via clear_active_plan)
    current_goal == "dead"
    brain_runtime: invalidated=False, queued=False, last_skip_reason="dead"
    brain_v3_context: intent_kind/objective_key/adapter_intent cleared
    brain_trace: current_thought updated to indicate death
"""
from __future__ import annotations

import uuid
from typing import Any

from app.games.zone_stalkers.decision.brain_runtime import ensure_brain_runtime
from app.games.zone_stalkers.decision.active_plan_manager import (
    clear_active_plan,
    get_active_plan,
    save_active_plan,
)
from app.games.zone_stalkers.decision.debug.brain_trace import append_brain_trace_event
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3


def kill_agent(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    cause: str,
    location_id: str | None = None,
    memory_title: str | None = None,
    memory_summary: str | None = None,
    memory_effects: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    emit_event: bool = True,
) -> None:
    """Canonical kill helper — apply all death-state invariants atomically."""
    # 1. Core state mutations
    agent["is_alive"] = False
    agent["hp"] = 0
    agent["scheduled_action"] = None
    agent["action_queue"] = []
    agent["current_goal"] = "dead"

    # 2. Active plan cleanup
    active_plan = get_active_plan(agent)
    if active_plan is not None:
        try:
            active_plan.abort("death", world_turn)
            save_active_plan(agent, active_plan)
        except Exception:
            pass
        clear_active_plan(agent)
    else:
        agent["active_plan_v3"] = None

    # 3. Brain runtime cleanup
    br = ensure_brain_runtime(agent, world_turn)
    br["invalidated"] = False
    br["invalidators"] = []
    br["queued"] = False
    br["queued_turn"] = None
    br["queued_priority"] = None
    br["last_skip_reason"] = "dead"
    br["valid_until_turn"] = world_turn

    # 4. Brain v3 context cleanup
    ctx = agent.get("brain_v3_context")
    if isinstance(ctx, dict):
        ctx["intent_kind"] = None
        ctx["objective_key"] = None
        ctx["objective_score"] = 0
        ctx["objective_reason"] = f"dead:{cause}"
        ctx["adapter_intent"] = None

    # 5. Brain trace update
    _death_thought = f"Погиб ({cause}); дальнейшие решения не принимаются."
    try:
        append_brain_trace_event(
            agent,
            world_turn=world_turn,
            mode="system",
            decision="no_op",
            summary=_death_thought,
            reason=cause,
            state=state,
        )
    except Exception:
        pass

    # 6. Death memory record
    _loc_id = location_id or agent.get("location_id")
    _title = memory_title or "💀 Смерть"
    _summary = memory_summary or f"Агент погиб. Причина: {cause}."
    _effects: dict[str, Any] = {"action_kind": "death", "cause": cause}
    if _loc_id:
        _effects["location_id"] = _loc_id
    if memory_effects:
        _effects.update(memory_effects)

    _memory_entry: dict[str, Any] = {
        "world_turn": world_turn,
        "type": "observation",
        "title": _title,
        "effects": _effects,
        "summary": _summary,
    }
    try:
        write_memory_event_to_v3(
            agent_id=agent_id,
            agent=agent,
            legacy_entry=_memory_entry,
            world_turn=world_turn,
        )
    except Exception:
        pass

    # 7. Emit agent_died event
    if emit_event and events is not None:
        events.append({
            "event_type": "agent_died",
            "payload": {
                "agent_id": agent_id,
                "cause": cause,
                "location_id": _loc_id,
                "world_turn": world_turn,
            },
        })
