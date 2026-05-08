# NPC Brain v3 — Addendum for PR 2 and PR 3 contracts

> Назначение: единый документ-дополнение к:
>
> - `docs/npc_brain_v3_pr2_revised_needs_liquidity_contract.md`
> - `docs/npc_brain_v3_pr3_memory_belief_contract.md`
>
> Цель: зафиксировать уточнения после финализации PR 1, где были добавлены:
>
> - `PlanMonitor`;
> - `brain_trace`;
> - 30-минутные эффекты сна;
> - динамическая длительность сна;
> - досрочное пробуждение при `sleepiness == 0`;
> - survival-safe rest;
> - подготовка перед сном через `prepare_sleep_food` / `prepare_sleep_drink`.

---

## 1. Короткое резюме

Документы PR 2 и PR 3 в целом правильные, но перед реализацией нужно внести несколько уточнений.

Главные изменения:

```text
PR 2:
  - добавить ImmediateNeed.trigger_context;
  - разделить global survival needs и rest-preparation needs;
  - выбирать ItemNeed по score/urgency, а не по старому фиксированному порядку;
  - добавить post-PR1 regression case;
  - запретить unaffordable buy loop;
  - добавить NeedEvaluationResult;
  - явно сохранить PR1 sleep semantics.

PR 3:
  - добавить sleep_completed / sleep_interrupted mapping;
  - не превращать каждый sleep_interval_applied в MemoryRecord;
  - сохранить merge-семантику старой памяти;
  - опционально добавить world_time и memory_used.used_for.
```

---

# Part A — Updates for PR 2 contract

---

## A1. Add `ImmediateNeed.trigger_context`

Текущий PR 2-документ правильно вводит `ImmediateNeed`, но нужно уточнить контекст срабатывания.

Не все immediate needs должны глобально перебивать intent selection.

Пример:

```text
thirst = 80
→ это survival immediate need
→ должно выбрать seek_water

thirst = 72
→ это не критическая жажда
→ не должно глобально перебивать все цели
→ но если NPC собирается спать, нужно сначала выпить
```

### Обновить модель `ImmediateNeed`

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImmediateNeed:
    key: str                         # drink_now, eat_now, heal_now
    urgency: float                   # 0..1
    current_value: float             # thirst/hunger/hp
    threshold: float

    # New:
    trigger_context: str = "survival"  # survival | rest_preparation | healing
    blocks_intents: frozenset[str] = field(default_factory=frozenset)

    available_inventory_item_types: frozenset[str] = field(default_factory=frozenset)
    selected_item_id: str | None = None
    selected_item_type: str | None = None
    reason: str = ""
    source_factors: tuple[dict[str, Any], ...] = ()
```

### Semantics

```text
trigger_context = "survival":
  Глобально блокирующая срочная нужда.
  Пример: thirst >= CRITICAL_THIRST_THRESHOLD, hunger >= CRITICAL_HUNGER_THRESHOLD.

trigger_context = "rest_preparation":
  Нужда, которая блокирует только сон/отдых.
  Пример: thirst >= SLEEP_SAFE_THIRST_THRESHOLD перед сном, но ниже critical threshold.

trigger_context = "healing":
  Срочная нужда лечения.
```

---

## A2. Clarify immediate survival need vs rest-preparation need

Добавить в PR 2 раздел:

```markdown
## ImmediateNeed context rules

Critical immediate needs affect intent selection.

Rest-preparation immediate needs affect only rest planning.
```

### Examples

```text
thirst = 80:
  seek_water should beat rest/resupply/get_rich.

hunger = 86:
  seek_food should beat rest/resupply/get_rich.

thirst = 72:
  if NPC is planning to sleep and has water:
    _plan_rest should insert consume drink before sleep.
  But if NPC is not trying to sleep:
    thirst=72 should not globally override everything.
```

Это сохраняет исправление PR 1 и не превращает безопасную подготовку ко сну в чрезмерно агрессивный global interrupt.

---

## A3. Change ItemNeed selection from old fixed order to score-first

В текущем PR 2-документе есть риск сохранить старую resupply-очередь:

```text
food stock → drink stock → armor → weapon → ammo → medicine
```

После PR 1 это не должно быть абсолютным порядком.

Почему:

```text
ImmediateNeed уже отвечает за критический голод/жажду.
ItemNeed отвечает за запасы/снаряжение.
```

Если NPC стабилен:

```text
hunger = 50
thirst = 57
weapon = null
food stock = 1/2
```

то отсутствие оружия с urgency `0.65` должно победить food stock urgency `0.55`.

### Replace old priority semantics

Вместо фиксированной очереди использовать:

```python
candidate_item_needs = [
    n for n in item_needs
    if n.urgency > 0 and n.key != "upgrade"
]
candidate_item_needs.sort(key=lambda n: (-n.urgency, n.priority, n.key))
dominant = candidate_item_needs[0] if candidate_item_needs else None
```

### Priority is only tie-breaker

Рекомендуемые priority:

```python
ITEM_NEED_PRIORITY = {
    "armor": 10,
    "weapon": 20,
    "ammo": 30,
    "food": 40,
    "drink": 50,
    "medicine": 60,
    "upgrade": 90,
}
```

Это означает:

```text
weapon urgency 0.65 beats food urgency 0.55.
food can still win if its urgency is higher.
```

### Required invariant

```text
ImmediateNeed first.
Then ItemNeed by urgency score.
Priority is deterministic tie-breaker only.
```

---

## A4. Add `NeedEvaluationResult`

Чтобы не пересчитывать `ImmediateNeed` / `ItemNeed` в нескольких местах и не мутировать `ctx` неявно, добавить явный контейнер.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class NeedEvaluationResult:
    scores: NeedScores
    immediate_needs: tuple[ImmediateNeed, ...]
    item_needs: tuple[ItemNeed, ...]
    liquidity_summary: dict | None = None
```

### Transition-compatible API

Можно сохранить старый контракт:

```python
def evaluate_needs(ctx, state) -> NeedScores:
    result = evaluate_needs_v3(ctx, state)
    ctx.evaluated_needs_v3 = result
    return result.scores
```

А новый код может читать:

```python
result = getattr(ctx, "evaluated_needs_v3", None)
```

### Rule

```text
Planner and brain_trace should use the same NeedEvaluationResult.
Do not recompute immediate_needs/item_needs separately in planner.
```

---

## A5. Add `ItemNeed.affordability_hint`

`ItemNeed` и `AffordabilityResult` должны оставаться разными сущностями, но для debug/trace полезно связать need с ожидаемой доступностью покупки.

Добавить optional поля:

```python
@dataclass(frozen=True)
class ItemNeed:
    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    priority: int = 100
    source_factors: tuple[dict[str, Any], ...] = ()

    # New optional debug/planner hints:
    expected_min_price: int | None = None
    affordability_hint: str | None = None  # affordable | unaffordable | unknown
```

Example:

```text
weapon:
  urgency = 0.65
  expected_min_price = 250
  affordability_hint = unaffordable
```

Это не заменяет `AffordabilityResult`, а только помогает `brain_trace` и planner explanations.

---

## A6. Add invariant: no unaffordable buy loop

Добавить в PR 2 contract:

```text
Planner must not repeatedly create the same unaffordable buy plan without changing conditions.
```

Если:

```text
money < cheapest_viable_item_price
```

то следующий план должен быть одним из:

```text
sell safe item
earn money / get_rich fallback
search remembered item
wait only if no actionable alternative exists
```

Он не должен быть:

```text
trade_buy_item weapon
trade_buy_item weapon
trade_buy_item weapon
...
```

### Required test

```text
poor NPC + no weapon + trader nearby + no affordable weapon
→ planner does not repeatedly choose trade_buy_item weapon
→ planner chooses liquidity/fallback path
```

---

## A7. Clarify selling policy for last survival items

PR 2 already says not to sell last water/food at high need. Strengthen it:

```text
Never sell below desired survival reserve unless the sale itself is needed
to resolve a higher-priority immediate survival need.
```

### Safe to sell

```text
artifacts
duplicate items
ammo of incompatible caliber
consumables above desired reserve
```

### Risky

```text
expensive consumables if cheaper replacements exist
part of medicine if HP is high
```

### Emergency only

```text
last medkit while HP is normal, if hunger/thirst emergency requires money and no other liquidity exists
```

### Forbidden

```text
equipped weapon
equipped armor
last water while thirst high
last food while hunger high
last heal item while HP low
```

### Post-PR1 `Поцик 1` example

```text
bread = last food
hunger = 50
food stock likely below desired count
→ bread should not be sold for normal weapon liquidity.
```

---

## A8. Add post-PR1 canonical regression case

The PR 2 document already contains the old critical `Поцик 1` case:

```text
hunger = 86
thirst = 80
sleepiness = 98
money = 59
weapon = null
armor = leather_jacket
inventory = bread, glucose, energy_drink, water, bandage, medkit
```

Add the stabilized post-PR1 case:

```text
hunger = 50
thirst = 57
sleepiness = 46
money = 29
weapon = null
armor = leather_jacket
inventory = bread, bandage, medkit
scheduled_action = null
current_goal = resupply
```

Expected PR 2 behavior:

```text
1. No critical ImmediateNeed is active.
2. ItemNeed.weapon is active with urgency 0.65.
3. Food stock may be active, but should not beat weapon if its urgency is 0.55.
4. Planner tries to resolve weapon need.
5. If no affordable weapon exists:
     - do not repeatedly issue unaffordable trade_buy_item;
     - evaluate liquidity;
     - if safe sale is insufficient or forbidden, fallback to money/resource acquisition.
6. Bread should not be sold if it is the last food and food stock is below desired count,
   unless an emergency liquidity rule explicitly allows it.
7. Medkit should not be sold if HP is low.
   If HP is 100, it can be risky or emergency-only depending on policy.
```

---

## A9. Preserve PR 1 sleep semantics in PR 2

PR 2 may refactor rest planning to use `ImmediateNeed`, but it must not regress PR 1 sleep behavior.

Add this section:

```markdown
## Preserve PR 1 sleep behavior

PR 2 may replace local `_build_sleep_preparation_steps()` logic with ImmediateNeed-based logic,
but all PR 1 sleep tests must keep passing.
```

### Must preserve

```text
- sleep duration is derived from current sleepiness;
- sleep duration is capped by DEFAULT_SLEEP_HOURS;
- sleep applies effects every SLEEP_EFFECT_INTERVAL_TURNS;
- sleep ends early when sleepiness reaches 0;
- rest preparation steps may be inserted before sleep;
- prepare_sleep_food / prepare_sleep_drink action kinds remain correct;
- critical hunger/thirst still beat sleepiness.
```

### Dynamic sleep duration

PR 1 currently maps sleepiness to sleep duration:

```python
sleepiness = max(0, int(agent.get("sleepiness", 0)))
sleepiness_per_hour = max(1, math.ceil(100 / DEFAULT_SLEEP_HOURS))
estimated_hours = max(1, math.ceil(sleepiness / sleepiness_per_hour))
sleep_hours = min(DEFAULT_SLEEP_HOURS, estimated_hours)
```

If PR 2 replaces `_build_sleep_preparation_steps()`, it must keep equivalent logic.

Recommended helper:

```python
def estimate_sleep_hours(sleepiness: int, default_sleep_hours: int) -> int:
    sleepiness = max(0, int(sleepiness))
    sleepiness_per_hour = max(1, math.ceil(100 / default_sleep_hours))
    estimated_hours = max(1, math.ceil(sleepiness / sleepiness_per_hour))
    return min(default_sleep_hours, estimated_hours)
```

### Early wake-up

PR 1 also ends sleep early when `sleepiness` reaches `0`:

```text
if _process_sleep_tick sets wake_due_to_rested:
  scheduled sleep completes early
```

PR 2 must not remove or bypass this behavior.

---

## A10. Add PR 2 tests for sleep preservation

Add to PR 2 test plan:

```text
low sleepiness schedules shorter sleep than high sleepiness
sleep wakes early when sleepiness reaches 0
refactoring rest through ImmediateNeed keeps prepare_sleep_food/drink
critical hunger/thirst still beat sleepiness
PR 1 sleep_effects tests still pass
```

---

## A11. Add reason-code taxonomy

Add stable reason strings for planner/trace/memory consistency:

```text
immediate_drink_inventory
immediate_food_inventory
immediate_heal_inventory

prepare_sleep_drink
prepare_sleep_food

buy_food_survival
buy_drink_survival
buy_food_stock
buy_drink_stock

buy_weapon_resupply
buy_armor_resupply
buy_ammo_resupply
buy_medical_resupply

sell_for_survival
sell_for_resupply

fallback_get_money
fallback_search_item
fallback_wait_no_action
```

This keeps `brain_trace`, memory and tests aligned.

---

## A12. Add brain_trace example for post-PR1 case

```json
{
  "current_thought": "Выживание стабилизировано. Нет оружия, но денег недостаточно для покупки.",
  "immediate_needs": [],
  "item_needs": [
    {
      "key": "weapon",
      "urgency": 0.65,
      "missing_count": 1,
      "reason": "Нет оружия",
      "expected_min_price": 250,
      "affordability_hint": "unaffordable"
    },
    {
      "key": "food",
      "urgency": 0.55,
      "missing_count": 1,
      "reason": "Еды 1/2"
    }
  ],
  "liquidity": {
    "can_buy_now": false,
    "money_missing": 221,
    "safe_sale_options": 0,
    "decision": "fallback_get_money"
  }
}
```

---

# Part B — Updates for PR 3 contract

---

## B1. Add sleep memory mapping to legacy bridge

PR 3 should explicitly map new PR 1 sleep events/memory into `memory_v3`.

Add to the legacy bridge section:

```text
action_kind = sleep_completed
→ layer = episodic
→ kind = sleep_completed
→ tags = ["sleep", "rest", "recovery"]
→ details include:
    sleep_intervals_applied
    turns_total
    turns_remaining_at_completion
    wake_due_to_rested
    sleepiness_after
```

For interrupted sleep:

```text
action_kind = plan_monitor_abort + scheduled_action_type = sleep
→ layer = episodic
→ kind = sleep_interrupted
→ tags = ["sleep", "rest", "plan_monitor", reason]
→ details include:
    sleep_intervals_applied
    sleep_progress_turns
    dominant_pressure
    sleepiness_after
    wake_due_to_rested = false
```

---

## B2. Do not store every `sleep_interval_applied` as memory

PR 1 may emit `sleep_interval_applied` events for debug/tests.

PR 3 should explicitly say:

```text
Do not convert every sleep_interval_applied event into a MemoryRecord.
```

Reason:

```text
A long sleep can produce many interval events.
Only final sleep outcomes should become memory.
```

Allowed sleep memory records:

```text
sleep_completed
sleep_interrupted
sleep_aborted_by_emergency
```

---

## B3. Add retention rules for sleep memory

Suggested retention:

```text
sleep_completed:
  medium retention

sleep_interrupted by hunger/thirst:
  medium retention

sleep_interrupted by emission/combat:
  high retention

death/combat/emission threat:
  high retention
```

Rationale:

```text
Sleep interruption due to hunger/thirst is useful behavioral feedback,
but less important than threat/death/combat memories.
```

---

## B4. Preserve merged-observation semantics

The current legacy memory system already merges repeated observations using fields like:

```text
first_seen_turn
last_seen_turn
times_seen
```

PR 3 must preserve this behavior.

Add:

```text
MemoryStore v3 must preserve semantic merge behavior of repeated observations.
If legacy memory has first_seen_turn / last_seen_turn / times_seen,
the bridge should map those fields into MemoryRecord.details.
```

This is important because PR 3 should not regress current memory quality while introducing indexes.

---

## B5. Add optional `world_time` to MemoryRecord

Since PR 1 `brain_trace` already includes `world_time`, memory records can include it too.

Add optional field:

```python
world_time: dict | None = None
```

This is not required for decision logic, but useful for frontend/debug.

---

## B6. Add `used_for` to `brain_trace.memory_used`

PR 3 should distinguish why a memory was used.

Example:

```json
{
  "id": "mem_trader_bunker",
  "kind": "trader_location_known",
  "summary": "Гнидорович обычно находится в Бункере торговца",
  "confidence": 0.92,
  "used_for": "find_trader"
}
```

Allowed examples:

```text
find_trader
find_food
find_water
find_ammo
avoid_threat
sell_artifacts
hunt_target
search_information
```

---

## B7. Clarify sleep and memory relationship

Add invariant:

```text
Sleep interval progress is state/action progress, not long-term memory.
Sleep completion/interruption is memory-worthy.
```

This prevents `memory_v3` from being flooded by low-value sleep interval records.

---

# Part C — Roadmap clarification

The ordering remains correct:

```text
PR 1:
  action monitoring
  brain_trace
  partial sleep
  dynamic sleep duration
  early wake-up
  survival-safe rest

PR 2:
  ImmediateNeed
  ItemNeed
  NeedEvaluationResult
  liquidity
  survival purchasing
  no unaffordable buy loops

PR 3:
  MemoryStore v3
  legacy bridge
  retrieval top-N
  BeliefState adapter

PR 4:
  Objective model and scoring

PR 5:
  ActivePlan source of truth
```

Important:

```text
Do not move MemoryStore into PR 2.
Do not move Objective scoring into PR 3.
Do not replace scheduled_action before ActivePlan ownership PR.
```

---

# Part D — Final merge instruction for Copilot

Please merge the contents of this addendum into the two main contract files:

```text
docs/npc_brain_v3_pr2_revised_needs_liquidity_contract.md
docs/npc_brain_v3_pr3_memory_belief_contract.md
```

After merging:

```text
- this addendum may remain as an appendix/reference;
- or it may be deleted if all sections are incorporated into the main files.
```

The main expected changes are:

```text
PR 2:
  - ImmediateNeed.trigger_context
  - NeedEvaluationResult
  - score-first ItemNeed selection
  - no unaffordable buy loop
  - post-PR1 Pozyc/Поцик regression case
  - preserve dynamic sleep duration and early wake-up

PR 3:
  - sleep_completed/sleep_interrupted memory mappings
  - do not store sleep_interval_applied as memory
  - preserve merge semantics
  - optional world_time
  - memory_used.used_for
```
