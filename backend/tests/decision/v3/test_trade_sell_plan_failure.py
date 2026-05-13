"""B9 — Trade-sell plan failure detection + cooldown suppression tests."""
from __future__ import annotations
from typing import Any


def _make_agent(agent_id: str = "bot1", *, money: int = 0) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "location_id": "loc_a",
        "money": money,
        "inventory": [],
        "equipment": {},
        "memory_v3": None,
        "action_used": False,
        "hp": 100,
        "max_hp": 100,
        "radiation": 0,
        "hunger": 0,
        "thirst": 0,
        "sleepiness": 0,
        "faction": "loner",
        "skills": {"trading": 0.5, "combat": 0.5, "survival": 0.5, "stealth": 0.5, "tech": 0.5},
        "experience": 0,
        "reputation": {},
        "risk_tolerance": 0.5,
    }


def _make_artifact_agent(agent_id: str = "bot1", *, artifact_type: str = "soul") -> dict:
    agent = _make_agent(agent_id)
    agent["inventory"] = [{"id": "art_1", "type": artifact_type, "quantity": 1}]
    return agent


def _make_trade_sell_step(*, item_category: str = "artifact", objective_key: str = "SELL_ARTIFACTS") -> Any:
    from app.games.zone_stalkers.decision.models.plan import PlanStep, STEP_TRADE_SELL_ITEM
    return PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={"item_category": item_category, "objective_key": objective_key})


def _make_plan_with_sell_step(**kw) -> Any:
    from app.games.zone_stalkers.decision.models.plan import Plan, STEP_TRADE_SELL_ITEM, PlanStep
    step = PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={"item_category": kw.get("item_category", "artifact"), "objective_key": "SELL_ARTIFACTS"})
    return Plan(intent_kind="SELL_ARTIFACTS", steps=[step], current_step_index=0)


def _make_minimal_state(agents=None) -> dict:
    agent_list = agents or []
    return {
        "world_turn": 10,
        "agents": {a["id"]: a for a in agent_list},
        "locations": {"loc_a": {"id": "loc_a", "name": "Test Location", "agents": ["bot1"], "items": [], "terrain_type": "plain", "anomaly_activity": 0}},
    }


def _make_ctx(agent_id: str, agent: dict, state: dict) -> Any:
    from app.games.zone_stalkers.decision.models.agent_context import AgentContext
    return AgentContext(
        agent_id=agent_id,
        self_state=agent,
        location_state=state["locations"].get(agent.get("location_id", "loc_a"), {}),
        world_context={"world_turn": state.get("world_turn", 10)},
    )


# ---------------------------------------------------------------------------
# B3: helper functions
# ---------------------------------------------------------------------------

class TestTradeSellHelpers:
    def test_event_type_reads_event_type_key(self) -> None:
        from app.games.zone_stalkers.decision.executors import _event_type
        assert _event_type({"event_type": "trade_sell"}) == "trade_sell"

    def test_event_type_falls_back_to_type_key(self) -> None:
        from app.games.zone_stalkers.decision.executors import _event_type
        assert _event_type({"type": "trade_sell"}) == "trade_sell"

    def test_event_type_empty_when_missing(self) -> None:
        from app.games.zone_stalkers.decision.executors import _event_type
        assert _event_type({}) == ""

    def test_is_trade_sell_success_by_event_type(self) -> None:
        from app.games.zone_stalkers.decision.executors import _is_trade_sell_success_event
        assert _is_trade_sell_success_event({"event_type": "trade_sell"}) is True

    def test_is_trade_sell_success_by_items_sold(self) -> None:
        from app.games.zone_stalkers.decision.executors import _is_trade_sell_success_event
        assert _is_trade_sell_success_event({"items_sold": ["art_1"]}) is True

    def test_is_trade_sell_success_by_money_gained(self) -> None:
        from app.games.zone_stalkers.decision.executors import _is_trade_sell_success_event
        assert _is_trade_sell_success_event({"money_gained": 500}) is True

    def test_is_trade_sell_success_false_for_failure_event(self) -> None:
        from app.games.zone_stalkers.decision.executors import _is_trade_sell_success_event
        assert _is_trade_sell_success_event({"event_type": "trade_sell_failed", "payload": {}}) is False

    def test_trade_sell_succeeded_any_success_event(self) -> None:
        from app.games.zone_stalkers.decision.executors import _trade_sell_succeeded
        assert _trade_sell_succeeded([{"event_type": "trade_sell"}]) is True

    def test_trade_sell_succeeded_empty_list(self) -> None:
        from app.games.zone_stalkers.decision.executors import _trade_sell_succeeded
        assert _trade_sell_succeeded([]) is False

    def test_trade_sell_succeeded_only_failure_events(self) -> None:
        from app.games.zone_stalkers.decision.executors import _trade_sell_succeeded
        assert _trade_sell_succeeded([{"event_type": "trade_sell_failed"}]) is False


# ---------------------------------------------------------------------------
# B4: execute_plan_step advance logic
# ---------------------------------------------------------------------------

class TestExecutePlanStepAdvance:
    def test_advance_on_success_event(self) -> None:
        """B4: Plan advances when trade_sell events signal success."""
        from app.games.zone_stalkers.decision.executors import execute_plan_step
        import app.games.zone_stalkers.decision.executors as _exec_module

        agent = _make_agent()
        state = _make_minimal_state([agent])
        plan = _make_plan_with_sell_step()
        ctx = _make_ctx("bot1", agent, state)

        def _fake_exec(agent_id, agent_dict, step, ctx_, state_dict, world_turn):
            return [{"event_type": "trade_sell", "items_sold": ["art_1"], "money_gained": 500}]

        original = _exec_module._exec_trade_sell
        _exec_module._exec_trade_sell = _fake_exec
        try:
            execute_plan_step(ctx, plan, state, world_turn=10)
        finally:
            _exec_module._exec_trade_sell = original
        assert plan.current_step_index > 0 or plan.is_complete

    def test_no_advance_on_failure_event(self) -> None:
        """B4: Plan does NOT advance when only trade_sell_failed events are returned."""
        from app.games.zone_stalkers.decision.executors import execute_plan_step
        import app.games.zone_stalkers.decision.executors as _exec_module

        agent = _make_agent()
        state = _make_minimal_state([agent])
        plan = _make_plan_with_sell_step()
        ctx = _make_ctx("bot1", agent, state)

        def _fake_exec(agent_id, agent_dict, step, ctx_, state_dict, world_turn):
            return [{"event_type": "trade_sell_failed", "payload": {"reason": "no_trader_at_location"}}]

        original = _exec_module._exec_trade_sell
        _exec_module._exec_trade_sell = _fake_exec
        try:
            execute_plan_step(ctx, plan, state, world_turn=10)
        finally:
            _exec_module._exec_trade_sell = original
        assert plan.current_step_index == 0
        assert plan.steps[0].payload.get("_trade_sell_failed") is True

    def test_failure_sets_failure_reason(self) -> None:
        """B4: _failure_reason is set in step payload on failed sell."""
        from app.games.zone_stalkers.decision.executors import execute_plan_step
        import app.games.zone_stalkers.decision.executors as _exec_module

        agent = _make_agent()
        state = _make_minimal_state([agent])
        plan = _make_plan_with_sell_step()
        ctx = _make_ctx("bot1", agent, state)

        def _fake_exec(agent_id, agent_dict, step, ctx_, state_dict, world_turn):
            step.payload["_failure_reason"] = "no_trader_at_location"
            return [{"event_type": "trade_sell_failed"}]

        original = _exec_module._exec_trade_sell
        _exec_module._exec_trade_sell = _fake_exec
        try:
            execute_plan_step(ctx, plan, state, world_turn=10)
        finally:
            _exec_module._exec_trade_sell = original
        assert plan.steps[0].payload.get("_failure_reason") == "no_trader_at_location"


# ---------------------------------------------------------------------------
# B5: _exec_trade_sell emits trade_sell_failed when no trader
# ---------------------------------------------------------------------------

class TestExecTradeSellFailure:
    def test_no_trader_returns_failure_event(self) -> None:
        """B5: When no trader is at the location, trade_sell_failed event is returned."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick
        original = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: None
        try:
            agent = _make_artifact_agent()
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=50)
        finally:
            if original is not None:
                _tick._find_trader_at_location = original
        assert any((e.get("event_type") or "") == "trade_sell_failed" for e in events), f"Got: {events}"

    def test_no_trader_sets_failure_reason_in_payload(self) -> None:
        """B5: Step payload._failure_reason is set to no_trader_at_location."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick
        original = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: None
        try:
            agent = _make_artifact_agent()
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent])
            ctx = _make_ctx("bot1", agent, state)
            _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=50)
        finally:
            if original is not None:
                _tick._find_trader_at_location = original
        assert step.payload.get("_failure_reason") == "no_trader_at_location"

    def test_no_items_sold_returns_failure_event(self) -> None:
        """B5: When trader present but nothing is sold, trade_sell_failed is emitted."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick
        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a"}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        orig_sell = getattr(_tick, "_bot_sell_to_trader", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        _tick._bot_sell_to_trader = lambda *a, **kw: []
        try:
            agent = _make_agent()
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=50)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find
            if orig_sell is not None:
                _tick._bot_sell_to_trader = orig_sell
        assert any((e.get("event_type") or "") == "trade_sell_failed" for e in events), f"Got: {events}"


# ---------------------------------------------------------------------------
# B7: memory_events cooldown injection
# ---------------------------------------------------------------------------

class TestTradeSellFailedMemoryEvent:
    def _write_tsf(self, agent, world_turn=100, **extra):
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
        effects = {"action_kind": "trade_sell_failed", "trader_id": "trader_1", "location_id": "loc_a", "item_types": ["soul"], "reason": "no_items_sold", **extra}
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry={"world_turn": world_turn, "type": "action", "title": "tsf", "effects": effects}, world_turn=world_turn)

    def test_trade_sell_failed_event_stored_in_memory(self) -> None:
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
        agent = _make_agent()
        self._write_tsf(agent)
        records = list(ensure_memory_v3(agent)["records"].values())
        assert any(r.get("kind") == "trade_sell_failed" for r in records)

    def test_trade_sell_failed_record_has_cooldown_tag(self) -> None:
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
        agent = _make_agent()
        self._write_tsf(agent)
        records = list(ensure_memory_v3(agent)["records"].values())
        tsf = next((r for r in records if r.get("kind") == "trade_sell_failed"), None)
        assert tsf is not None
        assert "cooldown" in (tsf.get("tags") or []), f"tags: {tsf.get('tags')}"

    def test_trade_sell_failed_dedup_prevents_flood(self) -> None:
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
        agent = _make_agent()
        for turn in range(100, 103):
            self._write_tsf(agent, world_turn=turn)
        records = list(ensure_memory_v3(agent)["records"].values())
        tsf = [r for r in records if r.get("kind") == "trade_sell_failed"]
        assert len(tsf) == 1, f"Expected 1 deduped record, got {len(tsf)}"


# ---------------------------------------------------------------------------
# B8: generator suppresses SELL_ARTIFACTS during cooldown
# ---------------------------------------------------------------------------

class TestGeneratorTradeSellCooldown:
    def _make_gen_ctx(self, *, world_turn, has_artifact=True, cooldown_record_turn=None) -> Any:
        import uuid
        from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
        from app.games.zone_stalkers.memory.models import LAYER_GOAL
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.beliefs import build_belief_state
        from app.games.zone_stalkers.decision.needs import evaluate_need_result
        from app.games.zone_stalkers.decision.target_beliefs import build_target_belief

        agent = _make_artifact_agent() if has_artifact else _make_agent()
        agent["global_goal"] = "get_rich"

        if cooldown_record_turn is not None:
            mem_v3 = ensure_memory_v3(agent)
            rec_id = str(uuid.uuid4())
            mem_v3["records"][rec_id] = {
                "id": rec_id, "agent_id": "bot1", "layer": LAYER_GOAL,
                "kind": "trade_sell_failed", "created_turn": cooldown_record_turn,
                "last_accessed_turn": None, "summary": "tsf", "details": {},
                "location_id": "loc_a", "entity_ids": [], "item_types": ["soul"],
                "tags": ["trade", "failure", "cooldown"], "importance": 0.9,
                "confidence": 1.0, "status": "active",
            }
            mem_v3["stats"]["records_count"] = len(mem_v3["records"])

        state = _make_minimal_state([agent])
        state["world_turn"] = world_turn
        ctx_base = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx_base, agent, world_turn)
        need_result = evaluate_need_result(ctx_base, state)
        target_belief = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=world_turn, belief_state=belief,
        )
        return ObjectiveGenerationContext(
            agent_id="bot1",
            world_turn=world_turn,
            belief_state=belief,
            need_result=need_result,
            active_plan_summary=None,
            personality=agent,
            target_belief=target_belief,
        )

    def test_sell_artifacts_generated_when_no_cooldown(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=500, has_artifact=True, cooldown_record_turn=None)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS in {keys}"

    def test_sell_artifacts_suppressed_during_cooldown(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=450, has_artifact=True, cooldown_record_turn=400)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected suppressed, got: {keys}"

    def test_sell_artifacts_returns_after_cooldown_expires(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=400, has_artifact=True, cooldown_record_turn=100)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS after cooldown, got: {keys}"

    def test_sell_artifacts_not_generated_without_artifact(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=500, has_artifact=False, cooldown_record_turn=None)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected no SELL_ARTIFACTS, got: {keys}"
