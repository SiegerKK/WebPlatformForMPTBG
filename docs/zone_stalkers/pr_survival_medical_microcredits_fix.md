# PR: Fix Survival Medical Microcredits and Credit-to-Purchase Consistency

## Problem

Logs showed repeated tiny `survival_medical` credits, often around `10 RU`, without immediately resolving the medical need:

```text
debt_credit_advanced purpose=survival_medical amount=10
debt_credit_advanced purpose=survival_medical amount=10
debt_credit_advanced purpose=survival_medical amount=10
```

This is incorrect.

For survival credit, the loan must be sized to make the next planned purchase possible. If the plan is:

```text
request_loan -> trade_buy_item(medical, survival_cheapest) -> consume_item
```

then the requested credit must cover the actual trader buy price of the cheapest viable medical item, minus the agent's current money.

Example:

```text
bandage base value = 50
trader buy price = int(50 * 1.5) = 75
agent money = 0
required survival credit = 75
```

A `10 RU` credit cannot buy the bandage, so it creates a loop:

```text
small loan -> still cannot buy -> trade_buy_failed -> replan -> small loan again
```

The PR must ensure survival credits are **purchase-sized**, **single-step useful**, and **not repeated as micro-advances**.

---

## Relevant current behavior

### Current affordability source

`evaluate_affordability(...)` computes the cheapest viable trader item and its buy price:

```python
required = int(cheapest["buy_price"])
missing = max(0, required - money)
```

This is the correct source of truth for survival credit sizing.

### Current survival credit policy

Debt/credit allows survival categories only:

```python
SURVIVAL_CREDIT_ALLOWED_CATEGORIES = {"drink", "food", "medical"}
```

This is correct. Do not allow survival credit for:

```text
ammo
weapon
armor
get_rich
```

### Current debt account model

Debt is already aggregated by debtor-creditor pair. `advance_survival_credit(...)` uses `get_or_create_debt_account(...)`, so repeated advances to the same trader go into one account. This is correct, but it does not solve microcredits by itself.

The bug is not "many accounts"; the bug is "many insufficient advances".

---

# Goal

For all survival credit paths:

```text
drink / food / medical
```

the credit amount must be:

```python
max(0, required_price - agent_money)
```

where `required_price` is the trader buy price of the exact item that the next `trade_buy_item` step is expected to buy.

The resulting plan must be internally consistent:

```text
request_loan.amount + current_money >= trade_buy_item.required_price
trade_buy_item.required_price == price(consume_item.item_type)
```

---

# Required changes

## 1. Add a canonical survival purchase quote helper

Create a helper, preferably in:

```text
backend/app/games/zone_stalkers/decision/liquidity.py
```

or a new small module:

```text
backend/app/games/zone_stalkers/decision/survival_credit.py
```

Suggested API:

```python
@dataclass(frozen=True)
class SurvivalPurchaseQuote:
    category: str
    item_type: str
    item_name: str
    required_price: int
    current_money: int
    principal_needed: int
    compatible_item_types: tuple[str, ...]


def quote_survival_purchase(
    *,
    agent: dict[str, Any],
    category: str,
    compatible_item_types: set[str] | None = None,
) -> SurvivalPurchaseQuote | None:
    ...
```

Implementation should delegate to `evaluate_affordability(...)`:

```python
afford = evaluate_affordability(
    agent=agent,
    trader={},
    category=category,
    compatible_item_types=compatible_item_types,
)

if afford.required_price is None or afford.cheapest_viable_item_type is None:
    return None

required_price = int(afford.required_price)
current_money = int(agent.get("money") or 0)
principal_needed = max(0, required_price - current_money)
```

Do not compute credit amount from:
- HP deficit;
- hunger/thirst severity;
- arbitrary minimum principal;
- old `SURVIVAL_LOAN_MIN_PRINCIPAL`;
- hardcoded `10`;
- hardcoded `45`.

The quote must be the only source for `request_loan.amount` in survival purchase plans.

---

## 2. Replace all survival loan amount calculations in planner

Search in:

```text
backend/app/games/zone_stalkers/decision/planner.py
```

for logic like:

```python
required_price = ...
loan_amount = ...
_build_survival_loan_payload(...)
```

All survival paths should use the new quote helper.

### Immediate survival path

For:

```text
immediate_need.trigger_context in ("survival", "healing")
```

when no inventory item exists and trader is local:

```text
request_loan -> trade_buy_item -> consume_item
```

must use:

```python
quote.required_price
quote.principal_needed
quote.item_type
```

Expected plan payload:

```python
PlanStep(
    STEP_REQUEST_LOAN,
    {
        "item_category": category,
        "required_price": quote.required_price,
        "amount": quote.principal_needed,
        "purpose": f"survival_{category}",
    },
)

PlanStep(
    STEP_TRADE_BUY_ITEM,
    {
        "item_category": category,
        "reason": f"buy_{category}_survival",
        "buy_mode": "survival_cheapest",
        "compatible_item_types": list(quote.compatible_item_types),
        "required_price": quote.required_price,
        "expected_item_type": quote.item_type,
    },
)

PlanStep(
    STEP_CONSUME_ITEM,
    {
        "item_type": quote.item_type,
        "reason": f"emergency_{category}",
    },
)
```

### Remote trader survival path

For:

```text
travel_to_trader -> request_loan -> trade_buy_item -> consume_item
```

use the same quote. The quote is based on infinite trader stock catalogue, so it does not need the trader object unless later trader inventory becomes finite.

### Soft restore at local trader

For local soft-but-actionable restore:

```text
RESTORE_FOOD / RESTORE_WATER / HEAL_SELF
```

if survival credit is allowed, use the same quote. Do not create a small placeholder loan.

---

## 3. Make `_build_survival_loan_payload` validate amount vs required price

In planner, update `_build_survival_loan_payload(...)` or its caller.

Invariant:

```python
amount >= max(0, required_price - agent_money)
```

If caller passes a smaller amount, auto-correct upward in production:

```python
principal_needed = max(0, int(required_price) - int(agent.get("money") or 0))
amount = max(int(amount), principal_needed)
```

Add payload fields:

```python
{
    "required_price": required_price,
    "principal_needed": principal_needed,
    "amount": amount,
    "survival_credit_quote_item_type": item_type,
    "survival_credit_sized_to_purchase": True,
}
```

Acceptance:

```text
No survival request_loan payload may have amount < required_price - current_money.
```

---

## 4. Add executor-side defensive validation

In:

```text
backend/app/games/zone_stalkers/decision/executors.py
```

for `STEP_REQUEST_LOAN` execution:

Before calling `advance_survival_credit(...)`, validate:

```python
required_price = int(step.payload.get("required_price") or 0)
current_money = int(agent.get("money") or 0)
principal_needed = max(0, required_price - current_money)
amount = int(step.payload.get("amount") or 0)
```

If:

```python
amount < principal_needed
```

auto-correct:

```python
amount = principal_needed
step.payload["amount"] = amount
step.payload["amount_corrected_to_required_price"] = True
```

Then proceed.

Also emit event/memory payload fields:

```python
"required_price": required_price,
"principal_needed": principal_needed,
"amount": amount,
"item_category": category,
```

This prevents stale or legacy planner payloads from creating tiny unusable credits.

---

## 5. Add post-loan purchase consistency guard

In `STEP_TRADE_BUY_ITEM` execution for:

```python
buy_mode == "survival_cheapest"
```

if the buy fails with `not_enough_money`, include a strong diagnostic payload:

```python
{
    "event_type": "trade_buy_failed",
    "payload": {
        "reason": "not_enough_money",
        "item_category": category,
        "buy_mode": "survival_cheapest",
        "required_price": required_price_from_payload,
        "agent_money": agent_money,
        "compatible_item_types": [...],
        "expected_item_type": expected_item_type,
        "previous_step_was_survival_credit": True or False,
    }
}
```

Then active plan runtime should abort/replan, not retry forever.

---

## 6. Add anti-microcredit guard per debtor/category

Even with corrected quote sizing, add a safety guard in debt code or planner:

If the agent received survival credit for the same category recently, but still cannot afford the expected item, do not issue another tiny credit. Rebuild the full quote.

Suggested helper:

```python
def get_recent_survival_credit_advance(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    purpose: str,
    world_turn: int,
    within_turns: int = 30,
) -> dict[str, Any] | None:
    ...
```

Use account metadata if available:

```python
account["last_advanced_turn"]
account["purposes"]
```

If recent same-purpose credit exists and agent still cannot afford the same item:

```text
recalculate principal_needed from required_price and current money
issue only the missing top-up amount if > 0
never issue a constant micro amount
```

Do not block survival credit entirely; survival credit is intentionally unlimited. The guard is only against repeated insufficient sizing.

---

## 7. Improve debt advance logging

Every `debt_credit_advanced` event/memory should include:

```python
{
    "amount": amount,
    "purpose": purpose,
    "item_category": item_category,
    "required_price": required_price,
    "principal_needed": principal_needed,
    "agent_money_before": money_before,
    "agent_money_after": money_after,
    "expected_item_type": item_type,
    "credit_sized_to_purchase": True,
    "account_id": account_id,
    "new_total": outstanding_total,
}
```

This makes future log analysis obvious:

```text
survival_medical amount=75 expected_item_type=bandage required_price=75
```

instead of:

```text
survival_medical amount=10
```

---

# Tests

## 1. Planner tests

File:

```text
backend/tests/decision/v3/test_survival_loan_planner.py
```

Add:

```python
def test_medical_survival_loan_amount_covers_cheapest_medical_buy_price():
    ...
```

Setup:

```text
agent hp low
money = 0
inventory = []
at trader
```

Expected:

```python
request_loan = plan.steps[0]
trade_buy = plan.steps[1]
consume = plan.steps[2]

assert request_loan.kind == STEP_REQUEST_LOAN
assert trade_buy.kind == STEP_TRADE_BUY_ITEM
assert consume.kind == STEP_CONSUME_ITEM

assert request_loan.payload["item_category"] == "medical"
assert request_loan.payload["required_price"] == expected_price
assert request_loan.payload["amount"] == expected_price

assert trade_buy.payload["required_price"] == expected_price
assert consume.payload["item_type"] == "bandage"
```

Compute expected price from catalogue:

```python
expected_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
```

Add:

```python
def test_survival_loan_amount_is_missing_money_not_constant():
    # money=25, bandage price=75 -> loan amount=50
```

Add equivalent tests for:

```text
drink: water price
food: bread price
medical: bandage price
```

## 2. Executor tests

File:

```text
backend/tests/decision/v3/test_survival_loan_executor.py
```

Add:

```python
def test_request_loan_autocorrects_too_small_medical_amount_to_required_price():
    ...
```

Setup a `STEP_REQUEST_LOAN` with:

```python
amount = 10
required_price = bandage_price
agent.money = 0
item_category = "medical"
```

Expected:

```python
debt_credit_advanced amount == bandage_price
agent.money == bandage_price
```

Add:

```python
def test_request_loan_partial_money_requests_only_missing_amount():
    # agent.money=25, required_price=75, amount stale=10
    # corrected amount=50
```

## 3. Full plan execution tests

Add:

```python
def test_request_loan_then_trade_buy_then_consume_medical_no_microcredit_loop():
    ...
```

Expected:

```text
plan completes
one debt_credit_advanced
one trade_buy
one consume_heal
no repeated debt_credit_advanced
no trade_buy_failed
```

Use bounded loop helper:

```python
_run_plan_until_complete(..., max_steps=5)
```

Never use:

```python
while not plan.is_complete:
```

without a max step cap.

## 4. Negative regression test

Add:

```python
def test_tiny_medical_loan_payload_does_not_create_tiny_credit():
    ...
```

Expected:

```text
no debt_credit_advanced amount=10 for medical if required_price=75
```

## 5. Simulation-level test

Add a small tick-level regression if cheap enough:

```python
def test_injured_poor_agent_at_trader_takes_single_medical_credit_and_heals():
    ...
```

Run 5-10 ticks.

Expected:

```text
debt_credit_advanced count for survival_medical <= 1
agent hp improves
no repeated survival_medical microcredits
```

---

# Acceptance criteria

```text
[ ] No survival_medical credit amount is less than the missing price of the planned medical item.
[ ] request_loan -> trade_buy_item -> consume_item medical plans complete with one credit advance.
[ ] A stale amount=10 medical loan payload is auto-corrected or fails without creating a 10 RU credit.
[ ] Logs include required_price, principal_needed, expected_item_type and credit_sized_to_purchase.
[ ] Food/drink survival credit behavior remains unchanged except for the same consistency guarantees.
[ ] Ammo/weapon/armor/get_rich still cannot use survival credit.
[ ] No unbounded while not plan.is_complete loops remain in survival loan tests.
```

---

# Focused commands

```bash
PYTHONPATH=backend pytest backend/tests/decision/v3/test_survival_loan_planner.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_survival_loan_executor.py -vv --tb=short
PYTHONPATH=backend pytest backend/tests/decision/v3/test_hunt_prep_trade_buy_integration.py -vv --tb=short
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

# Expected log change

Before:

```text
debt_credit_advanced survival_medical amount=10
debt_credit_advanced survival_medical amount=10
debt_credit_advanced survival_medical amount=10
```

After:

```text
debt_credit_advanced survival_medical amount=75 required_price=75 expected_item_type=bandage
trade_buy medical item_type=bandage price=75
consume_heal bandage
```

If agent already has some money:

```text
agent_money_before=25
required_price=75
principal_needed=50
debt_credit_advanced amount=50
```

---

# Notes

Do not solve this by blocking medical survival credit after one attempt.

Survival credit is intentionally unlimited. The correct fix is:

```text
credit amount must be useful and purchase-sized
```

not:

```text
credit is limited or denied
```

The NPC should still be able to borrow repeatedly over time for survival, but each credit advance should correspond to a concrete purchasable item.
