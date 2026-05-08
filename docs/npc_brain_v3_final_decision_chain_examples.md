# NPC Brain v3 — итоговая цепочка принятия решений после PR 1–PR 5

> Назначение: объяснить, как будет выглядеть полный цикл принятия решений NPC после реализации всей серии PR, и как каждый PR постепенно меняет эту цепочку.

---

## 1. Итоговая цепочка после полной реализации

После полной реализации NPC Brain v3 цепочка принятия решений должна выглядеть так:

```text
observe world
→ update memory
→ build BeliefState
→ evaluate ImmediateNeed / ItemNeed / liquidity / other drives
→ generate Objectives
→ score Objectives
→ compare with current ActivePlan
→ continue / repair / pause / abort / replace plan
→ execute current PlanStep through runtime bridge / scheduled_action
→ write brain_trace + memory
```

Иначе говоря, NPC больше не должен думать так:

```text
NeedScores высокий
→ выбрать Intent
→ planner что-то придумал
→ scheduled_action живёт отдельно
```

Новая модель:

```text
Что я знаю?
Что я вижу?
Что со мной происходит?
Какие цели сейчас возможны?
Какая цель важнее?
Мой текущий план всё ещё хорош?
Что я должен сделать следующим шагом?
```

Главная итоговая формула:

```text
Memory → Belief → Needs → Objectives → ActivePlan → PlanStep → scheduled_action
```

А не:

```text
NeedScores → Intent → scheduled_action
```

---

## 2. Как каждый PR меняет цепочку

---

## PR 1 — PlanMonitor, brain_trace, сон и survival-safe rest

### До PR 1

Если у NPC есть `scheduled_action`, он почти не переоценивает ситуацию:

```text
scheduled_action есть
→ NPC почти не думает
→ ждём окончания sleep/travel/explore
```

Проблемы:

```text
- NPC может спать, пока голод/жажда становятся критическими;
- прерывание сна не даёт полезного частичного эффекта;
- frontend плохо показывает, что NPC “думает”;
- scheduled_action живёт почти отдельно от decision logic.
```

### После PR 1

Появляется `PlanMonitor`:

```text
scheduled_action есть
→ PlanMonitor проверяет ситуацию
→ если всё нормально: continue
→ если критический голод/жажда/HP/опасность: abort
→ brain_trace объясняет решение
```

Сон становится процессом, а не атомарным действием:

```text
sleep
→ каждые 30 минут снижает sleepiness
→ повышает hunger/thirst
→ может закончиться раньше, если sleepiness = 0
→ если прерван, уже полученный эффект сна сохраняется
```

PR 1 не делает NPC полностью умным, но убирает самое слепое поведение.

### Что добавляет PR 1 в итоговую цепочку

```text
scheduled_action
→ PlanMonitor
→ brain_trace
→ partial progress for long actions
```

---

## PR 2 — ImmediateNeed, ItemNeed, liquidity

### До PR 2

Есть монолитная потребность:

```text
reload_or_rearm = 0.65
```

Но непонятно:

```text
это нет оружия?
нет еды?
нет воды?
нет патронов?
нет брони?
нет денег?
```

Также система путает:

```text
“я голоден прямо сейчас”
```

и:

```text
“у меня мало еды в запасе”
```

### После PR 2

Появляется разделение:

```text
ImmediateNeed:
  drink_now
  eat_now
  heal_now

ItemNeed:
  weapon
  armor
  ammo
  food stock
  drink stock
  medicine stock

Liquidity:
  могу купить?
  не хватает денег?
  что можно безопасно продать?
```

Пример:

```text
hunger = 86
inventory = bread

ImmediateNeed:
  eat_now = 0.86, selected_item = bread

Это не resupply.
Это не покупка еды.
Это срочное потребление еды из инвентаря.
```

PR 2 также должен устранить цикл:

```text
хочу купить оружие
денег не хватает
снова хочу купить оружие
денег всё ещё не хватает
...
```

Новая логика:

```text
money < cheapest_viable_item_price
→ evaluate liquidity
→ sell safe item / get money / search remembered item
→ не повторять unaffordable buy plan без изменения условий
```

### Что добавляет PR 2 в итоговую цепочку

```text
Belief / Context
→ ImmediateNeed
→ ItemNeed
→ Liquidity
→ NeedEvaluationResult
```

---

## PR 3 — MemoryStore v3 и BeliefState

### До PR 3

Память существует в основном как плоский список:

```text
agent["memory"] = [...]
```

Planner местами ищет записи вручную или использует частично неявные знания.

### После PR 3

Появляется структурированная память:

```text
MemoryStore v3:
  records
  indexes:
    by_layer
    by_kind
    by_location
    by_entity
    by_item_type
    by_tag
```

NPC начинает доставать top-N релевантных воспоминаний:

```text
я помню торговца в Бункере
я помню воду на Старой ферме
я помню, что Болото опасно
я помню, что этот путь был пустым
```

Появляется `BeliefState`:

```text
AgentContext + current observations + memory retrieval
→ BeliefState
```

Это важно: NPC больше не должен мыслить напрямую через “весь state мира”. Он должен действовать на основе того, что видит и помнит.

### BrainTrace после PR 3

`brain_trace` сможет показывать:

```text
Использованная память:
- “Гнидорович обычно находится в Бункере”
- “В Бункере недавно покупал воду”
- “Болото опасно после выброса”
```

### Что добавляет PR 3 в итоговую цепочку

```text
memory ingest
→ MemoryStore v3
→ retrieval top-N
→ BeliefState
→ brain_trace.memory_used
```

---

## PR 4 — Objective generation и scoring

### До PR 4

Даже после PR 3 выбор цели ещё частично размазан между:

```text
NeedScores
select_intent()
planner.py
special cases
PlanMonitor
legacy helper functions
```

### После PR 4

Появляется центральный слой:

```text
generate Objectives:
  RESTORE_WATER
  RESTORE_FOOD
  HEAL_SELF
  REST
  RESUPPLY_WEAPON
  RESUPPLY_FOOD
  GET_MONEY_FOR_RESUPPLY
  FIND_ARTIFACTS
  SELL_ARTIFACTS
  REACH_SAFE_SHELTER
  CONTINUE_CURRENT_PLAN

score Objectives:
  urgency
  expected_value
  confidence
  memory_confidence
  goal_alignment
  risk
  time_cost
  resource_cost
  switch_cost
```

Теперь NPC выбирает не просто intent, а цель с объяснимой оценкой.

Пример `brain_trace`:

```text
Выбрана цель:
  RESUPPLY_WEAPON, score 0.68

Отвергнутые альтернативы:
  RESUPPLY_FOOD, score 0.55
  REST, score 0.46
  FIND_ARTIFACTS, score 0.40
```

PR 4 — это этап, где поведение становится системно объяснимым.

### Что добавляет PR 4 в итоговую цепочку

```text
NeedEvaluationResult + BeliefState
→ Objective candidates
→ Objective scoring
→ selected Objective
→ rejected alternatives
→ Objective → Intent compatibility adapter
```

---

## PR 5 — ActivePlan становится source of truth

### До PR 5

Даже после Objective scoring длительное поведение ещё может быть привязано к:

```text
scheduled_action
action_queue
```

А `active_plan_v3` всё ещё может быть только debug/metadata.

### После PR 5

Центр управления поведением переносится в `ActivePlan`:

```text
ActivePlan = главный план
PlanStep = текущий шаг
scheduled_action = runtime-таймер текущего шага
```

Пример:

```text
ActivePlan:
  objective: RESUPPLY_WEAPON
  steps:
    1. travel_to_bunker
    2. sell_safe_item
    3. buy_weapon
    4. buy_ammo

scheduled_action:
  travel to bunker, осталось 12 минут
```

`scheduled_action` больше не объясняет, зачем NPC что-то делает. Он только исполняет текущий шаг.

### Возможные решения по плану

После PR 5 NPC может:

```text
continue:
  текущий план остаётся актуальным

pause:
  временно остановить план ради срочной нужды

resume:
  вернуться к старому плану после короткой вставки

repair:
  цель всё ещё актуальна, но план нужно поправить

abort:
  план больше невалиден

replace:
  новая цель победила старую по scoring
```

### Что добавляет PR 5 в итоговую цепочку

```text
ObjectiveDecision
→ ActivePlan
→ PlanStep lifecycle
→ runtime bridge
→ scheduled_action as execution detail
→ plan repair / pause / resume
```

---

# 3. Пример 1 — голодный, хочет пить, очень хочет спать

## Состояние

```text
hunger = 86
thirst = 80
sleepiness = 98
inventory = bread, water, glucose, energy_drink
scheduled_action = sleep
```

---

## До PR 1

```text
NPC спит 6 часов.
Голод/жажда растут.
Может умереть или проснуться слишком поздно.
```

---

## После PR 1

```text
PlanMonitor видит:
  hunger >= 80
  thirst >= 80

sleep abort
brain_trace:
  “Прерываю sleep из-за critical_thirst / critical_hunger”
```

Сон больше не атомарный:

```text
если NPC успел поспать 3 часа,
sleepiness уже снизилась.
```

---

## После PR 2

```text
ImmediateNeed:
  drink_now = 0.80, selected_item = water
  eat_now = 0.86, selected_item = bread
```

NPC не покупает новую еду, если еда уже есть в инвентаре.

Цепочка:

```text
seek_water
→ consume water

seek_food
→ consume bread

rest
→ sleep
```

---

## После PR 3

Если воды нет в инвентаре:

```text
MemoryStore retrieval:
  remembered water at old farm
  remembered trader at bunker
```

NPC выбирает путь не по всеведущему state, а по памяти.

---

## После PR 4

Objective scoring:

```text
RESTORE_WATER score 0.92
RESTORE_FOOD  score 0.88
REST          raw 0.98, but blocked by thirst/hunger
RESUPPLY_WEAPON score 0.65
```

Выбор:

```text
selected Objective = RESTORE_WATER
```

---

## После PR 5

Планы:

```text
ActivePlan: RESTORE_WATER
  1. consume water

ActivePlan: RESTORE_FOOD
  1. consume bread

ActivePlan: REST
  1. sleep_for_hours
```

Если сон прерывается:

```text
partial sleep effect remains
plan can be repaired/replaced
```

---

# 4. Пример 2 — стабилен, но без оружия и почти без денег

## Состояние

```text
hunger = 50
thirst = 57
sleepiness = 46
money = 29
weapon = null
armor = leather_jacket
inventory = bread, bandage, medkit
known trader = bunker
```

---

## До PR 2

Система видит:

```text
reload_or_rearm = 0.65
```

Но не может хорошо объяснить:

```text
это из-за оружия?
еды?
патронов?
денег?
```

Может возникать неудачный цикл:

```text
попробовать купить оружие
денег не хватает
снова попробовать купить оружие
...
```

---

## После PR 2

```text
ImmediateNeed:
  none critical

ItemNeed:
  weapon urgency 0.65
  food stock urgency 0.55
  medicine stock urgency 0.45

Liquidity:
  cheapest weapon price = 250
  money = 29
  money_missing = 221
  safe_sale_options = maybe 0
```

Вывод:

```text
Нужно оружие, но купить нельзя.
Не повторять buy_weapon бесконечно.
Нужен fallback_get_money / search_weapon / sell_safe_item.
```

---

## После PR 3

BeliefState добавляет память:

```text
known trader: bunker
known artifact zone: swamp
known danger: swamp risky
known weapon seen: old checkpoint, confidence 0.42
```

Возможные варианты:

```text
купить у торговца — денег не хватает
искать оружие на old checkpoint — далеко, confidence 0.42
искать артефакт и продать — риск 0.55
```

---

## После PR 4

Objective scoring:

```text
RESUPPLY_WEAPON:
  value high
  urgency 0.65
  blocked by money
  final score 0.50

GET_MONEY_FOR_RESUPPLY:
  value high
  urgency 0.60
  risk depends on artifact route
  final score 0.62

RESUPPLY_FOOD:
  urgency 0.55
  not critical
  final score 0.45

REST:
  urgency 0.46
  final score 0.30
```

Выбор:

```text
selected Objective = GET_MONEY_FOR_RESUPPLY
```

`brain_trace`:

```text
“Нет оружия, но денег недостаточно. Сначала нужно добыть деньги.”
```

---

## После PR 5

ActivePlan:

```text
Objective: GET_MONEY_FOR_RESUPPLY

Steps:
  1. travel_to_anomaly_location
  2. explore_anomaly_location
  3. pickup_artifact
  4. travel_to_trader
  5. sell_artifact
  6. buy_weapon
```

Если по пути начинается выброс:

```text
PlanContinuity:
  pause GET_MONEY_FOR_RESUPPLY
  new blocking objective REACH_SAFE_SHELTER
  after emission resume old plan if still valid
```

---

# 5. Пример 3 — идёт продавать артефакт, но начинает хотеть пить

## Состояние

```text
ActivePlan:
  SELL_ARTIFACTS
  current step: travel_to_trader
  remaining travel: 8 minutes

thirst = 72
water in inventory = yes
```

---

## После PR 1

Если `thirst < 80`, `PlanMonitor` не прерывает travel.

NPC продолжает идти.

---

## После PR 2

`ImmediateNeed` может быть:

```text
drink_now trigger_context = rest_preparation? no
survival? no, because thirst < 80
```

То есть пить ещё не обязательно.

---

## После PR 4

Objective scoring видит:

```text
CONTINUE_CURRENT_PLAN score 0.70
RESTORE_WATER score 0.60
```

Решение:

```text
continue current plan
```

Потому что жажда ещё не критична, а торговец близко.

Если позже:

```text
thirst = 83
```

Тогда:

```text
RESTORE_WATER becomes blocking
```

---

## После PR 5

Вместо полного abort появляется pause/resume:

```text
Pause SELL_ARTIFACTS

ActivePlan: RESTORE_WATER
  1. consume water

Resume SELL_ARTIFACTS
  continue travel_to_trader
```

Это ключевое отличие полной схемы: не “всё бросить”, а временно вставить нужный шаг.

---

# 6. Кодовая цепочка после полной реализации

В кодовом смысле цикл должен выглядеть примерно так:

```text
tick_zone_map
  → observe_agent_world
  → memory_system.ingest
  → build_agent_context
  → build_belief_state
  → evaluate_needs_v3
      → ImmediateNeed
      → ItemNeed
      → Liquidity
      → NeedScores compatibility
  → retrieve_relevant_memory
  → generate_objectives
  → score_objectives
  → assess_current_active_plan
  → choose objective:
      continue
      switch
      pause
      repair
      abort
  → build/update ActivePlan
  → runtime_bridge executes current PlanStep
      immediate step → executor
      long step → scheduled_action
  → update memory
  → write brain_trace
```

---

# 7. Принципиальное отличие новой схемы

Сейчас поведение ещё местами похоже на:

```text
сработал score
выбран intent
planner что-то сделал
scheduled_action живёт своей жизнью
```

После PR 5:

```text
NPC имеет цель.
У цели есть score и объяснение.
У цели есть план.
У плана есть шаги.
Каждый тик план переоценивается.
Если проблема временная — план ставится на паузу.
Если шаг заблокирован — план ремонтируется.
Если цель больше не актуальна — план отменяется.
```

Итог:

```text
NPC Brain v3 becomes the primary behavior architecture.
scheduled_action becomes only runtime execution detail.
```
