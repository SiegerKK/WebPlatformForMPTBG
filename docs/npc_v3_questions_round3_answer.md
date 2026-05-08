# Ответ на вопросы по NPC Brain v3 — Раунд 3

> Документ-ответ на `docs/npc_v3_questions_round3.md`.
>
> Цель: зафиксировать конкретные implementation decisions для PR 1, чтобы `PlanMonitor`, `brain_trace`, `active_plan_v3` и текущий `scheduled_action` не конфликтовали с существующим `tick_rules.py`.

---

## Короткое резюме решений

Для PR 1 принимаем консервативный подход:

1. **Не переписываем порядок всего `tick_zone_map()`**.
2. **`PlanMonitor` вставляем перед `_process_scheduled_action()`**, но только для bot stalker agents.
3. **`scheduled_action` остаётся runtime-механизмом исполнения**.
4. **`active_plan_v3` пока является wrapper/metadata поверх `scheduled_action`**, а не полноценной заменой плана.
5. **`brain_trace` пишется и при мониторинге активного плана, и при обычном v2 decision pipeline**.
6. **При abort очищаем и `scheduled_action`, и `action_queue`**, потому что очередь почти всегда является частью старого плана.
7. **`emergency_flee=True` считается non-interruptible** для PR 1.
8. **Тесты PlanMonitor делаем unit-level на минимальных state, плюс 1–2 интеграционных теста через `tick_zone_map()`**.

---

# 1. Бот или игрок: как фильтровать агентов в петле `scheduled_action`

## Решение

Использовать **комбинированную проверку**:

```python
def _is_v3_monitored_bot(agent: dict) -> bool:
    return (
        agent.get("archetype") == "stalker_agent"
        and agent.get("controller", {}).get("kind") == "bot"
        and agent.get("is_alive", True)
        and not agent.get("has_left_zone")
    )
```

## Почему не только `controller.kind == "bot"`

`controller.kind` — правильный основной критерий, потому что он отделяет bot от human. Но для безопасности лучше дополнительно проверять `archetype == "stalker_agent"`, чтобы `PlanMonitor` случайно не начал применять stalker-логику к сущностям другого типа, если они когда-нибудь окажутся в `state["agents"]`.

## Почему не только `archetype == "stalker_agent"`

Потому что human player тоже является `stalker_agent`. Если ориентироваться только на archetype, `PlanMonitor` начнёт вмешиваться в действия игрока.

## Что с торговцами и мутантами

Для PR 1:

- `PlanMonitor` применяется **только к bot stalker agents**.
- `traders` и `mutants` не мониторятся.
- Если у них когда-нибудь появятся свои `scheduled_action`, они должны идти через отдельные AI-модули, а не через stalker `PlanMonitor`.

`_process_scheduled_action()` при этом остаётся универсальным обработчиком, как сейчас: если у любого агента есть `scheduled_action`, он может его обработать. Меняется только то, что перед этим для bot stalker agent запускается `PlanMonitor`.

## Рекомендуемая вставка

```python
for agent_id, agent in state.get("agents", {}).items():
    if not agent.get("is_alive", True):
        continue
    if agent.get("has_left_zone"):
        continue

    sched = agent.get("scheduled_action")
    if not sched:
        continue

    if _is_v3_monitored_bot(agent):
        monitor_result = assess_scheduled_action_v3(
            agent_id=agent_id,
            agent=agent,
            scheduled_action=sched,
            state=state,
            world_turn=world_turn,
        )
        apply_plan_monitor_result(...)
        if monitor_result.decision in ("abort", "pause", "adapt"):
            # Не вызываем _process_scheduled_action для старого sched.
            continue

    new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
    events.extend(new_evs)
```

---

# 2. `emergency_flee` flag и `PlanMonitor`

## Решение

Выбрать вариант C:

> `emergency_flee=True` означает `interruptible=False`, и `PlanMonitor` обязан это уважать.

Для PR 1 `emergency_flee` нельзя прерывать из-за жажды, голода, сна, resupply или get_rich.

```python
if scheduled_action.get("emergency_flee"):
    return PlanMonitorResult(
        decision="continue",
        reason="Текущее действие — emergency_flee; его нельзя прерывать, чтобы агент не погиб от выброса и не попал в цикл отмены побега.",
        interruptible=False,
    )
```

## Почему

`emergency_flee` уже решает задачу с максимальным immediate risk: не умереть от выброса. Если агент остановится искать воду при `thirst=95`, он может погибнуть от выброса сразу, тогда как жажда обычно убивает не мгновенно.

Это не значит, что жажда игнорируется полностью. Она должна остаться в `brain_trace` как active pressure:

```json
{
  "decision": "continue",
  "reason": "Продолжаю emergency_flee несмотря на жажду 95%, потому что выброс опаснее прямо сейчас",
  "pressures": [
    {"key": "thirst", "urgency": 0.95},
    {"key": "emission_shelter", "urgency": 1.0}
  ]
}
```

## Что можно прерывать при `emergency_flee=True`

Почти ничего. Допустимые реакции:

1. **Continue** — основной случай.
2. **Adapt route** — если путь стал невозможным, но цель остаётся той же: добраться до укрытия.
3. **Fail/abort только технически** — если агент умер, вышел из Зоны, target location удалена или action некорректен.

Но не переключаться на `seek_water`, `eat`, `resupply`, `get_rich`.

---

# 3. `action_queue` при abort

## Решение

При `PlanMonitor abort` в PR 1 нужно очищать и `scheduled_action`, и `action_queue`.

```python
agent["scheduled_action"] = None
agent["action_queue"] = []
```

## Почему

`action_queue` почти всегда является продолжением старого намерения/плана. Если мы abort'им текущий `scheduled_action`, но оставим очередь, на следующем тике агент может продолжить выполнять нерелевантные действия.

Пример проблемы:

```text
План: travel_to_trader → sell_artifact → buy_water
PlanMonitor abort из-за выброса
scheduled_action очищен
action_queue всё ещё содержит sell_artifact → buy_water
```

Это создаёт скрытый баг: агент вроде отменил старый план, но старые хвостовые действия остались.

## Исключение на будущее

Когда появится полноценный `ActivePlan`, можно будет различать:

```text
ABORT  → очистить queue
PAUSE  → сохранить queue внутри paused plan
ADAPT  → частично переписать queue
```

Но для PR 1 безопаснее:

```text
abort = clear scheduled_action + clear action_queue
```

---

# 4. Двойная запись `brain_trace` в одном тике

## Решение

Двойная запись **желательна**, но не как перезапись.

Нужно хранить внутри одного `brain_trace` список событий текущего тика:

```json
agent["brain_trace"] = {
  "turn": 1234,
  "events": [
    {"mode": "plan_monitor", "decision": "abort", "reason": "Жажда стала критической"},
    {"mode": "decision", "intent_kind": "seek_water", "reason": "Критическая жажда"}
  ],
  "current_thought": "Прервал путь и ищу воду",
  "active_objective": "seek_water"
}
```

## Не нужен флаг `_brain_trace_written_this_tick`

Флаг запрещающий перезапись будет скрывать важную информацию. Если в одном тике произошло:

1. abort старого плана;
2. выбор нового intent;
3. создание нового scheduled_action;

то frontend должен иметь возможность это показать.

## Рекомендуемый helper

```python
def append_brain_trace_event(agent: dict, world_turn: int, event: dict) -> None:
    trace = agent.get("brain_trace")
    if not trace or trace.get("turn") != world_turn:
        trace = {
            "turn": world_turn,
            "events": [],
        }
        agent["brain_trace"] = trace

    trace.setdefault("events", []).append(event)

    # Ограничение размера на всякий случай.
    trace["events"] = trace["events"][-5:]
```

Дополнительно можно обновлять summary-поля:

```python
trace["current_thought"] = event.get("thought") or trace.get("current_thought")
trace["last_mode"] = event.get("mode")
trace["last_decision"] = event.get("decision")
```

## Важно

`brain_trace` — это не вечная история. Это состояние последнего/текущего тика для UI. Если нужно сохранить важное событие надолго, оно пишется в `agent["memory"]` / `memory_v3` как decision memory.

---

# 5. Где в `tick_zone_map` вставляется PlanMonitor: до или после деградации нужд

## Решение для PR 1

Не менять общий порядок `tick_zone_map()`.

`PlanMonitor` остаётся перед `_process_scheduled_action()`, то есть до блока деградации голода/жажды.

Но чтобы не пропустить кейс `thirst=88 → 93`, в `PlanMonitor` нужно использовать **projected survival state** для текущего тика.

## Почему не переносить PlanMonitor после деградации

Перенос после деградации кажется логичным, но он меняет порядок существующей симуляции:

```text
сейчас:
  1. scheduled actions
  2. hourly degradation
  3. emissions/combat/decisions

если перенести:
  1. degradation
  2. monitor scheduled actions
  3. process scheduled actions
```

Это может неожиданно изменить старые тесты и поведение travel/explore/sleep.

## Рекомендуемый компромисс

Добавить helper:

```python
def project_agent_needs_after_tick(agent: dict, state: dict) -> dict:
    projected = {
        "hp": agent.get("hp", 100),
        "hunger": agent.get("hunger", 0),
        "thirst": agent.get("thirst", 0),
        "sleepiness": agent.get("sleepiness", 0),
    }

    new_minute = (state.get("world_minute", 0) + 1) % 60
    if new_minute == 0:
        projected["hunger"] = min(100, projected["hunger"] + 3)
        projected["thirst"] = min(100, projected["thirst"] + 5)
        projected["sleepiness"] = min(100, projected["sleepiness"] + 4)

        if projected["thirst"] >= 80:
            projected["hp"] = max(0, projected["hp"] - 2)
        if projected["hunger"] >= 80:
            projected["hp"] = max(0, projected["hp"] - 1)

    return projected
```

`PlanMonitor` использует projected values для interrupt scoring, но не мутирует agent.

## Пример

```text
world_minute = 59
thirst = 88
projected_thirst = 93
PlanMonitor видит projected_thirst=93
→ может pause/abort travel до того, как агент пропустит критический тик
```

---

# 6. `brain_trace` при `continue`

## Решение

Для PR 1 выбрать вариант A:

> `PlanMonitor` пишет лёгкий `brain_trace` при каждом assess, включая `continue`.

Но при `continue` запись должна быть компактной и не попадать в долговременную память.

Пример:

```json
{
  "mode": "plan_monitor",
  "decision": "continue",
  "thought": "Продолжаю путь: текущий план всё ещё актуален",
  "scheduled_action": {
    "type": "travel",
    "target_id": "bar",
    "turns_remaining": 6
  },
  "top_pressures": [
    {"key": "thirst", "value": 0.55},
    {"key": "resupply_ammo", "value": 0.40}
  ]
}
```

## Почему

Если при `continue` ничего не писать, frontend будет показывать устаревшую мысль. Игрок откроет профиль и увидит старый trace, хотя агент уже несколько тиков продолжает идти.

## Что не делать

Не писать `continue` каждый тик в `agent["memory"]`. Это быстро засорит память.

Правило:

```text
brain_trace — обновлять каждый тик
memory — писать только значимые события: abort, pause, adapt, completed, failed, new decision
```

---

# 7. `active_plan_v3` — когда создаётся в первый раз

## Решение

Для PR 1 достаточно, чтобы `PlanMonitor` умел работать только с `scheduled_action`.

Но при первом обнаружении `scheduled_action` без `active_plan_v3` он может создать **retrofit wrapper**.

То есть выбрать гибрид A + C:

```text
PlanMonitor не требует active_plan_v3 для работы.
Если active_plan_v3 отсутствует, но есть scheduled_action:
  создать минимальный wrapper для UI/debug.
```

## Минимальный wrapper

```json
agent["active_plan_v3"] = {
  "version": 1,
  "source": "retrofit_from_scheduled_action",
  "status": "active",
  "objective_key": null,
  "created_turn": 1234,
  "last_evaluated_turn": 1234,
  "current_step": {
    "kind": "scheduled_action",
    "scheduled_action_type": "travel",
    "target_id": "bar",
    "turns_remaining": 6,
    "turns_total": 12
  },
  "steps": [],
  "debug_summary": "Выполняю travel → bar"
}
```

## Почему не обязательно создавать внутри `_run_bot_decision_v2_inner` в PR 1

`schedule_action` сейчас создаётся глубоко через executor/legacy helpers. Если пытаться сразу прокинуть полноценный `ActivePlan` из `_run_bot_decision_v2_inner`, PR станет шире.

Для PR 1 достаточно:

```text
scheduled_action = source of runtime truth
active_plan_v3 = optional metadata/debug wrapper
```

В PR 2 можно уже создавать `active_plan_v3` прямо там, где создаётся `scheduled_action`.

---

# 8. Travel, который прерывается на многохоповом маршруте

## Решение

Если `PlanMonitor` abort'ит travel, он должен записать memory entry сам, потому что `_process_scheduled_action()` в этом случае не вызывается и legacy `travel_interrupted` не будет записан.

Это не дублирование, потому что событие происходит в другой ветке управления.

## Что записывать

При abort travel нужно записать:

```text
где агент сейчас находится
какой current hop был отменён
какая была финальная цель
почему план прерван
какое новое давление победило
```

Пример effects:

```json
{
  "action_kind": "plan_monitor_travel_aborted",
  "scheduled_action_type": "travel",
  "current_location_id": "garbage",
  "cancelled_hop_target": "bar_road",
  "cancelled_final_target": "bar",
  "reason": "critical_thirst",
  "dominant_pressure": "thirst",
  "dominant_pressure_value": 0.93
}
```

Пример summary:

```text
Прервал путь к «Бар», остановился в «Свалка»: жажда стала критической.
```

## Важная деталь модели travel

В текущей модели агент физически перемещается только когда `_process_scheduled_action()` завершает hop. Если `PlanMonitor` сработал до `_process_scheduled_action`, агент ещё **не добрался** до target текущего hop.

Поэтому memory должна писать:

```text
current_location_id = agent["location_id"]
cancelled_hop_target = sched["target_id"]
cancelled_final_target = sched.get("final_target_id", sched["target_id"])
```

Не надо писать, что агент добрался до `target_id`, если `_process_scheduled_action()` не был выполнен.

## Anti-spam

Чтобы не спамить память, писать такую запись только при `abort/pause/adapt`, но не при `continue`.

---

# 9. Тесты для `PlanMonitor`: реальный state или mock

## Решение

Нужны оба уровня тестов.

### 9.1. Unit tests для `assess_scheduled_action_v3`

Использовать минимальный state/mock.

Цель: быстро и изолированно проверить правила monitor'а.

Пример:

```python
state = {
    "world_turn": 100,
    "world_minute": 59,
    "emission_active": False,
    "locations": {
        "bar": {"terrain_type": "buildings", "name": "Бар"},
        "swamp": {"terrain_type": "swamp", "name": "Болото"},
    },
}
agent = {
    "id": "bot1",
    "archetype": "stalker_agent",
    "controller": {"kind": "bot"},
    "is_alive": True,
    "location_id": "bar",
    "hp": 30,
    "hunger": 20,
    "thirst": 96,
    "sleepiness": 10,
    "memory": [],
}
sched = {"type": "travel", "target_id": "swamp", "turns_remaining": 6}
```

Unit tests должны покрыть:

```text
continue обычного travel
abort при critical thirst
continue emergency_flee даже при thirst=95
abort/continue при emission threat
projected thirst на minute boundary
clear action_queue при abort через apply result
brain_trace event создаётся при continue
brain_trace event создаётся при abort
```

### 9.2. Integration tests через `tick_zone_map`

Нужны 1–2 теста, чтобы убедиться, что вставка в `tick_zone_map` работает.

Примеры:

```text
bot with scheduled travel + critical thirst
→ tick_zone_map
→ scheduled_action старого travel отменён
→ action_queue очищена
→ bot получил новое решение seek_water или хотя бы прошёл decision pipeline
```

```text
bot with emergency_flee + critical thirst
→ tick_zone_map
→ scheduled_action не отменён
→ trace объясняет, почему emergency_flee продолжен
```

## Где хранить тесты

Как в ответе раунда 2:

```text
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_tick_integration.py
```

---

# 10. `_run_bot_decision_v2_inner` и `brain_trace` в PR 1

## Решение

В PR 1 `brain_trace` должен заполняться **и** при `PlanMonitor`, **и** при обычном `_run_bot_decision_v2_inner`.

Иначе все боты без `scheduled_action` не получат `brain_trace`, а frontend будет неполным.

## Почему это важно

PR 1 должен дать видимость поведения НПЦ. Если trace будет только у ботов, у которых уже есть активное действие, мы не увидим самое важное: почему свободный бот выбрал новый intent и plan.

## Как минимально встроить

В `_run_bot_decision_v2_inner` уже есть момент, где построены:

```text
ctx
needs
intent
plan
```

После выбора/исполнения первого шага нужно вызвать helper:

```python
write_decision_brain_trace_from_v2(
    agent=agent,
    world_turn=world_turn,
    ctx=ctx,
    needs=needs,
    intent=intent,
    plan=plan,
    events=events,
)
```

Этот helper должен записывать event в тот же `agent["brain_trace"]["events"]`, что и `PlanMonitor`.

Пример event:

```json
{
  "mode": "decision",
  "decision": "new_intent",
  "intent_kind": "seek_water",
  "intent_score": 0.93,
  "thought": "Ищу воду: жажда критическая",
  "plan": {
    "intent_kind": "seek_water",
    "steps": [
      {"kind": "travel_to_location", "label": "Идти к торговцу"},
      {"kind": "trade_buy_item", "label": "Купить воду"}
    ],
    "confidence": 0.7
  },
  "top_drives": [
    {"key": "drink", "value": 0.93},
    {"key": "reload_or_rearm", "value": 0.55}
  ]
}
```

## Связь с `explain_intent.py`

`explain_intent.py` не заменяется. Он остаётся read-only debug-инструментом, который может построить объяснение по текущему state.

`brain_trace` отличается тем, что он фиксирует **фактическое решение**, принятое в тике.

Для PR 1 можно переиспользовать идеи и формат из `explain_intent.py`, но не вызывать его как источник истины внутри decision pipeline, потому что он заново строит context/needs/intent/plan и может объяснить гипотетическое решение, а не фактически исполненное.

---

# Дополнительные implementation details для PR 1

## A. Новый модуль `plan_monitor.py`

Рекомендуемый путь:

```text
backend/app/games/zone_stalkers/decision/plan_monitor.py
```

Минимальные сущности:

```python
@dataclass
class PlanMonitorResult:
    decision: Literal["continue", "pause", "abort", "adapt"]
    reason: str
    dominant_pressure: str | None = None
    dominant_pressure_value: float | None = None
    interruptible: bool = True
    should_run_decision_pipeline: bool = False
    should_clear_action_queue: bool = False
    memory_event: dict | None = None
    trace_event: dict | None = None
```

Для PR 1 реально использовать:

```text
continue
abort
```

`pause/adapt` можно оставить как future values.

## B. Новый модуль `brain_trace.py`

Рекомендуемый путь:

```text
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
```

Минимальные helpers:

```python
append_brain_trace_event(agent, world_turn, event)
write_plan_monitor_trace(agent, world_turn, monitor_result, scheduled_action)
write_decision_brain_trace_from_v2(agent, world_turn, ctx, needs, intent, plan, events)
```

## C. Применение результата monitor'а

Рекомендуемый helper в `tick_rules.py` или рядом:

```python
def _apply_plan_monitor_result(
    agent_id: str,
    agent: dict,
    sched: dict,
    result: PlanMonitorResult,
    state: dict,
    world_turn: int,
) -> list[dict]:
    events = []

    if result.trace_event:
        append_brain_trace_event(agent, world_turn, result.trace_event)

    if result.decision == "continue":
        return events

    if result.decision == "abort":
        agent["scheduled_action"] = None
        agent["action_queue"] = []
        if agent.get("active_plan_v3"):
            agent["active_plan_v3"]["status"] = "aborted"
            agent["active_plan_v3"]["aborted_turn"] = world_turn
            agent["active_plan_v3"]["abort_reason"] = result.reason

        # Важно: не ставить action_used=True.
        # Тогда бот сможет пройти _run_bot_decision_v2_inner в этом же тике.
        if result.memory_event:
            _add_memory(...)

        events.append({
            "event_type": "plan_monitor_aborted_action",
            "payload": {
                "agent_id": agent_id,
                "reason": result.reason,
                "dominant_pressure": result.dominant_pressure,
            },
        })

    return events
```

## D. Как пустить бота в `_run_bot_decision_v2_inner` в том же тике

Если monitor abort'ит action на шаге 1, нужно не ставить `action_used=True`. Тогда на шаге 3 существующий цикл bot decisions увидит:

```text
scheduled_action = None
action_used = False
controller.kind = bot
```

и запустит `_run_bot_decision_v2(...)`.

Это желаемое поведение.

---

# Итоговое решение по 10 вопросам

| № | Решение |
|---|---|
| 1 | Фильтр monitor'а: `archetype == stalker_agent` + `controller.kind == bot`. `_process_scheduled_action` остаётся для всех. |
| 2 | `emergency_flee=True` = non-interruptible. Не прерывать ради жажды/еды/resupply. |
| 3 | При abort очищать `scheduled_action` и `action_queue`. |
| 4 | `brain_trace` хранит несколько events за текущий тик; не нужен флаг запрета перезаписи. |
| 5 | Не менять порядок `tick_zone_map`; PlanMonitor остаётся перед process scheduled, но использует projected needs на hour boundary. |
| 6 | При `continue` PlanMonitor пишет лёгкий trace, но не пишет долговременную memory. |
| 7 | PR 1 может работать только с `scheduled_action`; при необходимости PlanMonitor создаёт retrofit `active_plan_v3`. |
| 8 | При abort travel PlanMonitor сам пишет memory entry о прерывании маршрута. |
| 9 | Unit tests на минимальном state + 1–2 integration tests через `tick_zone_map`. |
| 10 | `brain_trace` писать и из PlanMonitor, и из `_run_bot_decision_v2_inner`. `explain_intent.py` остаётся read-only debug рядом. |

---

# Минимальный scope PR 1 после уточнений раунда 3

## Backend

1. Добавить `decision/plan_monitor.py`.
2. Добавить `decision/debug/brain_trace.py`.
3. Вставить monitor в шаг 1 `tick_zone_map()` перед `_process_scheduled_action()`.
4. Фильтровать только bot stalker agents.
5. Уважать `emergency_flee=True`.
6. При abort очищать `scheduled_action` и `action_queue`.
7. Не ставить `action_used=True` при abort.
8. Добавить projected needs helper.
9. Писать `brain_trace` при monitor continue/abort.
10. Писать `brain_trace` в конце `_run_bot_decision_v2_inner` по фактическому intent/plan.
11. Писать memory entry при monitor abort travel/explore.

## Tests

1. `backend/tests/decision/v3/test_plan_monitor.py`
2. `backend/tests/decision/v3/test_brain_trace.py`
3. `backend/tests/decision/v3/test_tick_integration.py`

## Frontend

В этом PR можно только начать отображать `agent.brain_trace` в `AgentProfileModal` простым collapsible/debug block. Более красивую панель оставить на следующий PR.

---

# Главная архитектурная позиция

PR 1 не должен пытаться заменить весь v2 pipeline. Его задача — устранить самый болезненный дефект текущей системы:

```text
Активный scheduled_action больше не должен полностью отключать мышление НПЦ.
```

Но сделать это нужно аккуратно:

```text
не ломать human actions
не ломать emergency_flee
не ломать legacy scheduled_action processing
не раздувать memory continue-записями
не делать ActivePlan полноценной новой системой раньше времени
```

После PR 1 у нас появится наблюдаемая и тестируемая основа для следующих этапов:

```text
item_needs → memory_v3 → objective scoring → полноценный ActivePlan → NPCBrain
```
