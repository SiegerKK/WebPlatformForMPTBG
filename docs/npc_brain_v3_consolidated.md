# NPC Brain v3 — Сводный документ

> Проект: `zone_stalkers`  
> Составлен из: `npc_behavior_architecture_v3.md`, `npc_v3_copilot_answer_and_redis.md`, `npc_v3_questions_round2–5_answer.md`  
> Цель: единый документ, описывающий всё обсуждённое: архитектуру, принятые решения по реализации и контракт PR 1.

---

## Оглавление

1. [Проблема текущей схемы](#1-проблема-текущей-схемы)
2. [Цели v3](#2-цели-v3)
3. [Основная идея: когнитивный цикл](#3-основная-идея-когнитивный-цикл)
4. [Новые базовые сущности](#4-новые-базовые-сущности)
5. [Память как основная механика](#5-память-как-основная-механика)
6. [Схема принятия решений](#6-схема-принятия-решений)
7. [Системная политика прерывания планов](#7-системная-политика-прерывания-планов)
8. [Примеры поведения](#8-примеры-поведения)
9. [Планирование](#9-планирование)
10. [Frontend-визуализация](#10-frontend-визуализация)
11. [Redis: позиция](#11-redis-позиция)
12. [Стратегия миграции — порядок PR](#12-стратегия-миграции--порядок-pr)
13. [Non-goals for PR 1](#13-non-goals-for-pr-1)
14. [Expected file changes for PR 1](#14-expected-file-changes-for-pr-1)
15. [PR 1 — финальный контракт](#15-pr-1--финальный-контракт)
16. [PR 1 — детальные implementation decisions](#16-pr-1--детальные-implementation-decisions)

---

## 1. Проблема текущей схемы

Текущая система уже движется в правильную сторону: есть `AgentContext`, `NeedScores`, `Intent`, `Plan`, `PlanStep`, `Executor`. Но фактически управление поведением всё ещё частично находится в `tick_rules.py` и `scheduled_action`.

Главные проблемы:

1. **Активное действие почти не переоценивается.**  
   Если у агента есть `scheduled_action`, обычный decision pipeline не запускается. Исключение одно: угроза выброса может прервать `travel` или `explore_anomaly_location`.

2. **Interrupt-логика разбросана.**  
   Часть прерываний описана в `intents.py`, часть в `_process_scheduled_action`, часть в executor'ах, часть в legacy helper'ах.

3. **Слишком много сущностей обозначают почти одно и то же.**  
   `global_goal`, `current_goal`, `NeedScores`, `Intent.kind`, `Plan.intent_kind`, `PlanStep.kind`, `scheduled_action.type`, `memory.effects.action_kind`, `reason` — из-за этого трудно понять: что НПЦ хочет, почему он этого хочет и что он делает прямо сейчас.

4. **Память используется как список событий, а не как полноценная когнитивная система.**  
   При десятках тысяч записей простой линейный список станет медленным и плохо управляемым.

5. **Дублирование логики между оценкой потребностей и планированием.**  
   `needs.py` считает, что НПЦ нужно пополнить запасы, а `planner.py` заново определяет, чего именно не хватает. Это создаёт риск рассинхронизации.

6. **Система выглядит как гибрид utility AI и дерева исключений.**  
   Есть численные scores, но поверх них много особых проверок: выброс, критическая жажда/голод, suppress get_rich, pre-decision equipment maintenance и т.д.

---

## 2. Цели v3

Новая схема должна:

1. **Каждый тик переоценивать ситуацию**, даже если у НПЦ уже есть активный план или длительное действие.
2. **Свести исключения к системным правилам.** Выброс, голод, жажда, низкий HP, бой, нехватка снаряжения — всё должно участвовать в общей системе оценки.
3. **Сделать память основной механикой поведения.** НПЦ должен принимать решения не по всеведущему `state`, а по тому, что видит, помнит, слышал или вывел из опыта.
4. **Поддержать десятки тысяч записей памяти.** Память должна индексироваться, агрегироваться, стареть, очищаться и превращаться в устойчивые знания.
5. **Сделать фронтовое объяснение поведения читаемым.** Игрок/разработчик должен видеть: что НПЦ думает, какие потребности активны, какой план выполняется, почему он выбран.
6. **Снизить дублирование.** Одно и то же знание не должно вычисляться в трёх местах по-разному.
7. **Сохранить атмосферу Zone Stalkers.** НПЦ не должен быть идеально рациональным.

---

## 3. Основная идея: когнитивный цикл

Вместо цепочки:

```text
tick → if no scheduled_action → context → needs → intent → plan → executor
```

используется единый когнитивный цикл:

```text
NPCBrain.tick(agent_id, world_state)
  1. observe_world()
  2. update_memory()
  3. build_belief_state()
  4. evaluate_drives()
  5. generate_objectives()
  6. evaluate_active_plan()
  7. choose_continue_adapt_or_replan()
  8. build_or_update_plan()
  9. execute_next_step()
 10. write_thought_trace()
```

**Ключевое отличие:** активный план не блокирует мышление.  
НПЦ каждый тик заново смотрит на ситуацию и решает:

```text
продолжить текущий план
адаптировать текущий план
поставить план на паузу
полностью отменить план
выбрать новый план
```

Это не набор исключений. Это результат сравнения полезности текущего плана и альтернатив.

---

## 4. Новые базовые сущности

### 4.1. NPCBrain

`NPCBrain` — главный объект принятия решений одного агента.

```python
class NPCBrain:
    def tick(self, agent_id: str, state: dict, world_turn: int) -> BrainTickResult:
        observations = self.observe(agent_id, state)
        memory_delta = self.memory_system.ingest(agent_id, observations, world_turn)
        beliefs = self.belief_builder.build(agent_id, state, observations)
        drives = self.drive_evaluator.evaluate(agent_id, beliefs)
        objectives = self.objective_generator.generate(agent_id, beliefs, drives)
        plan_assessment = self.plan_monitor.assess(agent_id, beliefs, objectives)
        chosen = self.decision_policy.choose(agent_id, objectives, plan_assessment)
        plan = self.planner.ensure_plan(agent_id, chosen, beliefs)
        action = self.executor.execute_next_step(agent_id, plan, state)
        trace = self.explainer.build_trace(...)
        return BrainTickResult(events=..., trace=trace)
```

На первом этапе это может быть набором функций, а не физическим классом.

### 4.2. BeliefState

`BeliefState` — не весь `state`, а то, что НПЦ считает истинным.

```python
@dataclass
class BeliefState:
    self_state: AgentSelfView
    current_location: LocationBelief
    visible_entities: list[EntityObservation]
    known_locations: list[LocationBelief]
    known_traders: list[TraderBelief]
    known_items: list[ItemBelief]
    known_threats: list[ThreatBelief]
    known_social_facts: list[SocialBelief]
    active_plan: ActivePlan | None
    world_signals: WorldSignals
```

Важно: `BeliefState` может быть неверным. НПЦ помнит, что в `loc_b` лежала вода, но за 300 ходов её мог подобрать кто-то другой.

**Отношение к `AgentContext`:** `BeliefState` является надстройкой:
- `AgentContext` — нормализованный snapshot текущей ситуации;
- `BeliefState` — интерпретированная картина мира глазами NPC, построенная из `AgentContext` + relevant memory + assumptions.

Переходный код:
```python
ctx = build_agent_context(agent_id, agent, state)
memories = memory_store.retrieve(agent_id, query_context)
beliefs = build_belief_state(ctx, memories)
```

Новый код NPCBrain/ObjectiveGenerator/Planner должен использовать `BeliefState`, а не напрямую весь `state`.

### 4.3. Drive

`Drive` — системное давление: голод, жажда, сон, лечение, безопасность, деньги, снаряжение, глобальная цель, социальная связь.

```python
@dataclass
class Drive:
    key: str
    urgency: float          # 0..1
    importance: float       # долгосрочная важность
    confidence: float       # уверенность в оценке
    source_factors: list[ScoreFactor]
```

В отличие от старого `NeedScores`, drive объясняет, из чего оно получилось.

Примеры базовых drives:
```text
health, thirst, hunger, sleep, radiation, safety, emission_shelter, combat_survival,
resupply_food, resupply_drink, resupply_medicine, resupply_ammo, resupply_weapon,
resupply_armor, wealth, global_goal, social, curiosity
```

**Важно**: `resupply` разбит на отдельные drives, а не один `reload_or_rearm`:
```text
resupply.weapon = 0.65
resupply.armor  = 0.00
resupply.ammo   = 0.40
resupply.food   = 0.55
resupply.drink  = 0.00
resupply.medicine = 0.45
```

### 4.4. ItemNeed

Промежуточная сущность для переходного периода:

```python
@dataclass(frozen=True)
class ItemNeed:
    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str]   # runtime only
    reason: str
```

**Сериализация:** `frozenset` — только в runtime. В JSON всегда `list[str]` (sorted).

```python
def serialize_item_need(need: ItemNeed) -> dict:
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

`needs.reload_or_rearm = max(n.urgency for n in item_needs)` — planner использует `item_needs`, не пересчитывает.

### 4.5. Objective

`Objective` — конкретная цель, которую можно планировать.

```text
Разница: Drive = "я хочу пить" | Objective = "найти воду у торговца Сидоровича"
```

```python
@dataclass
class Objective:
    key: str
    source_drive: str
    target: ObjectiveTarget | None
    urgency: float
    expected_value: float
    risk: float
    time_pressure: float
    confidence: float
    required_capabilities: list[str]
    reasons: list[str]
```

Примеры ключей: `RESTORE_WATER`, `RESTORE_FOOD`, `HEAL_SELF`, `REACH_SAFE_SHELTER`, `RESUPPLY_AMMO`, `BUY_ARMOR`, `SELL_ARTIFACTS`, `FIND_ARTIFACTS`, `LEAVE_ZONE`.

### 4.6. ActivePlan

`scheduled_action` должен быть обёрнут в `ActivePlan`.

```python
@dataclass
class ActivePlan:
    id: str
    objective_key: str
    status: Literal["active", "paused", "completed", "failed", "aborted"]
    steps: list[PlanStep]
    current_step_index: int
    created_turn: int
    last_evaluated_turn: int
    expected_total_cost: float
    expected_total_risk: float
    expected_total_value: float
    commitment_strength: float
    switch_cost: float
    memory_refs: list[str]
    debug_summary: str
```

В переходный период: `scheduled_action` отвечает за техническое длительное действие, `active_plan_v3` — за смысл и план.

### 4.7. PlanStep

```python
@dataclass
class PlanStep:
    kind: str
    payload: dict
    preconditions: list[Condition]
    expected_effects: list[Effect]
    cost: float
    risk: float
    duration_ticks: int
    interruptibility: Interruptibility
    checkpoint_policy: CheckpointPolicy
```

---

## 5. Память как основная механика

### 5.1. Слои памяти

#### A. Working Memory
Кратковременная память текущей ситуации. TTL: 1–20 тиков. Объём: ~100–300 записей.

```json
{
  "id": "wm_123",
  "type": "working_observation",
  "turn": 4800,
  "summary": "Вижу торговца Сидоровича в Баре",
  "entities": ["trader_0"],
  "locations": ["bar"],
  "ttl_turns": 10
}
```

#### B. Episodic Memory
Событийная память: пришёл в локацию, нашёл/продал артефакт, был ранен, убежал от выброса. Стареет и агрегируется.

```json
{
  "id": "mem_8f71",
  "memory_layer": "episodic",
  "kind": "item_seen",
  "turn": 3120,
  "location_id": "old_farm",
  "item_type": "water",
  "summary": "Видел воду на Старой ферме",
  "importance": 0.45,
  "confidence": 0.9,
  "decay_rate": 0.02,
  "tags": ["item", "water", "resupply"],
  "expires_turn": 6000
}
```

#### C. Semantic Memory
Обобщённые знания: "В Баре есть торговец", "Болото опасно при выбросе". Живёт дольше episodic.

```json
{
  "id": "sem_trader_bar",
  "memory_layer": "semantic",
  "kind": "trader_location_known",
  "subject_id": "trader_0",
  "location_id": "bar",
  "summary": "Сидорович обычно находится в Баре",
  "confidence": 0.82,
  "importance": 0.9,
  "last_confirmed_turn": 4500,
  "evidence_refs": ["mem_101", "mem_884", "mem_1204"],
  "tags": ["trader", "bar", "trade"]
}
```

#### D. Spatial Memory
Карта мира глазами НПЦ: известные локации, пути, travel_time, опасность, вероятность артефактов.

#### E. Social Memory
Память об отношениях: кто помог, кто атаковал, кому можно доверять, кто опасен.

#### F. Threat Memory
Выбросы, аномалии, мутанты, засады, закрытые пути, локации, где НПЦ получил урон.

#### G. Goal Memory
Память о долгосрочной цели: где искать документы, сколько денег осталось, какие попытки провалились. Живёт дольше episodic.

### 5.2. Единый формат MemoryRecord

```python
@dataclass
class MemoryRecord:
    id: str
    agent_id: str
    layer: MemoryLayer
    kind: str
    created_turn: int
    last_accessed_turn: int
    last_confirmed_turn: int | None
    summary: str
    details: dict
    location_id: str | None
    entity_ids: list[str]
    item_types: list[str]
    tags: list[str]
    importance: float       # 0..1
    confidence: float       # 0..1
    emotional_weight: float # 0..1
    novelty: float          # 0..1
    decay_rate: float
    min_retention_turns: int
    expires_turn: int | None
    access_count: int
    source: str             # seen | heard | inferred | traded | group_signal
    status: str             # active | stale | contradicted | archived
    supersedes: list[str]
    evidence_refs: list[str]
```

### 5.3. Индексы памяти

```python
MemoryStore:
    by_id: dict[str, MemoryRecord]
    by_layer: dict[str, set[str]]
    by_kind: dict[str, set[str]]
    by_location: dict[str, set[str]]
    by_entity: dict[str, set[str]]
    by_item_type: dict[str, set[str]]
    by_tag: dict[str, set[str]]
    recent_ring_buffer: deque[str]
    priority_heap_for_decay: heap[(score, memory_id)]
```

### 5.4. Retrieval

Память не должна вся передаваться в decision loop. Запросы:

```python
MemoryQuery(
    purpose="find_water",
    tags=["water", "drink", "resupply"],
    near_location=current_location,
    max_results=10,
)
```

Формула релевантности:
```text
memory_relevance_score =
    semantic_match      * 0.35
  + location_relevance  * 0.20
  + importance          * 0.15
  + confidence          * 0.15
  + recency             * 0.10
  + access_boost        * 0.05
  - staleness_penalty
  - contradiction_penalty
```

### 5.5. Старение и удаление

| Тип | TTL |
|---|---|
| Working observations, idle decisions | 50–500 тиков |
| Item seen, last known location агента | 1000–5000 тиков с decay |
| Известный торговец, успешный маршрут | 10000+ тиков |
| Базовая карта, глобальная цель | Почти постоянно |

### 5.6. Memory consolidation

Раз в N тиков повторяющиеся episodic-записи агрегируются в semantic:

```text
100 episodic "видел Сидоровича в Баре"
→ semantic: "Сидорович — торговец, обычно находится в Баре, продаёт воду/еду/медицину"
```

**Периодичность (MVP):**
- `ingest` — каждый тик
- `decay` — раз в 50–100 тиков
- `consolidation` — раз в 500–1000 тиков

### 5.7. Хранение памяти (MVP vs Later)

**MVP:** память остаётся в JSON/state blob через новый `MemoryStore` API.

```python
agent["memory_v3"] = {
    "working": [], "episodic": [], "semantic": [],
    "spatial": {}, "social": {}, "threat": [], "goal": [],
    "indexes": {"by_location": {}, "by_entity": {}, "by_item_type": {}, "by_tag": {}},
}
```

Game logic не должна напрямую лазить в эту структуру — только через методы `ingest/retrieve/decay/consolidate`.

`agent["memory"]` сохраняется как legacy compatibility layer.

**MemoryStore protocol:**
```python
class MemoryStoreProtocol:
    def ingest(self, agent_id: str, records: list[MemoryRecord]) -> None: ...
    def retrieve(self, agent_id: str, query: MemoryQuery) -> list[MemoryRecord]: ...
    def mark_stale(self, agent_id: str, memory_ids: list[str]) -> None: ...
    def decay(self, agent_id: str, world_turn: int) -> MemoryDecayResult: ...
    def consolidate(self, agent_id: str, world_turn: int) -> MemoryConsolidationResult: ...
```

---

## 6. Схема принятия решений

### 6.1. Observe
НПЦ получает наблюдения только из доступного мира: текущая локация, предметы на земле, артефакты, другие агенты, торговцы, мутанты, выбросы, открытые/закрытые пути.

### 6.2. Update memory
```text
Наблюдение: "на локации лежит вода"
  → episodic.item_seen
  → spatial.location_known_item += water

Наблюдение: "торговец рядом"
  → working.visible_trader
  → episodic.trader_seen
  → semantic.trader_location_known обновить confidence
```

### 6.3. Build beliefs
```python
beliefs = BeliefState(
    known_traders=memory.retrieve("known_trader", near=current_location),
    known_water_sources=memory.retrieve("water"),
    known_shelters=memory.retrieve("safe_from_emission"),
    active_plan=agent.get("active_plan"),
    ...
)
```

### 6.4. Evaluate drives

Drives считаются системно на основе beliefs, не через `state` напрямую.

### 6.5. Generate objectives

Каждый drive создаёт варианты objective:
```text
thirst=0.8
  → DRINK_EXISTING_WATER if water in inventory
  → BUY_WATER_FROM_TRADER if known trader
  → TRAVEL_TO_REMEMBERED_WATER if memory has item_seen water
  → SEARCH_FOR_WATER_IN_SAFE_LOCATION
```

### 6.6. Score objectives

**MVP формула:**
```text
objective_score =
    urgency            * 0.50
  + expected_value     * 0.20
  + memory_confidence  * 0.10
  + goal_alignment     * 0.10
  - risk               * risk_sensitivity
  - distance_cost      * 0.10
  - switch_cost
```

`risk_sensitivity = 1.0 - risk_tolerance`. С первого дня хранить `score_factors` в `brain_trace`.

### 6.7. Evaluate active plan

Активный план оценивается как один из вариантов:
```text
continue_current_plan_score = 0.62
best_new_objective_score    = 0.74
switch_cost                 = 0.10
0.74 - 0.10 = 0.64 > 0.62 → можно переключиться
```

---

## 7. Системная политика прерывания планов

```python
class PlanContinuityDecision(Enum):
    CONTINUE = "continue"
    ADAPT    = "adapt"
    PAUSE    = "pause"
    ABORT    = "abort"
    COMPLETE = "complete"
```

### CONTINUE
Продолжать, если цель валидна, риск не вырос, нет альтернативы с существенно большей полезностью.

### ADAPT
Адаптировать, если цель прежняя, но путь/шаг надо изменить (путь закрылся, нашёл предмет раньше времени).

### PAUSE
Поставить на паузу при краткосрочной задаче, после которой старый план ещё актуален (жажда 80, вода в инвентаре → consume, resume get_rich plan).

### ABORT
Полностью отменить, если цель больше не имеет смысла, риск неприемлем, ключевая память оказалась ложной, план слишком долго без прогресса.

---

## 8. Примеры поведения

### Пример 1. НПЦ идёт за артефактами, но хочет пить

```text
active_plan: FIND_ARTIFACTS at swamp, remaining 8 ticks
thirst: 62 → через несколько тиков: 82

continue_find_artifacts = 0.58, buy_water = 0.61, switch_cost = 0.12
0.61 - 0.12 = 0.49 < 0.58 → продолжить

позже:
buy_water = 0.82, switch_cost = 0.10
0.82 - 0.10 = 0.72 > 0.52 → PAUSE, новый план: идти к торговцу
```

### Пример 2. НПЦ идёт за патронами, но начался выброс

```text
continue_resupply_ammo = 0.44
reach_shelter          = 0.98, switch_cost = 0.05
0.98 - 0.05 > 0.44 → ABORT resupply, objective REACH_SAFE_SHELTER
```

### Пример 3. НПЦ нашёл ложную память

```text
Память: "на ферме лежит аптечка", confidence 0.7
НПЦ пришёл: аптечки нет.
→ запись помечается contradicted
→ spatial memory обновляется
→ confidence похожих записей снижается
→ план abort
→ новый objective: buy_medical_from_trader
```

---

## 9. Планирование

MVP: гибрид utility scoring + HTN-like templates.

Шаги имеют preconditions/effects. Примеры templates:

```text
RESTORE_WATER:
  A. consume water from inventory (if available)
  B. buy_water from known_trader → travel_to + trade
  C. travel_to_remembered_water_location → pickup/consume

REACH_SAFE_SHELTER:
  A. travel_to nearest known_shelter (emergency_flee=True if emission threat)
```

---

## 10. Frontend-визуализация

**MVP место:** `frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx`

**Данные:** приходят как часть обычного state response (GET `/api/games/zone_stalkers/{game_id}/state`), отдельный endpoint не нужен.

**Показываем:**
```text
Текущая мысль:
  Прерываю путь к Болоту: жажда стала критической.

События мышления:
  [plan_monitor] abort — критическая жажда
  [decision] new_intent — seek_water
```

**НЕ показываем в PR 1:**
- `active_plan_v3` как основной UI
- Полную историю планов

---

## 11. Redis: позиция

**MVP: Redis не используем.** Память остаётся в state/JSON/Python-структурах.

Но проектируем `MemoryStore` так, чтобы позже можно было заменить backend на Redis/PostgreSQL.

**Redis точно имеет смысл, когда:**
1. У одного NPC реально 10 000+ активных memory records
2. Линейное сканирование стало заметным bottleneck
3. Нужно быстро отдавать live brain_trace на frontend
4. Появился semantic retrieval по embedding
5. Память нужно шарить между процессами

До этого Redis будет premature optimization. Главная опасность — он начнёт диктовать форму памяти до того, как спроектирована правильная domain-модель.

**Идеальная схема на будущее (если нужно):**
```text
PostgreSQL = source of truth / persisted memory
Redis      = hot cache / indexes / retrieval acceleration
```

---

## 12. Стратегия миграции — порядок PR

**Главный принцип:** не реализуем v3 целиком одним большим рефакторингом. Делаем инкрементальную миграцию поверх текущей системы.

```text
PR 1: brain_trace + active_plan_v3 (retrofit wrapper) + PlanMonitor
PR 2: ItemNeeds вместо монолитного reload_or_rearm
PR 3: MemoryStore v3 поверх старой памяти
PR 4: Objective scoring
PR 5: BeliefState и DriveEvaluator
PR 6: постепенная замена legacy scheduled_action ownership
```

**Не делаем в PR 1:**
- полный NPCBrain
- полная замена AgentContext на BeliefState
- полная замена NeedScores на Drive
- полная замена scheduled_action на ActivePlan
- Redis-backed MemoryStore
- semantic/vector memory retrieval

**Тесты:** старые тесты (`test_needs.py`, `test_planner.py`, `test_intents.py`) остаются, пока работают старые модули. Новые v3-тесты добавляются в `backend/tests/decision/v3/`.

---

## 13. Non-goals for PR 1

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

### Scope note: "reevaluate every tick" в PR 1

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

Таким образом, PR 1 — это первый шаг к "thinking every tick", а не финальная реализация всей v3-архитектуры.

---

## 14. Expected file changes for PR 1

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

---

## 15. PR 1 — финальный контракт

### Backend guarantees

```text
1. Every alive bot stalker has brain_trace for current tick.
2. brain_trace public enum contains only PR 1 values:
   mode:     plan_monitor | decision | system
   decision: continue | abort | new_intent | no_op
3. Transient fields starting with _v3 are removed before state is returned/persisted.
4. active_plan_v3 is optional backend/debug metadata, not stable UI contract.
5. plan_monitor_aborted_action event has stable required and optional payload fields.
6. plan-monitor memory dedup uses a generic helper, but only abort is emitted in PR 1.
```

### Frontend guarantees

Frontend в PR 1 полагается на:
```text
agent.brain_trace.schema_version
agent.brain_trace.turn
agent.brain_trace.mode
agent.brain_trace.current_thought
agent.brain_trace.events
```

Frontend в PR 1 НЕ полагается на:
```text
agent.active_plan_v3
agent.brain_trace.active_plan
pause/adapt/complete events
```

### TypeScript-типы

```ts
type BrainTraceMode = 'plan_monitor' | 'decision' | 'system';

type BrainTraceDecision =
  | 'continue'
  | 'abort'
  | 'new_intent'
  | 'no_op';

type BrainTraceEvent = {
  turn: number;
  mode: BrainTraceMode;
  decision: BrainTraceDecision;
  summary: string;
  reason?: string;
  scheduled_action_type?: string | null;
  intent_kind?: string | null;
  intent_score?: number | null;
  dominant_pressure?: { key: string; value: number; } | null;
};

type BrainTrace = {
  schema_version: 1;
  turn: number;
  mode: BrainTraceMode;
  current_thought: string;
  events: BrainTraceEvent[];     // max 5
  active_plan?: unknown;         // debug only, не стабильный контракт
  top_drives?: Array<{ key: string; value: number; rank: number }>;
};
```

### Event: plan_monitor_aborted_action

```ts
type PlanMonitorAbortedActionPayload = {
  // Обязательные поля
  agent_id: string;
  scheduled_action_type: string;
  reason: string;
  dominant_pressure: { key: string; value: number; };

  // Optional, но имена и типы зафиксированы
  cancelled_target?: string | null;
  cancelled_final_target?: string | null;
  current_location_id?: string | null;
  turns_remaining?: number | null;
};
```

### Acceptance tests для PR 1

```text
test_all_alive_bot_stalkers_get_brain_trace_each_tick
test_brain_trace_pr1_enums_only
test_v3_transient_flags_are_removed
test_active_plan_v3_is_optional_debug_metadata
test_plan_monitor_aborted_action_payload_contract
test_plan_monitor_memory_dedup_generic_helper_for_abort
test_plan_monitor_does_not_run_for_human_with_scheduled_action
```

### Backward compatibility with existing saves

Все новые v3-поля должны быть optional.

Старый `state_blob`, в котором нет `brain_trace`, `active_plan_v3`, `memory_v3` или других v3-полей, должен продолжать нормально проходить через `tick_zone_map()`.

Alembic migration не нужна, если агент хранится внутри JSONB. Но нужна runtime-migration / defaulting logic на уровне state.

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

### Schema versioning

Все persisted/debug v3-структуры должны иметь `schema_version`.

```python
agent["brain_trace"]    = {"schema_version": 1, ...}
agent["active_plan_v3"] = {"schema_version": 1, ...}
agent["memory_v3"]      = {"schema_version": 1, ...}
```

Для PR 1 стабильным публичным контрактом является только:

- `brain_trace.schema_version = 1`
- `brain_trace.turn`
- `brain_trace.mode`
- `brain_trace.current_thought`
- `brain_trace.events`

`active_plan_v3.schema_version` и `memory_v3.schema_version` могут быть добавлены как debug/internal metadata, но не являются frontend-contract в PR 1.

### Failure policy

NPC Brain v3 PR 1 должен быть fail-safe.

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

### Performance budget

#### PR 1

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

#### Memory v3 (позже)

Когда появится `MemoryStore`:

- default `max_results = 10`;
- hard cap `max_results <= 50`;
- no full memory scan in decision loop;
- retrieval должен идти через индексы;
- decay/consolidation не должны выполняться для всех агентов каждый тик без ограничения.

### Determinism

NPC Brain v3 должен быть воспроизводимым.

При одинаковом `state`, `world_turn`, `state["seed"]`, `agent_id` — результат `PlanMonitor` / `brain_trace` / выбранного intent должен быть одинаковым.

Правила:

- сортировки objective/memory candidates должны быть стабильными;
- при равных score использовать deterministic tie-breaker;
- не использовать `random.random()` без seed;
- если нужен random, seed должен включать `state.seed`, `world_turn`, `agent_id`.

Пример tie-breaker:

```python
candidates.sort(key=lambda c: (-c.score, c.key, c.target_id or ""))
```

Для PR 1 `PlanMonitor` желательно делать полностью deterministic без random.

### Debug/explain ownership

В проекте есть несколько debug/explain механизмов с разными ролями:

**`_v2_context`** — legacy/debug snapshot текущего v2 pipeline. Может оставаться; не является source of truth для фактического поведения; может не совпадать с `brain_trace`, если action был прерван `PlanMonitor`.

**`explain_intent.py`** — read-only preview/explanation. Не мутирует state. Показывает, что pipeline выбрал бы сейчас. Не обязательно совпадает с тем, что уже произошло в тике.

**`brain_trace`** — фактический trace поведения агента в текущем тике. Persisted inside agent state. Пишется во время `tick_zone_map`. Показывает, что реально произошло. Frontend должен показывать `brain_trace` как главный источник объяснения.

**Frontend priority в `AgentProfileModal`:**

1. `brain_trace` — primary factual explanation
2. `_v2_context` — optional legacy/debug block
3. `explain_intent` preview — только как manual debug action, не как "что NPC реально сделал"

### Observability contract

После PR 1 разработчик должен иметь возможность ответить по каждому живому bot stalker:

1. Что NPC сейчас "думает"?
2. Продолжил ли он `scheduled_action` или прервал?
3. Если прервал — почему?
4. Какое давление было доминирующим?
5. Был ли forced replan после abort?
6. Какой новый intent выбран после abort?
7. Была ли создана memory-запись?
8. Был ли создан public event?
9. Почему human agent не был затронут `PlanMonitor`?

Минимальные источники ответа:

- `agent["brain_trace"]`
- event `plan_monitor_aborted_action`
- legacy `agent["memory"]`
- tests in `backend/tests/decision/v3/`

### Definition of Done for PR 1

PR 1 считается готовым, если выполнены все пункты:

#### Backend behavior

- [ ] Каждый живой bot stalker получает `brain_trace.turn == world_turn_before_increment`.
- [ ] Human agents с `scheduled_action` не проходят через `PlanMonitor`.
- [ ] `emergency_flee=True` не прерывается из-за жажды/голода/обычных survival pressures.
- [ ] Обычный `travel` / `explore` может быть aborted при критической жажде/голоде/HP.
- [ ] При `abort` очищаются `scheduled_action` и `action_queue`.
- [ ] После `abort` допускается максимум один forced replan на агента за тик.
- [ ] Transient `_v3_*` flags не остаются в итоговом state.
- [ ] `brain_trace.events.length <= 5`.
- [ ] Memory-записи от PlanMonitor dedup/throttle работают.

#### Events

- [ ] При abort эмитится `event_type="plan_monitor_aborted_action"`.
- [ ] Event содержит обязательные поля: `agent_id`, `scheduled_action_type`, `reason`, `dominant_pressure.key`, `dominant_pressure.value`.
- [ ] Optional event fields имеют стабильные имена: `cancelled_target`, `cancelled_final_target`, `current_location_id`, `turns_remaining`.

#### Frontend

- [ ] `AgentProfileModal` показывает `brain_trace.current_thought`.
- [ ] `AgentProfileModal` показывает последние `brain_trace.events`.
- [ ] Frontend не зависит от `active_plan_v3` как от стабильного контракта.

#### Compatibility

- [ ] Старые state blobs без v3-полей не ломаются.
- [ ] Старые decision/planner tests продолжают проходить.
- [ ] Новые tests в `backend/tests/decision/v3/` проходят.

#### Scope

- [ ] PR 1 не внедряет Redis.
- [ ] PR 1 не заменяет полностью `NeedScores`.
- [ ] PR 1 не заменяет полностью `scheduled_action`.
- [ ] PR 1 не реализует `pause/adapt`.

---

## 16. PR 1 — детальные implementation decisions

### 16.1. Структура новых файлов

```text
backend/app/games/zone_stalkers/rules/tick_constants.py
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
backend/tests/decision/v3/test_plan_monitor.py
backend/tests/decision/v3/test_brain_trace.py
backend/tests/decision/v3/test_tick_integration.py
```

### 16.2. tick_constants.py

Выносим коэффициенты деградации в общие константы (не дублировать между `tick_zone_map` и `PlanMonitor`):

```python
HUNGER_INCREASE_PER_HOUR = 3
THIRST_INCREASE_PER_HOUR = 5
SLEEPINESS_INCREASE_PER_HOUR = 4

CRITICAL_THIRST_THRESHOLD = 80
CRITICAL_HUNGER_THRESHOLD = 80

HP_DAMAGE_PER_HOUR_CRITICAL_THIRST = 2
HP_DAMAGE_PER_HOUR_CRITICAL_HUNGER = 1
```

### 16.3. Фильтр bot stalker

Размещать в `decision/plan_monitor.py`, импортировать в `tick_rules.py`:

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

Почему нужны и `archetype`, и `controller.kind`:
- `controller.kind == "bot"` — отличает NPC от игрока
- `archetype == "stalker_agent"` — исключает мутантов, торговцев, будущих non-stalker bots

Для PR 1: `PlanMonitor` применяется **только к bot stalker agents**. Торговцы и мутанты не мониторятся.

### 16.4. PlanMonitorResult

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

В PR 1 реально используем только `continue` и `abort`. `pause/adapt` — future values.

### 16.5. Точка вставки PlanMonitor в tick_zone_map

```python
for agent_id, agent in state.get("agents", {}).items():
    if not agent.get("is_alive", True):
        continue
    if agent.get("has_left_zone"):
        continue

    sched = agent.get("scheduled_action")
    if not sched:
        continue

    if is_v3_monitored_bot(agent):
        monitor_result = assess_scheduled_action_v3(
            agent_id=agent_id,
            agent=agent,
            scheduled_action=sched,
            state=state,
            world_turn=world_turn,
        )
        apply_plan_monitor_result(agent_id, agent, sched, monitor_result, state, world_turn)
        if monitor_result.decision in ("abort", "pause", "adapt"):
            events.extend(monitor_result.events)
            # Не вызываем _process_scheduled_action для старого sched
            continue

    new_evs = _process_scheduled_action(agent_id, agent, sched, state, world_turn)
    events.extend(new_evs)
```

Старый emission interrupt внутри `_process_scheduled_action` — **не удалять** в PR 1. Остаётся как safety net.

### 16.6. emergency_flee

`emergency_flee=True` → `interruptible=False`. PlanMonitor обязан это уважать:

```python
if scheduled_action.get("emergency_flee"):
    return PlanMonitorResult(
        decision="continue",
        reason="Текущее действие — emergency_flee; его нельзя прерывать.",
        interruptible=False,
    )
```

Жажда/голод при `emergency_flee` остаётся в `brain_trace` как active pressure — для UI-видимости.

### 16.7. При abort: очистка

```python
agent["scheduled_action"] = None
agent["action_queue"] = []
agent["active_plan_v3"]["status"] = "aborted"
```

Очистка обоих, потому что `action_queue` почти всегда является продолжением старого намерения.

**Исключение на будущее (после PR 1):**
```text
ABORT  → очистить queue
PAUSE  → сохранить queue внутри paused plan
ADAPT  → частично переписать queue
```

### 16.8. Двойной проход в одном тике (guard)

После `PlanMonitor abort` бот идёт в `_run_bot_decision_v2_inner`. Это разрешено. Но нужен guard:

```python
# Перед forced replan:
if agent.get("_v3_replanned_after_monitor_turn") == world_turn:
    continue  # не запускать повторно

agent["_v3_replanned_after_monitor_turn"] = world_turn
bot_evs = _run_bot_decision_v2(agent_id, agent, state, world_turn)
```

Очистка в конце тика (transient flag **обязан** удаляться):

```python
# В конце tick_zone_map, для всех агентов:
for agent in state.get("agents", {}).values():
    agent.pop("_v3_replanned_after_monitor_turn", None)
```

Test:
```python
def test_v3_replanned_flag_is_not_persisted_after_tick():
    new_state, _ = tick_zone_map(state)
    for agent in new_state["agents"].values():
        assert "_v3_replanned_after_monitor_turn" not in agent
```

### 16.9. Projected needs helper

PlanMonitor запускается до деградации нужд. Чтобы не пропустить пограничный тик, использовать projected values:

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

PlanMonitor использует projected values для interrupt scoring, но не мутирует agent.

### 16.10. Interrupt thresholds (интеграция с intents.py)

**Для PR 1:** не копировать thresholds в `plan_monitor.py`. Использовать `evaluate_needs + select_intent`:

```python
ctx = build_agent_context(agent_id, agent, state)
needs = evaluate_needs(ctx, state)
candidate_intent = select_intent(ctx, needs, world_turn)

if candidate_intent.kind in EMERGENCY_INTENTS:
    return PlanMonitorResult(
        decision="abort",
        reason=f"Emergency intent: {candidate_intent.kind}",
    )
```

Старые thresholds в `intents.py` не удалять. В будущем: вынести в `interrupt_policy.py`.

### 16.11. brain_trace

**Структура:**

```json
{
  "schema_version": 1,
  "turn": 1234,
  "mode": "plan_monitor",
  "current_thought": "Прерываю путь к Болоту: жажда стала критической.",
  "events": [
    {
      "turn": 1234,
      "mode": "plan_monitor",
      "decision": "abort",
      "reason": "critical_thirst",
      "summary": "Прервал travel из-за критической жажды",
      "scheduled_action_type": "travel",
      "dominant_pressure": {"key": "thirst", "value": 0.95}
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

**Правила:**
- `brain_trace` — последний компактный snapshot, не growing history
- Обновляется каждый тик, когда агент обрабатывается
- При `continue` — лёгкий update (turns_remaining, top_pressures), не пишем в `memory`
- При `abort/pause/adapt` — полная запись + в `memory`
- `events` max 5: `events = (old_events + [new_event])[-5:]`

**Двойная запись в одном тике (желательна):**

```python
def append_brain_trace_event(agent: dict, world_turn: int, event: dict) -> None:
    trace = agent.get("brain_trace")
    if not trace or trace.get("turn") != world_turn:
        trace = {"schema_version": 1, "turn": world_turn, "events": []}
        agent["brain_trace"] = trace
    trace.setdefault("events", []).append(event)
    trace["events"] = trace["events"][-5:]
    trace["current_thought"] = event.get("thought") or trace.get("current_thought")
    trace["last_mode"] = event.get("mode")
    trace["last_decision"] = event.get("decision")
```

**Modules в `debug/brain_trace.py`:**
```python
append_brain_trace_event(agent, world_turn, event)
write_plan_monitor_trace(agent, world_turn, monitor_result, scheduled_action)
write_decision_brain_trace_from_v2(agent, world_turn, ctx, needs, intent, plan, events)
ensure_brain_trace_for_tick(agent, world_turn)  # fallback для legacy ветвей
```

**brain_trace должен писаться и из PlanMonitor, и из _run_bot_decision_v2_inner** — иначе боты без `scheduled_action` не получат trace.

### 16.12. active_plan_v3

**В PR 1:** только retrofit/debug wrapper. Не стабильный frontend contract.

При первом обнаружении `scheduled_action` без `active_plan_v3`:

```json
{
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
  "debug_summary": "Выполняю travel → bar"
}
```

При нормальном завершении action (B/C hybrid):
```python
agent["active_plan_v3"]["status"] = "completed"
agent["active_plan_v3"]["completed_turn"] = world_turn
```

Если `active_plan_v3` отсутствует — не создавать в completion path насильно.  
В TS: `active_plan_v3?: unknown;` — не рендерить как основной UI.

### 16.13. Memory при abort: dedup

Деградация нужд может генерировать повторные abort-записи. Нужен throttle:

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

Для abort signature:
```python
signature = {
    "reason": reason,
    "scheduled_action_type": sched.get("type"),
    "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
}
```

Helper — общего назначения для всех plan-monitor memory events, но в PR 1 используем только для `abort`.

При dedup: в `memory` не пишем, но `brain_trace.events` всё равно обновляем.

### 16.14. Memory запись при abort travel

PlanMonitor сам пишет memory, потому что `_process_scheduled_action()` не вызывается:

```python
_add_memory(
    agent, world_turn, state,
    "decision",
    "⛔ Прерываю текущее действие",
    {
        "action_kind": "plan_monitor_action_aborted",
        "reason": reason,
        "scheduled_action_type": sched.get("type"),
        "cancelled_target": sched.get("target_id"),
        "cancelled_final_target": sched.get("final_target_id", sched.get("target_id")),
        "current_location_id": agent.get("location_id"),
        "dedup_signature": signature,
    },
    summary=summary,
)
```

**Важно:** `current_location_id = agent["location_id"]`, не `sched["target_id"]` (агент ещё не дошёл до цели хопа).

### 16.15. brain_trace coverage acceptance

```python
def test_all_alive_bot_stalkers_get_brain_trace_each_tick():
    before_turn = state["world_turn"]
    new_state, _events = tick_zone_map(state)

    for agent in new_state["agents"].values():
        if (agent.get("is_alive", True)
            and not agent.get("has_left_zone")
            and agent.get("archetype") == "stalker_agent"
            and agent.get("controller", {}).get("kind") == "bot"):
                assert agent.get("brain_trace", {}).get("turn") == before_turn
```

Если полное покрытие сложно добиться сразу — использовать `ensure_brain_trace_for_tick` как fallback.

### 16.16. Human agent — инвариант

```python
def test_plan_monitor_does_not_run_for_human_with_scheduled_action():
    # После tick_zone_map:
    # - нет event_type == plan_monitor_aborted_action
    # - нет memory.effects.action_kind == plan_monitor_action_aborted
    # - scheduled_action обработан legacy путём
```

### 16.17. Тесты PlanMonitor

**Unit tests (минимальный state/mock):**
```python
state = {
    "world_turn": 100,
    "world_minute": 59,
    "emission_active": False,
    "locations": {"bar": {...}, "swamp": {...}},
}
agent = {
    "id": "bot1",
    "archetype": "stalker_agent",
    "controller": {"kind": "bot"},
    "is_alive": True,
    "location_id": "bar",
    "hp": 30, "hunger": 20, "thirst": 96, "sleepiness": 10,
    "memory": [],
}
sched = {"type": "travel", "target_id": "swamp", "turns_remaining": 6}
```

Покрыть:
```text
continue обычного travel
abort при critical thirst
continue emergency_flee даже при thirst=95
abort/continue при emission threat
projected thirst на minute boundary
clear action_queue при abort
brain_trace event при continue и abort
```

**Integration tests (через tick_zone_map):**
```text
bot with scheduled travel + critical thirst → abort, replan
bot with emergency_flee + critical thirst → continue, trace объясняет
```

### 16.18. Personality

**MVP:** только `risk_tolerance`. Структуру можно добавить сразу, но не подключать к scoring:

```python
agent["personality"] = {
    "risk_tolerance": agent.get("risk_tolerance", 0.5),
    "greed": 0.5,
    "caution": 1.0 - agent.get("risk_tolerance", 0.5),
    "sociability": 0.5,
    "loyalty": 0.5,
}
```

### 16.19. Итоговый план Backend PR 1

1. Добавить `rules/tick_constants.py`
2. Добавить `decision/plan_monitor.py`:
   - `is_v3_monitored_bot`
   - `PlanMonitorResult`
   - `assess_scheduled_action_v3`
   - projection helper или импорт
3. Добавить `decision/debug/brain_trace.py`:
   - `append_brain_trace_event`
   - `write_plan_monitor_trace`
   - `write_decision_brain_trace_from_v2`
   - `ensure_brain_trace_for_tick`
4. Вставить PlanMonitor перед `_process_scheduled_action` для bot stalkers
5. При abort: brain_trace event + memory с dedup + emit event + clear sched + clear queue + mark v3 status + разрешить forced replan (с guard)
6. В `_run_bot_decision_v2_inner` добавить запись `brain_trace` по фактическому intent/plan
7. В конце обработки bot stalker — fallback `ensure_brain_trace_for_tick`
8. В конце `tick_zone_map` — очистить transient flags `_v3_*`

### 16.20. Итоговый план Frontend PR 1

1. Добавить TypeScript-типы `BrainTrace`, `BrainTraceEvent` в `AgentForProfile`
2. В `AgentProfileModal.tsx` добавить collapsible/debug блок:
   - `current_thought`
   - список `events` (mode, decision, summary, dominant_pressure)
3. `active_plan_v3?: unknown` — в типе, но не рендерить как основной UI

### 16.21. brain_trace lifecycle for death / has_left_zone

**Alive bot stalker:** после `tick_zone_map` каждый живой bot stalker, который не `has_left_zone`, должен иметь `agent["brain_trace"]["turn"] == world_turn_before_increment`.

**Agent died during this tick:** если bot stalker умер в этом тике — `brain_trace` можно записать с `mode="system"`:

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

Future ticks не обязаны обновлять `brain_trace` мёртвого агента.

**has_left_zone:** если `has_left_zone == true` — `brain_trace` больше не обязан обновляться; последний trace можно оставить как historical snapshot.

### 16.22. brain_trace event ordering

`brain_trace.events` хранятся в хронологическом порядке: старые события идут раньше, новые — в конец.

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

Если лимит превышен, удаляются самые старые события. Новые всегда в конце.

### 16.23. active_plan_v3 source of truth

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
