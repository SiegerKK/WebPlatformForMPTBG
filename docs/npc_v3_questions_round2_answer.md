# Ответы на вопросы по NPC Brain v3 — Раунд 2

> Документ-ответ на `docs/npc_v3_questions_round2.md`.
>
> Цель: зафиксировать конкретные implementation decisions для первого PR и не превращать v3 в большой одномоментный переписанный движок.

---

## Краткое резюме решений

| Вопрос | Решение |
|---|---|
| Где вставлять `PlanMonitor` | В `tick_zone_map()` перед вызовом `_process_scheduled_action`, то есть до обработки активного `scheduled_action` |
| Что делать с `_process_scheduled_action` | Не ломать в PR 1; оставить как executor длительного действия и safety net |
| `active_plan_v3` | Хранить внутри agent JSON как компактный wrapper над `scheduled_action`; Alembic не нужен |
| `brain_trace` | Хранить в `agent["brain_trace"]` как последний компактный snapshot; перезаписывать каждый тик/decision tick |
| `ItemNeed.compatible_item_types` | Runtime можно `frozenset`, JSON всегда `list[str]`, лучше sorted list |
| Interrupt thresholds | В идеале вынести в общий `interrupt_policy.py`; в PR 1 допустимо оставить старое, но PlanMonitor должен использовать общий helper или `evaluate_needs + select_intent` |
| `score_factors` | Хранить внутри `brain_trace`, не в отдельном ключе |
| Frontend | MVP — блок в `AgentProfileModal.tsx`, данные приходят в обычном state; memory не тащить целиком |
| `memory_v3` + legacy `memory` | В PR 1 писать gameplay-critical записи в legacy `memory`, новые расширенные — в `memory_v3`; legacy capped by `MAX_AGENT_MEMORY` |
| Тесты | Новые v3-тесты класть в `backend/tests/decision/v3/` |
| `explain_intent.py` | Не заменять; `brain_trace.py` живёт рядом и отличается тем, что описывает фактическое последнее решение/план, а не preview |

---

# 1. Точка входа в `tick_rules.py` для `PlanMonitor`

## Решение

Выбираем вариант, близкий к **B**, но с практической формулировкой:

```text
В tick_zone_map(), в цикле обработки scheduled_action:

if agent has scheduled_action:
    monitor_result = plan_monitor.assess_scheduled_action(...)
    if monitor_result.decision in (ABORT, PAUSE, ADAPT):
        clear or transform scheduled_action
        write brain_trace / memory
        do NOT set action_used
        continue
    else:
        _process_scheduled_action(...)
```

То есть `PlanMonitor` должен запускаться **перед** `_process_scheduled_action`, а не внутри неё.

## Почему не вариант A

Вариант A, где `PlanMonitor` вставляется внутрь `_process_scheduled_action`, технически проще, но архитектурно хуже.

`_process_scheduled_action` сейчас отвечает за физическое выполнение длительного действия:

- уменьшить `turns_remaining`;
- завершить travel/explore/sleep;
- применить эффекты;
- записать события;
- обработать route changes;
- обработать legacy emission interrupt.

Если добавить туда полноценную переоценку плана, функция станет одновременно:

```text
executor длительного действия
+ monitor активного плана
+ decision gate
+ частичный planner
```

Это усилит уже существующую проблему: слишком много ответственности в `tick_rules.py`.

## Почему не вариант C

Вариант C — отдельный pre-step перед всей логикой тика агента — правильный как конечная архитектура, но для PR 1 он слишком размыт.

В текущем `tick_zone_map()` порядок такой:

```text
1. process scheduled actions
2. degrade needs / apply hunger-thirst damage
3. emission mechanics
4. combat
5. bot decisions
6. observations
7. advance time
```

Если сделать большой pre-step “перед всей логикой тика”, придётся сразу решать, до или после деградации голода/жажды, до или после выброса, до или после боя он должен срабатывать. Это уже тянет на реорганизацию `tick_zone_map()`.

Для PR 1 лучше сделать локальную точку вставки перед `_process_scheduled_action`.

## Конкретная схема PR 1

```python
for agent_id, agent in state.get("agents", {}).items():
    if not agent.get("is_alive", True):
        continue
    if agent.get("has_left_zone"):
        continue

    sched = agent.get("scheduled_action")
    if not sched:
        continue

    monitor_result = assess_scheduled_action_v3(
        agent_id=agent_id,
        agent=agent,
        scheduled_action=sched,
        state=state,
        world_turn=world_turn,
    )

    if monitor_result.decision in ("abort", "pause", "adapt"):
        apply_plan_monitor_decision(
            agent_id=agent_id,
            agent=agent,
            result=monitor_result,
            state=state,
            world_turn=world_turn,
        )
        events.extend(monitor_result.events)
        # Important:
        # Do not set action_used.
        # Later in the same tick the normal bot decision loop may run.
        continue

    new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
    events.extend(new_evs)
```

## Что делать со старой interrupt-логикой выброса внутри `_process_scheduled_action`

В PR 1 её **не удалять**.

Она остаётся safety net, пока `PlanMonitor` не покрыт тестами. После нескольких PR можно будет перенести emission interrupt целиком в `PlanMonitor`.

Итог:

```text
PR 1:
  PlanMonitor перехватывает активные scheduled_action до выполнения.
  Старый emission interrupt остаётся как fallback.

Позже:
  emission interrupt уезжает из _process_scheduled_action в PlanMonitor.
```

---

# 2. `active_plan_v3` — хранение в БД

## Нужна ли Alembic-миграция?

Если агент уже хранится как JSONB-блоб внутри state/context, то **Alembic-миграция для нового поля не нужна**.

`active_plan_v3` просто появляется как новый ключ:

```json
{
  "active_plan_v3": {
    "id": "plan_123",
    "objective_key": "RESTORE_WATER",
    "status": "active",
    "current_step_index": 0,
    "steps": [...]
  }
}
```

Alembic понадобится только если мы добавляем новую колонку или отдельную таблицу.

## Нужно ли добавлять в Pydantic-схему?

Если в проекте есть строгая Pydantic-схема агента, то лучше добавить поле как optional:

```python
active_plan_v3: dict[str, Any] | None = None
brain_trace: dict[str, Any] | None = None
```

Но для PR 1 можно писать напрямую:

```python
agent["active_plan_v3"] = serialize_active_plan(plan)
```

Рекомендация:

```text
Если схема уже мешает сериализации — добавить optional поля сразу.
Если схемы нет или она permissive — не блокировать PR 1.
```

## Ограничение по размеру JSONB

Теоретически JSONB может быть очень большим, но практически нельзя раздувать agent blob.

`active_plan_v3` должен быть компактным:

```text
OK:
  текущий план
  1–5 шагов
  summary
  ids использованной памяти
  краткие score/reason

НЕ OK:
  вся история планов
  полные memory records
  большой thought log
  snapshots всего state
```

Практический лимит для MVP:

```text
active_plan_v3 <= 5–10 KB на агента
brain_trace    <= 10–20 KB на агента
```

Если хочется хранить историю планов, она должна идти в memory/decision summaries, а не в `active_plan_v3`.

---

# 3. `brain_trace` — когда писать и где хранить

## Решение

`brain_trace` — это **последний компактный snapshot мышления NPC**, а не лог всех мыслей.

```text
agent["brain_trace"] = latest trace only
```

Он перезаписывается:

1. когда бот проходит полный decision pipeline;
2. когда `PlanMonitor` оценивает активный `scheduled_action`;
3. когда план abort/pause/adapt/continue получает важное объяснение;
4. когда длительный action продолжает выполняться и нужно обновить `remaining_ticks`.

## Каждый тик или только при изменении плана?

Для MVP — **каждый тик, когда агент обрабатывается**.

Причина: даже если план не изменился, меняются:

- `turns_remaining`;
- `thirst`;
- `hunger`;
- `hp`;
- `plan_assessment`;
- `continue_score`;
- `interrupt_watchlist`.

Но `brain_trace` должен оставаться маленьким. Никакого append-only списка внутри него.

## Персистируется ли `brain_trace`?

Да, в MVP он хранится в agent JSON, потому что state уже сериализуется и фронт может его прочитать.

Но семантически это не “история”, а “последнее состояние отладки”.

```text
persistent latest snapshot: да
growing history: нет
```

## Нужно ли ограничивать размер?

Да.

Рекомендованный shape:

```json
{
  "turn": 12345,
  "mode": "scheduled_action_monitor",
  "current_thought": "Продолжаю идти в Бар за водой.",
  "active_objective": {...},
  "active_plan": {...},
  "scheduled_action": {...},
  "top_drives": [...],
  "plan_assessment": {...},
  "alternatives": [...],
  "memory_used": [...],
  "score_factors": [...],
  "interrupt_watchlist": [...]
}
```

Ограничения:

```text
top_drives: max 5
alternatives: max 3–5
memory_used: max 5–10, только id + summary + confidence
score_factors: max 10
plan.steps: max active plan steps, но без огромных payload
```

## Кто читает `brain_trace`?

В PR 1 — обычный state response:

```text
GET /api/games/zone_stalkers/{game_id}/state
```

Отдельный endpoint пока не нужен.

Если later state станет слишком большим, можно вынести:

```text
GET /api/games/zone_stalkers/{game_id}/agents/{agent_id}/brain_trace
```

Но на MVP лучше не плодить endpoint'ы.

---

# 4. `ItemNeed.compatible_item_types` — сериализация

## Решение

Используем два уровня:

```text
Runtime Python:
  frozenset[str] допустим и удобен

Serialized JSON:
  list[str], желательно sorted
```

Пример runtime dataclass:

```python
@dataclass(frozen=True)
class ItemNeed:
    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str]
    reason: str
```

Сериализация:

```python
def serialize_item_need(need: ItemNeed) -> dict[str, Any]:
    return {
        "key": need.key,
        "desired_count": need.desired_count,
        "current_count": need.current_count,
        "missing_count": need.missing_count,
        "urgency": round(need.urgency, 3),
        "compatible_item_types": sorted(need.compatible_item_types),
        "reason": need.reason,
    }
```

Десериализация:

```python
def deserialize_item_need(data: dict[str, Any]) -> ItemNeed:
    return ItemNeed(
        key=data["key"],
        desired_count=data["desired_count"],
        current_count=data["current_count"],
        missing_count=data["missing_count"],
        urgency=float(data["urgency"]),
        compatible_item_types=frozenset(data.get("compatible_item_types", [])),
        reason=data.get("reason", ""),
    )
```

## Нужно ли писать `ItemNeed` в agent JSON?

Не обязательно.

В PR 1–2 `ItemNeed` лучше использовать как runtime model и отображать в `brain_trace`.

Если сохранять:

```text
agent["item_needs"] = [serialized ItemNeed]
```

то только как debug/cache, не как source of truth.

Source of truth остаётся:

```text
agent.equipment
agent.inventory
agent.risk_tolerance
item catalogue
```

---

# 5. `PlanMonitor` и текущая interrupt-логика в `intents.py`

## Проблема

В `intents.py` уже есть thresholds:

```python
_HARD_INTERRUPT_SURVIVE_NOW = 0.70
_HARD_INTERRUPT_EMISSION = 0.80
_HARD_INTERRUPT_HEAL = 0.80
_HARD_INTERRUPT_NEEDS = 0.90
```

Если `PlanMonitor` заведёт свои такие же константы, появится дублирование и риск рассинхронизации.

## Решение для PR 1

Лучший вариант: создать маленький общий модуль.

```text
backend/app/games/zone_stalkers/decision/interrupt_policy.py
```

В нём:

```python
HARD_INTERRUPT_SURVIVE_NOW = 0.70
HARD_INTERRUPT_EMISSION = 0.80
HARD_INTERRUPT_HEAL = 0.80
HARD_INTERRUPT_NEEDS = 0.90

@dataclass
class InterruptSignal:
    key: str
    severity: float
    reason: str
    must_interrupt: bool
```

И helper:

```python
def detect_interrupt_signals(ctx, needs) -> list[InterruptSignal]:
    ...
```

`PlanMonitor` использует этот helper.

`intents.py` можно перевести на импорт этих констант сразу или в отдельном маленьком refactor-коммите.

## Если нужно минимально и безопасно

Если не хочется трогать `intents.py` в PR 1:

```text
PR 1:
  PlanMonitor вызывает evaluate_needs(ctx, state)
  PlanMonitor вызывает select_intent(ctx, needs, world_turn)
  Если selected_intent.kind является emergency intent,
  и текущий scheduled_action interruptible,
  то abort/pause.
```

Тогда дублирование порогов не появляется, потому что `select_intent()` уже применил пороги.

Пример:

```python
ctx = build_agent_context(agent_id, agent, state)
needs = evaluate_needs(ctx, state)
candidate_intent = select_intent(ctx, needs, world_turn)

if candidate_intent.kind in EMERGENCY_INTENTS:
    return PlanMonitorResult(
        decision="abort",
        reason=f"Emergency intent selected: {candidate_intent.kind}",
        candidate_intent=asdict(candidate_intent),
    )
```

## Рекомендация

Для PR 1:

```text
1. Не копировать thresholds в plan_monitor.py.
2. Использовать evaluate_needs + select_intent.
3. Добавить interrupt_policy.py только если это не раздувает PR.
4. Старые thresholds в intents.py не удалять сразу.
```

---

# 6. `score_factors` — где живут в состоянии агента

## Решение

`score_factors` живут внутри `brain_trace`.

```json
agent["brain_trace"]["score_factors"] = [...]
```

Почему:

- это debug/explain информация;
- она относится к последней оценке;
- она не должна быть source of truth;
- она может меняться каждый тик;
- её удобно показывать на фронте.

## Не хранить отдельно

Не нужен отдельный ключ:

```json
agent["objective_score_debug"]
```

Это увеличит количество разрозненных debug-полей.

## Что хранить в `active_plan_v3`

В `active_plan_v3` можно хранить только краткую информацию, нужную для продолжения плана:

```json
{
  "objective_key": "RESTORE_WATER",
  "objective_score": 0.82,
  "objective_reason": "Жажда высокая, торговец известен",
  "created_turn": 1200
}
```

Но полный список factors лучше не хранить в плане.

## Пример `score_factors`

```json
{
  "score_factors": [
    {
      "label": "Жажда",
      "value": 0.68,
      "weight": 0.45,
      "impact": 0.306,
      "explanation": "Жажда 68%, воды в инвентаре нет"
    },
    {
      "label": "Память о торговце",
      "value": 0.91,
      "weight": 0.15,
      "impact": 0.137,
      "explanation": "Сидорович обычно находится в Баре"
    },
    {
      "label": "Дистанция",
      "value": -0.12,
      "weight": 1.0,
      "impact": -0.12,
      "explanation": "До Бара 12 ходов"
    }
  ]
}
```

---

# 7. Frontend: endpoint и компонент

## Правильное место

Да, для MVP правильное место:

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

Это уже модалка полного профиля агента, там уже показываются:

- характеристики;
- снаряжение;
- инвентарь;
- память;
- `_v2_context`;
- scheduled action;
- текущая цель.

`brain_trace` логично показывать там же.

## Endpoint

В PR 1 `brain_trace` должен приходить как часть обычного state response.

Отдельный endpoint пока не нужен.

Причина:

```text
brain_trace маленький,
показывает текущее состояние,
нужен вместе с агентом,
не является большим memory log.
```

## Ограничения размера `/state`

Да, ограничения есть, поэтому:

```text
brain_trace должен быть компактным
memory_used должен содержать только id + summary + confidence
не включать full memory records
не включать history
не включать весь objective list
```

Если станет тяжело:

```text
brain_trace_summary в /state
full brain_trace через отдельный endpoint
```

Но это later.

## UI MVP

Лучше не просто `<pre>` навсегда, но для первого PR допустим collapsible debug block.

Минимум:

```tsx
{agent.brain_trace && (
  <Section label="🧠 Мысли NPC">
    <details open>
      <summary>{agent.brain_trace.current_thought ?? 'Brain trace'}</summary>
      <pre>{JSON.stringify(agent.brain_trace, null, 2)}</pre>
    </details>
  </Section>
)}
```

Лучше сразу чуть readable:

```text
Текущая мысль
Активная цель
Активный план
Top drives
Почему выбран
Альтернативы
Память
```

Но если задача PR 1 — backend behavior, то `<details><pre>` достаточно.

## TypeScript

В `AgentForProfile` добавить optional field:

```ts
brain_trace?: {
  turn?: number;
  mode?: string;
  current_thought?: string;
  active_objective?: unknown;
  active_plan?: unknown;
  top_drives?: Array<unknown>;
  alternatives?: Array<unknown>;
  memory_used?: Array<unknown>;
  score_factors?: Array<unknown>;
  interrupt_watchlist?: Array<string>;
};
```

Позже заменить `unknown` на строгие типы.

---

# 8. `MemoryStore.ingest()` — совместимость с `agent["memory"]`

## Решение

В PR 1 не переводим всю память на `memory_v3`.

Используем двойную стратегию:

```text
legacy agent["memory"]:
  всё, что нужно старым gameplay-модулям

agent["memory_v3"]:
  новые структурированные записи, индексы, retrieval-ready data
```

## Почему нельзя писать только в `memory_v3`

Потому что текущий код уже читает `agent["memory"]`.

Примеры текущих зависимостей:

```text
context_builder:
  _entities_from_memory
  _locations_from_memory
  _hazards_from_memory
  _traders_from_visible_and_memory

needs.py:
  _is_emission_warned

planner.py / tick_rules.py helpers:
  _find_item_memory_location
  _confirmed_empty_locations
  _bot_ask_colocated_stalkers_about_item
  _bot_ask_colocated_stalkers_about_agent
```

Если новые записи начать писать только в `memory_v3`, старые маршруты поведения могут перестать видеть:

- предупреждения о выбросе;
- известных торговцев;
- места с предметами;
- сведения о целях;
- подтверждённо пустые локации.

## Нужно ли писать всё в оба места?

Нет.

Писать в legacy `memory` только compatibility-critical records.

Пример bridge policy:

```python
LEGACY_MIRRORED_KINDS = {
    "emission_imminent",
    "emission_started",
    "emission_ended",
    "trader_visit",
    "item_seen",
    "item_location_known",
    "location_observed",
    "location_confirmed_empty",
    "target_last_seen",
    "intel_received",
    "decision",
    "death",
}
```

Все новые подробные v3-записи могут идти только в `memory_v3`.

## Как не раздуть `agent["memory"]`

Legacy `memory` уже должен оставаться под `MAX_AGENT_MEMORY = 2000`.

Правило:

```text
agent["memory"] = capped compatibility event stream
agent["memory_v3"] = structured memory with own retention/decay/consolidation
```

Для legacy memory:

- не писать каждую working observation;
- не писать low-level score factors;
- не писать каждый тик “continue plan”;
- писать только значимые changes:
  - plan started;
  - plan aborted;
  - plan paused;
  - important observation;
  - item found;
  - item missing/contradicted;
  - danger;
  - trade;
  - death;
  - goal progress.

## Для PR 1

Если `MemoryStore` ещё не реализован, не блокировать PR.

Сделать минимум:

```text
brain_trace пишет объяснение в agent["brain_trace"]
старые _add_memory остаются как есть
для plan abort/pause добавить legacy decision memory через _add_memory
```

`memory_v3` можно начать отдельным PR.

---

# 9. Расположение новых тестов

## Решение

Класть новые v3-тесты в:

```text
backend/tests/decision/v3/
```

Пример:

```text
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_active_plan.py
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_item_needs.py
backend/tests/decision/v3/test_memory_store.py
```

Почему не плоско в `backend/tests/decision/`:

- там уже много v2/legacy тестов;
- v3 будет расти;
- проще видеть границу миграции;
- проще потом удалять/переносить v2.

Shared fixtures можно оставить в:

```text
backend/tests/decision/conftest.py
```

или добавить:

```text
backend/tests/decision/v3/conftest.py
```

если v3 нужны отдельные factories.

## Старые тесты

Не переписывать сразу.

Стратегия:

```text
old tests:
  продолжают проверять compatibility behavior

new v3 tests:
  проверяют PlanMonitor, brain_trace, item needs, ActivePlan wrapper

когда конкретный legacy module удаляется:
  тогда удаляем/переписываем соответствующие старые tests
```

---

# 10. `explain_intent.py` и новый `brain_trace.py`

## Что сейчас делает `explain_intent.py`

`explain_intent.py` — полезный модуль. Он без side effects строит объяснение decision pipeline для агента:

```text
context_summary
need_scores
selected_intent
active_plan
```

Он вызывает:

```text
build_agent_context
evaluate_needs
select_intent
build_plan
```

Это делает его хорошим debug-preview инструментом.

## Но это не то же самое, что `brain_trace`

Ключевая разница:

```text
explain_intent.py:
  “Что бы агент выбрал сейчас, если прогнать pipeline?”

brain_trace.py:
  “Что агент фактически думал/решил в последнем тике, 
   включая active scheduled_action, PlanMonitor и continue/pause/abort?”
```

Это важно.

Если у агента есть активный `scheduled_action`, `explain_intent.py` может построить новый hypothetical plan, но это не обязательно то, что агент реально делает. А `brain_trace` должен объяснять именно реальное поведение:

```text
Продолжаю старый план
Поставил план на паузу
Отменил travel из-за жажды
Перешёл к emergency shelter
```

## Решение

`brain_trace.py` не заменяет `explain_intent.py`.

Он живёт рядом:

```text
backend/app/games/zone_stalkers/decision/debug/explain_intent.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
```

## Как избежать дублирования

Вынести общие форматтеры:

```text
backend/app/games/zone_stalkers/decision/debug/formatters.py
```

Например:

```python
format_context_summary(ctx, agent, state)
format_need_scores(needs)
format_intent(intent)
format_plan(plan)
format_scheduled_action(scheduled_action)
```

Тогда:

```text
explain_intent.py:
  использует format_context_summary / format_need_scores / format_intent / format_plan

brain_trace.py:
  использует те же formatters
  + добавляет plan_monitor_result
  + добавляет actual active_plan_v3
  + добавляет alternatives
  + добавляет memory_used
```

## Нужно ли перед PR 1 читать `explain_intent.py`

Да, обязательно.

Из него стоит переиспользовать:

- структуру readable output;
- `context_summary`;
- формат `need_scores.top_3`;
- формат `selected_intent`;
- формат `active_plan`;
- принцип “no side effects”.

Но не стоит использовать его как единственный источник brain_trace, потому что он не описывает `PlanMonitor` и фактическое продолжение active scheduled_action.

---

# Рекомендуемый scope PR 1

## Backend files

Добавить:

```text
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/decision/models/active_plan.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
backend/app/games/zone_stalkers/decision/debug/formatters.py   # optional but recommended
```

Изменить:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/debug/explain_intent.py   # maybe reuse formatters only
```

Опционально:

```text
backend/app/games/zone_stalkers/decision/interrupt_policy.py
```

## Frontend files

Изменить:

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

Добавить optional field:

```ts
brain_trace?: ...
active_plan_v3?: ...
```

## Tests

Добавить:

```text
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_active_plan.py
```

---

# Минимальное поведение PR 1

PR 1 считается успешным, если:

1. Бот с активным `scheduled_action` получает `brain_trace`.
2. `PlanMonitor` оценивает active scheduled action до `_process_scheduled_action`.
3. При emergency intent (`flee_emission`, `wait_in_shelter`, `escape_danger`, `heal_self`, `seek_water`, `seek_food`) активный interruptible action может быть отменён.
4. После отмены `scheduled_action` бот может пройти обычный decision pipeline в тот же тик.
5. `brain_trace` показывает:
   - current thought;
   - scheduled action;
   - monitor decision;
   - top needs;
   - selected emergency intent or continue reason.
6. Старые тесты не ломаются.
7. Legacy emission interrupt остаётся как fallback.

---

# Пример PR 1: scheduled travel прерывается жаждой

Состояние:

```json
{
  "scheduled_action": {
    "type": "travel",
    "target_id": "swamp",
    "turns_remaining": 10
  },
  "thirst": 95,
  "inventory": []
}
```

PlanMonitor:

```json
{
  "decision": "abort",
  "reason": "Критическая жажда — нужно искать воду",
  "candidate_intent": {
    "kind": "seek_water",
    "score": 0.95
  }
}
```

Effect:

```text
agent["scheduled_action"] = null
agent["action_used"] remains false
agent["brain_trace"] updated
normal bot decision loop runs later this tick
```

Brain trace:

```json
{
  "turn": 5000,
  "mode": "plan_monitor",
  "current_thought": "Я прерываю движение: жажда стала критической.",
  "scheduled_action": {
    "type": "travel",
    "target_id": "swamp",
    "turns_remaining": 10
  },
  "plan_monitor": {
    "decision": "abort",
    "reason": "Критическая жажда — нужно искать воду"
  },
  "top_drives": [
    {"key": "drink", "score": 0.95},
    {"key": "reload_or_rearm", "score": 0.40}
  ],
  "interrupt_watchlist": [
    "emission_on_dangerous_terrain",
    "hp_critical",
    "thirst_critical",
    "hunger_critical",
    "route_blocked"
  ]
}
```

---

# Пример PR 1: scheduled travel продолжается

Состояние:

```json
{
  "scheduled_action": {
    "type": "travel",
    "target_id": "bar",
    "turns_remaining": 4
  },
  "thirst": 45,
  "hp": 90
}
```

PlanMonitor:

```json
{
  "decision": "continue",
  "reason": "Нет критических причин отменять путь; цель близко."
}
```

Effect:

```text
_process_scheduled_action executes normally
turns_remaining decreases
brain_trace updated
```

Brain trace:

```json
{
  "turn": 5001,
  "mode": "plan_monitor",
  "current_thought": "Продолжаю путь: цель близко, критических угроз нет.",
  "scheduled_action": {
    "type": "travel",
    "target_id": "bar",
    "turns_remaining": 4
  },
  "plan_monitor": {
    "decision": "continue",
    "reason": "Нет критических причин отменять путь"
  }
}
```

---

# Что не входит в PR 1

Чтобы PR не стал слишком большим, не включать:

```text
полную MemoryStore v3
Redis
новые PostgreSQL tables
полную замену NeedScores на Drive
полную замену AgentContext на BeliefState
полную ObjectiveGenerator систему
удаление scheduled_action
удаление legacy memory
полный красивый frontend component
```

---

# Итоговая позиция

Раунд 2 вопросов правильный: они фиксируют реальные точки стыка между v3 и текущим кодом.

Главный ответ:

```text
PR 1 не должен переписывать AI.
PR 1 должен добавить способность NPC переоценивать active scheduled_action
и объяснять это через brain_trace.
```

Самый безопасный первый шаг:

```text
tick_rules.py:
  before _process_scheduled_action
    → PlanMonitor
    → maybe clear scheduled_action
    → write brain_trace
    → normal decision pipeline can run later this tick
```

Так мы получаем самое важное изменение поведения — НПЦ больше не “слепнет” во время длительного действия — без полной замены всей архитектуры.
