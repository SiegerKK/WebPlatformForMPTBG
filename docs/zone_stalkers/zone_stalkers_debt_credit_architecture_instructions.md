# PR: Debt/Credit Mechanics — Correct Zone Stalkers Architecture and Implementation Plan

## Purpose

This document replaces the incorrect `backend/game_sdk/*` implementation with a Zone Stalkers-native debt/credit design.

The goal is to fix the long-run survival/economy failure:

```text
poor NPC at trader
needs water/food/medicine
has no enough money
has no sellable inventory
planner attempts trade_sell_item anyway
trade_sell_failed:no_items_sold repeats
NPC may die at the trader
```

The first runtime use case is **trader survival credit**, but the model must be generic enough for future NPC↔NPC debts.

---

# 1. Architecture verdict

## 1.1 Current branch problem

The current branch created files under:

```text
backend/game_sdk/
```

This is not how Zone Stalkers domain logic is structured in this repository.

The real game runtime already lives under:

```text
backend/app/games/zone_stalkers/
```

Existing Zone Stalkers subsystems are organized by domain:

```text
backend/app/games/zone_stalkers/balance/
backend/app/games/zone_stalkers/decision/
backend/app/games/zone_stalkers/knowledge/
backend/app/games/zone_stalkers/memory/
backend/app/games/zone_stalkers/rules/
backend/app/games/zone_stalkers/runtime/
```

The current `main` does not use `backend/game_sdk` as a production domain layer. Adding debt logic there creates a parallel architecture outside the actual game module.

## 1.2 Do we need files outside `zone_stalkers`?

No.

For this feature, **no new production files should be added outside**:

```text
backend/app/games/zone_stalkers/
```

The only files outside `zone_stalkers` should be tests under:

```text
backend/tests/
```

This PR should delete the new `backend/game_sdk/*` files and reimplement the feature inside Zone Stalkers.

## 1.3 Files to remove from the current branch

Delete:

```text
backend/game_sdk/__init__.py
backend/game_sdk/debt_ledger.py
backend/game_sdk/executors/__init__.py
backend/game_sdk/executors/execute_take_survival_loan.py
backend/game_sdk/plan_steps/__init__.py
backend/game_sdk/plan_steps/take_survival_loan.py
backend/tests/test_debt_ledger.py
backend/tests/test_take_survival_loan.py
```

Why delete the tests too?

Because they test the wrong architecture and the wrong mechanics:
- they validate a simplified ledger without daily interest;
- they require trader cash for survival loans;
- they test isolated helper modules instead of the actual Zone Stalkers planner/executor/runtime flow.

Replace them with Zone Stalkers tests under `backend/tests/decision/v3/`.

---

# 2. Correct file layout

## 2.1 New production files

Create:

```text
backend/app/games/zone_stalkers/economy/__init__.py
backend/app/games/zone_stalkers/economy/debts.py
```

Optional if constants become large:

```text
backend/app/games/zone_stalkers/economy/constants.py
```

If not creating `constants.py`, constants may live at the top of `debts.py`.

## 2.2 Existing production files to modify

Modify:

```text
backend/app/games/zone_stalkers/decision/models/plan.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/projections.py
backend/app/games/zone_stalkers/router.py
```

Optional, if relevant export logic is elsewhere:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## 2.3 Tests to add

Create:

```text
backend/tests/decision/v3/test_debt_mechanics.py
backend/tests/decision/v3/test_survival_loan_planner.py
backend/tests/decision/v3/test_survival_loan_executor.py
backend/tests/decision/v3/test_survival_loan_e2e.py
```

Alternatively, combine them into fewer files, but they must live in the Zone Stalkers decision/v3 test area, not generic top-level `backend/tests/test_*.py`.

---

# 3. Correct debt model

## 3.1 Canonical state location

Debt state must live in the world state:

```python
state["debt_ledger"]
```

Canonical schema:

```python
state["debt_ledger"] = {
    "version": 1,
    "debts": {
        debt_id: DebtContract,
    },
    "by_debtor": {
        debtor_id: [debt_id, ...],
    },
    "by_creditor": {
        creditor_id: [debt_id, ...],
    },
}
```

The ledger is authoritative.

Agent/trader summaries are derived/cache fields only.

## 3.2 DebtContract schema

Use this schema, not the simplified `principal/balance/interest_rate=0` schema.

```python
DebtContract = {
    "id": "debt_<uuid>",

    "debtor_id": "agent_debug_5",
    "debtor_type": "agent",

    "creditor_id": "trader_1",
    "creditor_type": "trader",  # future: "agent"

    "principal": 45,
    "outstanding_principal": 45,
    "accrued_interest": 0.0,
    "total_repaid": 0,

    "daily_interest_rate": 0.05,
    "created_turn": 4354,
    "last_accrued_turn": 4354,
    "due_turn": 5794,

    "purpose": "survival_drink",
    "allowed_item_category": "drink",

    "created_location_id": "loc_debug_61",

    "status": "active",  # active | repaid | overdue | defaulted | forgiven

    "collateral_item_ids": [],
    "source": "trader_survival_credit",
    "notes": {},
}
```

## 3.3 Required constants

Place in `economy/debts.py` or `economy/constants.py`:

```python
SURVIVAL_LOAN_DAILY_INTEREST_RATE = 0.05
SURVIVAL_LOAN_DUE_TURNS = 1440
SURVIVAL_LOAN_MAX_PRINCIPAL = 300
SURVIVAL_LOAN_MIN_PRINCIPAL = 1
SURVIVAL_LOAN_MAX_ACTIVE_TOTAL = 500
SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY = 100

SURVIVAL_LOAN_ALLOWED_CATEGORIES = frozenset({
    "drink",
    "food",
    "medical",
})

SURVIVAL_LOAN_PURPOSE_BY_CATEGORY = {
    "drink": "survival_drink",
    "food": "survival_food",
    "medical": "survival_medical",
}
```

Do not use `money < 15` as eligibility. That misses real cases where the NPC has 20–40 money but still cannot afford water/food.

---

# 4. `economy/debts.py` API

Implement these functions.

## 4.1 `ensure_debt_ledger`

```python
def ensure_debt_ledger(state: dict[str, Any]) -> dict[str, Any]:
    ...
```

Rules:
- create ledger if missing;
- repair missing `debts`, `by_debtor`, `by_creditor`;
- return ledger.

## 4.2 `accrue_debt_interest`

```python
def accrue_debt_interest(
    debt: dict[str, Any],
    *,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Simple non-compounding prorated daily interest:

```python
delta_turns = max(0, world_turn - int(debt.get("last_accrued_turn", world_turn)))
interest_delta = (
    float(debt["outstanding_principal"])
    * float(debt["daily_interest_rate"])
    * delta_turns
    / 1440
)
debt["accrued_interest"] += interest_delta
debt["last_accrued_turn"] = world_turn
```

Do not create memory records for interest accrual.

## 4.3 `mark_overdue_debts`

```python
def mark_overdue_debts(
    state: dict[str, Any],
    *,
    world_turn: int,
) -> int:
    ...
```

Rules:
- if `status == "active"` and `world_turn > due_turn`, set `status = "overdue"`;
- return count changed.

Defaulting may be simple:

```python
if status == "overdue" and world_turn > due_turn + 1440:
    status = "defaulted"
```

No collection AI in this PR.

## 4.4 `get_debtor_active_debts`

```python
def get_debtor_active_debts(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
    creditor_id: str | None = None,
) -> list[dict[str, Any]]:
    ...
```

Rules:
- accrue interest before returning;
- include `active` and `overdue`;
- exclude `repaid`, `forgiven`, `defaulted` unless explicitly needed.

## 4.5 `get_debtor_outstanding_total`

```python
def get_debtor_outstanding_total(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> int:
    ...
```

Return:

```python
ceil(sum(outstanding_principal + accrued_interest))
```

## 4.6 `can_request_survival_loan`

```python
def can_request_survival_loan(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    creditor_type: str,
    item_category: str,
    required_price: int,
    world_turn: int,
) -> tuple[bool, str]:
    ...
```

Eligibility rules:

```text
allow only item_category in drink/food/medical
allow only if required_price > debtor.money
allow only if debtor is alive
allow only if creditor exists and is active/alive
allow only if active survival debt total is below credit limit
deny if debtor has defaulted debt
deny if requested principal exceeds max principal
```

Important:

```text
Trader survival credit must NOT require trader.money >= amount.
```

The trader sells life-saving goods on credit. This is store credit/accounts receivable, not a cash loan.

For future NPC↔NPC cash loans, creditor money can matter, but not for this first trader survival-credit path.

## 4.7 `create_debt`

```python
def create_debt(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    debtor_type: str = "agent",
    principal: int,
    purpose: str,
    allowed_item_category: str,
    location_id: str,
    daily_interest_rate: float,
    due_turn: int,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Update:
- `debt_ledger["debts"]`;
- `by_debtor`;
- `by_creditor`;
- `debtor["economic_state"]` if available through a separate summary helper;
- `trader["accounts_receivable"]` if called from trader credit executor.

## 4.8 `repay_debt`

```python
def repay_debt(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    debt_id: str,
    amount: int,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Rules:
- accrue interest first;
- payment pays `accrued_interest` first;
- then `outstanding_principal`;
- decrement debtor money by actual payment;
- increment creditor money by actual payment if creditor exists;
- update `total_repaid`;
- if fully paid, set `status = "repaid"`.

## 4.9 `auto_repay_debts`

```python
def auto_repay_debts(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    world_turn: int,
    reserve_money: int = SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY,
) -> list[dict[str, Any]]:
    ...
```

Rules:
- only repay if `debtor.money > reserve_money`;
- never reduce below reserve;
- prioritize:
  1. overdue debts;
  2. higher daily interest;
  3. oldest debt;
- return repayment result events, including `debt_repaid` when a debt is fully paid.

## 4.10 `summarize_debtor_economic_state`

```python
def summarize_debtor_economic_state(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Return:

```python
{
    "debt_total": 90,
    "active_debt_count": 2,
    "overdue_debt_count": 0,
    "defaulted_debt_count": 0,
    "creditors": ["trader_1"],
}
```

This summary may be stored on the agent for debug/export.

---

# 5. Plan model integration

## 5.1 Step name

Prefer a generic name:

```python
STEP_REQUEST_LOAN = "request_loan"
```

This is better than `STEP_TAKE_SURVIVAL_LOAN`, because the mechanic is intended to support future NPC↔NPC loans.

If keeping the current name to reduce churn, the payload still must be generic and explicit. But preferred final architecture is:

```python
STEP_REQUEST_LOAN
```

Add in:

```text
backend/app/games/zone_stalkers/decision/models/plan.py
```

Payload:

```python
{
    "creditor_id": "trader_1",
    "creditor_type": "trader",
    "amount": 45,
    "purpose": "survival_drink",
    "item_category": "drink",
    "required_price": 45,
    "daily_interest_rate": 0.05,
    "reason": "survival_credit_drink",
}
```

---

# 6. Executor integration

Modify:

```text
backend/app/games/zone_stalkers/decision/executors.py
```

## 6.1 Add dispatch

```python
STEP_REQUEST_LOAN: _exec_request_loan
```

## 6.2 Implement `_exec_request_loan`

```python
def _exec_request_loan(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    ...
```

Required behavior:

1. Resolve creditor.
2. Validate via `can_request_survival_loan`.
3. Create debt.
4. For trader store credit:
   - add amount to debtor money;
   - do **not** subtract trader money;
   - increment trader `accounts_receivable`.
5. Return event:

```python
{
    "event_type": "debt_created",
    "payload": {
        "debt_id": debt_id,
        "debtor_id": agent_id,
        "creditor_id": creditor_id,
        "creditor_type": "trader",
        "principal": amount,
        "purpose": purpose,
        "daily_interest_rate": daily_interest_rate,
        "location_id": agent.get("location_id"),
    },
}
```

## 6.3 Do not advance plan after failed loan

Current branch auto-advances the loan step because it is listed among one-tick actions.

That is unsafe.

Required:

```text
loan success → plan.advance()
loan failed/skipped → mark current step failed and abort/replan
```

Implementation option:

- Do not include `STEP_REQUEST_LOAN` in unconditional one-tick auto-advance.
- Add helper:

```python
def _loan_request_succeeded(events: list[dict[str, Any]]) -> bool:
    return any(ev.get("event_type") == "debt_created" for ev in events)
```

Then:

```python
elif step.kind == STEP_REQUEST_LOAN:
    if _loan_request_succeeded(events):
        plan.advance()
    else:
        step.payload["_loan_failed"] = True
        step.payload["_failure_reason"] = ...
```

The active plan runtime should then abort/replan the failed step the same way failed trade_sell is handled.

---

# 7. Planner integration

Modify:

```text
backend/app/games/zone_stalkers/decision/planner.py
```

The loan path belongs only to immediate survival branches:

```text
drink
food
medical
```

Do not use loans for:

```text
ammo
weapon
armor
equipment
get_rich
reserve stock purchase
non-critical rest preparation
```

## 7.1 Correct decision order

For critical survival item:

```text
if can buy now:
    trade_buy_item → consume_item

elif has safe/emergency sellable liquidity:
    trade_sell_item → trade_buy_item → consume_item

elif can request survival loan:
    request_loan → trade_buy_item → consume_item

else:
    fallback get_money / search / wait
```

## 7.2 Amount must be derived from current item

Do not call a global `calculate_loan_amount(npc, state)` that chooses category independently.

The planner already knows:
- current category;
- compatible item types;
- required price;
- current money.

Use:

```python
amount = max(0, required_price - int(agent.get("money") or 0))
```

Clamp to max principal.

## 7.3 Same-location trader

If trader is co-located:

```python
steps = [
    PlanStep(STEP_REQUEST_LOAN, loan_payload, interruptible=False),
    PlanStep(STEP_TRADE_BUY_ITEM, buy_payload, interruptible=False),
    PlanStep(STEP_CONSUME_ITEM, consume_payload, interruptible=False),
]
```

## 7.4 Remote trader

If trader is remote:

```python
steps = [
    PlanStep(STEP_TRAVEL_TO_LOCATION, travel_payload, interruptible=True),
    PlanStep(STEP_REQUEST_LOAN, loan_payload, interruptible=False),
    PlanStep(STEP_TRADE_BUY_ITEM, buy_payload, interruptible=False),
    PlanStep(STEP_CONSUME_ITEM, consume_payload, interruptible=False),
]
```

But only build this if travel is plausibly survivable.

## 7.5 Medical path must consume/heal

Emergency medical loan path must be:

```text
request_loan → trade_buy_item → consume/heal item
```

Do not stop after buying the medkit.

## 7.6 Prevent impossible sell plans

If no liquidity and loan is available, do not create `STEP_TRADE_SELL_ITEM`.

This is the central regression target.

---

# 8. Trade buy integration

No direct credit purchase is required in this PR.

Recommended first implementation:

```text
request_loan adds enough debtor money
trade_buy_item uses existing buy flow
consume_item uses existing consume flow
```

This keeps trade buy logic stable.

But because trader survival credit is store credit, loan issuance must not reduce trader cash.

---

# 9. Repayment integration

## 9.1 After successful sale

In `_exec_trade_sell`, after successful sale and after money is added to the agent:

```python
repayment_events = auto_repay_debts(
    state=state,
    debtor=agent,
    creditor=trader,
    world_turn=world_turn,
)
events.extend(repayment_events)
```

## 9.2 Repayment events

Return events:

```python
{
    "event_type": "debt_payment",
    "payload": {
        "debt_id": debt_id,
        "debtor_id": agent_id,
        "creditor_id": creditor_id,
        "amount": amount,
        "remaining_total": remaining_total,
    },
}
```

If fully repaid:

```python
{
    "event_type": "debt_repaid",
    "payload": {
        "debt_id": debt_id,
        "debtor_id": agent_id,
        "creditor_id": creditor_id,
        "total_repaid": total_repaid,
    },
}
```

## 9.3 Never starve the debtor via repayment

Reserve:

```python
SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY = 100
```

If debtor has 120, max payment is 20.

---

# 10. Memory integration

Modify:

```text
backend/app/games/zone_stalkers/memory/memory_events.py
```

Add policies:

```python
"debt_created": "memory",
"debt_payment": "memory",
"debt_repaid": "memory",
"debt_defaulted": "memory_critical",
```

Do not create memory records for interest accrual.

Add `_ACTION_KIND_MAP` entries:

```python
"debt_created": (LAYER_GOAL, "debt_created", ("economy", "debt", "credit")),
"debt_payment": (LAYER_GOAL, "debt_payment", ("economy", "debt", "repayment")),
"debt_repaid": (LAYER_GOAL, "debt_repaid", ("economy", "debt", "repayment")),
"debt_defaulted": (LAYER_GOAL, "debt_defaulted", ("economy", "debt", "default")),
```

If using `write_memory_event_to_v3`, ensure executor events are routed through the same canonical path used by other action events.

---

# 11. Export/debug integration

Modify:

```text
backend/app/games/zone_stalkers/projections.py
backend/app/games/zone_stalkers/router.py
```

Add per-agent export/debug summary:

```python
"economic_state": {
    "debt_total": 90,
    "active_debt_count": 2,
    "overdue_debt_count": 0,
    "defaulted_debt_count": 0,
    "creditors": ["trader_1"],
}
```

Add `_summary.json` debt summary:

```python
"debt_summary": {
    "active_debts": 12,
    "total_outstanding": 540,
    "overdue_debts": 0,
    "defaulted_debts": 0,
    "survival_loans_created": 18,
    "survival_loans_repaid": 6,
}
```

This is important for the next long-run log analysis.

---

# 12. What the current branch got wrong

## 12.1 Wrong module location

Wrong:

```python
from game_sdk.debt_ledger import ...
```

Correct:

```python
from app.games.zone_stalkers.economy.debts import ...
```

## 12.2 Wrong creditor cash semantics

Wrong:

```python
trader["money"] -= amount
agent["money"] += amount
```

Correct for trader survival credit:

```python
agent["money"] += amount
trader["accounts_receivable"] += amount
```

## 12.3 Wrong eligibility

Wrong:

```python
npc.money < 15
```

Correct:

```python
agent.money < required_price
```

## 12.4 Wrong loan amount

Wrong:

```python
calculate_loan_amount(npc, state)
```

Correct:

```python
amount = required_price - agent.money
```

## 12.5 Wrong failed-step behavior

Wrong:

```text
loan failed → plan.advance() → trade_buy_item
```

Correct:

```text
loan failed → fail current step → abort/replan
```

## 12.6 Missing medical consume

Wrong:

```text
loan → buy medical
```

Correct:

```text
loan → buy medical → consume/heal
```

## 12.7 Missing interest

Wrong:

```python
interest_rate = 0.0
```

Correct:

```python
daily_interest_rate = 0.05
accrued_interest grows with turns
```

## 12.8 Missing integration tests

Current tests validate isolated helper functions. They do not validate the actual game behavior.

---

# 13. Required tests

## 13.1 Core debt mechanics

```python
def test_create_survival_debt_contract_schema(): ...
def test_interest_accrues_after_one_day_without_compounding(): ...
def test_partial_repayment_pays_interest_first(): ...
def test_full_repayment_marks_debt_repaid(): ...
def test_debt_becomes_overdue_after_due_turn(): ...
def test_defaulted_debt_blocks_new_survival_credit(): ...
def test_active_debt_cap_blocks_infinite_loans(): ...
```

## 13.2 Planner tests

```python
def test_poor_thirsty_agent_at_trader_gets_loan_plan_not_sell_plan(): ...
def test_poor_hungry_agent_at_trader_gets_loan_plan_not_sell_plan(): ...
def test_poor_injured_agent_at_trader_gets_loan_buy_heal_consume_plan(): ...
def test_agent_with_safe_sellable_item_sells_before_taking_loan(): ...
def test_no_loan_for_ammo_weapon_armor_or_get_rich(): ...
def test_agent_with_some_money_but_below_required_price_can_take_loan(): ...
```

The last test is important because real logs show NPCs with 20–40 money dying near the trader.

## 13.3 Executor tests

```python
def test_request_loan_success_creates_debt_and_accounts_receivable(): ...
def test_request_loan_does_not_require_trader_cash(): ...
def test_failed_request_loan_does_not_advance_plan_to_trade_buy(): ...
def test_request_loan_then_trade_buy_then_consume_water(): ...
def test_request_loan_then_trade_buy_then_consume_food(): ...
def test_request_loan_then_trade_buy_then_consume_medical(): ...
```

## 13.4 Repayment tests

```python
def test_successful_artifact_sale_auto_repays_debt_interest_first(): ...
def test_auto_repay_never_reduces_agent_below_reserve(): ...
def test_full_repayment_emits_debt_repaid_event(): ...
```

## 13.5 E2E regression

```python
def test_e2e_poor_stalker_survives_at_trader_by_taking_survival_credit(): ...
```

Scenario:

```text
agent at loc_debug_61
critical thirst
money below water price
inventory empty
trader present
```

Expected:

```text
plan does not contain trade_sell_item
plan contains request_loan
agent buys water
agent consumes water
debt exists
no trade_sell_failed:no_items_sold loop
agent survives the focused window
```

---

# 14. Acceptance criteria

This PR is complete only when:

```text
[ ] No production files remain under backend/game_sdk.
[ ] Debt logic lives under backend/app/games/zone_stalkers/economy/.
[ ] Trader survival credit does not require or subtract trader cash.
[ ] Debt contract has principal, outstanding principal, accrued interest, daily rate, due turn, status.
[ ] Interest accrues by turn.
[ ] Repayment pays interest first.
[ ] Survival loan eligibility uses required_price, not fixed money < 15.
[ ] Loan amount is required_price - current_money.
[ ] Failed loan does not advance into trade_buy_item.
[ ] Medical loan path buys and consumes/heals.
[ ] Successful sales can auto-repay debt while preserving reserve money.
[ ] Debt events are routed through memory policy.
[ ] Debt summaries appear in debug/export.
[ ] Focused poor-at-trader E2E passes.
[ ] `trade_sell_failed:no_items_sold` is reduced in focused simulation.
[ ] Full backend non-e2e suite passes.
```

---

# 15. Validation commands

Run:

```bash
pytest backend/tests/decision/v3/test_debt_mechanics.py -q
pytest backend/tests/decision/v3/test_survival_loan_planner.py -q
pytest backend/tests/decision/v3/test_survival_loan_executor.py -q
pytest backend/tests/decision/v3/test_survival_loan_e2e.py -q
pytest backend/tests/decision/v3/test_trade_sell_plan_failure.py -q
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_observation_memory_cutover.py -q
pytest backend/tests -k "not e2e" -q
cd frontend && npm run build
```

---

# 16. Suggested Copilot task

Use this as the direct task instruction:

```text
Rework branch copilot/add-debt-credit-mechanics.

Delete backend/game_sdk/* and move debt/credit mechanics into Zone Stalkers domain code.

Implement debts in backend/app/games/zone_stalkers/economy/debts.py.

Integrate request_loan plan step into existing Zone Stalkers planner/executor/runtime.

Trader survival credit must be store credit:
- does not require trader cash;
- does not subtract trader money;
- increments accounts_receivable;
- creates a debt contract with daily interest.

Fix planner so poor critical food/drink/medical agents use:
request_loan → trade_buy_item → consume_item
instead of impossible trade_sell_item.

Add interest, due turns, overdue/defaulted statuses, repayment after successful sales, memory policy, debug/export summaries, and focused integration/E2E tests.

Do not leave production debt code outside backend/app/games/zone_stalkers/.
```
