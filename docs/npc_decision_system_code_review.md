# Ревью системы принятия решений НПЦ — Актуальное состояние кода

> Дата: 2026-03-22  
> Охват: `backend/app/games/zone_stalkers/rules/tick_rules.py` (v1-каскад) +
> `backend/app/games/zone_stalkers/decision/` (v2-архитектура)

---

## 1. Общая архитектура

### Два параллельных движка

| | Где живёт | Как вызывается | Влияет на игру |
|---|---|---|---|
| **v1 (каскад)** | `tick_rules.py` → `_run_bot_action_inner()` | Каждый тик для каждого бота без `scheduled_action` | **Да** — единственный принимающий решения движок |
| **v2 (теневой режим)** | `decision/` (5 модулей) | Только если `state["_v2_decision_pipeline"] == True` | **Нет** — только пишет `agent["_v2_context"]` для наблюдения |

v2 включается вручную через команду `debug_toggle_v2_pipeline`. Пока включён — каждый тик для каждого живого бота запускается полный pipeline (Context → Needs → Intent → Plan), результат сохраняется в `agent["_v2_context"]`, но реальные решения всё равно принимает v1-каскад.

### Взаимодействие компонентов v2

```
build_agent_context()          context_builder.py
       ↓  AgentContext
evaluate_needs()               needs.py → NeedScores (16 drives, float [0,1])
       ↓  NeedScores
select_intent()                intents.py → Intent (kind + score + reason)
       ↓  Intent
build_plan()                   planner.py → Plan (1–3 PlanSteps)
       ↓  Plan
execute_plan_step()            executors.py   ← НЕ вызывается в shadow mode
```

`executors.py` определён и полностью реализован, но в shadow-режиме его не вызывают — тик только вычисляет результат до `build_plan()` включительно.

---

## 2. Что сделано хорошо

- **Разделение ответственности** в v2 чёткое: каждый файл делает ровно одну вещь.
- **Нулевые побочные эффекты** shadow-режима: вся цепочка v2 обёрнута в `try/except`, ошибка не ломает игру.
- **Совместимость через `bridges.py`**: `plan_from_scheduled_action` / `scheduled_action_from_plan_step` позволяют постепенно переносить логику не меняя агентский state.
- **Полные тесты**: `tests/test_npc_decision_v2.py` и `tests/test_zone_stalkers_tick.py` (317 тестов) покрывают оба движка.
- **Debug/explain**: `explain_agent_decision()` вызывает оба движка и возвращает структурированный diff — удобно для сравнения поведения.
- **Документация**: `npc_decision_tree.md`, `migration_plan_from_tick_rules.md`, spec + addendum описывают намерения архитектуры.

---

## 3. Рудиментарные и избыточные элементы

### 3.1 `_describe_bot_decision` (tick_rules.py:4788)

```python
def _describe_bot_decision(agent, events, state):
    tree = _describe_bot_decision_tree(agent, events, state)
    return {"goal": tree["goal"], "action": tree["chosen"]["action"], "reason": tree["chosen"]["reason"]}
```

Это тонкая обёртка над `_describe_bot_decision_tree`, вызываемая ровно в одном месте (world_rules.py). Никакой логики не добавляет. **Можно удалить, заменив вызов прямым обращением к `_describe_bot_decision_tree`.**

### 3.2 `_GOAL_MIN_FLOOR = 0.10` в needs.py

Константа объявлена, но нигде не используется в коде `needs.py`. Комментарий в спецификации обещает «минимальный floor для goal-driven drives», но в реальном коде floor не применяется — функции возвращают 0.0 при wealth > threshold. **Мёртвый код.**

### 3.3 Константы `_HUNGER_CRITICAL = 80`, `_THIRST_CRITICAL = 80` в needs.py

Объявлены в заголовке `needs.py` как «original critical threshold from tick_rules», но в формуле `eat = hunger/100` и `drink = thirst/100` не используются. В v1 порог критической еды — 70, в v2 — нет порога вообще (линейная шкала). Константы оставляют ложный след о намерении выровнять поведение, которое так и не было реализовано. **Рудимент.**

### 3.4 `STEP_HEAL_ALLY`, `STEP_START_DIALOGUE`, `STEP_JOIN_COMBAT`, `STEP_RETREAT_FROM_COMBAT`, `STEP_FOLLOW_LEADER`, `STEP_SHARE_SUPPLIES` в plan.py

Шесть констант step-видов объявлены, но ни в `planner.py`, ни в `executors.py` не используются. Соответствующих executor-функций нет. **Преждевременная декларация без реализации.** Пока они не нужны — это шум в коде.

### 3.5 `known_locations.has_trader` вычисляется неправильно (context_builder.py:183)

```python
"has_trader": any(
    a.get("archetype") == "trader_agent" and a.get("location_id") == loc_id
    for a in locations.get(loc_id, {}).get("agents", [])
)
```

`locations[loc_id]["agents"]` — это список **строковых ID** агентов, не объектов. Итерация `a.get(...)` по строкам всегда вернёт `False`. В результате `has_trader` в `known_locations` всегда `False`. Это поле нигде не читается в v2 (planner.py использует `_nearest_trader_location()` напрямую), поэтому баг пока бессимптомный, но является **скрытой ошибкой**.

### 3.6 Дублирование `_EMISSION_DANGEROUS_TERRAIN`

Одно и то же frozenset определено в трёх местах:

| Файл | Расположение | Вид |
|---|---|---|
| `tick_rules.py` | строка 53 | модульный уровень `_EMISSION_DANGEROUS_TERRAIN` |
| `needs.py` | внутри `_score_avoid_emission()` | локальная переменная |
| `intents.py` | внутри `select_intent()` | локальная переменная |

Нет единого источника правды. Изменение одного не меняет другие.

### 3.7 Дублирование логики определения «предупреждён ли агент о выбросе»

`_is_emission_warned()` в `needs.py` и идентичный inline-цикл в `_run_bot_action_inner()` (tick_rules.py:3402–3416) решают одну и ту же задачу разными кодовыми путями. Функция v2 более читаема (остановка при нахождении обоих событий), но оба варианта идут в разных направлениях итерации: v2 использует `reversed()`, v1 — прямой цикл, накапливающий максимум.

---

## 4. Логические конфликты между v1 и v2

### 4.1 Порог критического HP

| Движок | Порог аварийного лечения | Влияние |
|---|---|---|
| v1 | `hp <= 30` → немедленно лечиться | Реальное поведение |
| v2 `survive_now` | 1.0 при `hp ≤ 10`, 0.0 при `hp > 30` | Только для отображения |
| v2 hard interrupt | `survive_now >= 0.70` ≡ `hp ≤ **16**` | Расчётный порог интерраппта |

**Конфликт**: v1 аварийно лечится при HP=30, v2 hard interrupt был бы при HP=16. Если и когда v2 станет основным движком, поведение изменится — агенты будут реагировать намного позже.

### 4.2 Порог голода и жажды

| Движок | Порог | Значение |
|---|---|---|
| v1 `EMERGENCY: Eat` | `hunger >= 70` | Реальное поведение |
| v1 `EMERGENCY: Drink` | `thirst >= 70` | Реальное поведение |
| Survival needs (tick_rules.py:145) | `hunger >= 80`, `thirst >= 80` | Нанесение HP урона |
| v2 `eat = hunger/100` | Линейная шкала, нет порога | Отображение / будущее |
| needs.py `_HUNGER_CRITICAL = 80` | Объявлено, не используется | Мёртвый код |

**Конфликт**: в v2 нет дискретного порога — `eat` при hunger=70 равен 0.70, что ниже `_NEGLIGIBLE_THRESHOLD=0.05` (*нет, это выше порога*), но выше hard interrupt 0.80 (hunger=80). Переход на v2 изменит срочность: v2 начнёт реагировать на голод при любом значении >5, а не только при ≥70.

### 4.3 Wealth gate — разные знаки условия

| | Условие | Эффект |
|---|---|---|
| v1 GOAL SELECTION | `wealth >= threshold` → pursue goal | Глобальная цель активна |
| v2 `get_rich` | `(1 - wealth_ratio) * 0.70` | Давление уменьшается при богатстве |
| v2 `hunt_target` | `0.8 * max(0.25, wealth_ratio)` | Давление **растёт** при богатстве |
| v2 `unravel` | `0.75 * max(0.40, wealth_ratio)` | Давление **растёт** при богатстве |

**Конфликт**: в v1 бедный агент (`wealth < threshold`) не идёт за глобальной целью — занимается сбором ресурсов. В v2 `hunt_target` для бедного агента `kill_stalker` = `0.8 * max(0.25, 0)` = **0.20** > 0.05, то есть намерение hunting всегда активно. Это противоречит v1, где бедный убийца сначала копит деньги. При переходе на v2 поведение существенно изменится.

### 4.4 `trade` drive — условие trader_colocated всегда False

```python
def _score_trade(agent, ctx):
    has_artifacts = any(i.get("type") in artifact_types for i in inventory)
    trader_colocated = any(e.get("is_trader") for e in ctx.visible_entities)
    if has_artifacts and trader_colocated:
        return 0.7
    return 0.0
```

Торговцы хранятся в `state["traders"]`, а не в `state["agents"]`. В `build_agent_context()` `visible_entities` строится из `state["agents"]`, причём `is_trader = other.get("archetype") == "trader_agent"`. Торговцы в `state["traders"]` там не перечисляются. **Результат**: `trader_colocated` всегда `False`, `trade` score всегда `0.0`. Intent `INTENT_SELL_ARTIFACTS` из v2 никогда не выбирается по этому пути.

### 4.5 Отсутствие интеграции `execute_plan_step` в цикл тика

Plan builder в `planner.py` создаёт планы с реальными шагами (STEP_TRAVEL_TO_LOCATION, STEP_CONSUME_ITEM и т.д.), а `executors.py` умеет их выполнять. Однако в shadow-режиме `execute_plan_step` **не вызывается** — вычисляется только `build_plan()`. Если переключить агента на полный v2 Pipeline без вызова executors, все планы будут висеть без исполнения. Отсутствует явный guard или флаг «v2 acts» vs «v2 observes».

### 4.6 `maintain_group` drive → `INTENT_FOLLOW_GROUP_PLAN` (несоответствие)

В priority map:

```python
("maintain_group", INTENT_FOLLOW_GROUP_PLAN, "Нужды группы"),
```

Драйв называется `maintain_group` (сохранить/поддержать группу), но маппится на `INTENT_FOLLOW_GROUP_PLAN` (следовать плану группы). `INTENT_MAINTAIN_GROUP` объявлен в `intent.py` как отдельная константа, но в priority map не используется. Это семантическое расхождение: агент с желанием «поддержать группу» получает намерение «выполнять план группы», что может иметь иную семантику.

### 4.7 Дублированный Dijkstra в `_run_bot_action_inner` vs `_dijkstra_reachable_locations`

Код flee_emission в `_run_bot_action_inner` (строки 3420–3455) содержит inline-реализацию Дейкстры: собственные переменные `_dijk_heap`, `_dijk_dist`, цикл. Функция `_dijkstra_reachable_locations()` (строка 1468) делает то же самое и используется в 5+ других местах. **Дублирование логики** увеличивает риск расхождения при изменениях (например, при учёте `closed` рёбер или весов `travel_time`).

---

## 5. Неоптимальные места

### 5.1 O(M) сканирование памяти в `_entities_from_memory` (context_builder.py)

На каждый тик на каждого агента итерируется весь `agent["memory"]` (до 2000 записей). Для 20 агентов = 40 000 операций/тик только для context_builder. Комментарий сам указывает на это: *«could be optimised in Phase 5+ with a memory index»*. Аналогично `_locations_from_memory` и `_hazards_from_memory`.

### 5.2 Повторный вызов `_find_nearest_trader_location` в `_run_bot_action_inner`

Внутри одного вызова `_run_bot_action_inner` функция `_find_nearest_trader_location` вызывается до 7 раз (для heal, eat, drink, weapon, armor, ammo, sell). Каждый раз BFS обходит весь граф (до 20 хопов). **Можно кэшировать результат в начале функции.**

### 5.3 Множественные `_agent_wealth` вызовы в одной функции

`_agent_wealth(agent)` вычисляет сумму `money + inv_values + eq_values` итерацией по inventory/equipment. В `_run_bot_action_inner` вызывается минимум дважды: при `_equip_wealth` (строка 3646) и при `wealth` (строка 3918). Inventory бота редко меняется внутри одного тика — можно вычислить один раз.

### 5.4 `_score_reload_or_rearm` — отсутствует проверка достаточного запаса патронов

Функция возвращает score ≠ 0 только если `has_ammo_score < 0.5` (меньше 10 патронов при `_DESIRED_AMMO_RESERVE = 20`). При 10–19 патронах score = 0, то есть агент не пополняет запас до порогового. В v1 нет вообще проверки *количества* патронов — только наличие. Это намеренное расхождение нигде не задокументировано.

### 5.5 `planner._plan_get_rich` — 5 путей кода, 2 из 5 реальные

```python
def _plan_get_rich(...):
    if has_artifacts and trader_loc:
        if trader_loc == agent_loc:   # sell immediately
        else:                         # travel then sell
    # Otherwise explore — delegate to legacy
    return Plan([STEP_LEGACY_SCHEDULED_ACTION], confidence=0.5)
```

В 60% случаев (нет артефактов) возвращает `STEP_LEGACY_SCHEDULED_ACTION`, что передаёт управление обратно в v1. По сути planner для `get_rich` — это stub с одним реальным кейсом. Аналогично `_plan_search_information`, `_plan_upgrade_equipment`, `_plan_follow_group`, `_plan_assist_ally` — все 4 возвращают только `STEP_LEGACY_SCHEDULED_ACTION`.

### 5.6 `_exec_legacy_passthrough` — нулевой executor

```python
def _exec_legacy_passthrough(...):
    # The actual decision is handled by the legacy cascade in tick_rules.
    # This step simply signals "let the old code decide" — no side effects here.
    return []
```

Функция ничего не делает. В shadow-mode это безопасно, но при включении v2 как реального движка этот executor не будет вызывать нужный v1-код. **Заглушка без механизма активации.**

---

## 6. Сводная таблица проблем

| # | Категория | Файл | Описание | Критичность |
|---|---|---|---|---|
| P1 | Конфликт логики | `needs.py` vs `tick_rules.py` | Порог HP: v1=30, v2 hard-interrupt=16 | 🔴 Высокая |
| P2 | Конфликт логики | `needs.py` vs `tick_rules.py` | `hunt_target` активен при любом богатстве (нарушает wealth gate) | 🔴 Высокая |
| P3 | Баг | `needs.py` (`_score_trade`) | `trader_colocated` всегда `False` → trade score всегда 0.0 | 🔴 Высокая |
| P4 | Баг | `context_builder.py` (строка 183) | `has_trader` в known_locations — итерация по строкам вместо объектов | 🟡 Средняя |
| P5 | Конфликт логики | `intents.py` | `maintain_group` → `INTENT_FOLLOW_GROUP_PLAN` вместо `INTENT_MAINTAIN_GROUP` | 🟡 Средняя |
| P6 | Рудимент | `needs.py` | `_GOAL_MIN_FLOOR = 0.10` объявлена, но не используется | 🟢 Низкая |
| P7 | Рудимент | `needs.py` | `_HUNGER_CRITICAL = 80`, `_THIRST_CRITICAL = 80` — мёртвый код | 🟢 Низкая |
| P8 | Рудимент | `plan.py` | 6 STEP_* констант объявлены без реализации в executors | 🟢 Низкая |
| P9 | Дублирование | `needs.py`, `intents.py`, `tick_rules.py` | `_EMISSION_DANGEROUS_TERRAIN` в 3 местах | 🟢 Низкая |
| P10 | Дублирование | `tick_rules.py` (строки 3420–3455) | Inline Dijkstra дублирует `_dijkstra_reachable_locations()` | 🟡 Средняя |
| P11 | Производительность | `context_builder.py` | O(M) сканирование памяти 3× за context build | 🟡 Средняя |
| P12 | Производительность | `tick_rules.py` | `_find_nearest_trader_location` вызывается до 7 раз/тик/агент | 🟡 Средняя |
| P13 | Неполнота | `executors.py` | `_exec_legacy_passthrough` — нулевой executor, нет механизма делегирования | 🟡 Средняя |
| P14 | Неполнота | `planner.py` | 4 builders возвращают только `STEP_LEGACY_SCHEDULED_ACTION` | 🟡 Средняя |
| P15 | Рудимент | `tick_rules.py` (строка 4788) | `_describe_bot_decision` — тонкая обёртка без логики | 🟢 Низкая |

---

## 7. Рекомендации по очерёдности исправлений

### Немедленно (до включения v2 как основного движка)

1. **P3**: исправить `_score_trade` — обращаться к `state["traders"]` напрямую или проверять `known_traders` из context.
2. **P1**: согласовать порог HP. Либо изменить `_HP_SURVIVE_NOW_UPPER` на 30 и `_HARD_INTERRUPT_SURVIVE_NOW` на 0.0 (= 30), либо документально зафиксировать намеренное расхождение.
3. **P2**: добавить wealth gate в `_score_hunt_target` аналогично v1: если `wealth_ratio < 0.25` → вернуть 0.0 (или floor).

### Приоритет 2 (чистка кода)

4. **P9**: вынести `_EMISSION_DANGEROUS_TERRAIN` в общий модуль (`balance/constants.py` или `decision/constants.py`).
5. **P10**: заменить inline Dijkstra в `_run_bot_action_inner` вызовом `_dijkstra_reachable_locations()`.
6. **P4**: исправить `has_trader` — итерировать по `state["traders"].values()` проверяя `location_id`.
7. **P5**: в priority map заменить `INTENT_FOLLOW_GROUP_PLAN` на `INTENT_MAINTAIN_GROUP` для drive `maintain_group`, либо документировать намеренный маппинг.

### Приоритет 3 (технический долг)

8. **P11/P12**: добавить кэш результатов `_find_nearest_trader_location` и `_agent_wealth` внутри тика.
9. **P6, P7, P8, P15**: удалить мёртвый код — `_GOAL_MIN_FLOOR`, `_HUNGER_CRITICAL`, `_THIRST_CRITICAL`, неиспользованные STEP_* константы, `_describe_bot_decision`.

### Перед Phase 5 (полная замена v1)

10. **P13**: в `_exec_legacy_passthrough` добавить реальный dispatch — либо вызывать соответствующую v1-функцию, либо реализовать отдельный executor.
11. **P14**: реализовать полноценные builders для `_plan_search_information`, `_plan_upgrade_equipment`, `_plan_follow_group`, `_plan_assist_ally`.
12. Добавить guard в tick-loop: флаг `_v2_acts` отдельно от `_v2_decision_pipeline`, чтобы нельзя было случайно включить «исполняемый» v2 без executor-реализаций.

---

## 8. Статус компонентов по Phase

| Компонент | Phase (spec) | Реальное состояние | Замечание |
|---|---|---|---|
| `context_builder` | 1 | ✅ Реализован | Баг в `has_trader` (P4) |
| `needs.py` | 2 | ✅ Реализован | 3 неиспользованные константы |
| `intents.py` | 3 | ✅ Реализован | Маппинг `maintain_group` спорный |
| `planner.py` | 4 | ⚠️ Частично | 4 builders — заглушки на legacy |
| `executors.py` | 4 | ⚠️ Частично | Реализован, но не вызывается |
| `bridges.py` | 4 | ✅ Реализован | Работает корректно |
| `social/` | 6 | 🔴 Не реализован | Только заготовки |
| `groups/` | 7 | 🔴 Не реализован | Только заготовки |
| `combat/` | 8 | 🔴 Не реализован | Пустой `__init__.py` |
| Shadow mode integration | — | ✅ Работает | Только observability, не act |

---

*Ревью подготовлено на основе прямого анализа актуального кода. Все номера строк указаны для текущего состояния ветки copilot/refactor-npc-decision-architecture.*
