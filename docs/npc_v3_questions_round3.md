# Вопросы по NPC Brain v3 — Раунд 3

> Контекст: прочитал `docs/npc_v3_questions_round2_answer.md` и детально изучил `tick_rules.py` (реальный код).
> Все вопросы конкретны и привязаны к конкретным строкам или конструкциям в существующем коде.

---

## 1. Бот или игрок: как фильтровать агентов в петле `scheduled_action`

Текущий код `tick_zone_map()` (шаг 1) обрабатывает `scheduled_action` для **всех** агентов:

```python
# tick_rules.py, ~строки 170–178
for agent_id, agent in state.get("agents", {}).items():
    if not agent.get("is_alive", True):
        continue
    if agent.get("has_left_zone"):
        continue
    sched = agent.get("scheduled_action")
    if sched:
        new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
        events.extend(new_evs)
```

`PlanMonitor` должен запускаться только для **бот-агентов**.

Вопрос: как правильно определить бота в этой петле?

```python
# Вариант A: по полю controller.kind
if agent.get("controller", {}).get("kind") == "bot":
    monitor_result = ...

# Вариант B: по archetype
if agent.get("archetype") == "stalker_agent":
    monitor_result = ...
```

Боты-торговцы, мутанты — у них тоже могут быть `scheduled_action`? Нужно ли их обходить?

---

## 2. `emergency_flee` flag и `PlanMonitor`

В `_process_scheduled_action` уже есть важное исключение:

```python
if not sched.get("emergency_flee") and _is_emission_threat(agent, state):
    agent["scheduled_action"] = None
    ...
```

Смысл: `emergency_flee` travel нельзя прерывать даже во время выброса, иначе агент застрянет в петле отмены.

Вопрос: нужно ли `PlanMonitor` тоже проверять `emergency_flee`?

Если агент бежит от выброса (`emergency_flee=True`) и при этом у него `thirst=95`, должен ли `PlanMonitor`:

- Вариант A: **не прерывать** — жажда не критичнее немедленной смерти от выброса
- Вариант B: **прерывать** — `select_intent` выберет `seek_water` с score 0.95, и это станет сигналом к abort
- Вариант C: `emergency_flee` задаёт флаг `interruptible=False` на уровне `scheduled_action`, и PlanMonitor обязан его уважать

Что правильно?

---

## 3. `action_queue` при abort — что с ней происходит?

В конце `_process_scheduled_action` есть обработка очереди:

```python
queue = agent.get("action_queue", [])
if queue and not agent.get("scheduled_action"):
    next_action = queue.pop(0)
    agent["action_queue"] = queue
    agent["scheduled_action"] = next_action
```

Если `PlanMonitor` принимает решение `abort` и очищает `scheduled_action`, `_process_scheduled_action` не вызывается вообще — и очередь остаётся нетронутой.

На следующем тике `scheduled_action` будет `None`, но в очереди может лежать нерелевантный план.

Вопрос: при `PlanMonitor abort` нужно ли **тоже очищать `action_queue`**?

Или пусть бот в этом же тике пройдёт `_run_bot_decision_v2_inner` и сам перепишет очередь?

---

## 4. Двойная запись `brain_trace` в одном тике

Если `PlanMonitor` принял решение `abort`, в одном тике произойдёт следующее:

1. `PlanMonitor` пишет `brain_trace` с `mode="plan_monitor"`, `decision="abort"`.
2. В шаге 3 (`_run_bot_decision_v2_inner`) бот принимает новое решение и может перезаписать `brain_trace` с `mode="decision"`.

Вопрос: это желательное или нежелательное поведение?

- Если **желательное** — финальный `brain_trace` должен содержать оба события (abort + new decision) в одном объекте, или достаточно только последнего?
- Если **нежелательное** — нужен специальный флаг `agent["_brain_trace_written_this_tick"] = True`, чтобы избежать перезаписи?

Рекомендованная схема для PR 1?

---

## 5. Где в `tick_zone_map` вставляется петля `PlanMonitor`: до или после деградации нужд?

Ответ раунда 2 говорит: вставить `PlanMonitor` перед `_process_scheduled_action` (шаг 1).

Но шаг 2 — деградация голода/жажды — идёт **после**.

```text
Шаг 1 (текущий): process scheduled actions (+ PlanMonitor сюда)
Шаг 2:           degrade needs / apply hunger-thirst damage
```

Значит, когда `PlanMonitor` оценивает `thirst`, он видит значение **до деградации** этого тика.

Например:
- `thirst=88` в начале тика
- PlanMonitor не видит проблемы (порог 90%), продолжает travel
- Шаг 2: `thirst = 88 + 5 = 93%` — превышено, но уже поздно

Вопрос: это принятый trade-off для PR 1, или PlanMonitor нужно перенести **после** шага 2?

---

## 6. `brain_trace` при `continue` — кто его пишет?

Если `PlanMonitor` принял решение `continue`, управление переходит в `_process_scheduled_action`.

Но `_process_scheduled_action` ничего не знает о `brain_trace`.

Вопрос: как `brain_trace` обновляется в случае `continue`?

- Вариант A: `PlanMonitor` пишет `brain_trace` **до** вызова `_process_scheduled_action`, вне зависимости от решения
- Вариант B: `brain_trace` пишется только при abort/pause/adapt, при `continue` не пишется (или только обновляется `turn` и `turns_remaining`)
- Вариант C: `_process_scheduled_action` получает callback/hook для обновления `brain_trace`

Что правильнее для PR 1?

---

## 7. `active_plan_v3` — когда создаётся в первый раз?

Ответ раунда 2 говорит, что `active_plan_v3` — это wrapper над `scheduled_action`.

Но `scheduled_action` создаётся глубоко внутри `_run_bot_decision_v2_inner` (через executor), а не в отдельном месте.

Вопрос: кто и когда впервые создаёт `agent["active_plan_v3"]`?

- Вариант A: `PlanMonitor`, если видит `scheduled_action` без `active_plan_v3` — создаёт retrofit wrapper
- Вариант B: новый код внутри `_run_bot_decision_v2_inner` при создании `scheduled_action`
- Вариант C: при первом запуске `PlanMonitor` он просто работает без `active_plan_v3`, а тот появляется в PR 2

Для PR 1 достаточно, если `PlanMonitor` работает только с `scheduled_action` и игнорирует `active_plan_v3`?

---

## 8. Что происходит с travel, который только что завершил хоп

В `_process_scheduled_action` при travel-хопе агент перемещается в промежуточную локацию и сразу создаёт **новый** `scheduled_action` для следующего хопа:

```python
agent["scheduled_action"] = _next_sched  # следующий хоп
```

Если `PlanMonitor` запускается **до** `_process_scheduled_action`, он видит `scheduled_action` хопа №3 → говорит `continue` → хоп №3 выполняется → создаётся хоп №4.

Но на следующем тике `PlanMonitor` снова оценит хоп №4. Это правильное поведение.

А вот другой сценарий: `PlanMonitor` видит хоп №3, говорит `abort`, очищает `scheduled_action`. Агент в этом тике принимает новое решение и может начать совершенно другой маршрут.

Вопрос: если агент прерывает многохоповый маршрут на промежуточном хопе, нужно ли записывать в память **куда именно он успел добраться** и **куда изначально шёл**?

Сейчас эта логика есть в `_process_scheduled_action` (записывает `travel_interrupted`). При `PlanMonitor` abort — аналогичную запись должен делать **PlanMonitor** или это дублирование?

---

## 9. Тесты для `PlanMonitor`: нужен ли реальный `state` или достаточно mock?

В `backend/tests/decision/v3/test_plan_monitor.py` нужно тестировать `assess_scheduled_action_v3(...)`.

Эта функция принимает `agent`, `scheduled_action`, `state`, `world_turn`.

Вопрос: тесты для `PlanMonitor` должны использовать **реальный state** (как в `conftest.py` есть `sample_state`) или достаточно минимального mock?

Например:
```python
# Минимальный mock
state = {
    "locations": {"bar": {"terrain_type": "buildings"}, "swamp": {"terrain_type": "swamp"}},
    "emission_active": False,
}
agent = {"hp": 30, "thirst": 96, "location_id": "bar", "controller": {"kind": "bot"}}
sched = {"type": "travel", "target_id": "swamp", "turns_remaining": 6}
```

Или нужен более реалистичный state с торговцами, агентами, routing?

---

## 10. `_run_bot_decision_v2_inner` и `brain_trace` — как они стыкуются в PR 1?

Ответ раунда 2 сказал: `brain_trace.py` и `explain_intent.py` живут рядом, `brain_trace.py` описывает **фактическое** поведение.

Но `_run_bot_decision_v2_inner` — это большая функция в `tick_rules.py`. В PR 1 её планируется трогать минимально.

Вопрос: в PR 1 `brain_trace` заполняется **только** в момент `PlanMonitor` оценки, или ещё и когда бот проходит полный `_run_bot_decision_v2_inner`?

Если только PlanMonitor — тогда боты, у которых нет `scheduled_action` (они идут по `_run_bot_decision_v2_inner`), **не получат `brain_trace`** в PR 1. Это допустимо?

Или нужно сразу добавить вызов `write_brain_trace(agent, ...)` в конец `_run_bot_decision_v2_inner`?
