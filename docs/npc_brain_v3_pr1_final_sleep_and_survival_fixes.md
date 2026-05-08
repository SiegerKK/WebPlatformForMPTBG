# NPC Brain v3 — PR 1 Finalization: Sleep, Survival Preconditions, Repair Plan

> Контекст: документ фиксирует последнюю пачку правок, которые нужно добавить к уже реализованному PR 1, чтобы PR 1 можно было считать завершённым.  
> Ветка: `copilot/rewrite-npc-equipment-rules`  
> Основная проблема: НПЦ может зациклиться между `sleep` и `PlanMonitor abort`, а длительный сон сейчас не даёт полезного частичного эффекта при прерывании.

---

## 1. Актуальное состояние PR 1

После последних доработок PR 1 уже содержит основные части:

- `PlanMonitor`;
- `brain_trace`;
- `world_time` в `brain_trace`;
- `no_op` fallback event;
- dedup для `plan_monitor_abort` memory-записей;
- тесты на:
  - abort scheduled action;
  - human agent not monitored;
  - emergency_flee not aborted;
  - action queue progression;
  - memory dedup;
  - decision trace;
  - transient `_v3_*` cleanup.

Это означает, что PR 1 уже близок к завершению.

Но обнаружилась системная проблема на дампе персонажа `Поцик 1`:

```text
hunger: 86
thirst: 80
sleepiness: 98
scheduled_action: sleep, 360 turns remaining
inventory: bread, glucose, energy_drink, water, bandage, medkit
```

NPC имеет еду и воду, но может попасть в цикл:

```text
sleepiness 98 → select_intent выбирает rest
→ scheduled_action = sleep
→ PlanMonitor видит hunger/thirst >= 80
→ abort sleep
→ decision pipeline снова видит sleepiness 98
→ снова выбирает rest
```

Проблема не в одном `if`, а в отсутствии системной подготовки к длительным действиям и частичных эффектов сна.

---

## 2. Цель финальных правок PR 1

После реализации этого документа PR 1 должен закрывать не только наблюдаемость и abort активных действий, но и базовую корректность сна:

1. Сон должен давать эффект постепенно, каждые 30 минут.
2. Если 8-часовой сон прерван на 5-м часу, NPC должен получить эффект за 5 часов сна.
3. NPC не должен начинать сон, если голод/жажда уже в опасной зоне и в инвентаре есть еда/вода.
4. Если NPC хочет спать, но голоден/хочет пить, план должен сначала включать repair-шаги:
   - выпить;
   - поесть;
   - затем спать.
5. `PlanMonitor` и `select_intent` не должны конфликтовать по порогам критического голода/жажды.
6. `brain_trace` должен объяснять:
   - почему сон был отложен;
   - какие preparation/repair steps были выполнены;
   - какой частичный эффект сна был применён.

---

## 3. Non-goals

Эти правки всё ещё относятся к PR 1 finalization и НЕ должны превращаться в PR 2:

- не внедряем полный `ItemNeed`;
- не внедряем `ObjectiveGenerator`;
- не внедряем полноценный `ActivePlan`;
- не внедряем `pause/adapt`;
- не внедряем Redis;
- не переносим память в `memory_v3`;
- не переписываем весь planner;
- не делаем полноценный GOAP/HTN.

Разрешённые изменения:

```text
sleep scheduled_action model
sleep partial effects
rest preconditions
rest repair steps
shared survival thresholds
tests
brain_trace summaries
```

---

## 4. Проблема 1: сон не должен быть атомарным действием на 6–8 часов

### Сейчас

`sleep` создаётся как длительный `scheduled_action`:

```python
agent["scheduled_action"] = {
    "type": "sleep",
    "hours": hours,
    "turns_remaining": turns,
    "turns_total": turns,
}
```

Если такой сон прерывается, частичный эффект сна либо не применяется, либо применяется недостаточно явно.

### Требование

Сон должен иметь checkpoint каждые 30 минут.

```text
SLEEP_EFFECT_INTERVAL_TURNS = 30
```

Каждые 30 минут сна:

```text
sleepiness уменьшается
hunger растёт
thirst растёт
```

Прерывание сна после нескольких checkpoint'ов должно сохранять уже полученный эффект.

### Новые константы

Добавить в `tick_constants.py`:

```python
SLEEP_EFFECT_INTERVAL_TURNS = 30

SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL = 10
HUNGER_INCREASE_PER_SLEEP_INTERVAL = 1
THIRST_INCREASE_PER_SLEEP_INTERVAL = 2

SLEEP_SAFE_HUNGER_THRESHOLD = 70
SLEEP_SAFE_THIRST_THRESHOLD = 70
```

Примечание:

- `SLEEP_EFFECT_INTERVAL_TURNS = 30` соответствует “каждые полчаса”.
- Значения восстановления можно балансировать, но для PR 1 нужны понятные и тестируемые числа.
- При 6 часах сна будет 12 интервалов × 10 = 120, то есть sleepiness гарантированно упадёт до 0.
- При 5 часах сна будет 10 интервалов × 10 = 100, тоже значимый эффект.
- Если нужно сделать сон мягче, можно поставить `SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL = 8`.

---

## 5. Новая структура `scheduled_action` для сна

При создании sleep action добавить поля:

```python
agent["scheduled_action"] = {
    "type": "sleep",
    "hours": hours,
    "turns_remaining": turns,
    "turns_total": turns,
    "sleep_progress_turns": 0,
    "sleep_intervals_applied": 0,
}
```

### Поля

```text
turns_remaining:
  сколько минут сна осталось

turns_total:
  сколько минут сна было запланировано

sleep_progress_turns:
  сколько минут накоплено с последнего 30-минутного checkpoint

sleep_intervals_applied:
  сколько 30-минутных эффектов уже применено
```

### Backward compatibility

Старые сохранения могут иметь sleep action без этих полей.

При обработке sleep нужно делать:

```python
sched.setdefault("sleep_progress_turns", 0)
sched.setdefault("sleep_intervals_applied", 0)
```

---

## 6. Функция применения одного интервала сна

Добавить helper в `tick_rules.py` или отдельный модуль, например:

```python
def _apply_sleep_interval_effect(
    agent_id: str,
    agent: dict,
    sched: dict,
    state: dict,
    world_turn: int,
) -> list[dict]:
    ...
```

### Поведение

```python
old_sleepiness = agent.get("sleepiness", 0)
old_hunger = agent.get("hunger", 0)
old_thirst = agent.get("thirst", 0)

agent["sleepiness"] = max(
    0,
    old_sleepiness - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL,
)
agent["hunger"] = min(
    100,
    old_hunger + HUNGER_INCREASE_PER_SLEEP_INTERVAL,
)
agent["thirst"] = min(
    100,
    old_thirst + THIRST_INCREASE_PER_SLEEP_INTERVAL,
)

sched["sleep_intervals_applied"] = int(sched.get("sleep_intervals_applied", 0)) + 1
```

### Event

Эмитить event не обязательно каждый раз, чтобы не шуметь. Но для тестируемости и debug можно сделать маленький event:

```json
{
  "event_type": "sleep_interval_applied",
  "payload": {
    "agent_id": "agent_debug_0",
    "interval_index": 3,
    "sleepiness_before": 98,
    "sleepiness_after": 88,
    "hunger_after": 87,
    "thirst_after": 82
  }
}
```

Если боимся event spam, можно не отдавать event наружу, но memory/trace итог сна должен отражать `sleep_intervals_applied`.

### Memory

Не писать memory каждые 30 минут. Это создаст шум.

Memory писать только:

- когда сон завершён;
- когда сон прерван после хотя бы одного интервала;
- когда сон прерван до эффекта.

---

## 7. Обработка sleep внутри `_process_scheduled_action`

Сейчас `_process_scheduled_action()` уменьшает `turns_remaining` и для `turns_remaining > 0` просто возвращает.

Для sleep нужно отдельное поведение.

### Предлагаемый порядок

В начале `_process_scheduled_action()` после decrement:

```python
action_type = sched["type"]
turns_remaining = sched["turns_remaining"] - 1
sched["turns_remaining"] = turns_remaining

if action_type == "sleep":
    events.extend(_process_sleep_tick(...))
    if turns_remaining > 0:
        return events
    # if completed, fall through or resolve sleep completion
```

### `_process_sleep_tick`

```python
def _process_sleep_tick(agent_id, agent, sched, state, world_turn):
    events = []

    sched.setdefault("sleep_progress_turns", 0)
    sched.setdefault("sleep_intervals_applied", 0)

    sched["sleep_progress_turns"] += 1

    while sched["sleep_progress_turns"] >= SLEEP_EFFECT_INTERVAL_TURNS:
        sched["sleep_progress_turns"] -= SLEEP_EFFECT_INTERVAL_TURNS
        events.extend(_apply_sleep_interval_effect(agent_id, agent, sched, state, world_turn))

    return events
```

### Завершение сна

Когда `turns_remaining <= 0`:

```python
agent["scheduled_action"] = None

_add_memory(
    agent,
    world_turn,
    state,
    "action",
    "😴 Сон завершён",
    {
        "action_kind": "sleep_completed",
        "sleep_intervals_applied": sched.get("sleep_intervals_applied", 0),
        "turns_total": sched.get("turns_total"),
    },
    summary=f"Проснулся после сна: восстановлено {intervals} интервал(ов)",
)
```

Не нужно дополнительно сбрасывать `sleepiness = 0` при завершении, если интервалы уже применяли эффект. Иначе будет двойной эффект.

### Важно

Если старый код при завершении сна делает:

```python
agent["sleepiness"] = 0
```

его нужно убрать или заменить на интервал-based эффект.

---

## 8. Что делать при прерывании сна

Если `PlanMonitor` abort'ит `sleep`, частичные эффекты уже должны быть применены на предыдущих 30-минутных checkpoint'ах.

При abort сна нужно записать в memory/trace:

```text
сон был прерван;
сколько интервалов сна успел получить;
текущие hunger/thirst/sleepiness.
```

### Event уже есть

`plan_monitor_aborted_action` уже эмитится.

Для sleep payload желательно расширить optional полями:

```json
{
  "sleep_intervals_applied": 10,
  "sleep_progress_turns": 12
}
```

Это optional, не ломает старый контракт.

### Memory summary

При abort sleep:

```text
Прервал сон из-за critical_hunger. Успел поспать 5.0 ч: усталость снизилась до 12%.
```

Это можно сделать в `_summary`, если `sched.type == "sleep"`.

---

## 9. Проблема 2: `PlanMonitor` и `select_intent` используют разные пороги

### Сейчас

`PlanMonitor` считает критичным:

```text
hunger >= 80
thirst >= 80
```

`select_intent()` hard interrupt делает только при:

```text
needs.eat >= 0.90
needs.drink >= 0.90
```

Из-за этого при:

```text
hunger = 86
thirst = 80
sleepiness = 98
```

`PlanMonitor` прерывает sleep, но `select_intent()` снова выбирает `rest`.

### Требование

Пороги должны быть едиными.

В `intents.py` нужно заменить:

```python
_HARD_INTERRUPT_NEEDS = 0.90
```

на использование констант из `tick_constants.py`:

```python
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)
```

И проверять не `0.90`, а конкретные agent values или derived scores:

```python
if agent.get("thirst", 0) >= CRITICAL_THIRST_THRESHOLD:
    return INTENT_SEEK_WATER

if agent.get("hunger", 0) >= CRITICAL_HUNGER_THRESHOLD:
    return INTENT_SEEK_FOOD
```

### Почему лучше проверять agent values

`needs.drink` и `needs.eat` сейчас равны `thirst / 100` и `hunger / 100`, но threshold в ticks хранится как integer percent.

Чтобы не было рассинхрона:

```text
source of truth = tick_constants.py
```

### Приоритет thirst vs hunger

Если оба критичны:

```text
thirst first
hunger second
```

Это уже соответствует текущему порядку `select_intent`.

---

## 10. Проблема 3: сон должен иметь preconditions

Даже после синхронизации порогов остаётся концептуальная проблема: `rest` может быть выбран при высоком sleepiness, когда hunger/thirst уже близко к критическим.

### Требование

`_plan_rest()` не должен сразу планировать сон, если:

```text
hunger >= SLEEP_SAFE_HUNGER_THRESHOLD
или
thirst >= SLEEP_SAFE_THIRST_THRESHOLD
```

Вместо этого он должен строить repair-plan:

```text
1. consume drink, если thirst high и есть drink
2. consume food, если hunger high и есть food
3. sleep
```

### Helper

Добавить:

```python
def _build_sleep_preparation_steps(agent: dict) -> list[PlanStep]:
    ...
```

### Логика

```python
steps = []

if agent["thirst"] >= SLEEP_SAFE_THIRST_THRESHOLD:
    drink = first drink item in inventory
    if drink:
        steps.append(PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": drink["type"], "reason": "prepare_sleep_drink"},
            interruptible=False,
            expected_duration_ticks=1,
        ))

if agent["hunger"] >= SLEEP_SAFE_HUNGER_THRESHOLD:
    food = first food item in inventory
    if food:
        steps.append(PlanStep(
            kind=STEP_CONSUME_ITEM,
            payload={"item_type": food["type"], "reason": "prepare_sleep_food"},
            interruptible=False,
            expected_duration_ticks=1,
        ))

steps.append(PlanStep(
    kind=STEP_SLEEP_FOR_HOURS,
    payload={"hours": DEFAULT_SLEEP_HOURS},
    interruptible=True,
    expected_duration_ticks=DEFAULT_SLEEP_HOURS * 60,
))
```

### Важное ограничение

В рамках PR 1 finalization repair должен использовать только предметы в инвентаре.

Не надо в этом PR строить:

```text
travel to trader → buy food → eat → sleep
```

Это уже PR 2 / PR 3.

Если еды/воды в инвентаре нет, `select_intent()` после синхронизации thresholds всё равно выберет `seek_food` или `seek_water`.

---

## 11. Обновить `_exec_consume` reason mapping

Сейчас `_exec_consume()` мапит только:

```python
"emergency_heal": "consume_heal",
"emergency_food": "consume_food",
"emergency_drink": "consume_drink",
```

Нужно добавить:

```python
"prepare_sleep_food": "consume_food",
"prepare_sleep_drink": "consume_drink",
```

Иначе подготовительное потребление еды/воды может записаться как `consume_heal` из-за fallback.

### Требование

В `executors.py`:

```python
action_kind_map = {
    "emergency_heal": "consume_heal",
    "emergency_food": "consume_food",
    "emergency_drink": "consume_drink",
    "prepare_sleep_food": "consume_food",
    "prepare_sleep_drink": "consume_drink",
    "opportunistic_food": "consume_food",
    "opportunistic_drink": "consume_drink",
}
```

Также стоит поправить fallback: если item type относится к FOOD/DRINK/HEAL, выбирать action_kind по категории предмета, а не всегда `consume_heal`.

---

## 12. Проблема 4: бедный NPC должен покупать survival-предметы по survival-логике

Это не обязательно завершать в PR 1, но текущий дамп показывает важный симптом: NPC покупал дорогие survival-предметы, хотя у него мало денег.

Примеры из дампа:

```text
glucose за 180
energy_drink за 120
water за 45
```

Для PR 1 finalization можно оставить это как follow-up, но в документе нужно зафиксировать минимальное правило.

### Минимальное правило для PR 1

Если intent `seek_food` / `seek_water` вызван критическим hunger/thirst:

```text
покупка должна выбирать cheapest affordable viable item,
а не лучший risk_tolerance score.
```

Это можно отложить в PR 2, если не хотим раздувать PR 1.

### Рекомендация

Для завершения PR 1 достаточно:

```text
не исправлять покупки сейчас,
но создать follow-up entry для PR 2:
survival-mode purchasing.
```

Если всё-таки включать в PR 1, то правка должна быть ограниченной:

```python
if purchase_reason in ("buy_food", "buy_drink") and critical need:
    choose cheapest affordable item that matches category
else:
    use existing scoring
```

---

## 13. Проблема 5: если денег недостаточно, planner не должен бесконечно пытаться купить

Это тоже лучше отнести к PR 2, но нужно зафиксировать.

Сейчас логика местами проверяет:

```python
money == 0
```

а нужна проверка:

```python
money < cheapest_viable_item_price
```

Для PR 1 finalization это не блокер, потому что в проблемном дампе еда/вода есть в inventory.

### Follow-up для PR 2

Добавить `LiquidityPlan`:

```text
если money < required_price:
  1. sell non-critical items
  2. buy survival item
```

Но не продавать:

```text
последнюю еду при hunger high
последнюю воду при thirst high
последнюю аптечку при low hp
экипированную броню/оружие
```

---

## 14. Конкретное ожидаемое поведение для дампа `Поцик 1`

Дано:

```text
hunger = 86
thirst = 80
sleepiness = 98
inventory содержит bread, glucose, energy_drink, water
scheduled_action = sleep
```

После правок:

### Вариант A: NPC уже спит

`PlanMonitor` видит:

```text
hunger >= 80
thirst >= 80
```

Он abort'ит sleep.

Но затем `select_intent()` уже не должен снова выбирать `rest`, потому что:

```text
thirst >= CRITICAL_THIRST_THRESHOLD
→ seek_water
```

План:

```text
consume water или energy_drink
```

Следующий тик:

```text
hunger still high
→ seek_food
```

План:

```text
consume bread или glucose
```

После этого, если sleepiness всё ещё высокая:

```text
rest
→ sleep
```

### Вариант B: NPC только собирается спать

`select_intent()` может выбрать `rest`, если hunger/thirst уже ниже critical, но выше safe threshold.

Например:

```text
hunger = 72
thirst = 74
sleepiness = 98
```

Тогда `_plan_rest()` должен построить:

```text
1. consume drink
2. consume food
3. sleep_for_hours
```

### Вариант C: NPC проспал 5 часов из 8

Если сон был 8 часов:

```text
turns_total = 480
```

После 5 часов:

```text
sleep_intervals_applied = 10
```

Если его прервали:

```text
sleepiness already reduced by 10 intervals
hunger/thirst increased by 10 intervals
memory says partial sleep happened
```

Он не должен потерять эффект этих 5 часов.

---

## 15. BrainTrace requirements

### При repair перед сном

Если `_plan_rest()` добавляет preparation steps, `brain_trace` должен показать readable thought:

```text
Очень устал, но сначала нужно поесть/попить перед сном.
```

Минимально можно сделать через обычный decision trace, но лучше добавить в reason:

```text
rest_preparation_required
```

### При sleep interval

Не обязательно писать trace каждый интервал.

### При abort sleep

Trace уже пишется через `write_plan_monitor_trace`.

Для sleep желательно улучшить summary:

```text
Прерываю sleep из-за critical_hunger. Успел поспать 5.0 ч.
```

---

## 16. Tests to add

### 16.1. Сон даёт частичный эффект каждые 30 минут

```python
def test_sleep_applies_effect_every_30_minutes():
    state = ...
    bot["sleepiness"] = 80
    bot["scheduled_action"] = {
        "type": "sleep",
        "hours": 8,
        "turns_remaining": 480,
        "turns_total": 480,
    }

    # run 30 ticks
    for _ in range(30):
        state, _ = tick_zone_map(state)

    assert bot["sleepiness"] == 70
    assert bot["scheduled_action"]["sleep_intervals_applied"] == 1
```

### 16.2. Прерывание на 5 часу сохраняет эффект

```python
def test_interrupted_sleep_keeps_partial_recovery():
    # start sleepiness 100
    # run 300 ticks = 5 hours
    # force hunger/thirst critical
    # PlanMonitor aborts sleep
    # assert sleepiness <= 0 or reduced by 10 intervals
    # assert scheduled_action is None
```

### 16.3. `select_intent` не выбирает rest при hunger/thirst >= 80

```python
def test_critical_hunger_beats_sleepiness():
    agent["hunger"] = 86
    agent["thirst"] = 20
    agent["sleepiness"] = 98

    intent = select_intent(...)
    assert intent.kind == INTENT_SEEK_FOOD
```

```python
def test_critical_thirst_beats_sleepiness():
    agent["hunger"] = 20
    agent["thirst"] = 80
    agent["sleepiness"] = 98

    intent = select_intent(...)
    assert intent.kind == INTENT_SEEK_WATER
```

### 16.4. `_plan_rest()` добавляет preparation consume steps

```python
def test_rest_plan_consumes_food_and_drink_before_sleep():
    agent["hunger"] = 72
    agent["thirst"] = 74
    agent["sleepiness"] = 98
    agent["inventory"] = [bread, water]

    plan = _plan_rest(...)

    assert plan.steps[0].kind == STEP_CONSUME_ITEM
    assert plan.steps[0].payload["reason"] == "prepare_sleep_drink"
    assert plan.steps[1].kind == STEP_CONSUME_ITEM
    assert plan.steps[1].payload["reason"] == "prepare_sleep_food"
    assert plan.steps[2].kind == STEP_SLEEP_FOR_HOURS
```

### 16.5. `prepare_sleep_*` consumption writes correct action_kind

```python
def test_prepare_sleep_food_records_consume_food():
    ...
```

### 16.6. PlanMonitor dedup still works

Существующий тест оставить.

### 16.7. No memory spam from sleep intervals

```python
def test_sleep_intervals_do_not_write_memory_every_30_minutes():
    ...
```

Memory entry should be written on completion/interruption only, not every interval.

---

## 17. Expected file changes

### Backend

```text
backend/app/games/zone_stalkers/rules/tick_constants.py
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/intents.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
```

### Tests

```text
backend/tests/decision/v3/test_tick_integration.py
backend/tests/decision/v3/test_sleep_effects.py
backend/tests/decision/test_intents.py
backend/tests/decision/test_planner.py
```

If there is no `test_sleep_effects.py`, create it.

---

## 18. Definition of Done

PR 1 finalization is complete when:

### Sleep mechanics

- [ ] Sleep applies effects every 30 minutes.
- [ ] Interrupted sleep keeps already applied interval effects.
- [ ] Completion of sleep does not double-apply full recovery.
- [ ] Sleep scheduled_action is backward-compatible with old saves.
- [ ] Sleep interval does not spam memory every 30 minutes.
- [ ] Sleep completion/interruption records useful summary.

### Survival vs sleep decision

- [ ] `PlanMonitor` and `select_intent` use shared critical thresholds.
- [ ] `thirst >= 80` beats `sleepiness = 100`.
- [ ] `hunger >= 80` beats `sleepiness = 100`.
- [ ] NPC does not immediately re-enter sleep after sleep was aborted for critical hunger/thirst.

### Rest preconditions

- [ ] `_plan_rest()` inserts drink/food consume steps before sleep if hunger/thirst are above safe sleep threshold and items are available.
- [ ] Preparation steps use correct `consume_food` / `consume_drink` action kinds.
- [ ] If no food/water is available and hunger/thirst are critical, `select_intent()` chooses `seek_food` / `seek_water`, not `rest`.

### PR 1 existing guarantees preserved

- [ ] Human agents are not monitored by PlanMonitor.
- [ ] `emergency_flee` is not interrupted.
- [ ] `plan_monitor_abort` memory dedup still works.
- [ ] `brain_trace` still exists for all living bot stalkers.
- [ ] `_v3_*` transient flags are removed after tick.
- [ ] Existing v3 tests still pass.

---

## 19. Recommended implementation order

1. Add sleep constants.
2. Add sleep interval fields to `_exec_sleep`.
3. Add `_process_sleep_tick()` and `_apply_sleep_interval_effect()`.
4. Update sleep completion logic to avoid double recovery.
5. Sync `select_intent()` critical hunger/thirst thresholds with `tick_constants.py`.
6. Add `_build_sleep_preparation_steps()` and update `_plan_rest()`.
7. Update `_exec_consume()` action_kind mapping for `prepare_sleep_food/drink`.
8. Improve PlanMonitor abort summary for sleep with partial sleep info.
9. Add tests.
10. Run full backend test suite.

---

## 20. Final position

These changes should still be considered PR 1 finalization, not PR 2.

Reason:

```text
PR 1 introduced PlanMonitor and brain_trace for active scheduled_action.
The sleep loop is a direct consequence of active scheduled_action reassessment.
Therefore sleep partial effects + rest preconditions are required to make PR 1 complete.
```

After these changes, PR 1 can be considered functionally complete:

```text
NPC can be monitored during long actions,
long sleep has meaningful partial progress,
critical hunger/thirst cannot create sleep-abort loops,
and frontend/debug can explain what happened.
```

Remaining economy/supply problems should move to PR 2:

```text
ItemNeed
LiquidityPlan
survival-mode purchasing
better resupply planning
```
