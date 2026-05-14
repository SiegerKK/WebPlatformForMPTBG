# PR: Rework Debt/Credit Mechanics v2 — Unlimited Survival Credit, Partial Repayment Objective, Daily +20% Rollover

## Context

The current debt implementation uses many small `DebtContract` records with 5% daily interest, overdue/defaulted states, active-debt limits, and repayment mostly attached to successful `trade_sell_item`.

Latest log analysis showed:

```text
agent_debug_39 was manually given 47k RU
agent_debug_39 had outstanding debts
agent_debug_39 did not repay anything
```

Reason:

```text
repayment currently runs after successful item sale,
but not as an independent objective,
not on trader contact,
and not simply because the debtor has money.
```

This means the current system can save NPCs from starvation, but does not create a believable debtor behavior loop.

We need to rework debts into a simpler, stronger, more gameable system.

---

# 1. New design

## Core rules

```text
1. Survival credit is unlimited.
2. Debt is aggregated as one account per debtor-creditor pair.
3. Each day, the remaining unpaid amount grows by 20%.
4. NPCs should repay debt as an important standalone task.
5. NPCs may repay partially if they cannot repay everything.
6. Partial repayment reduces the amount that will grow tomorrow.
7. If total debt reaches 5000 RU, the NPC should flee the Zone from debt.
```

This replaces the previous "full repayment only" proposal.

Correct behavior:

```text
If NPC owes 1000 RU and pays 300 RU today,
then only remaining 700 RU grows by 20% tomorrow.
```

So next day:

```text
remaining 700 → 840
```

not:

```text
1000 → 1200
```

Partial repayment matters and must be encouraged.

---

# 2. Required architecture

All production code must stay inside Zone Stalkers:

```text
backend/app/games/zone_stalkers/
```

Do not add production debt code to:

```text
backend/game_sdk/
```

Debt code belongs in:

```text
backend/app/games/zone_stalkers/economy/debts.py
```

Planner/executor integration belongs in existing Zone Stalkers modules:

```text
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/decision/models/plan.py
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/projections.py
backend/app/games/zone_stalkers/router.py
```

---

# 3. Debt ledger v2

## 3.1 State shape

Replace many small debts with one account per creditor:

```python
state["debt_ledger"] = {
    "version": 2,
    "accounts": {
        account_id: DebtAccount,
    },
    "by_debtor": {
        debtor_id: [account_id, ...],
    },
    "by_creditor": {
        creditor_id: [account_id, ...],
    },
    "by_pair": {
        f"{debtor_id}:{creditor_id}": account_id,
    },
}
```

## 3.2 DebtAccount schema

```python
DebtAccount = {
    "id": "debt_account_<uuid>",

    "debtor_id": "agent_debug_39",
    "debtor_type": "agent",

    "creditor_id": "trader_debug_0",
    "creditor_type": "trader",

    # One aggregated amount owed to this creditor.
    "outstanding_total": 476,

    # Bookkeeping.
    "principal_advanced_total": 476,
    "repaid_total": 0,
    "rollover_added_total": 0,

    # Daily rollover.
    "daily_rollover_rate": 0.20,
    "created_turn": 4330,
    "last_advanced_turn": 6359,
    "last_payment_turn": None,
    "last_rollover_turn": None,
    "next_due_turn": 5770,
    "rollover_count": 0,

    # Status.
    "status": "active",  # active | repaid | escaped

    # Purpose statistics.
    "purposes": {
        "survival_drink": 5,
        "survival_food": 4,
        "survival_medical": 1,
    },

    "created_location_id": "loc_debug_61",
    "source": "trader_survival_credit",
    "notes": {},
}
```

No `overdue` or `defaulted` is required for survival credit.

Instead:

```text
due missed → rollover +20%
debt >= 5000 → flee Zone
```

---

# 4. Constants

In `economy/debts.py`:

```python
SURVIVAL_CREDIT_ROLLOVER_RATE = 0.20
SURVIVAL_CREDIT_ROLLOVER_TURNS = 1440

DEBT_ESCAPE_THRESHOLD = 5000

DEBT_REPAYMENT_MIN_PAYMENT = 10
DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE = 120
DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE = 60
DEBT_REPAYMENT_URGENT_DUE_WITHIN_TURNS = 180

SURVIVAL_CREDIT_ALLOWED_CATEGORIES = frozenset({
    "drink",
    "food",
    "medical",
})
```

Remove or stop using these as blockers for survival credit:

```python
SURVIVAL_LOAN_MAX_ACTIVE_TOTAL
SURVIVAL_LOAN_MAX_PRINCIPAL
has_defaulted_debt blocker
active debt cap blocker
```

Survival credit should be unlimited.

---

# 5. Debt account API

## 5.1 `ensure_debt_ledger`

```python
def ensure_debt_ledger(state: dict[str, Any], *, world_turn: int | None = None) -> dict[str, Any]:
    ...
```

Must:
- create ledger if missing;
- create v2 indexes;
- migrate v1 `debts` to v2 `accounts`.

## 5.2 Migration from current v1 debts

Current saves may have:

```python
state["debt_ledger"]["debts"]
```

Migrate by debtor-creditor pair:

```text
group old debts by debtor_id + creditor_id
sum outstanding_principal + accrued_interest into one outstanding_total
sum principal into principal_advanced_total
sum total_repaid into repaid_total
status active if outstanding_total > 0 else repaid
next_due_turn = min(due_turn of active debts) if available else world_turn + 1440
preserve old rows under ledger["legacy_debts"] for debug if desired
ledger.version = 2
```

Do not lose current debt state.

## 5.3 `get_or_create_debt_account`

```python
def get_or_create_debt_account(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    debtor_type: str = "agent",
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Rules:
- same debtor-creditor pair must reuse same active account;
- if old account was repaid and new credit is issued, it may be reopened or replaced;
- account count must stay bounded by debtor-creditor pairs, not by purchases.

## 5.4 `advance_survival_credit`

```python
def advance_survival_credit(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    amount: int,
    purpose: str,
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Behavior:
- apply due rollovers first;
- get debtor-creditor account;
- `outstanding_total += amount`;
- `principal_advanced_total += amount`;
- `last_advanced_turn = world_turn`;
- if account has no `next_due_turn`, set it to `world_turn + 1440`;
- increment `purposes[purpose]`.

For trader credit:
- debtor gets temporary purchasing power;
- trader money is **not** reduced;
- trader `accounts_receivable` increases by amount.

## 5.5 `apply_due_rollovers`

```python
def apply_due_rollovers(
    *,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    ...
```

For each active account:

```python
while world_turn >= account["next_due_turn"] and account["outstanding_total"] > 0:
    added = ceil(account["outstanding_total"] * 0.20)
    account["outstanding_total"] += added
    account["rollover_added_total"] += added
    account["rollover_count"] += 1
    account["last_rollover_turn"] = account["next_due_turn"]
    account["next_due_turn"] += 1440
```

Emit one event per rollover:

```python
{
    "event_type": "debt_rolled_over",
    "payload": {
        "account_id": account_id,
        "debtor_id": debtor_id,
        "creditor_id": creditor_id,
        "added": added,
        "new_total": account["outstanding_total"],
        "rollover_count": account["rollover_count"],
    },
}
```

## 5.6 `repay_debt_account`

Partial repayment is allowed.

```python
def repay_debt_account(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    account_id: str,
    amount: int,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Rules:
- apply due rollovers first;
- payment amount cannot exceed debtor money;
- payment amount cannot exceed account outstanding;
- payment reduces `outstanding_total`;
- `repaid_total += paid`;
- `last_payment_turn = world_turn`;
- creditor money increases by paid amount;
- trader `accounts_receivable` decreases by paid amount;
- if `outstanding_total <= 0`, account status becomes `repaid`.

Return:

```python
{
    "status": "ok",
    "paid": paid,
    "remaining_total": account["outstanding_total"],
    "fully_repaid": account["status"] == "repaid",
}
```

## 5.7 `choose_debt_repayment_amount`

NPC must decide how much to pay.

```python
def choose_debt_repayment_amount(
    *,
    debtor: dict[str, Any],
    account: dict[str, Any],
    world_turn: int,
    critical_needs: bool,
) -> int:
    ...
```

Suggested policy:

```text
if debtor money <= minimum reserve:
    pay 0

if critical survival needs:
    keep larger reserve

if due within 180 turns:
    pay more aggressively

if debt is above 5000 threshold:
    do not repay; debt escape should trigger instead

otherwise:
    pay a meaningful fraction of surplus, at least min payment
```

Concrete simple rule:

```python
money = int(debtor.get("money") or 0)
reserve = DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE if critical_needs else DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE
surplus = max(0, money - reserve)

if surplus < DEBT_REPAYMENT_MIN_PAYMENT:
    return 0

remaining = int(account["outstanding_total"])

if surplus >= remaining:
    return remaining

turns_to_due = int(account["next_due_turn"]) - world_turn

if turns_to_due <= DEBT_REPAYMENT_URGENT_DUE_WITHIN_TURNS:
    return min(remaining, surplus)

return min(remaining, max(DEBT_REPAYMENT_MIN_PAYMENT, surplus // 2))
```

This means:
- rich NPC repays fully;
- moderately rich NPC repays part;
- poor NPC keeps enough money to survive;
- near due date, NPC pays more aggressively to reduce tomorrow's +20%.

## 5.8 `repay_debts_to_creditor_if_useful`

```python
def repay_debts_to_creditor_if_useful(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    world_turn: int,
    critical_needs: bool = False,
) -> list[dict[str, Any]]:
    ...
```

Used when debtor is co-located with creditor.

Behavior:
- find account by debtor-creditor pair;
- choose amount;
- if amount > 0, call `repay_debt_account`;
- emit `debt_payment`;
- emit `debt_repaid` if fully repaid.

## 5.9 `get_debtor_debt_total`

```python
def get_debtor_debt_total(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> int:
    ...
```

Apply rollovers before calculating total.

## 5.10 `should_escape_zone_due_to_debt`

```python
def should_escape_zone_due_to_debt(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> bool:
    return get_debtor_debt_total(...) >= DEBT_ESCAPE_THRESHOLD
```

---

# 6. Survival credit eligibility

Survival credit is unlimited.

```python
def can_request_survival_credit(...):
    if item_category not in {"drink", "food", "medical"}:
        return False, "unsupported_item_category"

    if required_price <= debtor.money:
        return False, "already_affordable"

    if debtor is dead:
        return False, "debtor_not_alive"

    if creditor missing/dead:
        return False, "creditor_not_available"

    return True, "ok"
```

Do not block on:
- defaulted debt;
- overdue debt;
- debt total;
- active account count;
- active debt cap.

If debt is huge, survival credit may still be issued until the NPC leaves the Zone. The strategic response to huge debt is escape, not starving at the trader.

---

# 7. Repayment as separate important task

## 7.1 New objective

Add a dedicated objective:

```text
REPAY_DEBT
```

or:

```text
REDUCE_DEBT
```

Use one name consistently.

## 7.2 When objective should appear

Add it when:

```text
debt_total > 0
and NPC has or can reach a creditor
and NPC has money above reserve OR debt is near rollover due
```

Priority:

```text
critical survival > emission survival > debt escape >= critical combat safety > repay debt > resupply > get_rich
```

Debt repayment should be higher than normal `get_rich` and normal `resupply`, but lower than immediate survival.

## 7.3 Objective score factors

Score should increase with:

```text
debt_total
days/turns until next rollover
rollover_count
money surplus
creditor co-located
current goal is not critical survival
```

Score should decrease with:
```text
critical thirst/hunger/health
no known route to creditor
not enough money to make useful payment
```

## 7.4 Plan for co-located creditor

If debtor and creditor are at same location:

```text
request_debt_repayment / repay_debt
```

One-step plan.

## 7.5 Plan for remote creditor

If creditor location known and travel safe:

```text
travel_to_location → repay_debt
```

Do not travel if the NPC is near death from thirst/hunger.

## 7.6 New plan step

Add:

```python
STEP_REPAY_DEBT = "repay_debt"
```

Payload:

```python
{
    "creditor_id": "trader_debug_0",
    "creditor_type": "trader",
    "account_id": "debt_account_...",
    "reason": "reduce_debt_before_rollover",
    "allow_partial": True,
}
```

Executor computes amount at execution time via `choose_debt_repayment_amount`.

---

# 8. Repayment hooks outside objective

The standalone objective is required, but we should also repay opportunistically.

Add repayment hooks:

```text
1. before any trader buy interaction;
2. before any trader sell interaction;
3. after successful sale;
4. when arriving at creditor location;
5. once per tick if debtor and creditor are co-located and debtor has surplus money.
```

This directly fixes `agent_debug_39`:

```text
agent_debug_39 has 47k RU
agent_debug_39 is at trader or interacts with trader
→ chooses repayment amount
→ pays debt
```

Do not rely only on artifact sale.

## Important

The hook must allow **partial repayment**.

If debtor has only 200 RU and owes 600 RU:
- pay something if safe;
- reduce remaining debt;
- next +20% applies only to remaining amount.

---

# 9. Debt escape behavior

If total debt reaches or exceeds:

```python
DEBT_ESCAPE_THRESHOLD = 5000
```

NPC should try to leave the Zone.

## 9.1 Objective

Add:

```text
LEAVE_ZONE_FROM_DEBT
```

or use existing `LEAVE_ZONE` with reason:

```text
debt_escape_threshold
```

## 9.2 Priority

```text
critical immediate survival > leave zone from debt > repay debt > normal economy
```

Rationale:
- if dying of thirst now, drink first;
- if alive but debt >=5000, leave;
- do not continue artifact economy indefinitely.

## 9.3 Memory event

Emit once per NPC:

```text
debt_escape_triggered
```

Payload:

```python
{
    "debtor_id": agent_id,
    "debt_total": debt_total,
    "threshold": 5000,
}
```

---

# 10. Executor changes

Modify:

```text
backend/app/games/zone_stalkers/decision/executors.py
```

## 10.1 `request_loan`

Rename internally or semantically shift to credit advance.

Current `STEP_REQUEST_LOAN` may remain for compatibility, but it should call:

```python
advance_survival_credit(...)
```

and should add to one account, not create one debt record per purchase.

Emit:

```text
debt_credit_advanced
```

or keep `debt_created` for compatibility with updated payload.

Required payload:

```python
{
    "account_id": account["id"],
    "debtor_id": agent_id,
    "creditor_id": creditor_id,
    "amount": amount,
    "new_total": account["outstanding_total"],
    "purpose": purpose,
}
```

## 10.2 `repay_debt`

Implement executor for:

```python
STEP_REPAY_DEBT
```

Flow:

```text
resolve creditor
resolve account
choose payment amount
if amount <= 0:
    mark step failed or no-op with reason no_safe_repayment_amount
else:
    repay_debt_account
    emit debt_payment / debt_repaid
    advance plan
```

## 10.3 Hook into trader interactions

Before `trade_buy_item`:
```text
if debtor has debt to trader and money surplus:
    repay useful amount first
```

After `trade_sell_item`:
```text
after sale money is added:
    repay useful amount
```

On `travel_arrived` or location arrival:
```text
if arrived at creditor location:
    repay useful amount
```

If implementing all hooks is too large, minimum acceptance:
- standalone `REPAY_DEBT` objective;
- pre-trade interaction repayment;
- post-sale repayment.

---

# 11. Memory/event policy

Update:

```text
backend/app/games/zone_stalkers/memory/memory_events.py
```

Events:

```python
"debt_credit_advanced": "memory",
"debt_payment": "memory",
"debt_repaid": "memory",
"debt_rolled_over": "memory",
"debt_escape_triggered": "memory_critical",
```

Do not write memory every tick.

`debt_rolled_over` is at most once per day per account.

---

# 12. Export/debug/UI

Update:

```text
backend/app/games/zone_stalkers/projections.py
backend/app/games/zone_stalkers/router.py
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

Per-agent `economic_state` should include:

```python
{
    "debt_total": 551,
    "active_debt_account_count": 1,
    "creditors": ["trader_debug_0"],
    "max_creditor_debt": 551,
    "rollover_count_total": 3,
    "next_due_turn_min": 8651,
    "debt_escape_threshold": 5000,
    "should_escape_zone_due_to_debt": False,
}
```

Also include enough account details for debug profile:

```python
"debt_accounts": [
    {
        "creditor_id": "trader_debug_0",
        "outstanding_total": 551,
        "next_due_turn": 8651,
        "rollover_count": 3,
        "last_payment_turn": 7900,
    }
]
```

NPC profile modal should show:

```text
💳 Долги
Всего долг: X RU
Кредиторы: trader_debug_0
Следующий рост: turn N
Ростов долга: K
Побег от долгов: yes/no
```

Summary export should include:

```python
"debt_summary": {
    "active_accounts": 17,
    "total_outstanding": 5517,
    "survival_credit_advances": 200,
    "debt_payments": 34,
    "accounts_repaid": 3,
    "rollovers_total": 134,
    "accounts_over_escape_threshold": 0,
    "debt_escape_triggered_count": 0,
}
```

---

# 13. Tests

## 13.1 Debt account mechanics

```python
def test_survival_credit_uses_single_account_per_creditor(): ...
def test_multiple_survival_advances_accumulate_in_one_account(): ...
def test_daily_rollover_adds_20_percent_to_remaining_total(): ...
def test_partial_payment_reduces_next_rollover_base(): ...
def test_multiple_rollovers_compound_remaining_total(): ...
def test_survival_credit_has_no_debt_cap_or_default_blocker(): ...
def test_debt_escape_threshold_at_5000(): ...
```

## 13.2 Partial repayment

```python
def test_partial_repayment_reduces_outstanding_total(): ...
def test_partial_repayment_emits_debt_payment_not_debt_repaid(): ...
def test_full_repayment_marks_account_repaid(): ...
def test_repayment_amount_keeps_survival_reserve_when_needs_are_critical(): ...
def test_near_due_repayment_pays_more_aggressively(): ...
```

## 13.3 Agent 39 regression

```python
def test_debtor_with_enough_money_repays_on_trader_interaction(): ...
```

Scenario:

```text
agent has debt account to trader
agent.money = 47435
agent is co-located with trader
agent performs trade_buy_item or any trader interaction
```

Expected:

```text
debt_payment emitted
debt_repaid emitted if amount covers all debt
account.outstanding_total == 0
agent.money decreased
```

## 13.4 Standalone repay objective

```python
def test_repay_debt_objective_selected_when_debtor_has_surplus_money(): ...
def test_repay_debt_objective_travels_to_known_creditor_when_safe(): ...
def test_repay_debt_objective_lower_priority_than_critical_thirst(): ...
def test_repay_debt_objective_higher_priority_than_get_rich(): ...
```

## 13.5 Debt escape

```python
def test_debt_total_above_5000_selects_leave_zone_from_debt(): ...
def test_debt_escape_priority_below_critical_survival(): ...
def test_debt_escape_event_written_once(): ...
```

## 13.6 No exploit credit

```python
def test_no_credit_for_ammo_weapon_armor(): ...
def test_no_credit_for_get_rich(): ...
def test_no_credit_for_stock_resupply(): ...
```

---

# 14. Acceptance criteria

This PR is complete when:

```text
[ ] Survival credit is unlimited for immediate drink/food/medical.
[ ] Debt is one account per debtor-creditor pair.
[ ] New credit advances add to the same account.
[ ] Daily rollover increases only remaining unpaid amount by 20%.
[ ] Partial repayments are allowed.
[ ] NPC has standalone REPAY_DEBT / REDUCE_DEBT objective.
[ ] NPC chooses repayment amount based on money, needs, due time, and reserve.
[ ] Repayment is not limited to artifact sale.
[ ] Debtor with enough money repays during trader interaction.
[ ] Agent_debug_39 regression is covered.
[ ] Debt >= 5000 triggers leave-zone-from-debt behavior.
[ ] Survival needs can still override debt escape if immediately critical.
[ ] No survival credit is used for weapons/ammo/armor/get_rich/resupply stock.
[ ] v1 debt ledger migrates safely to v2 accounts.
[ ] Export/profile show account-based debt status.
```

---

# 15. Validation commands

```bash
pytest backend/tests/decision/v3/test_debt_mechanics.py -q
pytest backend/tests/decision/v3/test_survival_loan_planner.py -q
pytest backend/tests/decision/v3/test_survival_loan_executor.py -q
pytest backend/tests/decision/v3/test_survival_loan_e2e.py -q
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests -k "not e2e" -q
cd frontend && npm run build
```

---

# 16. Expected long-run outcome

After this PR, logs should show:

```text
survival deaths remain low
survival credit continues even for high-debt NPCs
debt accounts count remains bounded
debt_payment events appear outside artifact sales
agent_debug_39-like cases repay immediately when rich
partial payments reduce next rollover base
total debt can grow if NPCs keep borrowing and do not earn
NPCs with debt >= 5000 flee the Zone
```

The intended loop becomes:

```text
poor NPC needs water
→ trader gives survival credit
→ debt account grows if unpaid
→ NPC later gets money
→ NPC chooses partial/full repayment as important task
→ if debt spirals to 5000, NPC runs from the Zone
```
