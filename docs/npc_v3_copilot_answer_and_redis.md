# Ответ на вопросы Copilot по NPC Brain v3 + решение по Redis

> Проект: `zone_stalkers`  
> Ветка: `copilot/rewrite-npc-equipment-rules`  
> Контекст: файл `docs/npc_v3_questions.md` с уточняющими вопросами по реализации новой архитектуры поведения НПЦ.

---

## 0. Краткое решение

Copilot задал правильные вопросы. Они показывают, что исходная v3-спецификация описывает желаемую архитектуру достаточно широко, но нуждается в отдельном разделе **Implementation Decisions / MVP Scope**.

Главное решение:

```text
Не реализуем NPC Brain v3 целиком одним большим рефакторингом.
Делаем инкрементальную миграцию поверх текущей системы.
```

Первый этап должен быть максимально безопасным:

```text
1. brain_trace
2. active_plan_v3 как wrapper над scheduled_action
3. PlanMonitor для переоценки активного scheduled_action
4. debug payload для frontend
5. минимальная фронтовая визуализация “что НПЦ думает / делает / планирует”
```

Полный `NPCBrain`, `BeliefState`, `MemoryStore`, `DriveEvaluator`, `ObjectiveGenerator`, `PlanMonitor` и новая memory subsystem не стоит делать за один PR. Это слишком большой риск для текущей игры.

---

## 1. Redis для памяти НПЦ: имеет ли смысл?

### 1.1. Короткий ответ

Redis может быть полезен, но **не как первый шаг**.

Для текущего этапа проекта Redis, скорее всего, сделает архитектуру сложнее раньше, чем даст реальную пользу. Сначала нужно спроектировать память как API и индексированную структуру внутри игры. После этого Redis можно подключить как backend для `MemoryStore`, если появятся реальные проблемы производительности.

Решение:

```text
MVP:
  Redis не используем.
  Память остаётся в state / JSON / Python-структурах.
  Но проектируем MemoryStore так, чтобы позже можно было заменить backend на Redis/PostgreSQL.

Later:
  Redis можно подключить для hot-memory cache, retrieval indexes, short-term memory,
  pub/sub/debug streams или vector/hybrid search, если это станет нужно.
```

---

### 1.2. Где Redis действительно может помочь

Redis хорошо подходит для:

```text
1. Быстрого доступа к hot memory
2. Индексов по location / entity / item / tag
3. Sorted sets для scoring, decay, recency, priority
4. Sets для быстрых пересечений тегов
5. Hashes/JSON для хранения самих records
6. Streams для event log / observation stream
7. TTL для working memory
8. Pub/Sub или Streams для frontend/debug updates
9. Vector search / hybrid search, если появится semantic retrieval
```

Пример возможного Redis-моделирования:

```text
memory:{agent_id}:{memory_id}           Hash/JSON одной записи
memory_idx:{agent_id}:tag:{tag}         Set memory_id
memory_idx:{agent_id}:location:{loc}    Set memory_id
memory_idx:{agent_id}:entity:{entity}   Set memory_id
memory_score:{agent_id}:importance      Sorted Set memory_id -> importance
memory_score:{agent_id}:recency         Sorted Set memory_id -> turn
memory_working:{agent_id}               Keys with TTL или List
memory_stream:{agent_id}                Stream наблюдений / решений
```

Retrieval может выглядеть так:

```text
Нужно найти воду:
  intersect(tag:water, status:active)
  score by recency + confidence + distance
  return top 10
```

---

### 1.3. Где Redis может навредить

Redis добавит проекту стоимость:

```text
1. Дополнительный сервис в dev/prod окружении
2. Синхронизация Redis ↔ PostgreSQL/state blob
3. Вопросы persistence и восстановления после рестарта
4. Проблемы consistency: что является source of truth?
5. Усложнение тестов
6. Усложнение CI
7. Усложнение локального запуска проекта
8. Возможные race conditions при параллельных тиках
```

Главная архитектурная опасность: если Redis подключить слишком рано, он может начать диктовать форму памяти ещё до того, как мы поняли правильную domain-модель.

То есть вместо:

```text
MemoryStore API → backend can be JSON/Postgres/Redis
```

мы рискуем получить:

```text
Redis keys everywhere → game logic depends on storage details
```

Этого нужно избежать.

---

### 1.4. Когда Redis точно имеет смысл

Redis стоит подключать, если появится хотя бы одно из условий:

```text
1. У одного NPC реально 10 000+ активных memory records.
2. В одном мире много NPC, например 100–1000, и каждый тик делает retrieval.
3. Линейное сканирование памяти стало заметным bottleneck.
4. Нужно быстро отдавать live brain_trace / thought stream на frontend.
5. Появился semantic retrieval по embedding/vector search.
6. Память нужно шарить между процессами или воркерами.
7. Tick processing выносится в отдельный worker/queue.
```

До этого Redis будет premature optimization.

---

### 1.5. Рекомендуемая стратегия

Нужно сделать так, чтобы Redis был возможен, но не обязателен.

```python
class MemoryStoreProtocol:
    def ingest(self, agent_id: str, records: list[MemoryRecord]) -> None: ...
    def retrieve(self, agent_id: str, query: MemoryQuery) -> list[MemoryRecord]: ...
    def mark_stale(self, agent_id: str, memory_ids: list[str]) -> None: ...
    def decay(self, agent_id: str, world_turn: int) -> MemoryDecayResult: ...
    def consolidate(self, agent_id: str, world_turn: int) -> MemoryConsolidationResult: ...
```

Первый backend:

```text
InMemoryJsonMemoryStore
```

Позже возможные backend'и:

```text
PostgresJsonbMemoryStore
RedisMemoryStore
HybridMemoryStore
```

Идеальная схема на будущее:

```text
PostgreSQL = source of truth / persisted memory
Redis      = hot cache / indexes / retrieval acceleration
```

То есть Redis не должен быть единственным местом хранения важной памяти, если не настроена понятная persistence/recovery стратегия.

---

### 1.6. Итог по Redis

Моё решение:

```text
Сейчас Redis не добавляем.
Но архитектуру MemoryStore проектируем так, чтобы Redis можно было подключить позже.
```

Почему:

```text
+ Мы ещё не знаем реальные bottleneck'и.
+ Текущий проект проще развивать без нового сервиса.
+ Сначала важнее исправить модель поведения и объяснимость.
+ Индексы памяти можно реализовать внутри JSON/Python-структуры.
+ Redis будет полезен позже как оптимизация, а не как фундамент доменной модели.
```

---

## 2. Ответы на вопросы Copilot

---

## 2.1. Отправная точка и scope

Вопрос Copilot:

```text
Документ описывает 6 этапов миграции. С какого начинаем?
```

Ответ:

Начинаем не с полного MVP, а с безопасного compatibility layer.

Первый PR:

```text
1. Добавить brain_trace.
2. Добавить active_plan_v3 как wrapper над scheduled_action.
3. Добавить PlanMonitor.
4. Научить PlanMonitor переоценивать active scheduled_action.
5. Отдавать debug payload для frontend.
6. Не ломать текущие NeedScores/Intent/Planner/Executor.
```

Не делаем сразу:

```text
- полный NPCBrain;
- полную замену AgentContext на BeliefState;
- полную замену NeedScores на Drive;
- полную замену scheduled_action на ActivePlan;
- Redis-backed MemoryStore;
- semantic/vector memory retrieval.
```

Рекомендуемый порядок:

```text
PR 1: brain_trace + active_plan_v3 + PlanMonitor
PR 2: ItemNeeds вместо монолитного reload_or_rearm
PR 3: MemoryStore v3 поверх старой памяти
PR 4: Objective scoring
PR 5: BeliefState и DriveEvaluator
PR 6: постепенная замена legacy scheduled_action ownership
```

---

## 2.2. Память: хранение

Вопрос Copilot:

```text
Новая многослойная память остаётся в JSON-поле агента или переезжает в отдельную таблицу с индексами?
```

Ответ:

Для MVP память остаётся в JSON/state blob, но через новый `MemoryStore` API.

Внутри агента можно временно хранить:

```python
agent["memory_v3"] = {
    "working": [],
    "episodic": [],
    "semantic": [],
    "spatial": {},
    "social": {},
    "threat": [],
    "goal": [],
    "indexes": {
        "by_location": {},
        "by_entity": {},
        "by_item_type": {},
        "by_tag": {},
    }
}
```

Но game logic не должна напрямую лазить в эту структуру. Только через методы:

```python
memory_store.ingest(...)
memory_store.retrieve(...)
memory_store.decay(...)
memory_store.consolidate(...)
```

`agent["memory"]` нужно сохранить как legacy compatibility layer, пока старые helper'ы, UI и тесты от него зависят.

Решение по decay/consolidation:

```text
MVP:
  decay/consolidation выполняются внутри обычного tick processing,
  но не обязательно каждый тик.

Например:
  ingest         — каждый тик
  decay          — раз в 50–100 тиков
  consolidation  — раз в 500–1000 тиков
```

В будущем можно вынести consolidation в отдельную background-задачу, но сейчас это преждевременно.

---

## 2.3. BeliefState vs AgentContext

Вопрос Copilot:

```text
BeliefState полностью заменяет AgentContext или является его надстройкой?
```

Ответ:

В MVP `BeliefState` является надстройкой над `AgentContext`.

```text
AgentContext:
  нормализованный snapshot текущей ситуации

BeliefState:
  интерпретированная картина мира глазами NPC,
  построенная из AgentContext + relevant memory + assumptions
```

Переходный код:

```python
ctx = build_agent_context(agent_id, agent, state)
memories = memory_store.retrieve(agent_id, query_context)
beliefs = build_belief_state(ctx, memories)
```

Ограничение “NPC не видит весь state” нужно вводить сразу как архитектурное правило, но не ломать всё одномоментно.

Правило:

```text
Новый код NPCBrain / ObjectiveGenerator / Planner должен использовать BeliefState,
а не напрямую весь state, кроме случаев системных правил исполнения.
```

Executor может использовать `state`, потому что он применяет реальные эффекты мира. Но decision layer должен опираться на beliefs.

---

## 2.4. Drive vs NeedScores

Вопрос Copilot:

```text
Нужно сразу заменить NeedScores на Drive?
```

Ответ:

Нет. Сразу заменять `NeedScores` не нужно.

Первый шаг:

```text
Оставить NeedScores.
Добавить ItemNeeds.
Перевести reload_or_rearm на max(ItemNeed.urgency).
```

Новая структура:

```python
@dataclass
class ItemNeed:
    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str]
    reason: str
```

Пример:

```python
ItemNeed(
    key="ammo",
    desired_count=3,
    current_count=1,
    missing_count=2,
    urgency=0.40,
    compatible_item_types=frozenset({"ammo_545"}),
    reason="АК-74 требует 5.45; есть 1 пачка из 3",
)
```

Тогда:

```python
needs.reload_or_rearm = max(n.urgency for n in item_needs)
```

Planner должен использовать `item_needs`, а не заново вычислять, чего не хватает.

Полная замена `NeedScores` на `Drive` — отдельный этап.

---

## 2.5. Objective scoring

Вопрос Copilot:

```text
Что входит в MVP? Можно пока urgency * weight - risk * risk_sensitivity?
```

Ответ:

Да, для MVP нужна упрощённая формула.

MVP scoring:

```text
objective_score =
    urgency
  + goal_alignment_bonus
  + memory_confidence_bonus
  - risk_penalty
  - distance_penalty
  - switch_cost
```

Более формально:

```text
objective_score =
    urgency * 0.50
  + expected_value * 0.20
  + memory_confidence * 0.10
  + goal_alignment * 0.10
  - risk * risk_sensitivity
  - distance_cost * 0.10
  - switch_cost
```

Если `expected_value` или `success_probability` пока неизвестны, их можно заменить простыми эвристиками.

Важно: с первого дня хранить `score_factors`.

Пример:

```json
{
  "objective": "BUY_WATER_FROM_TRADER",
  "score": 0.82,
  "score_factors": [
    {"label": "Жажда высокая", "value": 0.68, "effect": "+"},
    {"label": "Торговец известен", "value": 0.91, "effect": "+"},
    {"label": "До Бара 6 ходов", "value": 0.12, "effect": "-"}
  ]
}
```

Это нужно для frontend-объяснения.

---

## 2.6. PlanMonitor и инерция плана

Вопрос Copilot:

```text
switch_cost фиксированный или динамический?
PlanMonitor — новый модуль или часть intents.py/planner.py?
Что является источником remaining_ticks?
```

Ответ:

`PlanMonitor` должен быть отдельным модулем:

```text
backend/app/games/zone_stalkers/decision/plan_monitor.py
```

Не надо смешивать его с `intents.py` или `planner.py`.

`switch_cost` в MVP может быть простым, но не полностью фиксированным.

Пример:

```python
switch_cost = 0.10

if current_step_almost_complete:
    switch_cost += 0.10

if current_plan_is_emergency:
    switch_cost += 0.30

if new_objective_is_survival_critical:
    switch_cost -= 0.20
```

Источник `remaining_ticks` в переходный период:

```python
agent["scheduled_action"].get("turns_remaining")
```

Источник смысла:

```python
agent["active_plan_v3"]
```

То есть:

```text
scheduled_action отвечает за техническое длительное действие,
active_plan_v3 отвечает за смысл и план.
```

---

## 2.7. scheduled_action → ActivePlan

Вопрос Copilot:

```text
Как они соотносятся в переходный период?
```

Ответ:

В переходный период:

```text
ActivePlan — источник смысла.
scheduled_action — runtime-механизм исполнения текущего длительного шага.
```

Пример:

```json
"active_plan_v3": {
  "objective_key": "RESTORE_WATER",
  "summary": "Иду в Бар купить воду",
  "steps": [
    {"kind": "travel_to_location", "target": "bar", "status": "running"},
    {"kind": "trade_buy_item", "item_category": "drink", "status": "pending"},
    {"kind": "consume_item", "item_type": "water", "status": "pending"}
  ]
}
```

```json
"scheduled_action": {
  "type": "travel",
  "target_id": "bar",
  "turns_remaining": 6,
  "turns_total": 12
}
```

Нужно сделать bridge:

```text
PlanStep → scheduled_action
```

Но не удалять `scheduled_action` сразу.

---

## 2.8. Personality

Вопрос Copilot:

```text
Какие поля нужны для MVP?
```

Ответ:

Для MVP достаточно `risk_tolerance`.

Но можно уже добавить структуру:

```python
agent["personality"] = {
    "risk_tolerance": agent.get("risk_tolerance", 0.5),
    "greed": 0.5,
    "caution": 1.0 - agent.get("risk_tolerance", 0.5),
    "sociability": 0.5,
    "loyalty": 0.5,
}
```

В MVP реально использовать только:

```text
risk_tolerance
caution = 1 - risk_tolerance
```

Остальные поля пока можно хранить, но не подключать к scoring, чтобы не раздувать поведение.

---

## 2.9. Frontend

Вопрос Copilot:

```text
Frontend входит в scope v3 или сначала только backend?
```

Ответ:

Frontend должен входить в scope первого этапа, но минимально.

Почему: без видимого `brain_trace` новую систему будет тяжело отлаживать.

MVP frontend:

```text
AgentProfileModal показывает:
  - current_thought
  - active_objective
  - active_plan_v3 summary
  - top drives / needs
  - alternatives
  - memory_used
  - interrupt_watchlist
```

Не обязательно сразу делать отдельный большой красивый компонент. Можно начать с блока в существующей модалке агента.

Backend должен отдавать:

```python
agent["brain_trace"] = {
    "current_thought": "Иду к торговцу за водой",
    "active_objective": {...},
    "top_drives": [...],
    "plan": {...},
    "alternatives": [...],
    "memory_used": [...],
    "interrupt_watchlist": [...],
}
```

---

## 2.10. Тесты

Вопрос Copilot:

```text
Старые тесты переписать или поддерживать параллельно?
```

Ответ:

Поддерживать параллельно.

Старые тесты остаются, пока работают старые модули:

```text
test_needs.py
test_planner.py
test_intents.py
test_thirst_at_trader.py
```

Новые тесты добавить рядом:

```text
test_brain_trace.py
test_active_plan_v3.py
test_plan_monitor.py
test_item_needs.py
test_memory_store.py
test_objective_scoring.py
```

Старые тесты переписывать только тогда, когда соответствующий legacy-модуль реально удаляется.

---

## 3. Предлагаемый список файлов для первого PR

Минимальный первый PR:

```text
backend/app/games/zone_stalkers/decision/brain_trace.py
backend/app/games/zone_stalkers/decision/active_plan.py
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/decision/item_needs.py
backend/tests/decision/test_brain_trace.py
backend/tests/decision/test_active_plan.py
backend/tests/decision/test_plan_monitor.py
backend/tests/decision/test_item_needs.py
```

В существующие файлы внести минимальные изменения:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/needs.py
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

---

## 4. MVP behavior для PlanMonitor

На первом этапе PlanMonitor должен уметь переоценивать активный `scheduled_action`.

Минимальные причины для прерывания/паузы:

```text
1. emission threat on dangerous terrain
2. hp critical
3. thirst critical
4. hunger critical
5. route blocked
6. target location invalid
7. target item contradicted by observation
```

Решения:

```text
CONTINUE
ADAPT
PAUSE
ABORT
COMPLETE
```

Пример:

```python
@dataclass
class PlanMonitorResult:
    decision: Literal["continue", "adapt", "pause", "abort", "complete"]
    reason: str
    current_plan_score: float
    best_alternative_score: float | None
    switch_cost: float
    recommended_objective: str | None
```

---

## 5. Как отвечать Copilot кратко

Можно отправить такой общий ответ:

```text
Делаем v3 инкрементально, не полным переписыванием.

Первый этап:
- active_plan_v3 как wrapper над scheduled_action;
- brain_trace для backend/frontend debug;
- PlanMonitor как отдельный модуль;
- базовая переоценка активного scheduled_action;
- ItemNeeds как подготовка к разбиению reload_or_rearm;
- старые NeedScores/Intent/Planner остаются совместимыми.

Память пока остаётся в JSON/state через MemoryStore API.
Redis сейчас не подключаем: проектируем MemoryStore так, чтобы Redis/Postgres backend можно было добавить позже.

BeliefState сначала надстройка над AgentContext.
Drive не заменяет NeedScores сразу.
Objective scoring в MVP простой: urgency + goal/memory bonuses - risk/distance/switch_cost.
Frontend входит в MVP минимально через brain_trace в AgentProfileModal.
Старые тесты сохраняем, новые добавляем параллельно.
```

---

## 6. Итог

Правильная стратегия:

```text
Сначала сделать поведение объяснимым и переоцениваемым.
Потом сделать память структурированной.
Потом ускорять память, если понадобится.
```

Redis может стать полезным ускорителем, но не должен быть фундаментом первой реализации.

Фундаментом должна быть доменная модель:

```text
MemoryStore API
BeliefState
ItemNeeds
Objective scoring
ActivePlan
PlanMonitor
BrainTrace
```

Только после этого можно безопасно выбрать backend памяти:

```text
JSON/state blob
PostgreSQL JSONB/table
Redis cache/index
Hybrid Postgres + Redis
```
