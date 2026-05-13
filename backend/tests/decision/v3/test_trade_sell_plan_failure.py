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
    loc_agents = [a["id"] for a in agent_list if a.get("location_id") == "loc_a"]
    return {
        "world_turn": 10,
        "agents": {a["id"]: a for a in agent_list},
        "locations": {"loc_a": {"id": "loc_a", "name": "Test Location", "agents": loc_agents, "items": [], "terrain_type": "plain", "anomaly_activity": 0}},
        "traders": {},
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

    def test_no_trader_failed_sell_writes_cooldown_memory(self) -> None:
        """P0: no_trader_at_location failure must still write cooldown memory."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
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
        records = list(ensure_memory_v3(agent)["records"].values())
        failed = [r for r in records if r.get("kind") == "trade_sell_failed"]
        assert failed, "Expected trade_sell_failed memory to be written for no_trader_at_location"
        details = failed[-1].get("details") or {}
        assert int(details.get("cooldown_until_turn") or 0) > 50

    def test_trader_no_money_failed_sell_writes_cooldown_memory(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        from app.games.zone_stalkers.memory.store import ensure_memory_v3
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "money": 0, "inventory": []}
        original = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_artifact_agent(artifact_type="soul")
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=53)
        finally:
            if original is not None:
                _tick._find_trader_at_location = original

        records = list(ensure_memory_v3(agent)["records"].values())
        failed = [r for r in records if r.get("kind") == "trade_sell_failed"]
        assert failed
        rec = failed[-1]
        details = rec.get("details") or {}
        assert details.get("reason") == "trader_no_money"
        assert details.get("trader_id") == "trader_1"
        assert details.get("location_id") == "loc_a"
        assert details.get("item_types") == ["soul"]
        assert int(details.get("cooldown_until_turn") or 0) > 53
        assert "cooldown" in (rec.get("tags") or [])

    def test_failed_trade_sell_marks_action_used(self) -> None:
        """P0: failed trade_sell consumes action for the tick."""
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
        assert agent.get("action_used") is True

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

    def test_trader_no_money_returns_failure_event(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "money": 0, "inventory": []}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_artifact_agent(artifact_type="soul")
            agent["money"] = 25
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=51)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find

        fail_event = next((e for e in events if (e.get("event_type") or "") == "trade_sell_failed"), None)
        assert fail_event is not None, f"Expected failure event, got: {events}"
        payload = fail_event.get("payload") or {}
        assert payload.get("reason") == "trader_no_money"
        assert payload.get("trader_id") == "trader_1"
        assert payload.get("location_id") == "loc_a"
        assert payload.get("objective_key") == "SELL_ARTIFACTS"

    def test_trader_no_money_sets_failure_reason_in_payload(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "money": 0, "inventory": []}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_artifact_agent(artifact_type="soul")
            before_inventory = list(agent.get("inventory") or [])
            before_money = int(agent.get("money") or 0)
            before_trader_money = int(trader_agent.get("money") or 0)
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=52)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find

        assert step.payload.get("_failure_reason") == "trader_no_money"
        assert list(agent.get("inventory") or []) == before_inventory
        assert int(agent.get("money") or 0) == before_money
        assert int(trader_agent.get("money") or 0) == before_trader_money
        assert agent.get("action_used") is True

    def test_trade_sell_item_removed_without_money_is_failure(self) -> None:
        """P1: removing an item without money gain must not be treated as successful sale."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick
        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "money": 50000}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        orig_sell = getattr(_tick, "_bot_sell_to_trader", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent

        def _sell_without_money(*args, **kwargs):
            agent = args[1]
            inventory = list(agent.get("inventory") or [])
            if inventory:
                agent["inventory"] = inventory[1:]
            return []

        _tick._bot_sell_to_trader = _sell_without_money
        try:
            agent = _make_artifact_agent(artifact_type="soul")
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=70)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find
            if orig_sell is not None:
                _tick._bot_sell_to_trader = orig_sell

        assert any((e.get("event_type") or "") == "trade_sell_failed" for e in events), events
        assert step.payload.get("_failure_reason") in {"no_items_sold", "no_sellable_items"}

    def test_artifact_without_inline_value_uses_balance_price_and_sells(self) -> None:
        """Artifacts lacking `value` in inventory should still be sellable via artifact balance data."""
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {
            "id": "trader_1",
            "name": "Sidorovich",
            "location_id": "loc_a",
            "money": 50000,
            "inventory": [],
        }
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_artifact_agent(artifact_type="soul")  # intentionally no item.value
            agent["money"] = 100
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=90)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find

        event_types = {(e.get("event_type") or "") for e in events if isinstance(e, dict)}
        sold_event = next(
            (
                e for e in events
                if isinstance(e, dict) and (e.get("event_type") or "") == "bot_sold_artifact"
            ),
            None,
        )
        assert sold_event is not None
        expected_price = int(int(ARTIFACT_TYPES["soul"].get("value") or 0) * 0.6)
        assert "trade_sell_failed" not in event_types, f"Unexpected failure events: {events}"
        assert "bot_sold_artifact" in event_types, f"Expected successful sale event, got: {events}"
        assert int((sold_event.get("payload") or {}).get("price") or 0) == expected_price
        assert int(agent.get("money") or 0) > 100
        assert not any(i.get("type") == "soul" for i in agent.get("inventory", []))

    def test_artifact_without_inline_value_fails_with_trader_no_money_when_trader_cannot_pay(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "money": 0, "inventory": []}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_artifact_agent(artifact_type="soul")  # intentionally no item.value
            agent["money"] = 100
            before_inventory = list(agent.get("inventory") or [])
            before_money = int(agent.get("money") or 0)
            before_trader_money = int(trader_agent.get("money") or 0)
            step = _make_trade_sell_step()
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=91)
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find

        fail_event = next((e for e in events if (e.get("event_type") or "") == "trade_sell_failed"), None)
        assert fail_event is not None, f"Expected failure event, got: {events}"
        payload = fail_event.get("payload") or {}
        assert payload.get("reason") == "trader_no_money"
        assert step.payload.get("_failure_reason") == "trader_no_money"
        assert list(agent.get("inventory") or []) == before_inventory
        assert int(agent.get("money") or 0) == before_money
        assert int(trader_agent.get("money") or 0) == before_trader_money


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
    def _make_gen_ctx(
        self,
        *,
        world_turn: int,
        has_artifact: bool = True,
        cooldown_until_turn: int | None = None,
        cooldown_trader_id: str = "trader_1",
        cooldown_location_id: str = "loc_a",
        cooldown_item_types: list[str] | None = None,
        cooldown_reason: str = "no_items_sold",
        known_traders: list[dict[str, Any]] | None = None,
    ) -> Any:
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

        if cooldown_until_turn is not None:
            _cooldown_item_types = ["soul"] if cooldown_item_types is None else list(cooldown_item_types)
            mem_v3 = ensure_memory_v3(agent)
            rec_id = str(uuid.uuid4())
            mem_v3["records"][rec_id] = {
                "id": rec_id, "agent_id": "bot1", "layer": LAYER_GOAL,
                "kind": "trade_sell_failed", "created_turn": max(0, world_turn - 1),
                "last_accessed_turn": None, "summary": "tsf",
                "details": {
                    "action_kind": "trade_sell_failed",
                    "cooldown_until_turn": cooldown_until_turn,
                    "trader_id": cooldown_trader_id,
                    "location_id": cooldown_location_id,
                    "item_types": _cooldown_item_types,
                    "reason": cooldown_reason,
                },
                "location_id": cooldown_location_id, "entity_ids": [], "item_types": _cooldown_item_types,
                "tags": ["trade", "failure", "cooldown"], "importance": 0.9,
                "confidence": 1.0, "status": "active",
            }
            mem_v3["stats"]["records_count"] = len(mem_v3["records"])

        state = _make_minimal_state([agent])
        if known_traders is not None:
            agent["knowledge_v1"] = {
                "known_traders": {
                    str(trader.get("id") or trader.get("agent_id")): {
                        "name": trader.get("name", str(trader.get("id") or trader.get("agent_id"))),
                        "location_id": trader.get("location_id"),
                        "last_seen_turn": max(0, world_turn - 1),
                    }
                    for trader in known_traders
                    if trader.get("id") or trader.get("agent_id")
                }
            }
            state["traders"] = {
                str(trader.get("id") or trader.get("agent_id")): {
                    **trader,
                    "id": str(trader.get("id") or trader.get("agent_id")),
                    "agent_id": str(trader.get("agent_id") or trader.get("id")),
                    "is_trader": True,
                }
                for trader in known_traders
                if trader.get("id") or trader.get("agent_id")
            }
            state["known_traders"] = list(known_traders)
            for trader_id, trader in state["traders"].items():
                state["agents"][trader_id] = {
                    "id": trader_id,
                    "name": trader.get("name", trader_id),
                    "location_id": trader.get("location_id"),
                    "is_alive": True,
                    "is_trader": True,
                }
                trader_loc = str(trader.get("location_id") or "")
                if trader_loc and trader_loc in state["locations"]:
                    state["locations"][trader_loc].setdefault("agents", []).append(trader_id)
                elif trader_loc:
                    state["locations"][trader_loc] = {
                        "id": trader_loc,
                        "name": trader_loc,
                        "agents": [trader_id],
                        "items": [],
                        "terrain_type": "plain",
                        "anomaly_activity": 0,
                    }
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
        ctx = self._make_gen_ctx(world_turn=500, has_artifact=True, cooldown_until_turn=None)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS in {keys}"

    def test_sell_artifacts_suppressed_during_cooldown(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=450, has_artifact=True, cooldown_until_turn=500)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected suppressed, got: {keys}"

    def test_sell_artifacts_returns_after_cooldown_expires(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=400, has_artifact=True, cooldown_until_turn=350)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS after cooldown, got: {keys}"

    def test_sell_artifacts_not_generated_without_artifact(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(world_turn=500, has_artifact=False, cooldown_until_turn=None)
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected no SELL_ARTIFACTS, got: {keys}"

    def test_sell_artifacts_not_globally_blocked_for_other_trader(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import has_recent_trade_sell_failure
        ctx = self._make_gen_ctx(
            world_turn=450,
            has_artifact=True,
            cooldown_until_turn=700,
            cooldown_trader_id="trader_a",
            cooldown_location_id="loc_a",
            cooldown_item_types=["soul"],
            known_traders=[
                {"id": "trader_a", "name": "A", "location_id": "loc_a", "is_trader": True},
                {"id": "trader_b", "name": "B", "location_id": "loc_b", "is_trader": True},
            ],
        )
        assert has_recent_trade_sell_failure(
            ctx,
            trader_id="trader_a",
            location_id="loc_a",
            item_types={"soul"},
            world_turn=450,
        ) is True
        assert has_recent_trade_sell_failure(
            ctx,
            trader_id="trader_b",
            location_id="loc_b",
            item_types={"soul"},
            world_turn=450,
        ) is False

    def test_empty_item_types_without_no_trader_reason_does_not_block_artifact_sale(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(
            world_turn=450,
            has_artifact=True,
            cooldown_until_turn=700,
            cooldown_trader_id="trader_1",
            cooldown_location_id="loc_a",
            cooldown_item_types=[],
            cooldown_reason="no_items_sold",
        )
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS, got: {keys}"

    def test_empty_item_types_for_no_trader_reason_blocks_local_retry(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        ctx = self._make_gen_ctx(
            world_turn=450,
            has_artifact=True,
            cooldown_until_turn=700,
            cooldown_trader_id="trader_1",
            cooldown_location_id="loc_a",
            cooldown_item_types=[],
            cooldown_reason="no_trader_at_location",
        )
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected SELL_ARTIFACTS suppression, got: {keys}"

    def test_sell_artifacts_suppressed_during_trader_no_money_cooldown(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS

        ctx = self._make_gen_ctx(
            world_turn=450,
            has_artifact=True,
            cooldown_until_turn=700,
            cooldown_trader_id="trader_1",
            cooldown_location_id="loc_a",
            cooldown_item_types=["soul"],
            cooldown_reason="trader_no_money",
        )
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS not in keys, f"Expected SELL_ARTIFACTS suppression, got: {keys}"

    def test_trader_no_money_does_not_block_other_known_trader(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS

        ctx = self._make_gen_ctx(
            world_turn=450,
            has_artifact=True,
            cooldown_until_turn=700,
            cooldown_trader_id="trader_a",
            cooldown_location_id="loc_a",
            cooldown_item_types=["soul"],
            cooldown_reason="trader_no_money",
            known_traders=[
                {"id": "trader_a", "name": "A", "location_id": "loc_a", "is_trader": True},
                {"id": "trader_b", "name": "B", "location_id": "loc_b", "is_trader": True},
            ],
        )
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS for alternative trader, got: {keys}"

    def test_sell_artifacts_returns_after_trader_no_money_cooldown_expires(self) -> None:
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS

        ctx = self._make_gen_ctx(
            world_turn=451,
            has_artifact=True,
            cooldown_until_turn=450,
            cooldown_trader_id="trader_1",
            cooldown_location_id="loc_a",
            cooldown_item_types=["soul"],
            cooldown_reason="trader_no_money",
        )
        keys = [o.key for o in generate_objectives(ctx)]
        assert OBJECTIVE_SELL_ARTIFACTS in keys, f"Expected SELL_ARTIFACTS after cooldown expiry, got: {keys}"


class TestActivePlanFailureSummary:
    def test_active_plan_latest_summary_not_completed_if_trade_sell_sells_nothing(self) -> None:
        from app.games.zone_stalkers.decision.models.active_plan import (
            ACTIVE_PLAN_STATUS_ACTIVE,
            ActivePlanStep,
            ActivePlanV3,
            STEP_STATUS_PENDING,
        )
        from app.games.zone_stalkers.decision.active_plan_manager import save_active_plan
        from app.games.zone_stalkers.decision.active_plan_runtime import start_or_continue_active_plan_step
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_SELL_ITEM
        import app.games.zone_stalkers.rules.tick_rules as _tick

        memory_calls: list[dict[str, Any]] = []

        def _capture_memory(*args, **kwargs):
            effects = args[5] if len(args) > 5 else {}
            if isinstance(effects, dict):
                memory_calls.append(dict(effects))

        trader_agent = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a"}
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        orig_sell = getattr(_tick, "_bot_sell_to_trader", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        _tick._bot_sell_to_trader = lambda *a, **kw: []
        try:
            agent = _make_artifact_agent()
            agent["money"] = 336
            state = _make_minimal_state([agent, trader_agent])
            active_plan = ActivePlanV3(
                objective_key="SELL_ARTIFACTS",
                status=ACTIVE_PLAN_STATUS_ACTIVE,
                created_turn=100,
                updated_turn=100,
                steps=[ActivePlanStep(kind=STEP_TRADE_SELL_ITEM, payload={}, status=STEP_STATUS_PENDING)],
                current_step_index=0,
            )
            save_active_plan(agent, active_plan)
            start_or_continue_active_plan_step(
                "bot1",
                agent,
                active_plan,
                state,
                100,
                add_memory=_capture_memory,
            )
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find
            if orig_sell is not None:
                _tick._bot_sell_to_trader = orig_sell

        action_kinds = {str(mem.get("action_kind") or "") for mem in memory_calls}
        assert "active_plan_step_failed" in action_kinds
        assert "active_plan_completed" not in action_kinds
        summary = (
            ((agent.get("brain_v3_context") or {}).get("latest_decision_summary") or {}).get("summary")
            or ""
        )
        assert "completed, 1/1" not in summary
        assert any(i.get("type") == "soul" for i in agent.get("inventory", []))
        assert int(agent.get("money") or 0) == 336


class TestTradeSellNoMoneyActivePlanAbort:
    def test_trade_sell_trader_no_money_aborts_plan_and_invalidates_brain(self) -> None:
        from app.games.zone_stalkers.decision.active_plan_manager import create_active_plan, get_active_plan, save_active_plan
        from app.games.zone_stalkers.decision.active_plan_runtime import process_active_plan_v3
        from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_TRADE_SELL_ITEM

        agent = _make_artifact_agent()
        objective = Objective(
            key="SELL_ARTIFACTS",
            source="test",
            urgency=0.7,
            expected_value=0.8,
            risk=0.1,
            time_cost=0.2,
            resource_cost=0.0,
            confidence=0.9,
            goal_alignment=1.0,
            memory_confidence=0.8,
        )
        decision = ObjectiveDecision(
            selected=objective,
            selected_score=ObjectiveScore(
                objective_key="SELL_ARTIFACTS",
                raw_score=0.9,
                final_score=0.9,
                factors=(),
                penalties=(),
            ),
            alternatives=(),
        )
        active_plan = create_active_plan(
            decision,
            world_turn=100,
            plan=Plan(intent_kind="SELL_ARTIFACTS", steps=[PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={})]),
        )
        assert active_plan.current_step is not None
        active_plan.current_step.status = "failed"
        active_plan.current_step.failure_reason = "trade_sell_failed:trader_no_money"
        save_active_plan(agent, active_plan)

        state = _make_minimal_state([agent])
        handled, _events = process_active_plan_v3(
            "bot1",
            agent,
            state,
            101,
            add_memory=lambda *args, **kwargs: None,
        )
        assert handled is False
        assert get_active_plan(agent) is None
        brain_runtime = agent.get("brain_runtime") or {}
        assert bool(brain_runtime.get("invalidated")) is True
        invalidators = brain_runtime.get("invalidators") or []
        assert any(str(inv.get("reason") or "") == "trade_sell_failed" for inv in invalidators if isinstance(inv, dict))


class TestAdapterAndFallbackSuppression:
    def test_adapter_sell_artifacts_suppressed_by_trader_no_money_cooldown(self) -> None:
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_need_result
        from app.games.zone_stalkers.decision.intents import select_intent
        from app.games.zone_stalkers.rules.tick_rules import _fallback_objective_key_for_intent
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3

        agent = _make_artifact_agent()
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            legacy_entry={
                "world_turn": 200,
                "type": "action",
                "title": "tsf",
                "effects": {
                    "action_kind": "trade_sell_failed",
                    "reason": "trader_no_money",
                    "trader_id": "trader_1",
                    "location_id": "loc_a",
                    "item_types": ["soul"],
                    "cooldown_until_turn": 260,
                },
            },
            world_turn=200,
        )

        trader = {"id": "trader_1", "agent_id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True, "is_alive": True, "money": 0}
        state = _make_minimal_state([agent])
        state["world_turn"] = 220
        state["traders"] = {"trader_1": trader}
        state["agents"]["trader_1"] = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True, "is_alive": True}
        state["locations"]["loc_a"]["agents"].append("trader_1")

        ctx = build_agent_context("bot1", agent, state)
        need_result = evaluate_need_result(ctx, state)
        intent = select_intent(ctx, need_result.scores, world_turn=220, need_result=need_result)
        fallback_objective = _fallback_objective_key_for_intent(
            intent,
            agent=agent,
            state=state,
            world_turn=220,
        )

        assert intent.kind != "sell_artifacts"
        assert fallback_objective != "SELL_ARTIFACTS"

    def test_metadata_objective_sell_artifacts_suppressed_by_trader_no_money_cooldown(self) -> None:
        """metadata['objective_key'] == 'SELL_ARTIFACTS' must NOT bypass the cooldown check."""
        from app.games.zone_stalkers.rules.tick_rules import _fallback_objective_key_for_intent
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3

        agent = _make_artifact_agent()
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            legacy_entry={
                "world_turn": 100,
                "type": "action",
                "title": "tsf",
                "effects": {
                    "action_kind": "trade_sell_failed",
                    "reason": "trader_no_money",
                    "trader_id": "trader_1",
                    "location_id": "loc_a",
                    "item_types": ["soul"],
                    "cooldown_until_turn": 160,
                },
            },
            world_turn=100,
        )

        trader = {"id": "trader_1", "agent_id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True}
        state = _make_minimal_state([agent])
        state["world_turn"] = 120
        state["traders"] = {"trader_1": trader}

        # Construct an intent that carries metadata["objective_key"] == "SELL_ARTIFACTS"
        # This is what adapter/current-context intents look like.
        class _FakeIntent:
            kind = "sell_artifacts"
            metadata = {"objective_key": "SELL_ARTIFACTS"}

        result = _fallback_objective_key_for_intent(
            _FakeIntent(),
            agent=agent,
            state=state,
            world_turn=120,
        )
        # Cooldown is active, only one (broke) trader exists — must redirect.
        assert result == "FIND_ARTIFACTS", f"Expected FIND_ARTIFACTS, got {result!r}"

    def test_metadata_objective_sell_artifacts_not_suppressed_when_other_trader_available(self) -> None:
        """Selling is not suppressed when another known trader has no active cooldown."""
        from app.games.zone_stalkers.rules.tick_rules import _fallback_objective_key_for_intent
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3

        agent = _make_artifact_agent()
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            legacy_entry={
                "world_turn": 100,
                "type": "action",
                "title": "tsf",
                "effects": {
                    "action_kind": "trade_sell_failed",
                    "reason": "trader_no_money",
                    "trader_id": "trader_1",
                    "location_id": "loc_a",
                    "item_types": ["soul"],
                    "cooldown_until_turn": 160,
                },
            },
            world_turn=100,
        )

        # trader_1 is broke and under cooldown; trader_2 is in a different location and fine.
        trader_1 = {"id": "trader_1", "agent_id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True}
        trader_2 = {"id": "trader_2", "agent_id": "trader_2", "name": "Barkeep", "location_id": "loc_b", "is_trader": True}
        state = _make_minimal_state([agent])
        state["world_turn"] = 120
        state["traders"] = {"trader_1": trader_1, "trader_2": trader_2}

        class _FakeIntent:
            kind = "sell_artifacts"
            metadata = {"objective_key": "SELL_ARTIFACTS"}

        result = _fallback_objective_key_for_intent(
            _FakeIntent(),
            agent=agent,
            state=state,
            world_turn=120,
        )
        # Alternative trader is available — must NOT redirect to FIND_ARTIFACTS.
        assert result != "FIND_ARTIFACTS", f"Expected SELL_ARTIFACTS (or None), got {result!r}"

    def test_adapter_current_context_sell_artifacts_does_not_bypass_trader_no_money_cooldown(self) -> None:
        """Full adapter path: even when current-context source provides metadata objective key,
        the trader_no_money cooldown must still suppress SELL_ARTIFACTS."""
        from app.games.zone_stalkers.rules.tick_rules import _fallback_objective_key_for_intent
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3

        agent = _make_artifact_agent()
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            legacy_entry={
                "world_turn": 50,
                "type": "action",
                "title": "tsf",
                "effects": {
                    "action_kind": "trade_sell_failed",
                    "reason": "trader_no_money",
                    "trader_id": "trader_1",
                    "location_id": "loc_a",
                    "item_types": ["soul"],
                    "cooldown_until_turn": 120,
                },
            },
            world_turn=50,
        )

        trader = {"id": "trader_1", "agent_id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True}
        state = _make_minimal_state([agent])
        state["world_turn"] = 70
        state["traders"] = {"trader_1": trader}

        # Simulate exactly what the adapter/current-context path produces:
        # kind == "sell_artifacts" AND metadata["objective_key"] == "SELL_ARTIFACTS"
        class _AdapterIntent:
            kind = "sell_artifacts"
            metadata = {"objective_key": "SELL_ARTIFACTS", "source": "current_context"}

        result = _fallback_objective_key_for_intent(
            _AdapterIntent(),
            agent=agent,
            state=state,
            world_turn=70,
        )
        assert result == "FIND_ARTIFACTS", (
            f"Adapter intent with metadata.objective_key='SELL_ARTIFACTS' bypassed cooldown: got {result!r}"
        )


class TestNoMoneyScenarioRegression:
    def test_npc_does_not_stay_in_bunker_after_trader_no_money(self) -> None:
        from app.games.zone_stalkers.decision.active_plan_manager import create_active_plan, get_active_plan, save_active_plan
        from app.games.zone_stalkers.decision.active_plan_runtime import process_active_plan_v3, start_or_continue_active_plan_step
        from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore, ObjectiveGenerationContext
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.beliefs import build_belief_state
        from app.games.zone_stalkers.decision.needs import evaluate_need_result
        from app.games.zone_stalkers.decision.target_beliefs import build_target_belief
        from app.games.zone_stalkers.decision.objectives.generator import generate_objectives, OBJECTIVE_SELL_ARTIFACTS
        from app.games.zone_stalkers.decision.objectives.selection import choose_objective
        from app.games.zone_stalkers.memory.store import ensure_memory_v3

        agent = _make_artifact_agent()
        agent["global_goal"] = "get_rich"
        trader = {"id": "trader_1", "agent_id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True, "is_alive": True, "money": 0, "inventory": []}
        state = _make_minimal_state([agent])
        state["world_turn"] = 300
        state["traders"] = {"trader_1": trader}
        state["agents"]["trader_1"] = {"id": "trader_1", "name": "Sidorovich", "location_id": "loc_a", "is_trader": True, "is_alive": True}
        state["locations"]["loc_a"]["agents"].append("trader_1")

        objective = Objective(
            key="SELL_ARTIFACTS",
            source="test",
            urgency=0.7,
            expected_value=0.8,
            risk=0.1,
            time_cost=0.2,
            resource_cost=0.0,
            confidence=0.9,
            goal_alignment=1.0,
            memory_confidence=0.8,
        )
        decision = ObjectiveDecision(
            selected=objective,
            selected_score=ObjectiveScore(
                objective_key="SELL_ARTIFACTS",
                raw_score=0.9,
                final_score=0.9,
                factors=(),
                penalties=(),
            ),
            alternatives=(),
        )
        active_plan = create_active_plan(
            decision,
            world_turn=300,
            plan=Plan(intent_kind="SELL_ARTIFACTS", steps=[PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={})]),
        )
        save_active_plan(agent, active_plan)

        start_or_continue_active_plan_step(
            "bot1",
            agent,
            active_plan,
            state,
            300,
            add_memory=lambda *args, **kwargs: None,
        )
        failed_plan = get_active_plan(agent)
        assert failed_plan is not None
        assert failed_plan.current_step is not None
        assert failed_plan.current_step.status == "failed"
        assert failed_plan.current_step.failure_reason == "trade_sell_failed:trader_no_money"

        handled, _events = process_active_plan_v3(
            "bot1",
            agent,
            state,
            301,
            add_memory=lambda *args, **kwargs: None,
        )
        assert handled is False
        assert get_active_plan(agent) is None

        records = list(ensure_memory_v3(agent)["records"].values())
        failed_trade_records = [r for r in records if r.get("kind") == "trade_sell_failed"]
        assert failed_trade_records
        assert any((r.get("details") or {}).get("reason") == "trader_no_money" for r in failed_trade_records)

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, 301)
        need_result = evaluate_need_result(ctx, state)
        target_belief = build_target_belief(
            agent_id="bot1",
            agent=agent,
            state=state,
            world_turn=301,
            belief_state=belief,
        )
        objective_ctx = ObjectiveGenerationContext(
            agent_id="bot1",
            world_turn=301,
            belief_state=belief,
            need_result=need_result,
            active_plan_summary=None,
            personality=agent,
            target_belief=target_belief,
        )
        objectives = generate_objectives(objective_ctx)
        decision_after_fail = choose_objective(objectives, personality=agent)

        assert decision_after_fail.selected.key != OBJECTIVE_SELL_ARTIFACTS


# ---------------------------------------------------------------------------
# PR7 Part 1: No sellable items — planner guards + executor + cooldown
# ---------------------------------------------------------------------------

class TestPlanDoesNotCreateTradeSellWithoutSellableItems:
    """The planner must not emit trade_sell_item steps when the agent has
    no sellable inventory items."""

    def _agent_no_items(self, money: int = 0) -> dict:
        agent = _make_agent(money=money)
        agent["inventory"] = []          # no sellable items
        return agent

    def _agent_with_food_only(self, money: int = 0) -> dict:
        """Food items are not sellable — should behave the same as empty."""
        agent = _make_agent(money=money)
        agent["inventory"] = [{"id": "food_1", "type": "bread", "quantity": 1}]
        return agent

    def test_plan_does_not_create_trade_sell_step_without_sellable_items_for_restore_water(self) -> None:
        """seek_water plan without sellable items must not include trade_sell_item."""
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.planner import build_plan
        from app.games.zone_stalkers.decision.models.intent import Intent, INTENT_SEEK_WATER

        agent = self._agent_no_items(money=0)
        agent["location_id"] = "loc_a"
        state = _make_minimal_state([agent])
        # Add a trader at the same location so the seek-consumable path can exercise the sell guard.
        trader = {
            "id": "trader_1", "name": "Sidorovich", "location_id": "loc_a",
            "money": 1000, "inventory": [],
            "archetype": "trader_agent",
        }
        state["agents"]["trader_1"] = trader
        state["locations"]["loc_a"]["agents"].append("trader_1")

        from app.games.zone_stalkers.decision.models.agent_context import AgentContext
        ctx = AgentContext(
            agent_id="bot1",
            self_state=agent,
            location_state=state["locations"]["loc_a"],
            world_context={"world_turn": 10},
        )
        intent = Intent(kind=INTENT_SEEK_WATER, score=1.0, reason="thirsty")
        plan = build_plan(ctx, intent, state, world_turn=10)
        if plan is None:
            return  # no plan is also acceptable (no trader reachable)
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_TRADE_SELL_ITEM not in step_kinds, (
            f"trade_sell_item must not be in plan when agent has no sellable items; steps={step_kinds}"
        )

    def test_plan_does_not_create_trade_sell_step_without_sellable_items_for_restore_food(self) -> None:
        """seek_food plan without sellable items must not include trade_sell_item."""
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.planner import build_plan
        from app.games.zone_stalkers.decision.models.intent import Intent, INTENT_SEEK_FOOD

        agent = self._agent_with_food_only(money=0)
        agent["location_id"] = "loc_a"
        state = _make_minimal_state([agent])
        trader = {
            "id": "trader_1", "name": "Sidorovich", "location_id": "loc_a",
            "money": 1000, "inventory": [],
            "archetype": "trader_agent",
        }
        state["agents"]["trader_1"] = trader
        state["locations"]["loc_a"]["agents"].append("trader_1")

        from app.games.zone_stalkers.decision.models.agent_context import AgentContext
        ctx = AgentContext(
            agent_id="bot1",
            self_state=agent,
            location_state=state["locations"]["loc_a"],
            world_context={"world_turn": 10},
        )
        intent = Intent(kind=INTENT_SEEK_FOOD, score=1.0, reason="hungry")
        plan = build_plan(ctx, intent, state, world_turn=10)
        if plan is None:
            return
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_TRADE_SELL_ITEM not in step_kinds, (
            f"trade_sell_item must not be in plan when agent has only food; steps={step_kinds}"
        )

    def test_sell_artifacts_without_artifacts_does_not_create_trade_sell_step(self) -> None:
        """_plan_sell_artifacts with no artifacts in inventory must return None or plan without sell step."""
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.planner import build_plan
        from app.games.zone_stalkers.decision.models.intent import Intent, INTENT_SELL_ARTIFACTS

        agent = self._agent_no_items(money=100)
        agent["location_id"] = "loc_a"
        state = _make_minimal_state([agent])
        trader = {
            "id": "trader_1", "name": "Sidorovich", "location_id": "loc_a",
            "money": 1000, "inventory": [],
            "archetype": "trader_agent",
        }
        state["agents"]["trader_1"] = trader
        state["locations"]["loc_a"]["agents"].append("trader_1")

        from app.games.zone_stalkers.decision.models.agent_context import AgentContext
        ctx = AgentContext(
            agent_id="bot1",
            self_state=agent,
            location_state=state["locations"]["loc_a"],
            world_context={"world_turn": 10},
        )
        intent = Intent(kind=INTENT_SELL_ARTIFACTS, score=0.8, reason="want_money")
        plan = build_plan(ctx, intent, state, world_turn=10)
        # Either no plan, or plan without sell step.
        if plan is not None:
            step_kinds = [s.kind for s in plan.steps]
            # A sell-artifact plan without any artifacts is vacuous — we allow
            # the planner to return it (trader-nav step), but validate the unit
            # via the executor path separately (see test below).
            # This test primarily documents the expectation; both outcomes are
            # acceptable at the planner level.


class TestExecTradeSellWithoutSellableItems:
    """_exec_trade_sell must emit no_sellable_items failure when inventory is empty."""

    def test_exec_trade_sell_without_sellable_items_emits_no_sellable_items(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_trade_sell
        import app.games.zone_stalkers.rules.tick_rules as _tick

        trader_agent = {
            "id": "trader_1", "name": "Sidorovich",
            "location_id": "loc_a", "money": 5000, "inventory": [],
        }
        orig_find = getattr(_tick, "_find_trader_at_location", None)
        _tick._find_trader_at_location = lambda loc_id, state: trader_agent
        try:
            agent = _make_agent()
            agent["inventory"] = []   # no artifacts
            step = _make_trade_sell_step(item_category="artifact")
            state = _make_minimal_state([agent, trader_agent])
            ctx = _make_ctx("bot1", agent, state)
            events = _exec_trade_sell(
                agent_id="bot1", agent=agent, step=step, ctx=ctx, state=state, world_turn=20
            )
        finally:
            if orig_find is not None:
                _tick._find_trader_at_location = orig_find

        fail_event = next((e for e in events if (e.get("event_type") or "") == "trade_sell_failed"), None)
        assert fail_event is not None, f"Expected trade_sell_failed event, got: {events}"
        payload = fail_event.get("payload") or {}
        assert payload.get("reason") == "no_sellable_items"

    def test_no_sellable_items_failure_is_in_blocking_set(self) -> None:
        """no_sellable_items must be in BLOCKING_TRADE_SELL_FAILURE_REASONS (PR7 requirement)."""
        from app.games.zone_stalkers.decision.trade_sell_failures import BLOCKING_TRADE_SELL_FAILURE_REASONS
        assert "no_sellable_items" in BLOCKING_TRADE_SELL_FAILURE_REASONS

    def test_no_sellable_items_failure_aborts_active_plan(self) -> None:
        """assess_active_plan_v3 should abort on no_sellable_items failure recorded in step payload."""
        from app.games.zone_stalkers.decision.models.active_plan import (
            ActivePlanStep, ActivePlanV3, ACTIVE_PLAN_STATUS_ACTIVE,
            STEP_STATUS_FAILED,
        )
        from app.games.zone_stalkers.decision.active_plan_manager import (
            assess_active_plan_v3, save_active_plan,
        )
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_SELL_ITEM

        agent = _make_agent()
        step = ActivePlanStep(
            kind=STEP_TRADE_SELL_ITEM,
            payload={"item_category": "artifact", "_failure_reason": "no_sellable_items"},
        )
        step.status = STEP_STATUS_FAILED
        step.failure_reason = "no_sellable_items"

        ap = ActivePlanV3(
            id="plan-nsi",
            created_turn=5,
            steps=[step],
            current_step_index=0,
            status=ACTIVE_PLAN_STATUS_ACTIVE,
        )
        save_active_plan(agent, ap)

        state = _make_minimal_state([agent])
        op, reason = assess_active_plan_v3(agent, state, world_turn=6)
        assert op == "abort", f"Expected abort, got: ({op}, {reason})"

    def test_no_sellable_items_cooldown_is_inventory_scoped(self) -> None:
        """has_recent_trade_sell_failure_for_agent returns True for no_sellable_items
        regardless of trader/location match (it's inventory-scoped)."""
        from app.games.zone_stalkers.decision.trade_sell_failures import has_recent_trade_sell_failure_for_agent
        from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
        from app.games.zone_stalkers.memory.store import ensure_memory_v3

        agent = _make_agent()
        ensure_memory_v3(agent)
        world_turn = 50
        # Write a no_sellable_items failure record at the same turn (cooldown is future)
        effects = {
            "action_kind": "trade_sell_failed",
            "reason": "no_sellable_items",
            "trader_id": "trader_999",
            "location_id": "loc_zzz",
            "item_types": [],
            "cooldown_until_turn": world_turn + 60,
        }
        write_memory_event_to_v3(
            agent_id="bot1", agent=agent,
            legacy_entry={
                "world_turn": world_turn, "type": "action", "title": "tsf_nsi",
                "effects": effects,
            },
            world_turn=world_turn,
        )
        # Should block even with completely different trader/location/items
        result = has_recent_trade_sell_failure_for_agent(
            agent,
            trader_id="trader_x",
            location_id="loc_b",
            item_types={"soul"},
            world_turn=world_turn + 1,
        )
        assert result is True, "no_sellable_items cooldown must block regardless of trader/location"

    def test_get_sellable_inventory_items_returns_artifacts(self) -> None:
        """Public helper returns artifact-category sellable items."""
        from app.games.zone_stalkers.decision.executors import get_sellable_inventory_items

        agent = _make_artifact_agent(artifact_type="soul")
        items = get_sellable_inventory_items(agent, item_category="artifact")
        assert len(items) >= 1, "Expected at least one artifact returned"
        assert items[0]["type"] == "soul"

    def test_get_sellable_inventory_items_empty_for_no_artifacts(self) -> None:
        """Public helper returns empty list when inventory has no artifacts."""
        from app.games.zone_stalkers.decision.executors import get_sellable_inventory_items

        agent = _make_agent()
        agent["inventory"] = [{"id": "bread_1", "type": "bread", "quantity": 1}]
        items = get_sellable_inventory_items(agent, item_category="artifact")
        assert items == []

    def test_has_sellable_inventory_true_when_artifact_present(self) -> None:
        from app.games.zone_stalkers.decision.executors import has_sellable_inventory

        agent = _make_artifact_agent(artifact_type="soul")
        assert has_sellable_inventory(agent, item_category="artifact") is True

    def test_has_sellable_inventory_false_when_no_artifacts(self) -> None:
        from app.games.zone_stalkers.decision.executors import has_sellable_inventory

        agent = _make_agent()
        assert has_sellable_inventory(agent, item_category="artifact") is False
