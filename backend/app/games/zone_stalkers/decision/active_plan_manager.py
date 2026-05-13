"""Active Plan Manager — CRUD and monitoring for ActivePlanV3.

Functions
---------
create_active_plan(objective_decision, world_turn, plan)
    Build an ``ActivePlanV3`` from an ``ObjectiveDecision`` + short ``Plan``.
get_active_plan(agent)
    Deserialise ``agent["active_plan_v3"]`` → ``ActivePlanV3`` (or ``None``).
save_active_plan(agent, active_plan)
    Serialise and persist ``ActivePlanV3`` into the agent dict.
clear_active_plan(agent)
    Remove ``active_plan_v3`` key from the agent dict.
assess_active_plan_v3(agent, state, world_turn)
    Main monitor: return ``(operation, reason)`` tuple.
repair_active_plan(agent, active_plan, repair_reason, world_turn)
    Attempt to repair a plan; returns the (possibly-aborted) plan.
should_replace_active_plan(agent, new_objective_key)
    ``True`` when the new objective differs from the current plan's objective.
"""
from __future__ import annotations

from typing import Any, Optional

from .models.active_plan import (
    ActivePlanStep,
    ActivePlanV3,
    ACTIVE_PLAN_STATUS_ACTIVE,
    ACTIVE_PLAN_STATUS_ABORTED,
    ACTIVE_PLAN_STATUS_COMPLETED,
    ACTIVE_PLAN_STATUS_REPAIRING,
    MAX_REPAIR_COUNT,
    STEP_STATUS_PENDING,
    STEP_STATUS_RUNNING,
    STEP_STATUS_FAILED,
)
from .models.plan import STEP_TRADE_SELL_ITEM
from .models.objective import ObjectiveDecision
from .models.plan import Plan
from .plan_monitor import _is_emission_threat_for_monitor

from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HP_THRESHOLD,
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)

_AGENT_KEY = "active_plan_v3"


def _extract_memory_refs(source_refs: list[str]) -> list[str]:
    return [
        ref.removeprefix("memory:")
        for ref in source_refs
        if isinstance(ref, str) and ref.startswith("memory:")
    ]


def _canonicalize_step_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    location_id = (
        normalized.get("location_id")
        or normalized.get("target_id")
        or normalized.get("final_target_id")
    )
    if location_id is not None:
        normalized.setdefault("location_id", location_id)

    required_item_type = (
        normalized.get("required_item_type")
        or normalized.get("required_item")
        or normalized.get("item_type")
    )
    if required_item_type is not None:
        normalized.setdefault("required_item_type", required_item_type)

    if kind.startswith("trade_") and normalized.get("trader_location_id") is None:
        normalized["trader_location_id"] = normalized.get("location_id")

    return normalized


def _step_location_id(step: ActivePlanStep | None) -> Optional[str]:
    if step is None:
        return None
    payload = step.payload or {}
    value = (
        payload.get("location_id")
        or payload.get("target_id")
        or payload.get("final_target_id")
        or payload.get("trader_location_id")
    )
    return str(value) if value is not None else None


def _step_required_item_type(step: ActivePlanStep | None) -> Optional[str]:
    if step is None:
        return None
    payload = step.payload or {}
    value = (
        payload.get("required_item_type")
        or payload.get("required_item")
        or payload.get("item_type")
    )
    return str(value) if value is not None else None


def _known_trader_ids(state: dict[str, Any]) -> set[str]:
    known_traders = state.get("known_traders") or state.get("traders") or []
    if isinstance(known_traders, dict):
        return {str(key) for key in known_traders.keys()}
    trader_ids: set[str] = set()
    for trader in known_traders:
        if isinstance(trader, str):
            trader_ids.add(trader)
        elif isinstance(trader, dict) and trader.get("id") is not None:
            trader_ids.add(str(trader["id"]))
    return trader_ids


def _known_trader_locations(state: dict[str, Any]) -> set[str]:
    known_traders = state.get("known_traders") or []
    trader_locations: set[str] = set()
    if isinstance(known_traders, dict):
        for trader in known_traders.values():
            if isinstance(trader, dict) and trader.get("location_id") is not None:
                trader_locations.add(str(trader["location_id"]))
        return trader_locations
    for trader in known_traders:
        if isinstance(trader, dict) and trader.get("location_id") is not None:
            trader_locations.add(str(trader["location_id"]))
    traders = state.get("traders") or {}
    if isinstance(traders, dict):
        for trader in traders.values():
            if isinstance(trader, dict) and trader.get("location_id") is not None:
                trader_locations.add(str(trader["location_id"]))
    return trader_locations


def _iter_memory_v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        return []
    records = memory_v3.get("records", {})
    if not isinstance(records, dict):
        return []
    return [record for record in records.values() if isinstance(record, dict)]


def _memory_v3_has_confirmed_empty(agent: dict[str, Any], location_id: str) -> bool:
    for record in _iter_memory_v3_records(agent):
        if record.get("status") in {"stale", "archived"}:
            continue
        kind = str(record.get("kind") or "")
        if kind not in {"location_empty", "target_not_found", "target_moved", "confirmed_empty"}:
            continue
        details = record.get("details", {}) or {}
        record_location_id = (
            record.get("location_id")
            or details.get("location_id")
            or details.get("from_location_id")
        )
        if record_location_id == location_id:
            return True
    return False


def _memory_v3_has_target_moved(agent: dict[str, Any], location_id: str) -> bool:
    for record in _iter_memory_v3_records(agent):
        if record.get("status") in {"stale", "archived"}:
            continue
        if str(record.get("kind") or "") != "target_moved":
            continue
        details = record.get("details", {}) or {}
        from_location_id = details.get("from_location_id") or record.get("location_id")
        if from_location_id == location_id:
            return True
    return False


def _memory_v3_marks_trader_unavailable(
    agent: dict[str, Any],
    *,
    trader_id: str | None = None,
    trader_location_id: str | None = None,
) -> bool:
    for record in _iter_memory_v3_records(agent):
        if record.get("status") in {"stale", "archived"}:
            continue
        kind = str(record.get("kind") or "")
        if kind not in {"trader_not_found", "trader_dead"}:
            continue
        details = record.get("details", {}) or {}
        record_trader_id = details.get("trader_id")
        record_location_id = record.get("location_id") or details.get("location_id")
        if trader_id is not None and record_trader_id == trader_id:
            return True
        if trader_location_id is not None and record_location_id == trader_location_id:
            return True
    return False


# ── Factory ───────────────────────────────────────────────────────────────────

def create_active_plan(
    objective_decision: ObjectiveDecision,
    world_turn: int,
    plan: Plan,
) -> ActivePlanV3:
    """Create a new ``ActivePlanV3`` from an ``ObjectiveDecision`` + short ``Plan``.

    Parameters
    ----------
    objective_decision
        The winning objective decision that triggered this plan.
    world_turn
        Current world turn (stored as ``created_turn``).
    plan
        The short ``Plan`` whose steps are promoted to ``ActivePlanStep`` list.
    """
    objective = objective_decision.selected
    steps: list[ActivePlanStep] = [
        ActivePlanStep(
            kind=ps.kind,
            payload=_canonicalize_step_payload(ps.kind, dict(ps.payload)),
            status=STEP_STATUS_PENDING,
        )
        for ps in plan.steps
    ]
    source_refs = list(objective.source_refs) if objective.source_refs else []
    memory_refs = _extract_memory_refs(source_refs)
    return ActivePlanV3(
        objective_key=objective.key,
        status=ACTIVE_PLAN_STATUS_ACTIVE,
        created_turn=world_turn,
        updated_turn=world_turn,
        steps=steps,
        current_step_index=0,
        source_refs=source_refs,
        memory_refs=memory_refs,
        repair_count=0,
        abort_reason=None,
    )


# ── Agent dict I/O ────────────────────────────────────────────────────────────

def get_active_plan(agent: dict[str, Any]) -> Optional[ActivePlanV3]:
    """Return ``ActivePlanV3`` from the agent dict, or ``None`` if absent."""
    raw = agent.get(_AGENT_KEY)
    if not isinstance(raw, dict):
        return None
    return ActivePlanV3.from_dict(raw)


def save_active_plan(agent: dict[str, Any], active_plan: ActivePlanV3) -> None:
    """Serialise and store ``ActivePlanV3`` in the agent dict (in-place)."""
    agent[_AGENT_KEY] = active_plan.to_dict()


def clear_active_plan(agent: dict[str, Any]) -> None:
    """Remove ``active_plan_v3`` from the agent dict."""
    agent.pop(_AGENT_KEY, None)


# ── Monitor ───────────────────────────────────────────────────────────────────

def assess_active_plan_v3(
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> tuple[str, Optional[str]]:
    """Assess whether the active plan should continue, be repaired, or aborted.

    Returns
    -------
    tuple of (operation, reason) where operation is one of:
        ``"continue"``  — plan is still valid, keep executing.
        ``"repair"``    — plan assumptions broke; attempt repair.
        ``"abort"``     — unrecoverable; clear the plan.
        ``"complete"``  — plan has finished all steps.
    """
    active_plan = get_active_plan(agent)

    if active_plan is None:
        return ("abort", "no_active_plan")

    # Already in a terminal state.
    if active_plan.status in (
        ACTIVE_PLAN_STATUS_COMPLETED,
        ACTIVE_PLAN_STATUS_ABORTED,
    ):
        return ("complete" if active_plan.status == ACTIVE_PLAN_STATUS_COMPLETED else "abort",
                active_plan.abort_reason)

    if active_plan.is_complete:
        return ("complete", None)

    # Too many repair attempts → abort.
    if active_plan.repair_count >= MAX_REPAIR_COUNT:
        return ("abort", "max_repairs_exceeded")

    # ── Emission threat ───────────────────────────────────────────────────────
    if _is_emission_threat_for_monitor(agent, state):
        return ("repair", "emission_interrupt")

    # ── Critical survival needs ───────────────────────────────────────────────
    hp = float(agent.get("hp", 100))
    thirst = float(agent.get("thirst", 0))
    hunger = float(agent.get("hunger", 0))

    if hp <= CRITICAL_HP_THRESHOLD:
        return ("repair", "critical_hp")
    if thirst >= CRITICAL_THIRST_THRESHOLD:
        return ("repair", "critical_thirst")
    if hunger >= CRITICAL_HUNGER_THRESHOLD:
        return ("repair", "critical_hunger")

    # ── Step-level assumption checks ──────────────────────────────────────────
    step = active_plan.current_step
    if step is not None:
        if step.status == STEP_STATUS_FAILED:
            failure_reason = str(step.failure_reason or "step_failed")
            if step.kind == STEP_TRADE_SELL_ITEM:
                if failure_reason.endswith("no_trader_at_location"):
                    return ("repair", "trader_unavailable")
                if failure_reason.endswith("trader_no_money"):
                    return ("abort", failure_reason)
                if failure_reason.endswith("no_items_sold"):
                    return ("abort", failure_reason)
                return ("abort", failure_reason)
            return ("repair", failure_reason)

        # Trader unavailable: step payload has trader_id but that trader is not
        # in state["known_traders"].
        trader_id = step.payload.get("trader_id")
        if trader_id is not None:
            trader_ids = _known_trader_ids(state)
            if trader_id not in trader_ids or _memory_v3_marks_trader_unavailable(agent, trader_id=str(trader_id)):
                return ("repair", "trader_unavailable")
        elif step.payload.get("trader_location_id") is not None:
            trader_locations = _known_trader_locations(state)
            trader_location_id = str(step.payload["trader_location_id"])
            if trader_location_id not in trader_locations or _memory_v3_marks_trader_unavailable(
                agent,
                trader_location_id=trader_location_id,
            ):
                return ("repair", "trader_unavailable")

        # Location empty: step payload has location_id and agent memory shows
        # confirmed_empty for that location.
        location_id = _step_location_id(step)
        if location_id is not None:
            if _memory_v3_has_target_moved(agent, location_id):
                return ("repair", "target_moved")
            if _memory_v3_has_confirmed_empty(agent, location_id):
                return ("repair", "target_location_empty")

        # Supply depletion mid-plan: step requires a supply item but inventory
        # is missing it.
        required_item = _step_required_item_type(step)
        if required_item is not None:
            inventory: list[dict[str, Any]] = agent.get("inventory") or []
            has_item = any(
                item.get("type") == required_item or item.get("id") == required_item
                for item in inventory
                if isinstance(item, dict)
            )
            if not has_item:
                return ("repair", "supplies_consumed_mid_plan")

    return ("continue", None)


# ── Repair ────────────────────────────────────────────────────────────────────

def repair_active_plan(
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    repair_reason: str,
    world_turn: int,
    state: Optional[dict[str, Any]] = None,
) -> ActivePlanV3:
    """Attempt to repair the plan.  Aborts if repair_count would exceed limit.

    The repair strategy resets the current step to *pending* (so the executor
    will re-evaluate) and transitions the plan to *active* state.  If
    ``repair_count`` already equals ``MAX_REPAIR_COUNT - 1`` after this
    increment, the plan is aborted instead.

    Parameters
    ----------
    agent
        Agent dict (not mutated here; caller must call ``save_active_plan``).
    active_plan
        The ``ActivePlanV3`` to repair (mutated in-place).
    repair_reason
        Why the repair was triggered.
    world_turn
        Current world turn.

    Returns
    -------
    The modified ``ActivePlanV3`` (same object).
    """
    active_plan.request_repair(repair_reason, world_turn)

    if active_plan.repair_count >= MAX_REPAIR_COUNT:
        active_plan.abort(f"max_repairs_exceeded_after_{repair_reason}", world_turn)
        return active_plan

    step = active_plan.current_step
    if repair_reason == "emission_interrupt" and step is not None and state is not None:
        from .context_builder import build_agent_context  # noqa: PLC0415
        from .models.plan import STEP_TRAVEL_TO_LOCATION, STEP_WAIT  # noqa: PLC0415
        from .planner import _nearest_safe_location  # noqa: PLC0415

        ctx = build_agent_context(str(agent.get("id", "active-plan-agent")), agent, state)
        safe_location = _nearest_safe_location(ctx, state)
        inserted_steps: list[ActivePlanStep] = []
        if safe_location and safe_location != agent.get("location_id"):
            inserted_steps.append(
                ActivePlanStep(
                    kind=STEP_TRAVEL_TO_LOCATION,
                    payload=_canonicalize_step_payload(
                        STEP_TRAVEL_TO_LOCATION,
                        {"target_id": safe_location, "reason": "flee_emission"},
                    ),
                    status=STEP_STATUS_PENDING,
                )
            )
        inserted_steps.append(
            ActivePlanStep(
                kind=STEP_WAIT,
                payload={"reason": "wait_in_shelter"},
                status=STEP_STATUS_PENDING,
            )
        )
        step.status = STEP_STATUS_PENDING
        step.started_turn = None
        step.failure_reason = None
        active_plan.steps[active_plan.current_step_index:active_plan.current_step_index] = inserted_steps
    elif repair_reason == "trader_unavailable" and step is not None and state is not None:
        trader_locations = sorted(_known_trader_locations(state))
        current_location_id = _step_location_id(step)
        alternative_location = next(
            (
                location_id for location_id in trader_locations
                if location_id != current_location_id
            ),
            None,
        )
        if alternative_location is None:
            active_plan.abort("trader_unavailable_replan", world_turn)
            return active_plan
        step.payload["trader_location_id"] = alternative_location
        step.payload["location_id"] = alternative_location
        step.payload.pop("trader_id", None)
        step.status = STEP_STATUS_PENDING
        step.started_turn = None
        step.failure_reason = None
    elif repair_reason in {
        "target_location_empty",
        "target_moved",
        "supplies_consumed_mid_plan",
        "critical_hp",
        "critical_thirst",
        "critical_hunger",
    }:
        active_plan.abort(f"{repair_reason}_replan", world_turn)
        return active_plan
    elif step is not None:
        if step.status == STEP_STATUS_RUNNING:
            step.failure_reason = repair_reason
        step.status = STEP_STATUS_PENDING
        step.started_turn = None
        step.failure_reason = None

    # Transition back to active once the repair setup is done.
    active_plan.status = ACTIVE_PLAN_STATUS_ACTIVE
    active_plan.abort_reason = repair_reason
    active_plan.updated_turn = world_turn
    return active_plan


# ── Replacement check ─────────────────────────────────────────────────────────

def should_replace_active_plan(
    agent: dict[str, Any],
    new_objective_key: str,
) -> bool:
    """Return ``True`` when the new objective differs from the current plan's.

    This allows the caller to decide whether to abandon the current plan in
    favour of a newly selected objective.
    """
    active_plan = get_active_plan(agent)
    if active_plan is None:
        return True
    if active_plan.status in (ACTIVE_PLAN_STATUS_ABORTED, ACTIVE_PLAN_STATUS_COMPLETED):
        return True
    return active_plan.objective_key != new_objective_key
