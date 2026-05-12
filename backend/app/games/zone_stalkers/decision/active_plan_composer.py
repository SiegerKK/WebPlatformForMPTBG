from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_ASK_FOR_INTEL,
    STEP_CONFIRM_KILL,
    STEP_EXPLORE_LOCATION,
    STEP_LOOK_FOR_TRACKS,
    STEP_MONITOR_COMBAT,
    STEP_QUESTION_WITNESSES,
    STEP_SEARCH_TARGET,
    STEP_START_COMBAT,
    STEP_TRAVEL_TO_LOCATION,
)


_STRATEGIC_COMPOSE_KEYS: frozenset[str] = frozenset({
    "FIND_ARTIFACTS",
    "GET_MONEY_FOR_RESUPPLY",
    "GATHER_INTEL",
    "LOCATE_TARGET",
    "VERIFY_LEAD",
    "TRACK_TARGET",
    "ENGAGE_TARGET",
    "CONFIRM_KILL",
})


def _clone_step(step: PlanStep) -> PlanStep:
    return PlanStep(
        kind=step.kind,
        payload=dict(step.payload),
        interruptible=step.interruptible,
        expected_duration_ticks=step.expected_duration_ticks,
    )



def _is_witness_source_exhausted(
    agent: dict,
    *,
    target_id: str,
    location_id: str,
    world_turn: int,
) -> bool:
    """Return True if the witness source at location_id is exhausted for target_id."""
    from app.games.zone_stalkers.rules.tick_rules import _v3_records_desc, _v3_action_kind, _v3_details  # noqa: PLC0415
    for rec in _v3_records_desc(agent):
        if _v3_action_kind(rec) != "witness_source_exhausted":
            continue
        fx = _v3_details(rec)
        if str(fx.get("target_id") or "") != target_id:
            continue
        if str(fx.get("location_id") or rec.get("location_id") or "") != location_id:
            continue
        cooldown_until = fx.get("cooldown_until_turn")
        if isinstance(cooldown_until, (int, float)) and int(cooldown_until) > world_turn:
            return True
    return False


def compose_active_plan_steps(
    *,
    objective_key: str,
    base_plan: Plan,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> list[PlanStep]:
    _state = state  # reserved for future use
    _world_turn = world_turn  # reserved for future use
    del _state, _world_turn
    steps = [_clone_step(step) for step in base_plan.steps]
    if objective_key not in _STRATEGIC_COMPOSE_KEYS or not steps:
        return steps

    first_step = steps[0]
    if objective_key in {"FIND_ARTIFACTS", "GET_MONEY_FOR_RESUPPLY"}:
        if any(step.kind == STEP_EXPLORE_LOCATION for step in steps):
            return steps
        if first_step.kind != STEP_TRAVEL_TO_LOCATION:
            return steps
        target_id = (
            first_step.payload.get("location_id")
            or first_step.payload.get("target_id")
            or first_step.payload.get("final_target_id")
        )
        if target_id is None:
            return steps

        steps.append(
            PlanStep(
                kind=STEP_EXPLORE_LOCATION,
                payload={
                    "target_id": target_id,
                    "location_id": target_id,
                    "reason": "active_plan_composed_explore_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=30,
            )
        )
        return steps

    if first_step.kind != STEP_TRAVEL_TO_LOCATION:
        return steps

    target_id = (
        first_step.payload.get("location_id")
        or first_step.payload.get("target_id")
        or first_step.payload.get("final_target_id")
    )
    if target_id is None:
        return steps

    if objective_key in {"GATHER_INTEL", "LOCATE_TARGET"} and not any(
        s.kind in {STEP_ASK_FOR_INTEL, STEP_QUESTION_WITNESSES} for s in steps
    ):
        # Fix 7: Check if the witness source at the destination is exhausted
        _kill_target_id = agent.get("kill_target_id") or ""
        _dest_witnesses_exhausted = _is_witness_source_exhausted(
            agent,
            target_id=str(_kill_target_id),
            location_id=str(target_id),
            world_turn=world_turn,
        )
        if _dest_witnesses_exhausted:
            # Fix 7 fallback: witnesses exhausted, use ask_for_intel (trader) instead
            steps.append(
                PlanStep(
                    kind=STEP_ASK_FOR_INTEL,
                    payload={
                        "target_id": _kill_target_id,
                        "reason": "active_plan_composed_fallback_after_exhausted_witnesses",
                    },
                    interruptible=True,
                    expected_duration_ticks=1,
                )
            )
        else:
            steps.append(
                PlanStep(
                    kind=STEP_QUESTION_WITNESSES,
                    payload={
                        "target_id": _kill_target_id,
                        "reason": "active_plan_composed_locate_after_travel",
                    },
                    interruptible=True,
                    expected_duration_ticks=1,
                )
            )
        return steps

    if objective_key == "VERIFY_LEAD" and not any(
        s.kind in {STEP_SEARCH_TARGET, STEP_LOOK_FOR_TRACKS, STEP_QUESTION_WITNESSES} for s in steps
    ):
        steps.extend([
            PlanStep(
                kind=STEP_SEARCH_TARGET,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "target_location_id": target_id,
                    "reason": "active_plan_composed_verify_search_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=1,
            ),
            PlanStep(
                kind=STEP_LOOK_FOR_TRACKS,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "target_location_id": target_id,
                    "reason": "active_plan_composed_verify_tracks_after_search",
                },
                interruptible=True,
                expected_duration_ticks=1,
            ),
            PlanStep(
                kind=STEP_QUESTION_WITNESSES,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_verify_witnesses_after_tracks",
                },
                interruptible=True,
                expected_duration_ticks=1,
            ),
        ])
        return steps

    if objective_key == "TRACK_TARGET" and not any(s.kind == STEP_SEARCH_TARGET for s in steps):
        steps.append(
            PlanStep(
                kind=STEP_SEARCH_TARGET,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "target_location_id": target_id,
                    "reason": "active_plan_composed_search_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=1,
            )
        )
        steps.append(
            PlanStep(
                kind=STEP_LOOK_FOR_TRACKS,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "target_location_id": target_id,
                    "reason": "active_plan_composed_tracks_after_search",
                },
                interruptible=True,
                expected_duration_ticks=1,
            )
        )
        return steps

    if objective_key == "ENGAGE_TARGET" and not any(s.kind == STEP_START_COMBAT for s in steps):
        steps.append(
            PlanStep(
                kind=STEP_START_COMBAT,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_engage_after_travel",
                },
                interruptible=False,
                expected_duration_ticks=1,
            )
        )
        steps.append(
            PlanStep(
                kind=STEP_MONITOR_COMBAT,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_monitor_after_combat_start",
                },
                interruptible=False,
                expected_duration_ticks=1,
            )
        )
        steps.append(
            PlanStep(
                kind=STEP_CONFIRM_KILL,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_confirm_after_engage",
                },
                interruptible=False,
                expected_duration_ticks=1,
            )
        )
        return steps

    return steps
