# PR 1 Review — NPC Brain v3 PlanMonitor / BrainTrace

> Ветка: `copilot/rewrite-npc-equipment-rules`  
> Документ: проверка реализации PR 1 после внесения `PlanMonitor`, `brain_trace`, frontend-блока и v3-тестов.  
> Статус: PR 1 в целом реализован, но есть несколько точечных доработок перед merge.

---

## 1. Важное уточнение по предыдущему ревью

Предыдущая претензия:

```text
tick_rules.py передаёт state=state в brain_trace helpers,
а brain_trace.py не принимает state.
```

На актуальной версии ветки это уже **не ошибка**.

Сейчас `brain_trace.py` содержит:

```python
def append_brain_trace_event(..., state: dict[str, Any] | None = None) -> None:
    ...

def write_plan_monitor_trace(..., state: dict[str, Any] | None = None) -> None:
    ...

def write_decision_brain_trace_from_v2(..., state: dict[str, Any] | None = None) -> None:
    ...

def ensure_brain_trace_for_tick(..., state: dict[str, Any] | None = None) -> None:
    ...
```

Также `brain_trace.py` добавляет `world_time` через `_time_payload()`.

Значит:

```text
TypeError из-за unexpected keyword argument 'state' больше быть не должно.
```

Это исправлено.

---

## 2. Общая оценка завершённости PR 1

PR 1 можно оценить как:

```text
~85–90% готовности по логике PR 1
```

Крупные логические части сделаны:

- `PlanMonitor` вынесен в отдельный модуль.
- `tick_rules.py` вызывает `PlanMonitor` перед legacy `_process_scheduled_action`.
- Human agents не мониторятся.
- `emergency_flee` не прерывается.
- Critical thirst / hunger / hp могут abort активный `scheduled_action`.
- При abort очищаются `scheduled_action` и `action_queue`.
- Эмитится `plan_monitor_aborted_action`.
- Появился `brain_trace`.
- `brain_trace` имеет `schema_version`, `turn`, `world_time`, `mode`, `current_thought`, `events`.
- Есть frontend-типизация и UI-блок в `AgentProfileModal`.
- Есть v3-тесты для `PlanMonitor`, `brain_trace`, tick integration.
- `_v3_*` transient поля очищаются в конце тика.
- Старые state blobs не должны ломаться, потому что новые поля выставляются через `setdefault`.

То есть PR 1 уже не выглядит как “кусок архитектуры без интеграции”. Он реально встроен в tick lifecycle.

---

## 3. Что сделано хорошо

### 3.1. Правильное место интеграции

`PlanMonitor` вызывается до `_process_scheduled_action`.

Это правильно: он получает шанс отменить длительное действие до того, как legacy-обработка уменьшит `turns_remaining` или завершит действие.

### 3.2. Фильтр bot stalker сделан правильно

`is_v3_monitored_bot()` проверяет:

```python
is_alive
not has_left_zone
archetype == "stalker_agent"
controller.kind == "bot"
```

Это соответствует контракту PR 1:

```text
мониторим только живых bot stalkers;
не трогаем human;
не трогаем non-stalker entities.
```

### 3.3. Emergency flee защищён

Если `scheduled_action.emergency_flee == True`, `PlanMonitor` возвращает `continue`.

Это важно, потому что иначе агент мог бы отменять побег от выброса из-за жажды/голода и попадать в цикл отмены.

### 3.4. `brain_trace` теперь согласован с frontend

Frontend ожидает optional `world_time`, и backend теперь его пишет.

Это хорошо для readable UI:

```text
День N · HH:MM
```

### 3.5. PR 1 не заменяет старую архитектуру

Это плюс.

`scheduled_action` остаётся source of truth, а v3 пока работает как monitoring/observability layer.

---

## 4. Что осталось доделать

### 4.1. Memory dedup для `plan_monitor_abort`

Статус: **не сделано / не видно в коде**.

Сейчас при abort вызывается `_add_memory(...)` напрямую:

```python
_add_memory(
    agent,
    world_turn,
    state,
    "decision",
    "⚡ PlanMonitor: прерываю активное действие",
    {
        "action_kind": "plan_monitor_abort",
        "reason": monitor_result.reason,
        "scheduled_action_type": sched.get("type"),
        "dominant_pressure": _dominant_pressure,
    },
    summary=_summary,
)
```

По контракту PR 1 мы хотели dedup/throttle, чтобы агент не забивал память одинаковыми abort-записями.

#### Почему это важно

Если NPC несколько раз подряд попадает в одинаковый сценарий:

```text
travel → abort critical_thirst → decision не смог решить проблему → travel снова → abort critical_thirst
```

то память может получить много одинаковых записей.

#### Рекомендуемая правка

Добавить helper:

```python
PLAN_MONITOR_MEMORY_DEDUP_TURNS = 10


def should_write_plan_monitor_memory_event(
    agent: dict,
    world_turn: int,
    *,
    action_kind: str,
    signature: dict,
    dedup_turns: int = PLAN_MONITOR_MEMORY_DEDUP_TURNS,
) -> bool:
    for mem in reversed(agent.get("memory", [])):
        mem_turn = mem.get("world_turn", 0)
        if world_turn - mem_turn > dedup_turns:
            break

        effects = mem.get("effects", {})
        if effects.get("action_kind") != action_kind:
            continue

        if effects.get("dedup_signature") == signature:
            return False

    return True
```

Использование:

```python
signature = {
    "reason": monitor_result.reason,
    "scheduled_action_type": sched.get("type"),
    "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
}

if should_write_plan_monitor_memory_event(
    agent,
    world_turn,
    action_kind="plan_monitor_abort",
    signature=signature,
):
    _add_memory(
        agent,
        world_turn,
        state,
        "decision",
        "⚡ PlanMonitor: прерываю активное действие",
        {
            "action_kind": "plan_monitor_abort",
            "reason": monitor_result.reason,
            "scheduled_action_type": sched.get("type"),
            "dominant_pressure": _dominant_pressure,
            "dedup_signature": signature,
        },
        summary=_summary,
    )
```

#### Тест

```python
def test_plan_monitor_abort_memory_is_deduplicated_within_window():
    ...
```

Ожидание:

```text
same abort signature within N ticks → одна memory-запись
different reason / different target → новая запись разрешена
```

#### Важность

```text
Средняя.
```

Это не runtime-blocker, но это часть PR 1-контракта.

---

### 4.2. Fallback `brain_trace` лучше должен писать `no_op` event

Статус: **частично сделано**.

Сейчас `ensure_brain_trace_for_tick()` гарантирует `brain_trace`, но если trace создаётся без event, то `events` может быть пустым.

Текущий смысл:

```text
brain_trace есть
current_thought есть
events может быть []
```

Это не ломает runtime, но хуже для UI/debug.

#### Почему лучше добавить `no_op`

Мы хотели, чтобы у разработчика всегда было понятно:

```text
в этот тик NPC не принял нового решения
```

Лучше, чтобы fallback trace выглядел так:

```json
{
  "schema_version": 1,
  "turn": 123,
  "world_time": { "world_day": 1, "world_hour": 7, "world_minute": 42 },
  "mode": "system",
  "current_thought": "Нет изменений плана в этом тике.",
  "events": [
    {
      "turn": 123,
      "world_time": { "world_day": 1, "world_hour": 7, "world_minute": 42 },
      "mode": "system",
      "decision": "no_op",
      "summary": "В этот тик не было нового решения NPC Brain."
    }
  ]
}
```

#### Рекомендуемая правка

В `ensure_brain_trace_for_tick()` при создании нового trace передавать event:

```python
event = {
    "turn": world_turn,
    "world_time": world_time,
    "mode": "system",
    "decision": "no_op",
    "summary": "В этот тик не было нового решения NPC Brain.",
}

agent["brain_trace"] = _new_trace(
    world_turn,
    "system",
    "Нет изменений плана в этом тике.",
    event,
    world_time=world_time,
)
```

Если trace уже есть, но от старого turn, можно либо:

1. заменить events на `[no_op_event]`, либо
2. append no_op event.

Для PR 1 я бы выбрал append с лимитом 5:

```python
trace["events"] = (list(trace.get("events", [])) + [event])[-BRAIN_TRACE_MAX_EVENTS:]
```

#### Важность

```text
Низкая–средняя.
```

Это не блокирует логику, но улучшает observability и соответствует изначальному контракту.

---

### 4.3. `active_plan_v3` пока почти не участвует

Статус: **ожидаемо / допустимо для PR 1**.

В начале `tick_zone_map()` поле создаётся:

```python
_agent.setdefault("active_plan_v3", None)
```

Но meaningful lifecycle (`active`, `aborted`, `completed`) пока не реализован.

#### Это проблема?

Для PR 1 — нет, если мы честно считаем:

```text
active_plan_v3 is optional debug metadata, not source of truth.
```

В PR 1 source of truth остаётся:

```text
scheduled_action
action_queue
brain_trace
events
memory
```

#### Что можно сделать, но не обязательно

При abort можно записывать:

```python
agent["active_plan_v3"] = {
    "schema_version": 1,
    "status": "aborted",
    "objective_key": None,
    "debug_summary": _summary,
    "updated_turn": world_turn,
}
```

Но это не обязательно, потому что мы специально не хотели закреплять `active_plan_v3` frontend-contract в PR 1.

#### Важность

```text
Низкая.
```

Это не значительный недостающий кусок логики PR 1.

---

### 4.4. `_v3_forced_replan` выставляется, но не используется

Статус: **не критично**.

При abort код делает:

```python
agent["_v3_forced_replan"] = True
```

Потом в конце тика все `_v3_*` поля удаляются.

Сейчас forced replan фактически достигается структурой tick loop:

```text
1. scheduled_action abort → scheduled_action = None
2. позже обычный bot decision loop видит, что scheduled_action нет
3. запускает _run_bot_decision_v2
```

То есть explicit flag не нужен.

#### Что лучше сделать

Вариант A — оставить как debug/transient:

```text
не мешает, удаляется в конце тика
```

Вариант B — убрать:

```text
меньше лишнего кода
```

Я бы выбрал B, если этот flag нигде не используется.

#### Важность

```text
Низкая.
```

Это cleanup, не логический пробел.

---

### 4.5. Нужно проверить, что `_run_bot_decision_v2` реально пишет decision trace

Статус: **нужно подтвердить тестом**.

`write_decision_brain_trace_from_v2()` существует, но важно, чтобы он реально вызывался после выбора intent.

Нужен integration test:

```python
def test_bot_decision_pipeline_writes_decision_brain_trace_event():
    state = make_state_with_free_bot_no_scheduled_action(...)
    new_state, events = tick_zone_map(state)

    trace = new_state["agents"]["bot1"]["brain_trace"]
    assert trace["turn"] == old_world_turn
    assert any(ev["mode"] == "decision" and ev["decision"] == "new_intent" for ev in trace["events"])
```

Если этот тест уже проходит — всё ок.

Если не проходит, PR 1 всё равно даёт fallback trace, но теряет важную часть observability:

```text
мы видим "нет изменений", но не видим фактический выбранный intent.
```

#### Важность

```text
Средняя.
```

Это не ломает gameplay, но важно для основной цели PR 1: “видеть, что NPC думает”.

---

### 4.6. Тест на cleanup `_v3_*` transient flags

Статус: **желательно добавить**.

Код cleanup есть:

```python
for _k in [k for k in list(agent.keys()) if k.startswith("_v3_")]:
    agent.pop(_k, None)
```

Но нужен тест:

```python
def test_v3_transient_flags_are_removed_after_tick():
    ...
    assert not any(k.startswith("_v3_") for k in bot.keys())
```

#### Важность

```text
Низкая–средняя.
```

Код есть, но тест защитит от регрессии.

---

## 5. Значительные куски логики или детали?

### Уже сделаны значительные куски

PR 1 реализовал основные крупные части:

```text
PlanMonitor
brain_trace
tick integration
frontend visibility
v3 tests
safe human exclusion
emergency_flee protection
scheduled_action abort path
event emission
transient cleanup
```

Это и есть ядро PR 1.

### Оставшиеся вещи — в основном детали / контрактные доводки

Осталось:

```text
memory dedup
fallback no_op event
тест на transient cleanup
проверка decision trace event
необязательный active_plan_v3 snapshot
cleanup unused _v3_forced_replan
```

Из них наиболее важная — **memory dedup**.

Она не ломает игру, но является невыполненным пунктом PR 1-контракта.

---

## 6. Финальная оценка

```text
PR 1 runtime-блокер по brain_trace/state: исправлен.
PR 1 крупная логика: в основном завершена.
PR 1 готовность: примерно 85–90%.
До merge желательно доделать 3–4 небольшие правки.
```

### Минимум перед merge

Я бы перед merge сделал обязательно:

1. Добавить memory dedup для `plan_monitor_abort`.
2. Добавить `no_op` event в fallback `ensure_brain_trace_for_tick`.
3. Добавить тест на `_v3_*` cleanup.
4. Добавить/проверить тест, что обычный `_run_bot_decision_v2` пишет `decision/new_intent` trace.

### Можно оставить на потом

1. Meaningful `active_plan_v3`.
2. Удаление `_v3_forced_replan`, если не мешает.
3. Более богатые reason codes.
4. Более красивая frontend-визуализация.

---

## 7. Рекомендуемый патч-план

### Patch 1 — Memory dedup

Файлы:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/tests/decision/v3/test_tick_integration.py
```

Добавить helper и тест.

### Patch 2 — Fallback no-op event

Файлы:

```text
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
backend/tests/decision/v3/test_brain_trace.py
```

Обновить `ensure_brain_trace_for_tick()`.

### Patch 3 — Decision trace coverage

Файлы:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/tests/decision/v3/test_tick_integration.py
```

Проверить, что после `_run_bot_decision_v2` пишется `decision/new_intent`.

### Patch 4 — Cleanup test

Файлы:

```text
backend/tests/decision/v3/test_tick_integration.py
```

Добавить тест на отсутствие `_v3_*` ключей после tick.

---

## 8. Итоговая позиция

PR 1 не нужно переписывать.

Он уже реализует основную идею первого этапа:

```text
NPC с active scheduled_action теперь может быть переоценён,
а frontend получает читаемый brain_trace.
```

Но перед merge стоит закрыть небольшие контрактные недочёты, особенно memory dedup и fallback `no_op` event.
