# Ответы на вопросы по NPC Brain v3 — Раунд 4

> Контекст: ответы предназначены для подготовки PR 1 по NPC Brain v3 в ветке `copilot/rewrite-npc-equipment-rules`.  
> Главная цель PR 1: добавить безопасный слой наблюдаемости и минимальную переоценку активных действий, не ломая текущую v2/legacy-логику.

---

## Короткое резюме решений

Для PR 1 принимаем такие решения:

1. Коэффициенты деградации нужно вынести в общие константы сразу, не дублировать.
2. Двойной проход после `PlanMonitor abort` допустим, но нужен guard: максимум один forced replan на агента за тик.
3. `brain_trace.events` фиксируем как стабильный минимальный контракт уже в PR 1.
4. `active_plan_v3` в PR 1 остаётся debug/retrofit metadata; при нормальном завершении действия обновляем статус, но не строим полноценный lifecycle.
5. `pause/adapt` описываем в типах как будущие значения, но поведение в PR 1 реализуем только для `continue` и `abort`.
6. Для memory-записей abort нужен dedup/throttle уже в PR 1.
7. Human-агенты должны остаться полностью legacy; это надо закрепить тестом.
8. Новый `event_type="plan_monitor_aborted_action"` полезен и должен быть стабильным, но минимальным.
9. `_is_v3_monitored_bot` лучше положить в `decision/plan_monitor.py`.
10. Acceptance-критерий: после `tick_zone_map` у каждого живого bot stalker должен быть `brain_trace` за текущий тик, кроме строго оговорённых случаев до внедрения полного покрытия.

---

## 1. `project_agent_needs_after_tick`: где source of truth для коэффициентов?

### Решение

Выбираем вариант **A**: вынести коэффициенты в общие константы уже в PR 1.

Не стоит дублировать:

```text
hunger +3
thirst +5
sleepiness +4
hp -2 при thirst >= 80
hp -1 при hunger >= 80
```

Если оставить дублирование даже временно, `PlanMonitor` быстро начнёт расходиться с реальной деградацией. Это особенно опасно, потому что PR 1 как раз добавляет принятие решений на границе тика.

### Предлагаемое место

Минимально инвазивный вариант:

```text
backend/app/games/zone_stalkers/rules/tick_constants.py
```

или, если не хочется создавать новый файл:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
```

но в верхней части файла как named constants.

Лучше всё-таки отдельный файл, чтобы `plan_monitor.py` не импортировал весь `tick_rules.py`.

### Константы

```python
# backend/app/games/zone_stalkers/rules/tick_constants.py

HUNGER_INCREASE_PER_HOUR = 3
THIRST_INCREASE_PER_HOUR = 5
SLEEPINESS_INCREASE_PER_HOUR = 4

CRITICAL_THIRST_THRESHOLD = 80
CRITICAL_HUNGER_THRESHOLD = 80

HP_DAMAGE_PER_HOUR_CRITICAL_THIRST = 2
HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER = 1
```

### Helper

```python
def project_agent_needs_after_tick(agent: dict, *, hour_boundary: bool) -> dict:
    projected = {
        "hp": agent.get("hp", 100),
        "hunger": agent.get("hunger", 0),
        "thirst": agent.get("thirst", 0),
        "sleepiness": agent.get("sleepiness", 0),
    }

    if not hour_boundary:
        return projected

    projected["hunger"] = min(100, projected["hunger"] + HUNGER_INCREASE_PER_HOUR)
    projected["thirst"] = min(100, projected["thirst"] + THIRST_INCREASE_PER_HOUR)
    projected["sleepiness"] = min(100, projected["sleepiness"] + SLEEPINESS_INCREASE_PER_HOUR)

    if projected["thirst"] >= CRITICAL_THIRST_THRESHOLD:
        projected["hp"] = max(0, projected["hp"] - HP_DAMAGE_PER_HOUR_CRITICAL_THIRST)

    if projected["hunger"] >= CRITICAL_HUNGER_THRESHOLD:
        projected["hp"] = max(0, projected["hp"] - HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER)

    return projected
```

### Почему не вариант C

Не проектировать деградацию — значит оставить дыру:

```text
thirst=88
PlanMonitor: continue
degradation: thirst=93
решение уже упущено
```

Для PR 1 projection — нормальный компромисс без перестановки больших блоков `tick_zone_map`.

---

## 2. Двойной запуск pipeline в одном тике: ограничиваем ли?

### Решение

Двойной запуск после `PlanMonitor abort` **разрешён**, потому что именно ради этого делается abort: НПЦ должен бросить старое длительное действие и принять новое решение в тот же тик.

Но нужен guard:

```text
максимум 1 forced replan after PlanMonitor abort на агента за тик
```

### Почему guard нужен

Без guard в будущем легко получить цепочку:

```text
PlanMonitor abort
→ _run_bot_decision_v2_inner
→ executor создал scheduled_action
→ другой hook снова вызвал monitor/replan
→ повтор
```

Даже если сейчас такого цикла нет, лучше сразу заложить защиту.

### Предлагаемый transient flag

```python
agent["_v3_replanned_after_monitor_turn"] = world_turn
```

Перед forced replan:

```python
if agent.get("_v3_replanned_after_monitor_turn") == world_turn:
    # не запускать повторно
    continue

agent["_v3_replanned_after_monitor_turn"] = world_turn
bot_evs = _run_bot_decision_v2(agent_id, agent, state, world_turn)
```

### Очистка

Это transient/debug поле. Его можно:

- оставить в state до следующего тика и перезаписывать;
- или чистить в конце `tick_zone_map` вместе с другими transient flags.

Для чистоты лучше в конце тика удалить:

```python
agent.pop("_v3_replanned_after_monitor_turn", None)
```

---

## 3. `brain_trace.events`: финальный контракт для фронта

### Решение

Да, минимальный контракт нужно зафиксировать уже в PR 1 и добавить в TS-тип `AgentForProfile`. Не стоит оставлять `unknown`, потому что фронт быстро начнёт зависеть от фактической формы данных.

### Минимальный backend contract

```json
{
  "turn": 1234,
  "schema_version": 1,
  "current_thought": "Прерываю путь к Болоту: жажда стала критической.",
  "mode": "plan_monitor",
  "events": [
    {
      "turn": 1234,
      "mode": "plan_monitor",
      "decision": "abort",
      "reason": "critical_thirst",
      "summary": "Прервал travel из-за критической жажды",
      "scheduled_action_type": "travel",
      "dominant_pressure": {
        "key": "thirst",
        "value": 0.95
      }
    },
    {
      "turn": 1234,
      "mode": "decision",
      "decision": "new_intent",
      "reason": "Критическая жажда — срочный поиск воды",
      "summary": "Выбран intent seek_water",
      "intent_kind": "seek_water",
      "intent_score": 0.95
    }
  ]
}
```

### Обязательные поля event

```ts
type BrainTraceEvent = {
  turn: number;
  mode: 'plan_monitor' | 'decision' | 'scheduled_action' | 'system';
  decision:
    | 'continue'
    | 'abort'
    | 'pause'
    | 'adapt'
    | 'new_intent'
    | 'execute_step'
    | 'complete'
    | 'no_op';
  summary: string;
  reason?: string;
  scheduled_action_type?: string | null;
  intent_kind?: string | null;
  intent_score?: number | null;
  dominant_pressure?: {
    key: string;
    value: number;
  } | null;
};
```

### Ограничение размера

```text
brain_trace.events.length <= 5
```

При добавлении нового event:

```python
events = (old_events + [new_event])[-5:]
```

### Верхнеуровневые поля `brain_trace`

```ts
type BrainTrace = {
  schema_version: 1;
  turn: number;
  mode: 'plan_monitor' | 'decision' | 'scheduled_action' | 'system';
  current_thought: string;
  events: BrainTraceEvent[];
  active_plan?: unknown;
  top_drives?: Array<{ key: string; value: number; rank: number }>;
};
```

Для PR 1 `active_plan` и `top_drives` можно сделать optional.

---

## 4. `active_plan_v3` lifecycle при нормальном завершении action

### Решение

Для PR 1 выбираем вариант **B/C hybrid**:

```text
active_plan_v3 остаётся debug/retrofit metadata,
но при нормальном завершении действия его статус обновляется.
```

Не надо полностью удалять объект сразу, потому что фронту полезно видеть последний завершённый план. Но и не надо обещать полноценный lifecycle.

### Поведение PR 1

Если `scheduled_action` завершился и нового `scheduled_action` нет:

```python
agent["active_plan_v3"]["status"] = "completed"
agent["active_plan_v3"]["completed_turn"] = world_turn
agent["active_plan_v3"]["last_scheduled_action"] = old_action_type
```

Если после завершения текущего action стартовал следующий action из `action_queue`:

```python
agent["active_plan_v3"]["status"] = "active"
agent["active_plan_v3"]["current_scheduled_action"] = next_action["type"]
agent["active_plan_v3"]["updated_turn"] = world_turn
```

Если `active_plan_v3` отсутствует — не создавать его в completion path насильно. Его создаёт retrofit/monitor path.

### Почему не очищать сразу

Если удалить `active_plan_v3`, фронт потеряет контекст:

```text
что только что закончилось?
почему NPC теперь свободен?
был ли план completed или aborted?
```

Поэтому оставляем snapshot, но небольшой.

---

## 5. `action_queue` при `pause/adapt`: фиксировать ли future-safe API?

### Решение

В PR 1 в типе результата можно оставить значения:

```text
continue | abort | pause | adapt
```

но поведение фиксируем только для:

```text
continue
abort
```

Для `pause/adapt` в PR 1:

```text
не реализовано;
не должно возвращаться из production logic;
если вернулось — treated as continue или safe no-op.
```

### Контракт PR 1

```text
continue:
  scheduled_action сохраняется
  action_queue сохраняется

abort:
  scheduled_action очищается
  action_queue очищается
  active_plan_v3.status = "aborted"
  запускается forced replan, если разрешено
```

### Документируем future semantics, но не тестируем как implemented

В документации можно оставить будущий контракт:

```text
pause:
  scheduled_action очищается или приостанавливается
  action_queue сохраняется
  active_plan_v3.status = "paused"

adapt:
  scheduled_action заменяется
  head queue заменяется
  tail queue сохраняется
```

Но в acceptance tests PR 1 нельзя требовать `pause/adapt`.

---

## 6. Память при `abort`: как дедуплицировать повторяющиеся прерывания?

### Решение

Dedup/throttle нужен уже в PR 1.

Иначе при баге или нестабильном состоянии агент быстро засорит `agent["memory"]`, а legacy memory всё ещё ограничена `MAX_AGENT_MEMORY = 2000`.

### Минимальный throttle

Не писать новую decision-запись, если за последние `N` тиков уже была такая же запись.

Рекомендуемое значение:

```python
PLAN_MONITOR_MEMORY_DEDUP_TURNS = 10
```

### Ключ дедупликации

```text
action_kind
reason
scheduled_action_type
cancelled_final_target
```

Пример:

```python
def _recent_plan_monitor_abort_exists(agent, world_turn, dedup_turns, signature):
    for mem in reversed(agent.get("memory", [])):
        if world_turn - mem.get("world_turn", 0) > dedup_turns:
            return False

        effects = mem.get("effects", {})
        if effects.get("action_kind") != "plan_monitor_action_aborted":
            continue

        existing_signature = (
            effects.get("reason"),
            effects.get("scheduled_action_type"),
            effects.get("cancelled_final_target"),
        )

        if existing_signature == signature:
            return True

    return False
```

### При dedup

Если запись не пишется в `memory`, `brain_trace.events` всё равно обновляется. Это разные вещи:

```text
memory = долговременная история
brain_trace = текущее объяснение для UI
```

---

## 7. Совместимость с human-агентом

### Решение

Да, нужен отдельный тест-инвариант.

Human agent с `scheduled_action` должен идти полностью по legacy path:

```text
human + scheduled_action
→ PlanMonitor не вызывается
→ _process_scheduled_action работает как раньше
→ brain_trace не обязан появиться
```

### Почему тест важен

Сейчас шаг 1 `tick_zone_map()` обрабатывает `scheduled_action` для всех живых агентов. Если случайно вставить `PlanMonitor` без фильтра, он начнёт отменять действия игрока. Это критический регресс.

### Тест

```python
def test_plan_monitor_does_not_run_for_human_with_scheduled_action():
    state = make_state_with_human_scheduled_travel(...)
    before_sched = copy.deepcopy(state["agents"]["agent_p0"]["scheduled_action"])

    new_state, events = tick_zone_map(state)

    # Проверяем, что поведение legacy:
    # scheduled_action обработан обычным путём,
    # нет plan_monitor_aborted_action,
    # нет brain_trace от PlanMonitor.
```

Если трудно проверить “не вызывается”, проверяем наблюдаемые эффекты:

```text
нет event_type == plan_monitor_aborted_action
нет memory.effects.action_kind == plan_monitor_action_aborted
```

---

## 8. Какие event'ы наружу при abort обязаны быть стабильными?

### Решение

Новый event нужен уже в PR 1.

`brain_trace` полезен для UI, memory полезна для истории агента, но `events` нужны для tick consumers, тестов и потенциального live feed.

Фиксируем минимальный event:

```json
{
  "event_type": "plan_monitor_aborted_action",
  "payload": {
    "agent_id": "agent_ai_1",
    "scheduled_action_type": "travel",
    "reason": "critical_thirst",
    "dominant_pressure": {
      "key": "thirst",
      "value": 0.95
    }
  }
}
```

### Optional payload

Можно добавить, но не считать обязательным:

```json
{
  "cancelled_target": "swamp",
  "cancelled_final_target": "bar",
  "current_location_id": "garbage",
  "turns_remaining": 6
}
```

### Стабильный контракт PR 1

Обязательные поля:

```text
agent_id
scheduled_action_type
reason
dominant_pressure.key
dominant_pressure.value
```

Остальное optional.

---

## 9. Где физически размещать helper `_is_v3_monitored_bot`

### Решение

Выбираем вариант **B**:

```text
backend/app/games/zone_stalkers/decision/plan_monitor.py
```

И оттуда импортируем в `tick_rules.py`.

### Почему не `tick_rules.py`

Если положить helper в `tick_rules.py`, unit tests для `plan_monitor.py` будут либо импортировать большой legacy-модуль, либо дублировать фильтр.

### Почему не отдельный `agent_filters.py`

Пока рано. Один маленький helper не стоит отдельного модуля. Если фильтров станет больше — вынесем позже.

### Функция

```python
def is_v3_monitored_bot(agent: dict) -> bool:
    if not agent.get("is_alive", True):
        return False
    if agent.get("has_left_zone"):
        return False
    if agent.get("archetype") != "stalker_agent":
        return False
    if agent.get("controller", {}).get("kind") != "bot":
        return False
    return True
```

### Почему нужен и `archetype`, и `controller.kind`

`controller.kind == "bot"` отличает NPC от игрока.  
`archetype == "stalker_agent"` исключает мутантов, торговцев и будущих non-stalker bots.

Для PR 1 мониторим только bot stalkers.

---

## 10. Минимальный acceptance-критерий для `brain_trace` у bot stalkers

### Решение

Для PR 1 принимаем вариант **A с оговоркой по умершим/вышедшим агентам**:

```text
После tick_zone_map у каждого живого bot stalker, который не has_left_zone,
должен быть brain_trace.turn == world_turn_before_increment.
```

Это сильный и полезный acceptance-критерий.

### Почему не вариант B

Если `brain_trace` будет только у тех, кто попал в monitor/decision ветку, фронт снова получит дырки:

```text
у одного NPC есть “мысль”
у другого нет
почему — непонятно
```

PR 1 как раз должен сделать UI-наблюдаемость системной.

### Почему не вариант C

Ранние ветки вроде `_bot_pickup_on_arrival`, `_bot_sell_on_arrival`, `_pre_decision_equipment_maintenance` тоже являются поведением агента. Если они обходят `_run_bot_decision_v2_inner`, значит надо писать минимальный trace:

```text
mode = "system"
decision = "execute_step" или "legacy_action"
summary = "Выполнил legacy-действие до decision pipeline"
```

### Практичный компромисс PR 1

Если сложно покрыть все ранние return сразу, допускается временный критерий:

```text
В PR 1.0:
  brain_trace обязателен для:
    - bot stalker с scheduled_action;
    - bot stalker, прошедшего _run_bot_decision_v2_inner.

В PR 1.1:
  brain_trace обязателен для всех живых bot stalkers,
  включая ранние legacy ветки.
```

Но лучше сразу стремиться к полному A.

### Предлагаемая функция fallback

В конце обработки каждого bot stalker можно вызывать:

```python
ensure_brain_trace_for_tick(agent, world_turn)
```

Если trace уже есть — ничего не делает.  
Если trace нет — пишет минимальный системный trace.

```python
def ensure_brain_trace_for_tick(agent: dict, world_turn: int) -> None:
    trace = agent.get("brain_trace")
    if trace and trace.get("turn") == world_turn:
        return

    agent["brain_trace"] = {
        "schema_version": 1,
        "turn": world_turn,
        "mode": "system",
        "current_thought": "Нет нового решения: агент продолжает текущее состояние.",
        "events": [{
            "turn": world_turn,
            "mode": "system",
            "decision": "no_op",
            "summary": "В этот тик не было нового решения NPC Brain."
        }],
    }
```

Тогда acceptance test становится простым и стабильным.

---

## Итоговый план PR 1 после раунда 4

### Backend

1. Добавить `rules/tick_constants.py`.
2. Добавить `decision/plan_monitor.py`:
   - `is_v3_monitored_bot`;
   - `PlanMonitorResult`;
   - `assess_scheduled_action_v3`;
   - projection helper или импорт projection helper.
3. Добавить `decision/debug/brain_trace.py`:
   - `append_brain_trace_event`;
   - `write_plan_monitor_trace`;
   - `write_decision_trace`;
   - `ensure_brain_trace_for_tick`.
4. Вставить PlanMonitor перед `_process_scheduled_action` для bot stalkers.
5. При abort:
   - записать brain_trace event;
   - записать memory с dedup;
   - emit `plan_monitor_aborted_action`;
   - clear `scheduled_action`;
   - clear `action_queue`;
   - пометить `active_plan_v3.status = "aborted"`;
   - разрешить один forced replan.
6. В `_run_bot_decision_v2_inner` добавить запись `brain_trace` по фактическому выбранному intent/plan.
7. В конце обработки bot stalker обеспечить fallback `brain_trace`, если его ещё нет.

### Frontend

1. Добавить типы `BrainTrace`, `BrainTraceEvent` в `AgentForProfile`.
2. В `AgentProfileModal.tsx` добавить collapsible/debug блок.
3. Для PR 1 допустим простой readable блок, не обязательно сложная визуализация.

### Tests

Новые тесты:

```text
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_tick_integration.py
```

Минимальные кейсы:

```text
bot + scheduled_action + critical thirst → abort + queue cleared + trace + event
bot + emergency_flee + critical thirst → continue
human + scheduled_action + critical thirst → legacy, no monitor abort
bot + scheduled_action + continue → trace exists
bot without scheduled_action + decision pipeline → trace exists
abort memory dedup within N ticks
all alive bot stalkers have brain_trace.turn == world_turn_before_increment
```

---

## Финальная позиция

PR 1 должен быть маленьким по архитектуре, но жёстким по контрактам:

```text
PlanMonitor работает только для bot stalkers.
Emergency flee нельзя прерывать.
Abort очищает scheduled_action и action_queue.
BrainTrace имеет стабильный контракт.
Memory abort-записи дедуплицируются.
У каждого живого bot stalker появляется brain_trace на тик.
```

Это даст понятную основу для следующих PR без большого переписывания `tick_rules.py`.
