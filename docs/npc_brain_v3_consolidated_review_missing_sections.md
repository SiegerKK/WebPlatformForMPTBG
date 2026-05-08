# NPC Brain v3 — что ещё стоит добавить в consolidated-документ

> Контекст: review документа `docs/npc_brain_v3_consolidated.md` перед началом реализации PR 1.  
> Цель: зафиксировать недостающие разделы и решения, которые снизят риск расползания scope, регрессий и неоднозначностей в тестах.

---

## Короткое резюме

Consolidated-документ уже хорошо собирает:

- проблему текущей схемы;
- цели v3;
- когнитивный цикл;
- сущности `BeliefState`, `Drive`, `Objective`, `ActivePlan`;
- многоуровневую память;
- Redis-позицию;
- frontend-визуализацию;
- PR 1-контракт.

Но перед реализацией стоит добавить ещё несколько защитных разделов:

1. `Non-goals for PR 1`
2. `Backward compatibility with existing saves`
3. `Schema versioning`
4. `Failure policy`
5. `Performance budget`
6. `Determinism`
7. `Debug/explain ownership`
8. `brain_trace lifecycle for death / has_left_zone`
9. `brain_trace event ordering`
10. `active_plan_v3 source of truth`
11. `Observability contract`
12. `Expected file changes for PR 1`
13. `Definition of Done`
14. уточнение: “переоценка каждый тик” в PR 1 — это только lightweight `PlanMonitor`, а не полный `NPCBrain.tick`.

---

## 1. Добавить раздел `Non-goals for PR 1`

### Почему

Сводный документ большой и описывает не только PR 1, но и всю будущую архитектуру v3. Без явного списка non-goals исполнитель может решить, что в первый PR нужно включить весь `NPCBrain`, `BeliefState`, `MemoryStore`, `DriveEvaluator` и т.д.

### Предлагаемый текст

```markdown
## Non-goals for PR 1

PR 1 intentionally does NOT implement the full NPC Brain v3 architecture.

В PR 1 НЕ делаем:

- полноценный `NPCBrain` class;
- полноценный `BeliefState` вместо `AgentContext`;
- Redis;
- отдельные PostgreSQL-таблицы для памяти;
- `pause` / `adapt` lifecycle для планов;
- полноценный `ActivePlan` UI;
- semantic/vector retrieval;
- полную замену `NeedScores` на `Drive`;
- полную замену `scheduled_action`;
- полную миграцию `agent["memory"]` на `memory_v3`;
- долгосрочную систему `ObjectiveGenerator`;
- полное HTN/GOAP-планирование.

PR 1 — это thin compatibility layer:

- `PlanMonitor` поверх существующего `scheduled_action`;
- `brain_trace` для видимости поведения;
- минимальная переоценка активных действий;
- сохранение legacy behavior везде, где v3 ещё не готов.
```

---

## 2. Добавить раздел `Backward compatibility with existing saves`

### Почему

Старые сохранения / state blobs не будут содержать:

```text
brain_trace
active_plan_v3
memory_v3
item_needs
npc_brain_v3
schema_version
```

Если это не описать, реализация может начать предполагать наличие новых полей и ломать старые игры.

### Предлагаемый текст

```markdown
## Backward compatibility with existing saves

Все новые v3-поля должны быть optional.

Старый `state_blob`, в котором нет `brain_trace`, `active_plan_v3`, `memory_v3`
или других v3-полей, должен продолжать нормально проходить через `tick_zone_map()`.

Alembic migration не нужна, если агент хранится внутри JSONB.
Но нужна runtime-migration / defaulting logic на уровне state.

Рекомендуемый helper:

```python
def ensure_agent_v3_defaults(agent: dict) -> None:
    agent.setdefault("brain_trace", None)
    agent.setdefault("active_plan_v3", None)
    agent.setdefault("memory_v3", None)
```

Важно:

- новые поля не должны быть обязательными для legacy paths;
- отсутствие `brain_trace` до первого v3-tick — нормальное состояние;
- отсутствие `active_plan_v3` при наличии `scheduled_action` — нормальное состояние;
- отсутствие `memory_v3` не должно ломать старую `agent["memory"]`.
```

---

## 3. Добавить общий `schema_version`

### Почему

В документе уже есть `brain_trace.schema_version`, но в будущем будут меняться и другие структуры. Нужно заранее договориться, как отличать версии.

### Предлагаемый текст

```markdown
## Schema versioning

Все persisted/debug v3-структуры должны иметь `schema_version`.

Минимум:

```python
agent["brain_trace"] = {
    "schema_version": 1,
    ...
}

agent["active_plan_v3"] = {
    "schema_version": 1,
    ...
}

agent["memory_v3"] = {
    "schema_version": 1,
    ...
}
```

Для PR 1 стабильным публичным контрактом является только:

- `brain_trace.schema_version = 1`;
- `brain_trace.turn`;
- `brain_trace.mode`;
- `brain_trace.current_thought`;
- `brain_trace.events`.

`active_plan_v3.schema_version` и `memory_v3.schema_version` могут быть добавлены как debug/internal metadata,
но не являются frontend-contract в PR 1.
```

---

## 4. Добавить `Failure policy`

### Почему

`tick_zone_map()` — центральная функция симуляции. V3-слой не должен ломать тик мира, если `PlanMonitor` или `brain_trace` неожиданно падают.

### Предлагаемый текст

```markdown
## Failure policy

NPC Brain v3 PR 1 must be fail-safe.

Если `PlanMonitor` не смог оценить действие:

- fallback decision = `continue`;
- legacy `_process_scheduled_action()` выполняется как раньше;
- ошибка логируется;
- по возможности пишется `brain_trace.mode = "system"`.

Если `brain_trace` writer падает:

- не отменять действие NPC;
- не ломать `tick_zone_map`;
- не блокировать legacy decision pipeline.

Если helper v3 не смог построить score/pressure:

- использовать safe default;
- не делать abort на основе неполных данных.

Главный принцип:

```text
v3 observability/monitoring layer must not break the simulation.
```

Для PR 1 допустимый fallback:

```python
try:
    monitor_result = assess_scheduled_action_v3(...)
except Exception:
    monitor_result = PlanMonitorResult(decision="continue", reason="monitor_error")
```

Желательно ограничить catch конкретными ожидаемыми исключениями, но в PR 1 важнее не ломать мир.
```

---

## 5. Добавить `Performance budget`

### Почему

Документ говорит о десятках тысяч записей памяти, но PR 1 не должен случайно начать сканировать огромные списки в каждом тике.

### Предлагаемый текст

```markdown
## Performance budget

### PR 1

`PlanMonitor` должен быть лёгким:

- O(1) или O(small-N) на агента;
- не сканировать всю память агента;
- не строить полный план заново, если decision = `continue`;
- не вызывать expensive retrieval;
- не включать большие payload в `brain_trace`;
- `brain_trace.events.length <= 5`.

Допустимые операции PR 1:

- посмотреть `scheduled_action`;
- посмотреть базовые поля агента: `hp`, `hunger`, `thirst`, `sleepiness`, `location_id`;
- посмотреть текущую локацию;
- проверить `emergency_flee`;
- проверить `emission_active` / `_is_emission_threat`;
- посмотреть последние N записей памяти для dedup.

### Memory v3 later

Когда появится `MemoryStore`:

- default `max_results = 10`;
- hard cap `max_results <= 50`;
- no full memory scan in decision loop;
- retrieval должен идти через индексы;
- decay/consolidation не должны выполняться для всех агентов каждый тик без ограничения.
```

---

## 6. Добавить `Determinism`

### Почему

Симуляция использует seed и должна быть тестируемой. Если scoring/tie-breakers будут нестабильными, тесты начнут флакать.

### Предлагаемый текст

```markdown
## Determinism

NPC Brain v3 должен быть воспроизводимым.

При одинаковом:

- `state`;
- `world_turn`;
- `state["seed"]`;
- `agent_id`;

результат `PlanMonitor` / `brain_trace` / выбранный intent должен быть одинаковым.

Правила:

- сортировки objective/memory candidates должны быть стабильными;
- при равных score использовать deterministic tie-breaker;
- не использовать `random.random()` без seed;
- если нужен random, seed должен включать `state.seed`, `world_turn`, `agent_id`.

Пример tie-breaker:

```python
candidates.sort(key=lambda c: (-c.score, c.key, c.target_id or ""))
```

Для PR 1 `PlanMonitor` желательно вообще делать deterministic без random.
```

---

## 7. Добавить `Debug/explain ownership`

### Почему

В проекте уже есть `_v2_context`, `explain_intent.py`, legacy preview и будущий `brain_trace`. Нужно развести их роли.

### Предлагаемый текст

```markdown
## Debug/explain ownership

В проекте есть несколько debug/explain механизмов. Их роли должны быть разделены.

### `_v2_context`

Legacy/debug snapshot текущего v2 pipeline.

- может оставаться;
- может использоваться для старой панели;
- не является source of truth для фактического поведения;
- может не совпадать с `brain_trace`, если action был прерван `PlanMonitor`.

### `explain_intent.py`

Read-only preview/explanation.

- не мутирует state;
- можно вызвать вручную;
- показывает, что pipeline выбрал бы сейчас;
- не обязательно совпадает с тем, что уже произошло в тике.

### `brain_trace`

Фактический trace поведения агента в текущем тике.

- persisted inside agent state;
- пишется во время `tick_zone_map`;
- показывает, что реально произошло;
- frontend должен показывать `brain_trace` как главный источник объяснения.

### Frontend priority

`AgentProfileModal` должен показывать:

1. `brain_trace` — primary factual explanation;
2. `_v2_context` — optional legacy/debug block;
3. `explain_intent` preview — только как manual debug action, не как “что NPC реально сделал”.
```

---

## 8. Добавить lifecycle `brain_trace` для смерти и выхода из Зоны

### Почему

Acceptance говорит “живые bot stalkers”, но нужно уточнить, что делать при смерти внутри тика.

### Предлагаемый текст

```markdown
## brain_trace lifecycle for death / has_left_zone

### Alive bot stalker

После `tick_zone_map` каждый живой bot stalker, который не `has_left_zone`,
должен иметь:

```python
agent["brain_trace"]["turn"] == world_turn_before_increment
```

### Agent died during this tick

Если bot stalker умер в этом тике:

- `brain_trace` можно записать с `mode="system"`;
- желательно добавить event `decision="no_op"` или future `decision="death"`;
- future ticks не обязаны обновлять `brain_trace`.

Минимальный PR 1 вариант:

```json
{
  "schema_version": 1,
  "turn": 1234,
  "mode": "system",
  "current_thought": "Агент погиб; дальнейшие решения не принимаются.",
  "events": [
    {
      "turn": 1234,
      "mode": "system",
      "decision": "no_op",
      "summary": "Агент погиб в этот тик."
    }
  ]
}
```

### has_left_zone

Если `has_left_zone == true`:

- `brain_trace` больше не обязан обновляться;
- последний trace можно оставить как historical snapshot.
```

---

## 9. Добавить порядок событий внутри `brain_trace.events`

### Почему

В одном тике возможны два события: `PlanMonitor abort` и новое решение pipeline. Нужно зафиксировать порядок.

### Предлагаемый текст

```markdown
## brain_trace event ordering

`brain_trace.events` хранятся в хронологическом порядке.

Пример:

```json
{
  "events": [
    {
      "mode": "plan_monitor",
      "decision": "abort",
      "summary": "Прервал travel из-за критической жажды"
    },
    {
      "mode": "decision",
      "decision": "new_intent",
      "summary": "Выбран intent seek_water"
    }
  ]
}
```

При добавлении нового события:

```python
events = (old_events + [new_event])[-5:]
```

То есть:

- старые события идут раньше;
- новые события добавляются в конец;
- если лимит превышен, удаляются самые старые.
```

---

## 10. Уточнить `active_plan_v3` source of truth

### Почему

Документ говорит, что `active_plan_v3` — debug/retrofit metadata, но нужно жёстко зафиксировать, что gameplay-логика PR 1 не должна на него опираться.

### Предлагаемый текст

```markdown
## active_plan_v3 source of truth

В PR 1 source of truth для исполнения остаётся:

```python
agent["scheduled_action"]
agent["action_queue"]
```

`active_plan_v3` — только debug/retrofit metadata.

Правила PR 1:

- отсутствие `active_plan_v3` не должно ломать тик;
- gameplay-логика не должна требовать `active_plan_v3`;
- frontend не должен считать `active_plan_v3` стабильным контрактом;
- `PlanMonitor` может создать/обновить `active_plan_v3`, но обязан работать и без него.

Позже, когда `ActivePlan` станет полноценной моделью, source of truth можно будет перенести.
```

---

## 11. Добавить `Observability contract`

### Почему

Главная ценность PR 1 — не только прерывания, но и объяснимость. Нужно зафиксировать, на какие вопросы разработчик должен получить ответ после тика.

### Предлагаемый текст

```markdown
## Observability contract

После PR 1 разработчик должен иметь возможность ответить по каждому живому bot stalker:

1. Что NPC сейчас “думает”?
2. Продолжил ли он `scheduled_action` или прервал?
3. Если прервал — почему?
4. Какое давление было доминирующим?
5. Был ли forced replan после abort?
6. Какой новый intent выбран после abort?
7. Была ли создана memory-запись?
8. Был ли создан public event?
9. Почему human agent не был затронут `PlanMonitor`?

Минимальные источники ответа:

- `agent["brain_trace"]`;
- event `plan_monitor_aborted_action`;
- legacy `agent["memory"]`;
- tests in `backend/tests/decision/v3/`.
```

---

## 12. Добавить список expected file changes для PR 1

### Почему

Это сильно помогает Copilot/Codex и ограничивает scope PR.

### Предлагаемый текст

```markdown
## Expected file changes for PR 1

### New backend files

```text
backend/app/games/zone_stalkers/rules/tick_constants.py
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
```

### Changed backend files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
```

Optional, if needed:

```text
backend/app/games/zone_stalkers/decision/debug/__init__.py
```

### New tests

```text
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_tick_integration.py
```

### Changed frontend files

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

Optional type extraction later:

```text
frontend/src/games/zone_stalkers/types.ts
```
```

---

## 13. Добавить `Definition of Done`

### Почему

Это самый полезный финальный чеклист для PR 1.

### Предлагаемый текст

```markdown
## Definition of Done for PR 1

PR 1 считается готовым, если выполнены все пункты:

### Backend behavior

- [ ] Каждый живой bot stalker получает `brain_trace.turn == world_turn_before_increment`.
- [ ] Human agents с `scheduled_action` не проходят через `PlanMonitor`.
- [ ] `emergency_flee=True` не прерывается из-за жажды/голода/обычных survival pressures.
- [ ] Обычный `travel` / `explore` может быть aborted при критической жажде/голоде/HP.
- [ ] При `abort` очищаются `scheduled_action` и `action_queue`.
- [ ] После `abort` допускается максимум один forced replan на агента за тик.
- [ ] Transient `_v3_*` flags не остаются в итоговом state.
- [ ] `brain_trace.events.length <= 5`.
- [ ] Memory-записи от PlanMonitor dedup/throttle работают.

### Events

- [ ] При abort эмитится `event_type="plan_monitor_aborted_action"`.
- [ ] Event содержит обязательные поля:
  - `agent_id`
  - `scheduled_action_type`
  - `reason`
  - `dominant_pressure.key`
  - `dominant_pressure.value`
- [ ] Optional event fields имеют стабильные имена:
  - `cancelled_target`
  - `cancelled_final_target`
  - `current_location_id`
  - `turns_remaining`

### Frontend

- [ ] `AgentProfileModal` показывает `brain_trace.current_thought`.
- [ ] `AgentProfileModal` показывает последние `brain_trace.events`.
- [ ] Frontend не зависит от `active_plan_v3` как от стабильного контракта.

### Compatibility

- [ ] Старые state blobs без v3-полей не ломаются.
- [ ] Старые decision/planner tests продолжают проходить.
- [ ] Новые tests в `backend/tests/decision/v3/` проходят.

### Scope

- [ ] PR 1 не внедряет Redis.
- [ ] PR 1 не заменяет полностью `NeedScores`.
- [ ] PR 1 не заменяет полностью `scheduled_action`.
- [ ] PR 1 не реализует `pause/adapt`.
```

---

## 14. Уточнить фразу “каждый тик переоценивать”

### Почему

В целях v3 сказано, что NPC должен переоценивать ситуацию каждый тик. Но PR 1 не реализует полный cognitive cycle — только lightweight мониторинг активного `scheduled_action`.

### Предлагаемый текст

```markdown
## Scope note: “reevaluate every tick” in PR 1

В полной v3-архитектуре NPC действительно проходит cognitive cycle каждый тик.

Но в PR 1 это означает только:

```text
Если у bot stalker есть active scheduled_action,
PlanMonitor делает lightweight reassessment перед legacy _process_scheduled_action.
```

PR 1 НЕ означает:

- полный `NPCBrain.tick`;
- полный `BeliefState`;
- полный `ObjectiveGenerator`;
- полный пересчёт всех альтернатив каждый тик;
- замену legacy pipeline.

Таким образом, PR 1 — это первый шаг к “thinking every tick”,
а не финальная реализация всей v3-архитектуры.
```

---

## Рекомендуемый порядок вставки в consolidated-документ

Предлагаемый порядок новых секций:

```text
после ## 12. Стратегия миграции:
  - Non-goals for PR 1
  - Expected file changes for PR 1

внутри/после ## 13. PR 1 — финальный контракт:
  - Backward compatibility with existing saves
  - Schema versioning
  - Failure policy
  - Performance budget
  - Determinism
  - Debug/explain ownership
  - Observability contract
  - Definition of Done

внутри ## 14. PR 1 — implementation decisions:
  - brain_trace lifecycle for death / has_left_zone
  - brain_trace event ordering
  - active_plan_v3 source of truth
  - Scope note: “reevaluate every tick” in PR 1
```

---

## Итог

Consolidated-документ уже достаточно хорош как архитектурная база, но перед реализацией PR 1 ему не хватает “операционных” гарантий:

- что не делаем;
- как не ломаем старые сейвы;
- что делать при ошибке;
- какие лимиты по производительности;
- как обеспечить детерминизм;
- что именно считается готовым PR.

Без этих разделов PR 1 всё ещё может получиться рабочим, но хрупким и неоднозначным.  
С этими разделами документ станет не просто архитектурной идеей, а полноценным implementation contract.
