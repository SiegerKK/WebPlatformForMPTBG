# Ответы на вопросы по NPC Brain v3 — Раунд 5

> Контекст: ответы предназначены для закрытия неоднозначностей перед PR 1.  
> Основной принцип этого раунда: **PR 1 должен иметь узкий, тестируемый и стабильный контракт**, но не должен преждевременно обещать полноценный future API.

---

## Короткое резюме решений

1. `brain_trace` coverage в PR 1 — **полный для всех живых bot stalkers**.
2. `brain_trace` enums — фиксируем **только реально используемые значения PR 1**.
3. `_v3_replanned_after_monitor_turn` — **удалять в конце тика**.
4. `active_plan_v3` / `brain_trace.active_plan` — **не публичный frontend-contract в PR 1**, только optional debug metadata.
5. `plan_monitor_aborted_action` — фиксируем обязательные поля и **точные optional-поля уже сейчас**.
6. Memory dedup — делать **общий helper для plan-monitor memory events**, но в PR 1 использовать его только для `abort`.

---

## 1. `brain_trace` coverage: полный критерий уже в PR 1 или phased rollout?

### Решение

Выбираем вариант **A**:

```text
В PR 1 сразу требуем полный coverage:
после tick_zone_map у каждого живого bot stalker,
который не has_left_zone,
должен быть brain_trace за текущий тик.
```

То есть официальный acceptance для PR 1:

```text
for every agent in state["agents"]:
  if agent.is_alive
  and not agent.has_left_zone
  and agent.archetype == "stalker_agent"
  and agent.controller.kind == "bot":
      agent["brain_trace"]["turn"] == world_turn_before_increment
```

### Почему не phased rollout

Phased rollout (`scheduled_action + _run_bot_decision_v2_inner` сейчас, early legacy позже) снова создаст дырки в UI:

```text
один NPC показывает мысль,
другой NPC не показывает мысль,
почему — непонятно
```

А смысл PR 1 — дать разработчику/игроку гарантированное окно в состояние NPC.

### Как покрыть ранние legacy-return без большого рефакторинга

Не надо сразу переписывать все ранние ветки. Достаточно добавить fallback:

```python
ensure_brain_trace_for_tick(agent, world_turn)
```

Эта функция вызывается в конце обработки bot stalker или в конце `tick_zone_map` для всех живых bot stalkers.

Если полноценный decision/monitor trace уже есть — она ничего не делает.

Если trace нет — пишет минимальный системный trace:

```json
{
  "schema_version": 1,
  "turn": 1234,
  "mode": "system",
  "current_thought": "Нет нового решения: агент продолжает текущее состояние.",
  "events": [
    {
      "turn": 1234,
      "mode": "system",
      "decision": "no_op",
      "summary": "В этот тик не было нового решения NPC Brain."
    }
  ]
}
```

### Acceptance test

```python
def test_all_alive_bot_stalkers_get_brain_trace_each_tick():
    before_turn = state["world_turn"]
    new_state, _events = tick_zone_map(state)

    for agent in new_state["agents"].values():
        if agent.get("is_alive", True)            and not agent.get("has_left_zone")            and agent.get("archetype") == "stalker_agent"            and agent.get("controller", {}).get("kind") == "bot":
            assert agent.get("brain_trace", {}).get("turn") == before_turn
```

---

## 2. `brain_trace` enums: только PR 1 или future-safe набор?

### Решение

Выбираем вариант **A**:

```text
В PR 1 публичным контрактом считаются только значения,
которые реально могут быть сгенерированы в PR 1.
```

Не надо фиксировать `pause/adapt/complete/scheduled_action`, если они пока не используются. Иначе фронт и тесты начнут считать их поддержанными.

### PR 1 enum: `mode`

```ts
type BrainTraceMode =
  | 'plan_monitor'
  | 'decision'
  | 'system';
```

### PR 1 enum: `decision`

```ts
type BrainTraceDecision =
  | 'continue'
  | 'abort'
  | 'new_intent'
  | 'no_op';
```

### Что не включаем в PR 1 public enum

Не включаем пока:

```text
pause
adapt
complete
scheduled_action
execute_step
```

Их можно добавить в следующих PR как **non-breaking enum extension**, если frontend код написан устойчиво к неизвестным значениям.

### Рекомендация для frontend

Даже при узком enum желательно иметь fallback rendering:

```ts
const label = KNOWN_DECISION_LABELS[event.decision] ?? event.decision;
```

Это позволит backend расширить enum позже без падения UI.

### Почему не future-safe enum прямо сейчас

Future-safe enum выглядит удобно, но создаёт ложное обещание:

```text
если значение есть в типе,
значит оно поддерживается контрактом.
```

Для PR 1 лучше честно сказать: поддерживаются только `continue`, `abort`, `new_intent`, `no_op`.

---

## 3. `_v3_replanned_after_monitor_turn`: обязательно ли чистить в конце тика?

### Решение

Выбираем вариант **A**:

```text
transient field обязан удаляться в конце tick_zone_map.
```

### Почему чистить

Поле начинается с `_`, но оно всё равно окажется внутри JSON state и может:

- попасть в PostgreSQL JSONB;
- появиться в debug export;
- усложнить snapshot-тесты;
- смутить фронт;
- накапливать технический мусор в state.

Да, проверка по `world_turn` делает поле логически безопасным, но для чистоты состояния его лучше удалить.

### Контракт

```text
_v3_replanned_after_monitor_turn — strictly transient runtime flag.
It must not survive the tick.
```

### Реализация

В конце `tick_zone_map`, рядом с reset `action_used`, добавить:

```python
for agent in state.get("agents", {}).values():
    agent.pop("_v3_replanned_after_monitor_turn", None)
```

Если в будущем появятся другие transient flags, можно сделать helper:

```python
def _clear_transient_agent_flags(agent: dict) -> None:
    agent.pop("_v3_replanned_after_monitor_turn", None)
    agent.pop("_brain_trace_written_this_tick", None)
```

### Test

```python
def test_v3_replanned_flag_is_not_persisted_after_tick():
    new_state, _ = tick_zone_map(state)
    for agent in new_state["agents"].values():
        assert "_v3_replanned_after_monitor_turn" not in agent
```

---

## 4. `active_plan_v3` в API PR 1

### Решение

Выбираем вариант **B**:

```text
В PR 1 active_plan_v3 / brain_trace.active_plan
не являются стабильным frontend-contract.
```

Они остаются backend/debug metadata.

Фронт в PR 1 должен опираться только на:

```text
agent.brain_trace.current_thought
agent.brain_trace.events
```

### Почему не фиксировать `active_plan_v3` сейчас

`active_plan_v3` пока является retrofit-wrapper над `scheduled_action`, а не полноценной моделью плана. Если зафиксировать его форму сейчас, потом будет сложнее изменить структуру, когда появятся настоящие:

```text
Objective
ActivePlan
PlanStep preconditions/effects
PlanMonitor lifecycle
pause/adapt
```

### Что можно показывать на фронте

В PR 1 можно показывать только существующий `scheduled_action` и `brain_trace.events`.

Например:

```text
Текущая мысль:
  Прерываю путь к Болоту: жажда стала критической.

События мышления:
  [plan_monitor] abort — критическая жажда
  [decision] new_intent — seek_water
```

Если `brain_trace.active_plan` случайно есть, фронт может проигнорировать его.

### TypeScript

В `AgentForProfile` можно добавить:

```ts
brain_trace?: BrainTrace;
```

Но не добавлять стабильный тип для `active_plan_v3` в PR 1. Максимум:

```ts
active_plan_v3?: unknown;
```

И не рендерить его как основной UI.

---

## 5. `plan_monitor_aborted_action`: optional payload в контракте

### Решение

Выбираем вариант **A**:

```text
В PR 1 сразу закрепляем точные имена и типы optional-полей.
```

Это не значит, что все optional-поля обязаны быть всегда. Но если backend их отправляет, они должны иметь стабильные имена и типы.

### Обязательный payload

```ts
type PlanMonitorAbortedActionPayload = {
  agent_id: string;
  scheduled_action_type: string;
  reason: string;
  dominant_pressure: {
    key: string;
    value: number;
  };
};
```

### Optional payload, тоже зафиксированный

```ts
type PlanMonitorAbortedActionPayload = {
  agent_id: string;
  scheduled_action_type: string;
  reason: string;
  dominant_pressure: {
    key: string;
    value: number;
  };

  cancelled_target?: string | null;
  cancelled_final_target?: string | null;
  current_location_id?: string | null;
  turns_remaining?: number | null;
};
```

### Рекомендованный backend event

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
    },
    "cancelled_target": "garbage",
    "cancelled_final_target": "swamp",
    "current_location_id": "bar",
    "turns_remaining": 6
  }
}
```

### Почему фиксировать optional сейчас

Иначе event consumers могут начать использовать фактические поля, а затем следующее изменение станет breaking change. Лучше сразу закрепить маленький, но явный контракт.

---

## 6. Memory dedup: только `abort` или общий helper?

### Решение

Выбираем вариант **A**:

```text
Сразу делаем общий helper для plan-monitor memory events,
но в PR 1 используем его только для abort.
```

### Почему общий helper лучше

Dedup — это не особенность `abort`, а особенность записей от `PlanMonitor`. В следующих PR появятся:

```text
plan_monitor_action_paused
plan_monitor_action_adapted
plan_monitor_action_completed
```

Им понадобится та же логика throttle.

Если сделать узкий helper только под abort, его почти сразу придётся переписывать.

### Но не переобобщать

Helper должен быть общий, но простой:

```python
def should_write_plan_monitor_memory_event(
    agent: dict,
    world_turn: int,
    *,
    action_kind: str,
    signature: dict[str, Any],
    dedup_turns: int = PLAN_MONITOR_MEMORY_DEDUP_TURNS,
) -> bool:
    ...
```

### Signature

Для abort:

```python
signature = {
    "reason": reason,
    "scheduled_action_type": sched.get("type"),
    "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
}
```

Для будущего pause/adapt можно будет передать другую signature, не меняя helper.

### Helper behavior

```python
def should_write_plan_monitor_memory_event(agent, world_turn, *, action_kind, signature, dedup_turns):
    for mem in reversed(agent.get("memory", [])):
        mem_turn = mem.get("world_turn", 0)
        if world_turn - mem_turn > dedup_turns:
            break

        effects = mem.get("effects", {})
        if effects.get("action_kind") != action_kind:
            continue

        existing_signature = effects.get("dedup_signature")
        if existing_signature == signature:
            return False

    return True
```

### Memory record

```python
_add_memory(
    agent,
    world_turn,
    state,
    "decision",
    "⛔ Прерываю текущее действие",
    {
        "action_kind": "plan_monitor_action_aborted",
        "reason": reason,
        "scheduled_action_type": sched.get("type"),
        "cancelled_target": sched.get("target_id"),
        "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
        "dedup_signature": signature,
    },
    summary=summary,
)
```

### PR 1 tests

Тестируем только abort use-case:

```text
same abort signature within N ticks
→ memory entry is written once

different reason or different final target
→ new memory entry is allowed
```

---

## Финальный PR 1 contract после раунда 5

### Backend guarantees

PR 1 должен гарантировать:

```text
1. Every alive bot stalker has brain_trace for current tick.
2. brain_trace public enum contains only PR 1 values:
   mode: plan_monitor | decision | system
   decision: continue | abort | new_intent | no_op
3. Transient fields starting with _v3 are removed before state is returned/persisted.
4. active_plan_v3 is optional backend/debug metadata, not stable UI contract.
5. plan_monitor_aborted_action event has stable required and optional payload fields.
6. plan-monitor memory dedup uses a generic helper, but only abort is emitted in PR 1.
```

### Frontend guarantees

Frontend PR 1 should rely on:

```text
agent.brain_trace.schema_version
agent.brain_trace.turn
agent.brain_trace.mode
agent.brain_trace.current_thought
agent.brain_trace.events
```

Frontend PR 1 should **not** rely on:

```text
agent.active_plan_v3
agent.brain_trace.active_plan
pause/adapt/complete events
```

### Tests to add or adjust

```text
test_all_alive_bot_stalkers_get_brain_trace_each_tick
test_brain_trace_pr1_enums_only
test_v3_transient_flags_are_removed
test_active_plan_v3_is_optional_debug_metadata
test_plan_monitor_aborted_action_payload_contract
test_plan_monitor_memory_dedup_generic_helper_for_abort
```

---

## Итоговая позиция

PR 1 должен быть строгим там, где это влияет на UI и тесты:

```text
brain_trace coverage
brain_trace enum
event payload
transient cleanup
memory dedup
```

И осторожным там, где модель ещё не созрела:

```text
active_plan_v3
pause/adapt
full ActivePlan frontend rendering
```

Так мы получаем понятный первый слой NPC Brain v3 без преждевременного закрепления архитектуры, которая ещё будет меняться.
