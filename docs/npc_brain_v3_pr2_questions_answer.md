# NPC Brain v3 — PR 2 Review and Answers

> Branch: `copilot/npc-brain-v3-pr-2`  
> Source questions file: `docs/npc_brain_v3_pr2_questions.md`  
> Reviewed against contract: `docs/npc_brain_v3_pr2_revised_needs_liquidity_contract.md`

---

## 1. Короткий ответ на файл вопросов

Файл `npc_brain_v3_pr2_questions.md` говорит, что после изучения архитектурных документов критичных блокирующих вопросов к PR 2 не осталось.

Ответ:

```text
Согласен: новых архитектурных блокирующих вопросов нет.
PR 2 можно продолжать реализовывать по контракту npc_brain_v3_pr2_revised_needs_liquidity_contract.md.
```

Но по текущей реализации PR 2 ещё нельзя считать завершённым.

Правильная формулировка:

```text
Архитектурные вопросы закрыты.
Implementation gaps ещё есть.
```

---

## 2. Общий статус реализации PR 2

Текущая ветка уже содержит основную заготовку PR 2:

```text
ImmediateNeed
ItemNeed
NeedEvaluationResult
AffordabilityResult
LiquidityOption
evaluate_immediate_needs
evaluate_item_needs
liquidity helpers
integrations in needs/intents/planner/brain_trace
basic frontend additions
basic v3 tests
```

Оценка текущей готовности:

```text
примерно 60–70%
```

Это уже не просто скелет, но ещё не полный PR 2.

---

## 3. Что уже сделано хорошо

### 3.1. Модели PR 2 созданы

Реализованы:

```text
ImmediateNeed
ItemNeed
NeedEvaluationResult
AffordabilityResult
LiquidityOption
```

`ImmediateNeed` уже содержит важные поля:

```text
trigger_context
blocks_intents
selected_item_id
selected_item_type
```

`ItemNeed` уже содержит:

```text
expected_min_price
affordability_hint
```

Это соответствует последнему контракту PR 2.

---

### 3.2. ImmediateNeed реализован по правильной идее

`evaluate_immediate_needs()` разделяет:

```text
survival needs:
  thirst >= critical
  hunger >= critical

rest_preparation needs:
  thirst >= sleep-safe threshold
  hunger >= sleep-safe threshold
```

Это правильно сохраняет PR 1-семантику:

```text
critical thirst/hunger globally block rest/resupply/get_rich;
rest_preparation needs affect sleep preparation only.
```

Также еда/вода из инвентаря выбираются по cheapest viable item, что правильно для survival mode.

---

### 3.3. ItemNeed реализован и выбирается score-first

`evaluate_item_needs()` создаёт needs для:

```text
food
drink
medicine
weapon
armor
ammo
upgrade
```

`choose_dominant_item_need()` сортирует:

```python
(-urgency, priority, key)
```

То есть старый absolute fixed order больше не является главным правилом.

Это важно для стабилизированного кейса:

```text
weapon urgency 0.65
food stock urgency 0.55
→ weapon should win
```

---

### 3.4. NeedEvaluationResult появился

`evaluate_need_result()` возвращает единый snapshot:

```text
scores
immediate_needs
item_needs
liquidity_summary
```

Это правильное направление: planner и brain_trace должны использовать один и тот же результат, а не пересчитывать needs отдельно.

---

### 3.5. Intent selection начал учитывать PR 2 needs

`select_intent()` принимает `need_result` и сначала проверяет critical `ImmediateNeed`:

```text
drink_now survival
eat_now survival
heal_now healing
```

Это правильно.

---

### 3.6. Planner начал использовать PR 2 entities

`build_plan()` уже принимает `need_result`.

Есть интеграция:

```text
seek_food / seek_water
rest
resupply
liquidity fallback
survival_cheapest buy mode
```

Это основная часть PR 2.

---

### 3.7. BrainTrace расширен

`brain_trace` теперь может включать:

```text
immediate_needs
item_needs
liquidity
```

Это соответствует PR 2 observability goal.

---

### 3.8. Тесты добавлены

Появились тесты:

```text
test_immediate_needs.py
test_item_needs.py
test_liquidity.py
test_survival_purchasing.py
test_brain_trace_needs.py
```

Это хорошо, но coverage пока недостаточный.

---

## 4. Что ещё нужно доделать

---

## 4.1. Проверить и зафиксировать сквозную передачу `NeedEvaluationResult`

### Проблема

Модели и функции уже есть:

```text
evaluate_need_result()
select_intent(..., need_result)
build_plan(..., need_result)
write_decision_brain_trace_from_v2(..., need_result)
```

Но для готовности PR 2 нужно жёстко зафиксировать тестом, что главный decision pipeline реально делает:

```text
evaluate_need_result
→ select_intent(..., need_result)
→ build_plan(..., need_result)
→ write_decision_brain_trace_from_v2(..., need_result)
```

### Почему это важно

Если где-то pipeline продолжит использовать только:

```python
evaluate_needs(ctx, state)
```

то PR 2 будет работать частично:

```text
scores есть,
но planner не видит immediate_needs/item_needs/liquidity.
```

### Required test

Добавить integration test:

```python
def test_decision_pipeline_passes_need_result_to_planner_and_trace():
    ...
```

Ожидание:

```text
bot with critical thirst + water in inventory
→ selected intent seek_water
→ plan consume water
→ brain_trace event contains immediate_needs
```

### Severity

```text
High
```

---

## 4.2. `affordability_hint` в ItemNeed пока не заполняется

### Проблема

`ItemNeed` имеет поля:

```python
expected_min_price
affordability_hint
```

Но `evaluate_item_needs()` заполняет только `expected_min_price`; `affordability_hint` остаётся `None`.

### Почему это важно

Контракт PR 2 хотел, чтобы trace мог объяснять:

```text
weapon:
  urgency = 0.65
  expected_min_price = 250
  affordability_hint = unaffordable
```

Сейчас это объяснение будет неполным.

### Рекомендация

В `evaluate_item_needs()` есть доступ к agent money, поэтому можно выставлять:

```python
def _affordability_hint(agent_money: int, expected_min_price: int | None) -> str:
    if expected_min_price is None:
        return "unknown"
    return "affordable" if agent_money >= expected_min_price else "unaffordable"
```

И применять к:

```text
weapon
armor
ammo
food
drink
medicine
```

### Severity

```text
Medium
```

---

## 4.3. Liquidity policy сейчас может продать ниже survival reserve

### Проблема

`find_liquidity_options()` правильно различает:

```text
safe
risky
emergency_only
forbidden / skipped
```

Но в planner для resupply сейчас допустимы:

```python
o.safety in ("safe", "risky")
```

Это опасно.

Для стабилизированного кейса `Поцик 1`:

```text
hunger = 50
thirst = 57
money = 29
weapon = null
inventory = bread, bandage, medkit
```

bread может быть последней едой и ниже desired food reserve. В такой ситуации он не должен продаваться ради обычного weapon resupply.

### Desired rule

```text
Normal resupply liquidity:
  allow only safe sale.

Survival liquidity:
  allow safe, and emergency_only only if it resolves a higher-priority immediate survival need.

Risky sale:
  should require explicit reason / stronger scoring,
  not be used automatically for normal resupply.
```

### Рекомендация

В `_plan_resupply()` заменить:

```python
sellable = next((o for o in liquidity_options if o.safety in ("safe", "risky")), None)
```

на:

```python
sellable = next((o for o in liquidity_options if o.safety == "safe"), None)
```

А `risky` оставить для будущего Objective scoring или отдельного policy decision.

### Required test

```python
def test_resupply_does_not_sell_last_food_below_reserve_for_weapon():
    ...
```

### Severity

```text
High
```

---

## 4.4. Survival purchase with remote trader can still ignore affordability

### Проблема

Для critical `seek_food` / `seek_water` при trader на другой локации planner может построить:

```text
travel_to_trader
trade_buy_item
```

без проверки, хватает ли денег.

Если денег не хватает, NPC может дойти до торговца и всё равно упереться в невозможную покупку.

### Desired behavior

Если known trader далеко, всё равно нужно заранее оценить:

```text
money < cheapest_viable_item_price
```

и при нехватке денег:

```text
1. если есть safe liquidity item и trader at current location → sell first;
2. если safe sale возможен только у trader, можно travel_to_trader → sell → buy;
3. если денег всё равно не хватит → fallback_get_money / search remembered item;
4. не строить голый travel → buy, если buy заведомо невозможен.
```

### Required test

```python
def test_critical_food_remote_trader_unaffordable_does_not_build_buy_loop():
    ...
```

### Severity

```text
High
```

---

## 4.5. `heal_self` всё ещё в основном legacy

### Проблема

`_plan_heal_or_flee()` ещё содержит старую логику:

```text
если money == 0 и есть sellable items → sell first
иначе buy medical
```

Но PR 2-контракт требует:

```text
money < required_price
liquidity options
do not sell last critical heal item if hp low
survival-mode affordability
```

### Рекомендация

Перевести heal на ту же схему, что seek_food/seek_water:

```text
ImmediateNeed.heal_now
→ consume inventory heal item
→ evaluate affordability for medical
→ if unaffordable, evaluate liquidity
→ buy medical only if affordable or after planned sale
```

### Required tests

```text
hp low + medkit in inventory → consume medkit, not buy
hp low + no medkit + money insufficient + artifact → sell artifact then buy medkit
hp low + no medkit + only last food/water → do not sell critical survival item
```

### Severity

```text
Medium/High
```

---

## 4.6. `_exec_consume` missing reason mappings for `need_food` / `need_drink`

### Проблема

`_plan_seek_consumable()` can create consume step with:

```python
reason = f"need_{category}"
```

For example:

```text
need_food
need_drink
```

But `_exec_consume()` maps only:

```text
emergency_heal
emergency_food
emergency_drink
prepare_sleep_food
prepare_sleep_drink
opportunistic_food
opportunistic_drink
```

Unknown reason falls back to:

```python
consume_heal
```

So `need_food` / `need_drink` can be logged as heal consumption.

### Рекомендация

Extend mapping:

```python
"need_food": "consume_food",
"need_drink": "consume_drink",
"need_medical": "consume_heal",
```

Even better: fallback by item category:

```text
if item_type in FOOD_ITEM_TYPES → consume_food
if item_type in DRINK_ITEM_TYPES → consume_drink
if item_type in HEAL_ITEM_TYPES → consume_heal
```

### Required test

```python
def test_need_food_records_consume_food_action_kind():
    ...
```

### Severity

```text
Medium
```

---

## 4.7. BrainTrace liquidity summary is still too shallow

### Проблема

`brain_trace` currently can show:

```text
safe_sale_options
risky_sale_options
emergency_sale_options
```

But для объяснения PR 2 нужно ещё:

```text
can_buy_now
required_price
money_missing
decision / reason
```

Пример желаемого вывода:

```json
{
  "liquidity": {
    "can_buy_now": false,
    "required_price": 250,
    "money_missing": 221,
    "safe_sale_options": 0,
    "decision": "fallback_get_money"
  }
}
```

### Рекомендация

Когда planner выбирает unaffordable resupply fallback, записывать в trace:

```text
liquidity.decision = fallback_get_money
liquidity.money_missing = ...
liquidity.required_price = ...
```

Для этого можно либо расширить `NeedEvaluationResult.liquidity_summary`, либо передавать planner decision trace отдельно.

### Severity

```text
Medium
```

---

## 4.8. Tests are currently too thin for PR 2 acceptance

### Current tests cover

```text
ImmediateNeed basic cases
ItemNeed basic cases
affordability basic case
do not sell last water under critical thirst
survival cheapest buy executor
brain_trace contains needs blocks
```

### Missing tests

Add:

```text
1. Full decision pipeline with NeedEvaluationResult.
2. Critical hunger/thirst with inventory creates consume plan.
3. Critical hunger + no weapon still chooses seek_food, not resupply.
4. Stabilized Поцик case:
   hunger 50, thirst 57, money 29, no weapon, bread/bandage/medkit.
5. No unaffordable buy loop for weapon.
6. Remote trader unaffordable survival purchase does not build invalid buy plan.
7. Resupply does not sell last food below reserve.
8. heal_self uses affordability/liquidity.
9. need_food/need_drink consume action_kind mapping.
10. PR1 sleep behavior still passes with PR2 rest refactor.
```

### Severity

```text
High for acceptance confidence
```

---

## 5. Что не является проблемой

### 5.1. PR 2 не обязан делать MemoryStore

Это PR 3.

### 5.2. PR 2 не обязан делать Objective scoring

Это PR 4.

### 5.3. PR 2 не обязан делать ActivePlan source of truth

Это PR 5.

### 5.4. Не нужно переносить всё на Redis/PostgreSQL

Не относится к PR 2.

---

## 6. Рекомендуемый порядок доделок

### Patch 1 — pipeline integration test

Убедиться, что `NeedEvaluationResult` проходит через весь decision pipeline.

### Patch 2 — consume reason mapping

Добавить `need_food` / `need_drink` / category fallback.

### Patch 3 — liquidity policy

Запретить автоматическую продажу `risky` items для normal resupply.

### Patch 4 — affordability-aware remote trader planning

Не строить travel → buy, если buy заведомо unaffordable and no liquidity path.

### Patch 5 — heal_self PR2 migration

Перевести heal на affordability/liquidity.

### Patch 6 — brain_trace liquidity details

Добавить `required_price`, `money_missing`, `decision`.

### Patch 7 — regression tests

Добавить canonical cases, особенно stabilized `Поцик 1`.

---

## 7. Answer to `npc_brain_v3_pr2_questions.md`

Исходный вопрос-файл говорит:

```text
критичных блокирующих вопросов к реализации PR 2 не осталось.
```

Финальный ответ:

```text
Да, архитектурно блокирующих вопросов нет.
Реализация должна продолжаться по контракту PR 2.

Но текущий PR 2 ещё не завершён:
- базовая структура реализована;
- главные модели добавлены;
- planner начал использовать новые сущности;
- trace/tests/UI начаты;
- но liquidity policy, unaffordable buy loop, heal_self migration,
  consume reason mapping и regression coverage ещё нужно закрыть.
```

---

## 8. Definition of Done delta for current branch

Перед тем как считать PR 2 готовым, должны быть выполнены дополнительные пункты:

```text
[ ] NeedEvaluationResult verified through full tick decision pipeline.
[ ] Resupply does not sell risky/last survival reserve items by default.
[ ] Unaffordable buy loop prevented for local and remote trader cases.
[ ] Heal_self uses affordability/liquidity.
[ ] need_food / need_drink consumption records correct action_kind.
[ ] BrainTrace liquidity summary includes money_missing/required_price/decision when relevant.
[ ] Stabilized Поцик regression case covered.
[ ] Existing PR1 sleep tests remain green.
```

---

## 9. Итог

PR 2 started well and has the right architecture.

Current status:

```text
Implementation direction: correct.
Architecture blockers: none.
PR completion: not yet.
Remaining work: important edge cases + tests.
```

The biggest practical risks right now:

```text
1. selling risky/last survival-reserve items for normal weapon resupply;
2. remote trader unaffordable buy paths;
3. heal_self still using legacy money==0 behavior;
4. insufficient integration/regression tests.
```
