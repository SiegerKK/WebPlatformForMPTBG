# Архитектура принятия решений НПЦ (v2)

> Актуально для коммита по состоянию на 2026-03-25.
> Файлы: `backend/app/games/zone_stalkers/decision/` + `tick_rules.py`.

---

## Содержание

1. [Обзор пайплайна](#1-обзор-пайплайна)
2. [Фаза 0 — Предварительные проверки (до пайплайна)](#2-фаза-0--предварительные-проверки-до-пайплайна)
3. [Фаза 1 — Контекст (AgentContext)](#3-фаза-1--контекст-agentcontext)
4. [Фаза 2 — Потребности (NeedScores)](#4-фаза-2--потребности-needscores)
5. [Фаза 3 — Намерение (Intent)](#5-фаза-3--намерение-intent)
6. [Фаза 4 — План (Plan)](#6-фаза-4--план-plan)
7. [Фаза 5 — Исполнение (Executor)](#7-фаза-5--исполнение-executor)
8. [Глобальная цель vs. локальная задача снаряжения](#8-глобальная-цель-vs-локальная-задача-снаряжения)
9. [Таблица: intent → current_goal](#9-таблица-intent--current_goal)
10. [Схема приоритетов конкуренции потребностей](#10-схема-приоритетов-конкуренции-потребностей)
11. [Известные проблемы и пути решения](#11-известные-проблемы-и-пути-решения)

---

## 1. Обзор пайплайна

На каждый тик (ход мира) для каждого живого бота выполняется следующая цепочка:

```
tick
 │
 ├─► Прибытие/продажа на месте (commitment logic)
 │       _bot_pickup_on_arrival, _bot_sell_on_arrival
 │
 ├─► Проверка завершения глобальной цели
 │       _check_global_goal_completion → если достигнута → маршрут к выходу (обходит пайплайн)
 │
 ├─► _pre_decision_equipment_maintenance  [ДО пайплайна, см. §2]
 │       если что-то сделано → КОНЕЦ тика (пайплайн пропускается)
 │
 └─► V2 Pipeline:
       build_agent_context(...)      → AgentContext
       evaluate_needs(ctx, state)    → NeedScores
       select_intent(ctx, needs)     → Intent
       build_plan(ctx, intent, state)→ Plan
       execute_plan_step(ctx, plan)  → [events]
```

**Точка входа:** `_run_bot_decision_v2_inner` в `tick_rules.py`.

---

## 2. Фаза 0 — Предварительные проверки (до пайплайна)

### `_pre_decision_equipment_maintenance` (`tick_rules.py:3390`)

Запускается **до** пайплайна потребностей. При срабатывании **прерывает** весь пайплайн.

Порядок проверок:

| Шаг | Условие | Действие | current_goal |
|-----|---------|----------|--------------|
| 1 | Нет оружия в слоте, оружие есть в инвентаре | Экипировать на месте | (не меняется) |
| 2 | Нет оружия, оружие лежит на земле рядом | Подобрать | (не меняется) |
| 3 | Нет оружия, оружие в памяти (другая локация) | Запланировать путешествие | `"get_weapon"` |
| 4 | Нет брони в слоте, броня в инвентаре | Экипировать | (не меняется) |
| 5 | Нет брони, броня на земле | Подобрать | (не меняется) |
| 6 | Нет нужных патронов, они на земле | Подобрать | (не меняется) |
| 7 | Нет нужных патронов, они в памяти | Запланировать путешествие | `"get_ammo"` |
| 8 | Нет медикаментов вообще, они в памяти | Запланировать путешествие | (не меняется) |

> **Важно:** Шаги 3 и 7 устанавливают `current_goal = "get_weapon"` / `"get_ammo"` напрямую (без пайплайна), через `_add_memory` и `_bot_schedule_travel`. Пайплайн не запускается — NeedScores не считаются.

---

## 3. Фаза 1 — Контекст (AgentContext)

Файл: `context_builder.py`

`build_agent_context(agent_id, agent, state)` → `AgentContext`

Собирает снапшот мира для одного агента:

| Поле | Содержимое |
|------|------------|
| `self_state` | Полный dict агента |
| `location_state` | Данные текущей локации |
| `visible_entities` | Сосуществующие агенты + торговцы на той же локации |
| `known_targets` | Список известных целей (kill_stalker) |
| `world_context` | Глобальное состояние мира (выброс, ход) |

---

## 4. Фаза 2 — Потребности (NeedScores)

Файл: `needs.py`

### Формулы оценки потребностей

| Потребность | Формула | Диапазон |
|-------------|---------|----------|
| `survive_now` | `(30 - hp) / 20`, clamp | 0–1 (1.0 при hp ≤ 10) |
| `heal_self` | `(50 - hp) / 30`, clamp | 0–1 (1.0 при hp ≤ 20) |
| `eat` | `hunger / 100` | 0–1 |
| `drink` | `thirst / 100` | 0–1 |
| `sleep` | `sleepiness / 100` | 0–1 |
| `reload_or_rearm` | см. ниже | 0 или 0.6–0.7 |
| `avoid_emission` | 1.0 если выброс + опасная местность | 0, 0.3, 0.9, 1.0 |
| `get_rich` | `(1 - wealth_ratio) × 0.70 × (1 - reload_or_rearm) × (1 - survival_pressure)` | 0–1 |
| `hunt_target` | `0.8 × max(0.25, wealth_ratio) × (1 - survival_pressure)` | 0–1 |
| `unravel` | `0.75 × max(0.40, wealth_ratio) × (1 - survival_pressure)` | 0–1 |
| `leave_zone` | 1.0 если цель достигнута и не вышел | 0 или 1 |
| `trade` | 0.7 если артефакты есть + торговец рядом | 0 или 0.7 |

### `reload_or_rearm` — подробнее

```python
if not has_weapon:       → 0.65
elif not has_armor:      → 0.70
elif нет патронов для оружия: → 0.60
else:                    → 0.0
```

Патроны ищутся через `AMMO_FOR_WEAPON[weapon_type]` где `weapon_type = equipment["weapon"].get("type")`.

> ⚠️ **Баг с патронами**: Если предмет оружия хранит `"type": "weapon"` (категорию), а не ключ `"ak74"`, то `AMMO_FOR_WEAPON.get("weapon")` вернёт None, и проверка патронов не сработает (агент посчитает себя снаряжённым).

### Блокировки и подавления

```
1. reload_or_rearm подавляет get_rich:
   get_rich = get_rich × (1 - reload_or_rearm)
   → при reload_or_rearm=0.65: get_rich_max = 0.70 × 0.35 = 0.245 < 0.65 ✓

2. survival_pressure подавляет рисковые потребности:
   survival_pressure = max(survive_now, heal_self × 0.5)
   get_rich ×= (1 - survival_pressure)
   hunt_target ×= (1 - survival_pressure)
   unravel ×= (1 - survival_pressure)

3. global_goal_achieved обнуляет hunt_target и unravel,
   поднимает leave_zone ≥ 0.8

4. wealth_ratio = min(1, liquid_wealth / material_threshold)
   liquid_wealth = money + inventory_value (без стоимости снаряжения)
```

---

## 5. Фаза 3 — Намерение (Intent)

Файл: `intents.py`

### Жёсткие прерывания (проверяются первыми, обходят priority walk)

| Условие | Результат |
|---------|-----------|
| `avoid_emission > 0.05` | Если на безопасной местности → `INTENT_WAIT_IN_SHELTER` |
| `avoid_emission > 0.05` | Если на опасной → `INTENT_FLEE_EMISSION` |
| `survive_now ≥ 0.70` | `INTENT_ESCAPE_DANGER` |
| `heal_self ≥ 0.80` | `INTENT_HEAL_SELF` |
| `drink ≥ 0.90` | `INTENT_SEEK_WATER` |
| `eat ≥ 0.90` | `INTENT_SEEK_FOOD` |

### Priority walk (конкурентный проход по приоритетам)

Из оставшихся потребностей выбирается **единственное** намерение с наибольшим score > 0.05:

```
Приоритет  Потребность          Intent
───────────────────────────────────────────────────────────
1          survive_now          INTENT_ESCAPE_DANGER
2          heal_self            INTENT_HEAL_SELF
3          avoid_emission       INTENT_FLEE_EMISSION
4          drink                INTENT_SEEK_WATER
5          eat                  INTENT_SEEK_FOOD
6          sleep                INTENT_REST
7          reload_or_rearm      INTENT_RESUPPLY          ← снаряжение
8          maintain_group       INTENT_MAINTAIN_GROUP
9          help_ally            INTENT_ASSIST_ALLY
10         trade                INTENT_SELL_ARTIFACTS
11         get_rich             INTENT_GET_RICH           ← разбогатеть
12         hunt_target          INTENT_HUNT_TARGET
13         unravel_zone_mystery INTENT_SEARCH_INFORMATION
14         leave_zone           INTENT_LEAVE_ZONE
15         negotiate            INTENT_NEGOTIATE
16         join_group           INTENT_FORM_GROUP
(нет)      —                    INTENT_IDLE (fallback)
```

> Победитель — потребность с **наибольшим числовым значением** (не приоритетом). Порядок в таблице используется только для разрешения ничьих.

---

## 6. Фаза 4 — План (Plan)

Файл: `planner.py`

Для каждого intent существует отдельный builder. Планы состоят из `PlanStep[]` (1–3 шага).

### `INTENT_RESUPPLY` → `_plan_resupply`

```
1. Искать предмет в памяти (путешествие к последней известной локации с нужным предметом)
2. Только если wealth ≥ material_threshold:
      → купить у торговца (travel + STEP_TRADE_BUY_ITEM с правильным _buy_category)
3. Fallback (wealth < material_threshold):
      → вызвать _plan_get_rich (!!!) с искусственным INTENT_GET_RICH
```

**Определение `_buy_category`:**
- Нет оружия → `"weapon"`
- Нет брони → `"armor"`
- Нет патронов → `"ammo"` (с проверкой AMMO_FOR_WEAPON)

### `INTENT_GET_RICH` → `_plan_get_rich`

```
1. Если есть артефакты и есть торговец:
      → продать артефакты (travel + STEP_TRADE_SELL_ITEM)
2. Текущая локация имеет аномалию и не исследована:
      → STEP_EXPLORE_LOCATION
3. Найти лучшую аномальную локацию (по risk_tolerance):
      → STEP_TRAVEL_TO_LOCATION
4. Нет кандидатов:
      → STEP_WAIT
```

### `INTENT_SEEK_WATER` / `INTENT_SEEK_FOOD` → `_plan_seek_consumable`

```
1. Предмет в инвентаре → STEP_CONSUME_ITEM
2. Торговец на той же локации:
      a. money==0 и есть артефакты → [SELL_ARTIFACT, BUY_consumable]
      b. иначе → [BUY_consumable]
3. Торговец в другой локации:
      a. Возможно: CONSUME_ITEM для второстепенной потребности (оппортунистически)
      b. money==0 и артефакты → добавить SELL_ARTIFACT перед покупкой
      c. [TRAVEL, (SELL,) BUY]
```

---

## 7. Фаза 5 — Исполнение (Executor)

Файл: `executors.py`

`execute_plan_step(ctx, plan, state, world_turn)` исполняет текущий шаг плана:

| PlanStep | Executor | Описание |
|----------|----------|----------|
| `STEP_TRAVEL_TO_LOCATION` | `_exec_travel` | Делегирует в `_bot_schedule_travel` |
| `STEP_TRADE_BUY_ITEM` | `_exec_trade_buy` | Покупка у ко-локированного торговца |
| `STEP_TRADE_SELL_ITEM` | `_exec_trade_sell` | Продажа артефактов |
| `STEP_CONSUME_ITEM` | `_exec_consume` | Потребление предмета из инвентаря |
| `STEP_EXPLORE_LOCATION` | `_exec_explore` | Запланировать исследование |
| `STEP_SLEEP_FOR_HOURS` | `_exec_sleep` | Запланировать сон |
| `STEP_WAIT` | `_exec_wait` | Ничего не делать |
| `STEP_ASK_FOR_INTEL` | `_exec_ask_for_intel` | Спросить сталкеров об агенте |

### `_exec_trade_buy` — защита от неверных покупок

```python
# Никогда не покупать оружие если оно уже есть
if category in ("weapon", "equipment") and eq.get("weapon") is not None:
    return []

# Никогда не покупать броню если она уже есть
if category == "armor" and eq.get("armor") is not None:
    return []

# Для патронов: купить только калибр оружия агента
if category == "ammo":
    required_ammo = AMMO_FOR_WEAPON.get(weapon.get("type"))
    if not required_ammo:
        return []  # неизвестный калибр — ничего не покупаем
```

---

## 8. Глобальная цель vs. локальная задача снаряжения

Это центральная проблема и источник путаницы.

### Как они взаимодействуют

```
Глобальная цель (agent["global_goal"])   Локальный intent (текущий тик)
─────────────────────────────────────    ──────────────────────────────
"get_rich"                               INTENT_GET_RICH, INTENT_SELL_ARTIFACTS
"kill_stalker"                           INTENT_HUNT_TARGET
"unravel_zone_mystery"                   INTENT_SEARCH_INFORMATION
"leave_zone" (после достижения)         INTENT_LEAVE_ZONE

Всегда возможны:
                                         INTENT_RESUPPLY (снаряжение)
                                         INTENT_SEEK_WATER / FOOD (выживание)
                                         INTENT_HEAL_SELF (лечение)
                                         INTENT_ESCAPE_DANGER (критично)
```

### Где происходит смешение

**Проблема 1: Fallback INTENT_RESUPPLY → _plan_get_rich**

Когда НПЦ без снаряжения И бедный (`wealth < material_threshold`):
- `reload_or_rearm = 0.65` → выигрывает → `INTENT_RESUPPLY`
- `_plan_resupply` не может ни найти предмет по памяти, ни купить у торговца (недостаточно денег)
- → Вызывает `_plan_get_rich` в качестве fallback

**Результат:** агент физически выполняет действия по накоплению богатства (ходит к аномалиям, продаёт артефакты), но `current_goal` выставляется как `"resupply"` (через `_INTENT_TO_GOAL[INTENT_RESUPPLY]`). Внешне это выглядит корректно, но логически является смешением.

**Проблема 2: _pre_decision_equipment_maintenance в обход пайплайна**

Если патроны известны в памяти → агент едет за ними через `_pre_decision_equipment_maintenance`, устанавливая `current_goal = "get_ammo"`. Пайплайн не запускается → NeedScores не считаются → никаких survival checks → если агент одновременно умирает от жажды, этот путь всё равно отправит его за патронами.

**Проблема 3: `sell_artifacts` → `get_rich` в `_INTENT_TO_GOAL`**

```python
_INTENT_TO_GOAL = {
    "sell_artifacts":  "get_rich",   # ← продажа артефактов = "разбогатеть"
    "get_rich":        "get_rich",
    ...
}
```

Когда агент продаёт артефакты для финансирования воды (`_plan_seek_consumable` → SELL + BUY), но intent при этом `INTENT_SEEK_WATER`, то `current_goal` = `"restore_needs"` — это корректно. Но если агент выбирает `INTENT_SELL_ARTIFACTS` (через `trade` drive), `current_goal` = `"get_rich"`, даже если он продаёт чтобы выжить.

---

## 9. Таблица: intent → current_goal

Из `_INTENT_TO_GOAL` в `tick_rules.py`:

| Intent | current_goal | Глобальная цель? |
|--------|-------------|-----------------|
| `escape_danger` | `emergency_heal` | нет |
| `heal_self` | `emergency_heal` | нет |
| `flee_emission` | `emergency_shelter` | нет |
| `seek_water` | `restore_needs` | нет |
| `seek_food` | `restore_needs` | нет |
| `rest` | `restore_needs` | нет |
| `resupply` | `resupply` | нет |
| `sell_artifacts` | `get_rich` | **да** (смешение!) |
| `get_rich` | `get_rich` | **да** |
| `hunt_target` | `kill_stalker` | **да** |
| `search_information` | `unravel_zone` | **да** |
| `leave_zone` | `leave_zone` | **да** |
| `idle` | `idle` | нет |

---

## 10. Схема приоритетов конкуренции потребностей

```
HP ≤ 10 (survive_now ≥ 1.0)     → INTENT_ESCAPE_DANGER   [hard interrupt ≥ 0.7]
     ↓ нет
HP ≤ 50 (heal_self ≥ 0.8)       → INTENT_HEAL_SELF        [hard interrupt ≥ 0.8]
     ↓ нет
Выброс + опасная местность       → INTENT_FLEE_EMISSION / WAIT_IN_SHELTER
     ↓ нет
Жажда ≥ 90% (drink ≥ 0.90)      → INTENT_SEEK_WATER       [hard interrupt ≥ 0.9]
     ↓ нет
Голод ≥ 90% (eat ≥ 0.90)        → INTENT_SEEK_FOOD        [hard interrupt ≥ 0.9]
     ↓ нет
[Priority walk — по наибольшему score]

  reload_or_rearm=0.65  vs  get_rich_max≈0.245  → RESUPPLY побеждает
  reload_or_rearm=0.65  vs  drink=0.75           → SEEK_WATER побеждает (drink > 0.65)
  reload_or_rearm=0.60  vs  drink=0.55           → RESUPPLY побеждает (0.60 > 0.55)
```

**Числовой пример: НПЦ с АК без патронов, жажда 70%:**

```
reload_or_rearm = 0.60 (нет патронов)
drink           = 0.70 (жажда 70%)
get_rich        = (1 - wealth_ratio) × 0.70 × (1 - 0.60) = max ≈ 0.28

Priority walk:
  drink=0.70 vs reload_or_rearm=0.60 → drink wins → INTENT_SEEK_WATER
```

→ НПЦ с жаждой 70% и без патронов пойдёт за водой. ✓

**Числовой пример: НПЦ с АК без патронов, жажда 55%:**

```
reload_or_rearm = 0.60
drink           = 0.55
get_rich        = max ≈ 0.28

Priority walk → reload_or_rearm=0.60 wins → INTENT_RESUPPLY
```

→ НПЦ пойдёт за патронами. Но если нет ни денег ни торговца → fallback _plan_get_rich → идёт за артефактами.

---

## 11. Известные проблемы и пути решения

### Проблема A: Покупка неверных патронов (баг из отчёта)

**Симптом:** НПЦ с АК-74 тратит деньги на патроны 9×18 (калибр ПМ).

**Причина:** Если поле `"type"` у предмета оружия в слоте хранит строку `"weapon"` (категорию предмета), то:
- `AMMO_FOR_WEAPON.get("weapon")` → `None`
- В `_score_reload_or_rearm`: амmo-check не срабатывает → агент считает себя «снаряжённым»
- В `_exec_trade_buy` для `category="ammo"`: `required_ammo=None` → `return []`

Но если поле `"type"` хранит конкретный ключ предмета (`"ak74"` или `"pistol"`), то `AMMO_FOR_WEAPON.get("ak74")` → `"ammo_545"`, а для ПМ → `"ammo_9x18"`. Нужно проверить формат хранения предмета в слоте equipment.

**Правильная цепочка должна быть:**
1. `equipment["weapon"]` содержит поле с ключом типа (не `"type"`, который всегда `"weapon"`)
2. Либо добавить поле `"item_key"` или использовать `"subtype"`/`"item_type"` для lookup в AMMO_FOR_WEAPON

### Проблема B: RESUPPLY fallback в get_rich при бедности

**Симптом:** НПЦ без снаряжения и с малым кошельком демонстрирует поведение `get_rich` (идёт к аномалиям), но `current_goal = "resupply"`.

**Причина:** `_plan_resupply` явно вызывает `_plan_get_rich` когда `wealth < material_threshold`:
```python
# Phase 1 fallback: resource-gathering (treat as get_rich)
get_rich_intent = Intent(kind=INTENT_GET_RICH, ...)
return _plan_get_rich(ctx, get_rich_intent, state, world_turn)
```

**Следствие:** Разделение между «собираю деньги на снаряжение» и «собираю деньги для глобальной цели» отсутствует на уровне плана. Оба сценария ведут к одинаковым физическим действиям.

**Возможное решение:**
- Ввести отдельный intent `INTENT_FARM_FOR_RESUPPLY` (или flag в intent.reason)
- Или разделить `_plan_resupply` и `_plan_get_rich` так, чтобы первый не делегировал второму

### Проблема C: _pre_decision_equipment_maintenance не проверяет выживание

**Симптом:** Агент умирает от жажды по пути за патронами, потому что `_pre_decision_equipment_maintenance` (шаги 3, 7) отправляет его за патронами без проверки критических survival needs.

**Причина:** Этот блок выполняется до пайплайна, до вычисления NeedScores.

**Возможное решение:**
- Перед запуском шагов 3, 7, 8 (путешествие за предметами) проверять критические thresholds: если `thirst > 70` или `hunger > 70` или `hp < 40` — не прерывать пайплайн, дать survival интентам выиграть.

### Проблема D: sell_artifacts → current_goal = "get_rich"

**Симптом:** Когда агент продаёт артефакты ради `INTENT_SELL_ARTIFACTS` (trade drive), `current_goal` становится `"get_rich"`, хотя агент может продавать чтобы оплатить воду/патроны.

**Причина:** `_INTENT_TO_GOAL["sell_artifacts"] = "get_rich"` — грубое упрощение.

**Возможное решение:** Не связывать `sell_artifacts` с `get_rich` в `current_goal` — сохранять предыдущий goal или использовать `"trade"`.
