"""explain_intent — human-readable decision explanation for one agent/tick.

``explain_agent_decision(agent_id, state)`` returns a structured dict that
describes the full decision pipeline for a single bot agent:

    {
        "agent_id": str,
        "agent_name": str,
        "world_turn": int,

        "context_summary": {
            "location": str,
            "hp": int,
            "hunger": int,
            "thirst": int,
            "sleepiness": int,
            "wealth": int,
            "material_threshold": int,
            "global_goal": str,
            "current_goal": str,
            "has_scheduled_action": bool,
            "scheduled_action_type": str | None,
            "in_combat": bool,
            "in_group": bool,
            "visible_agents": int,
        },

        "need_scores": {
            "survive_now": float,
            "heal_self": float,
            ...
            "top_3": [(name, score), ...]
        },

        "selected_intent": {
            "kind": str,
            "score": float,
            "reason": str,
            "source_goal": str | None,
        },

        "active_plan": {
            "intent_kind": str,
            "total_steps": int,
            "current_step_index": int,
            "current_step": {"kind": str, "payload": dict} | None,
            "is_complete": bool,
            "confidence": float,
        } | None,
    }

This module has NO side effects — it is safe to call at any time without
affecting game state.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..context_builder import build_agent_context
from ..needs import evaluate_needs
from ..intents import select_intent
from ..planner import build_plan
from ..bridges import plan_from_scheduled_action


def explain_agent_decision(
    agent_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build a full decision explanation for one bot agent.

    Parameters
    ----------
    agent_id
        The agent to explain.
    state
        The current world state (read-only).

    Returns
    -------
    dict
        Structured explanation dict; safe to serialise as JSON.
    """
    agents = state.get("agents", {})
    agent = agents.get(agent_id)
    if agent is None:
        return {"error": f"Agent '{agent_id}' not found"}

    world_turn: int = state.get("world_turn", 0)

    # ── 1. Build context ──────────────────────────────────────────────────────
    ctx = build_agent_context(agent_id, agent, state)

    # ── 2. Evaluate needs ─────────────────────────────────────────────────────
    needs = evaluate_needs(ctx, state)
    needs_dict = asdict(needs)
    scores_sorted = sorted(needs_dict.items(), key=lambda x: -x[1])
    top_3 = [(name, round(score, 3)) for name, score in scores_sorted[:3] if score > 0]

    # ── 3. Select intent ──────────────────────────────────────────────────────
    intent = select_intent(ctx, needs, world_turn)

    # ── 4. Build plan ─────────────────────────────────────────────────────────
    plan = build_plan(ctx, intent, state, world_turn)

    # ── 5. Assemble context summary ───────────────────────────────────────────
    loc_id = agent.get("location_id", "")
    loc = state.get("locations", {}).get(loc_id, {})
    scheduled = agent.get("scheduled_action")

    from ..needs import agent_wealth as _agent_wealth
    wealth = _agent_wealth(agent)

    context_summary: dict[str, Any] = {
        "location": loc.get("name", loc_id),
        "location_id": loc_id,
        "terrain_type": loc.get("terrain_type", "unknown"),
        "hp": agent.get("hp", 100),
        "hunger": agent.get("hunger", 0),
        "thirst": agent.get("thirst", 0),
        "sleepiness": agent.get("sleepiness", 0),
        "wealth": wealth,
        "material_threshold": agent.get("material_threshold", 3000),
        "global_goal": agent.get("global_goal", "get_rich"),
        "current_goal": agent.get("current_goal"),
        "has_scheduled_action": scheduled is not None,
        "scheduled_action_type": scheduled.get("type") if scheduled else None,
        "in_combat": ctx.combat_context is not None,
        "in_group": ctx.group_context is not None,
        "visible_agents": len(ctx.visible_entities),
    }

    # ── 6. Plan summary ───────────────────────────────────────────────────────
    plan_summary: dict[str, Any] | None = None
    if plan and plan.steps:
        cs = plan.current_step
        plan_summary = {
            "intent_kind": plan.intent_kind,
            "total_steps": len(plan.steps),
            "current_step_index": plan.current_step_index,
            "current_step": {"kind": cs.kind, "payload": cs.payload} if cs else None,
            "is_complete": plan.is_complete,
            "confidence": round(plan.confidence, 2),
        }

    return {
        "agent_id": agent_id,
        "agent_name": agent.get("name", agent_id),
        "world_turn": world_turn,
        "context_summary": context_summary,
        "need_scores": {
            **{k: round(v, 3) for k, v in needs_dict.items()},
            "top_3": top_3,
        },
        "selected_intent": {
            "kind": intent.kind,
            "score": round(intent.score, 3),
            "reason": intent.reason,
            "source_goal": intent.source_goal,
            "target_id": intent.target_id,
            "target_location_id": intent.target_location_id,
        },
        "active_plan": plan_summary,
    }


def summarise_all_bots(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return explain output for every alive bot agent in the state.

    Parameters
    ----------
    state
        The current world state.

    Returns
    -------
    list[dict]
        One explanation dict per bot agent.
    """
    result: list[dict[str, Any]] = []
    for agent_id, agent in state.get("agents", {}).items():
        if not agent.get("is_alive", True):
            continue
        if agent.get("has_left_zone"):
            continue
        if agent.get("controller", {}).get("kind") != "bot":
            continue
        result.append(explain_agent_decision(agent_id, state))
    return result
