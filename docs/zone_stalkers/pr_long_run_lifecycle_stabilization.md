# PR: Long-Run Survival, Debt Lifecycle, and Exit-Zone Stabilization

## Context

A very long simulation export at `turn=241261` exposed long-run technical failures that short CI and short simulation batches did not catch.

Observed symptoms from the long run:

```text
agents_total = 42
alive_agents = 0
last_agent_death_turn ≈ 56699
export_turn = 241261

debt_ledger.total_outstanding ≈ 1.0e17
active_debt_accounts = 16
accounts_over_escape_threshold = 16
debt_escape_triggered_count = 16
survival_credit_advances = 1894
rollovers_total = 2612

survival_medical credits ≫ consume_heal
rich NPCs died with 250k+ RU
some NPCs completed global goal but did not leave the Zone
```

The previous PRs fixed several local issues:
- sleeping direct observation;
- hunter preparation;
- trade-buy failure handling;
- purchase-sized survival medical credit;
- CI diagnosis.

This PR must address the next layer: long-run lifecycle correctness.

---

# 1. High-level problems

## P0.1 Debt rollover continues after debtor death / escape

Current debt rollover processes all accounts with `status == "active"` and positive outstanding total. It does not check whether the debtor is dead, has escaped due to debt, or has already left the Zone.

Observed result:

```text
all NPCs dead by turn ~56699
world exported at turn 241261
debt accounts still active
daily +20% rollover continues for ~128 extra days
debt grows to ≈1.0e17
```

This is a technical bug, not intended economy.

## P0.2 Medical survival purchase episode can still loop

PR47 fixed **undersized** medical credits. However the long log still showed repeated top-up patterns:

```text
money_before=65
required_price=75
principal_needed=10
amount=10
money_after=75
expected_item_type=bandage
```

This is no longer an undersized loan. It means the **episode**:

```text
request_loan -> trade_buy_item -> consume_item
```

does not reliably complete or does not suppress immediate re-entry into the same medical credit episode.

## P0.3 Rich/global-goal-completed NPCs fail to leave the Zone

Several NPCs reached goal completion or accumulated huge money, then died from hunger/thirst instead of leaving.

Observed pattern:

```text
global_goal_completed
LEAVE_ZONE selected
then REST / RESTORE_WATER / RESTORE_FOOD / HEAL_SELF loop
eventual death from starvation_or_thirst
```

A completed goal must transition into an exit lifecycle. Survival can interrupt, but only as a short emergency episode, after which `LEAVE_ZONE` must resume.

## P0.4 Debt repayment / restore / rest can block exit

After goal completion, objectives like `REPAY_DEBT`, `REST`, `RESTORE_FOOD`, `RESTORE_WATER`, `HEAL_SELF` can still keep an NPC in the Zone too long.

Correct priority after `global_goal_achieved`:

```text
critical survival purchase/use
LEAVE_ZONE
debt repayment only if already at creditor and does not delay exit materially
rest only if movement impossible / unconscious
```

## P0.5 Emission shelter failure is too deadly

15 NPCs died from emission. Some had `REACH_SAFE_SHELTER` just before death. This may be legitimate in some cases, but the long-run mortality rate suggests shelter selection / travel / preemption needs diagnostics and safeguards.

---

# 2. Required production changes

## 2.1 Debt account terminal states

Add terminal/frozen debt statuses:

```python
DEBT_STATUS_ACTIVE = "active"
DEBT_STATUS_REPAID = "repaid"
DEBT_STATUS_ESCAPED = "escaped"
DEBT_STATUS_DEBTOR_DEAD = "debtor_dead"
DEBT_STATUS_DEBTOR_LEFT_ZONE = "debtor_left_zone"
DEBT_STATUS_UNCOLLECTABLE = "uncollectable"
```

Update active statuses:

```python
_ACTIVE_ACCOUNT_STATUSES = frozenset({"active"})
_TERMINAL_ACCOUNT_STATUSES = frozenset({
    "repaid",
    "escaped",
    "debtor_dead",
    "debtor_left_zone",
    "uncollectable",
})
```

A terminal account must not:
- rollover;
- trigger new debt escape events;
- count as active account;
- keep updating debtor economic pressure.

## 2.2 Freeze accounts for dead / escaped / left-zone debtors

Add helper in `economy/debts.py`:

```python
def freeze_debtor_accounts(
    *,
    state: dict[str, Any],
    debtor_id: str,
    world_turn: int,
    status: str,
    reason: str,
) -> list[dict[str, Any]]:
    ...
```

Behavior:
- iterate all account IDs for debtor;
- if account status is active:
  - set `status = status`;
  - set `closed_turn = world_turn`;
  - set `closure_reason = reason`;
  - set `next_due_turn = None`;
  - preserve `outstanding_total` for analytics but do not accrue interest;
  - append event:

```python
{
    "event_type": "debt_account_frozen",
    "payload": {
        "account_id": ...,
        "debtor_id": debtor_id,
        "creditor_id": ...,
        "status": status,
        "reason": reason,
        "outstanding_total": ...,
        "world_turn": world_turn,
    },
}
```

Statuses:
- debtor death: `debtor_dead`;
- successful debt escape: `escaped`;
- normal left zone: `debtor_left_zone`.

## 2.3 Call freeze helper on death / escape / leave-zone

Add calls from relevant runtime places.

### On NPC death

In death handling / kill / starvation / thirst / emission death path:

```python
freeze_debtor_accounts(
    state=state,
    debtor_id=agent_id,
    world_turn=world_turn,
    status="debtor_dead",
    reason="agent_death",
)
```

### On debt escape

When debt escape triggers and NPC leaves / is marked as escaped:

```python
freeze_debtor_accounts(
    state=state,
    debtor_id=agent_id,
    world_turn=world_turn,
    status="escaped",
    reason="debt_escape",
)
```

### On normal leave zone

When `_execute_leave_zone` or equivalent sets:

```python
agent["has_left_zone"] = True
```

call:

```python
freeze_debtor_accounts(
    state=state,
    debtor_id=agent_id,
    world_turn=world_turn,
    status="debtor_left_zone",
    reason="left_zone",
)
```

## 2.4 Guard rollover against invalid debtors

In `apply_due_rollovers_with_affected_debtors(...)`, before rollover loop:

```python
debtor_id = str(account.get("debtor_id") or "")
debtor = (state.get("agents") or {}).get(debtor_id)

if not isinstance(debtor, dict):
    freeze account as uncollectable / debtor_missing
    continue

if not bool(debtor.get("is_alive", True)):
    freeze as debtor_dead
    continue

if bool(debtor.get("has_left_zone")):
    freeze as debtor_left_zone
    continue

if bool(debtor.get("debt_escape_completed")) or bool(debtor.get("escaped_due_to_debt")):
    freeze as escaped
    continue
```

This makes debt lifecycle robust even if death/leave callbacks miss something.

## 2.5 Do not resurrect terminal debt accounts automatically

`get_or_create_debt_account(...)` must not reactivate accounts with terminal statuses:

```text
escaped
debtor_dead
debtor_left_zone
uncollectable
```

If debtor is alive and in-zone after a repaid account, either reuse with a generation counter or create a new account. But never reactivate terminal dead/escaped accounts.

Extend `can_request_survival_credit(...)` to reject:
- dead debtor;
- `has_left_zone`;
- debt escape completed.

---

# 3. Medical survival episode stabilization

## 3.1 Add survival purchase episode metadata

When planner creates:

```text
request_loan -> trade_buy_item -> consume_item
```

include shared episode id:

```python
episode_id = f"survival_{category}_{world_turn}_{agent_id}"
```

Payload fields on all three steps:

```python
{
    "survival_episode_id": episode_id,
    "survival_episode_category": category,
    "expected_item_type": quote.item_type,
    "required_price": quote.required_price,
}
```

## 3.2 Track recent survival episodes on agent

Add small bounded structure:

```python
agent["survival_episode_state"] = {
    "last_episode_id": ...,
    "category": "medical",
    "expected_item_type": "bandage",
    "required_price": 75,
    "started_turn": 17286,
    "loan_turn": 17286,
    "buy_turn": 17287,
    "consume_turn": 17288,
    "status": "completed" | "failed" | "aborted",
    "failure_reason": ...,
}
```

Keep only current/last episode, not event history.

## 3.3 Prevent same-category re-entry loop

Before issuing another survival loan for same category:

```python
state = agent.get("survival_episode_state") or {}
if (
    state.category == category
    and state.status in {"started", "loaned"}
    and world_turn - state.started_turn < 30
):
    # do not issue another loan
    # instead continue/repair existing episode or fail/abort it explicitly
```

If the previous episode has `loaned` but no buy:
- try `trade_buy_item` next;
- if trade buy fails, mark episode failed and clear/abort active plan;
- do not issue another loan in the same tick window.

## 3.4 Add pending purchase repair marker

If active plan is invalidated between loan and buy, preserve:

```python
agent["pending_survival_purchase"] = {
    "category": category,
    "expected_item_type": expected_item_type,
    "required_price": required_price,
    "loan_turn": world_turn,
    "expires_turn": world_turn + 30,
}
```

Planner should consume this pending purchase first:

```text
if pending_survival_purchase exists and agent.money >= required_price:
    trade_buy_item -> consume_item
```

not another `request_loan`.

## 3.5 Add event diagnostics

Add events:
- `survival_purchase_episode_started`
- `survival_purchase_episode_loaned`
- `survival_purchase_episode_bought`
- `survival_purchase_episode_consumed`
- `survival_purchase_episode_failed`

Acceptance:

```text
For a medical episode, repeated loans without buy/consume within 30 turns must be impossible.
```

---

# 4. Goal completed -> exit-zone lifecycle

## 4.1 Add explicit exit mode

When `global_goal_achieved == True` and `has_left_zone != True`, set:

```python
agent["exit_zone_mode"] = {
    "active": True,
    "started_turn": world_turn,
    "reason": "global_goal_completed" | "debt_escape" | ...
}
```

This is not just another objective. It is a lifecycle state.

## 4.2 Objective generation priority

When exit mode is active:

Allowed objectives:
1. `LEAVE_ZONE`
2. critical survival immediate action:
   - drink/eat/heal if death risk is immediate;
   - buy/consume if already at trader or on route.
3. `REACH_SAFE_SHELTER` if emission active/imminent.

Disallowed / heavily suppressed:
- `REPAY_DEBT`;
- `GET_MONEY_FOR_RESUPPLY`;
- `PREPARE_FOR_HUNT`;
- `HUNT_TARGET`;
- `REST` unless movement impossible / sleep deprivation hard-critical.

Pseudo:

```python
if exit_zone_mode.active and not has_left_zone:
    objectives = [LEAVE_ZONE]
    objectives += critical_survival_objectives_only
    objectives += emission_shelter_if_needed
    return rank_with_exit_dominance(objectives)
```

## 4.3 Active-plan repair after survival interrupt

If exit mode is active and a survival objective completes:

```text
RESTORE_WATER completed
RESTORE_FOOD completed
HEAL_SELF completed
```

force replan reason:

```text
resume_exit_zone
```

Next selected objective should be `LEAVE_ZONE` unless a new critical threat exists.

## 4.4 Debt repayment during exit mode

Debt repayment should not block exit. Allowed only if:
- NPC is already at creditor/trader;
- payment can be done as one tick;
- does not consume money below survival/travel reserve;
- does not replace `LEAVE_ZONE` path.

Otherwise suppress.

---

# 5. Skip

---

# 6. Trade-sell/no-items and anomaly-search noise

## 6.1 No sell without sellable inventory

Before generating `SELL_ARTIFACTS` / `trade_sell_item`, check:

```python
has_sellable_inventory(agent, item_category)
```

If false:
- do not generate sell plan;
- log one debug event at most;
- avoid repeated `trade_sell_failed:no_items_sold`.

## 6.2 Anomaly search exhaustion backoff

When a location produces `anomaly_search_exhausted` or `no_artifact_found_after_exploration`, add per-agent/location cooldown:

```python
agent["location_search_cooldowns"][loc_id] = world_turn + N
```

Do not choose the same exhausted location for artifact search until cooldown expires or new intel appears.

---

# 7. Tests

## 7.1 Debt lifecycle tests

Add:

```python
def test_dead_debtor_account_does_not_rollover(): ...
def test_left_zone_debtor_account_does_not_rollover(): ...
def test_debt_escape_freezes_accounts(): ...
def test_rollover_guard_freezes_dead_debtor_even_if_death_hook_missed(): ...
def test_terminal_debt_account_not_counted_as_active(): ...
def test_no_astronomical_rollover_after_all_agents_dead_long_run(): ...
```

## 7.2 Survival episode tests

Add:

```python
def test_medical_loan_topup_then_buy_then_consume_no_repeat(): ...
def test_pending_survival_purchase_resumes_buy_after_replan(): ...
def test_failed_medical_buy_marks_episode_failed_and_does_not_reloan_same_tick_window(): ...
def test_survival_episode_state_is_bounded_not_memory_spam(): ...
```

## 7.3 Exit-zone lifecycle tests

Add:

```python
def test_goal_completed_selects_leave_zone_over_repay_debt(): ...
def test_goal_completed_survival_interrupt_resumes_leave_zone_after_drink(): ...
def test_rich_goal_completed_agent_does_not_die_from_thirst_before_exit(): ...
def test_exit_mode_suppresses_rest_if_agent_can_move(): ...
def test_exit_mode_freezes_debt_accounts_on_successful_leave(): ...
```

## 7.4 Emission tests

Add:

```python
def test_emission_selects_reachable_shelter_before_deadline(): ...
def test_flee_emission_not_interrupted_by_repay_debt_or_hunt(): ...
def test_no_reachable_shelter_logs_diagnostic(): ...
```

## 7.5 Search/sell noise tests

Add:

```python
def test_no_trade_sell_plan_without_sellable_inventory(): ...
def test_anomaly_search_exhaustion_adds_location_cooldown(): ...
def test_exhausted_anomaly_location_not_reselected_until_cooldown(): ...
```

---

# 8. Long-run regression test

Add a small but meaningful long-run smoke test.

Do not run 241k turns in regular CI. Instead:

```python
def test_long_run_lifecycle_smoke_5000_turns(): ...
```

Assertions:
- no active debt account for dead/left-zone agents;
- total outstanding bounded;
- no account rollover count above reasonable cap;
- no repeated medical loan episode without buy/consume;
- any global_goal_completed alive agent has exit mode active or has_left_zone;
- no `trade_sell_failed:no_items_sold` spam above threshold.

Run in a dedicated CI shard or nightly if too slow.

---

# 9. Acceptance criteria

```text
[ ] Debt accounts stop accruing after debtor death, normal exit, or debt escape.
[ ] Debt total cannot grow after all agents are dead.
[ ] Medical survival episode cannot issue repeated loans without buy/consume/failure.
[ ] money_before=65 required_price=75 produces at most one 10-RU top-up for one episode.
[ ] Rich/global-goal-completed NPC prioritizes LEAVE_ZONE and does not die while looping RESTORE/REST/REPAY.
[ ] Debt repayment does not block exit mode.
[ ] Emission shelter decisions include reachability diagnostics.
[ ] No repeated trade_sell_failed:no_items_sold spam.
[ ] Exhausted anomaly locations get cooldown/backoff.
[ ] Long-run smoke test remains bounded.
```

---

# 10. Focused commands

```bash
PYTHONPATH=backend pytest backend/tests/decision/v3/test_survival_loan_executor.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_debt_lifecycle.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_exit_zone_lifecycle.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_emission_shelter.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_search_backoff.py -vv --tb=short
```

Then:

```bash
PYTHONPATH=backend pytest backend/tests/decision/v3 -vv --tb=short \
  --ignore=backend/tests/decision/v3/test_e2e_brain_v3_goals.py \
  --ignore=backend/tests/decision/v3/test_hunt_leads.py \
  --ignore=backend/tests/decision/v3/test_hunt_fixes.py \
  --ignore=backend/tests/decision/v3/test_hunt_kill_stalker_goal.py
```

---

# Notes

This PR should not introduce new gameplay mechanics beyond stabilizing existing lifecycle behavior.

Do not implement location knowledge / Zone exploration in this PR. That should be a separate PR series because it changes pathfinding, planning, trading intel, and knowledge propagation.
