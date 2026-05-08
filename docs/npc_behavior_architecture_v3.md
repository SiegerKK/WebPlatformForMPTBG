# Архитектура поведения НПЦ v3

> Проект: `zone_stalkers`  
> Предлагаемый путь в репозитории: `docs/npc_behavior_architecture_v3.md`  
> Цель документа: описать новую, более системную и читаемую схему поведения НПЦ, которая заменяет разрозненную смесь `NeedScores`, `Intent`, `Plan`, `scheduled_action`, legacy helper-функций и специальных исключений.  
> Основной принцип: НПЦ должен принимать решения через единый когнитивный цикл, опираясь на память, системные механики, оценки риска/выгоды и состояние активного плана.

---

## 1. Проблема текущей схемы

Текущая система уже движется в правильную сторону: есть `AgentContext`, `NeedScores`, `Intent`, `Plan`, `PlanStep`, `Executor`. Но фактически управление поведением всё ещё частично находится в `tick_rules.py` и `scheduled_action`.

Главные проблемы:

1. **Активное действие почти не переоценивается.**  
   Если у агента есть `scheduled_action`, обычный decision pipeline не запускается. Исключение в основном одно: угроза выброса может прервать `travel` или `explore_anomaly_location`.

2. **Interrupt-логика разбросана.**  
   Часть прерываний описана в `intents.py`, часть в `_process_scheduled_action`, часть в executor'ах, часть в legacy helper'ах.

3. **Слишком много сущностей обозначают почти одно и то же.**  
   Есть `global_goal`, `current_goal`, `NeedScores`, `Intent.kind`, `Plan.intent_kind`, `PlanStep.kind`, `scheduled_action.type`, `memory.effects.action_kind`, `reason`. Из-за этого трудно понять: что НПЦ хочет, почему он этого хочет и что он делает прямо сейчас.

4. **Память используется как список событий, а не как полноценная когнитивная система.**  
   НПЦ должен опираться на память как на ключевую механику: где он видел торговцев, где лежали предметы, где были опасности, кто ему помог, где уже пусто, где были артефакты, кто видел цель. При десятках тысяч записей простой линейный список станет медленным и плохо управляемым.

5. **Дублирование логики между оценкой потребностей и планированием.**  
   Например, `needs.py` считает, что НПЦ нужно пополнить запасы, а `planner.py` заново определяет, чего именно не хватает. Это создаёт риск рассинхронизации.

6. **Система выглядит как гибрид utility AI и дерева исключений.**  
   Есть численные scores, но поверх них много особых проверок: выброс, критическая жажда, критический голод, suppress get_rich, pre-decision equipment maintenance и т.д. В итоге поведение трудно предсказывать.

---

## 2. Цели v3

Новая схема должна:

1. **Каждый тик переоценивать ситуацию**, даже если у НПЦ уже есть активный план или длительное действие.
2. **Свести исключения к системным правилам.**  
   Выброс, голод, жажда, низкий HP, бой, нехватка снаряжения — всё должно участвовать в общей системе оценки, а не жить как отдельные `if` в разных местах.
3. **Сделать память основной механикой поведения.**  
   НПЦ должен принимать решения не по всеведущему `state`, а по тому, что видит, помнит, слышал или вывел из опыта.
4. **Поддержать десятки тысяч записей памяти.**  
   Память должна индексироваться, агрегироваться, стареть, очищаться и превращаться в устойчивые знания.
5. **Сделать фронтовое объяснение поведения читаемым.**  
   Игрок/разработчик должен видеть: что НПЦ думает, какие потребности у него активны, какой план выполняется, почему он выбран, какие альтернативы отвергнуты и какая память использована.
6. **Снизить дублирование.**  
   Одно и то же знание о потребности, предмете, риске или цели не должно вычисляться в трёх разных местах по-разному.
7. **Сохранить атмосферу Zone Stalkers.**  
   НПЦ не должен быть идеально рациональным. Он должен ошибаться, забывать, верить устаревшей информации, переоценивать риски и действовать в соответствии со своим опытом.

---

## 3. Основная идея v3

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

Ключевое отличие: **активный план не блокирует мышление**.  
НПЦ каждый тик заново смотрит на ситуацию и решает:

```text
продолжить текущий план
адаптировать текущий план
поставить план на паузу
полностью отменить план
выбрать новый план
```

Это не должно быть реализовано через набор исключений. Это должно быть результатом сравнения полезности текущего плана и альтернатив.

---

## 4. Новые базовые сущности

### 4.1. NPCBrain

`NPCBrain` — главный объект принятия решений одного агента.

Примерная ответственность:

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

`NPCBrain` не обязан быть физическим классом сразу. На первом этапе это может быть набор функций. Но архитектурно поведение должно быть именно таким.

### 4.2. BeliefState

`BeliefState` — это не весь `state`, а то, что НПЦ считает истинным.

Он строится из:

- текущих наблюдений;
- кратковременной памяти;
- долговременной памяти;
- социальных сведений;
- карты известного мира;
- предположений;
- уровня уверенности.

Пример:

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

Важно: `BeliefState` может быть неверным. Например, НПЦ помнит, что в `loc_b` лежала вода, но за 300 ходов её мог подобрать кто-то другой.

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

Примеры:

```text
drive.thirst.urgency = 0.82
drive.health.urgency = 0.35
drive.resupply.urgency = 0.60
drive.get_rich.urgency = 0.28
```

В отличие от старого `NeedScores`, drive должен не просто хранить число, но и объяснять, из чего оно получилось.

### 4.4. Objective

`Objective` — конкретная цель, которую можно планировать.

Примеры:

```text
RESTORE_WATER
RESTORE_FOOD
HEAL_SELF
REACH_SAFE_SHELTER
RESUPPLY_AMMO
BUY_ARMOR
SELL_ARTIFACTS
FIND_ARTIFACTS
INVESTIGATE_SECRET_DOCUMENTS
HUNT_AGENT
LEAVE_ZONE
MAINTAIN_GROUP
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

Разница между `Drive` и `Objective`:

```text
Drive:     “я хочу пить”
Objective: “найти воду у торговца Сидоровича”
```

### 4.5. ActivePlan

`scheduled_action` должен быть заменён или обёрнут в `ActivePlan`.

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

`scheduled_action` можно оставить как runtime-представление текущего физического действия, но оно не должно быть источником истины о плане. Источник истины — `ActivePlan`.

### 4.6. PlanStep

`PlanStep` должен иметь preconditions/effects/cost/risk.

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

Пример:

```python
PlanStep(
    kind="travel_to_location",
    payload={"target_location_id": "bar"},
    preconditions=["route_known", "target_reachable"],
    expected_effects=["agent.location_id = bar"],
    cost=12,
    risk=0.15,
    duration_ticks=12,
    interruptibility="checkpointed",
    checkpoint_policy="every_tick",
)
```

---

## 5. Память как основная механика

### 5.1. Почему простой список памяти не подходит

Если у НПЦ будет 10 000–100 000 записей, простой `agent["memory"] = []` с линейным сканированием станет проблемой:

- дорого искать нужные сведения;
- сложно удалять старые записи без потери важного опыта;
- трудно отделить “я видел мутанта 5 минут назад” от “я знаю, что в Баре есть торговец”;
- трудно объяснять решения на фронте;
- трудно поддерживать устаревание и доверие.

Новая память должна быть не одним списком, а системой слоёв.

---

## 5.2. Слои памяти

### A. Working Memory

Кратковременная память текущей ситуации.

Содержит:

- текущие наблюдения;
- последние решения;
- активный план;
- последние несколько использованных memory refs;
- временные сигналы: “сейчас выброс”, “я в бою”, “вижу воду”.

TTL: 1–20 тиков.  
Объём: маленький, например 100–300 записей.

Пример:

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

### B. Episodic Memory

Событийная память: “что случилось”.

Содержит:

- пришёл в локацию;
- нашёл артефакт;
- продал артефакт;
- был ранен аномалией;
- убежал от выброса;
- встретил сталкера;
- получил информацию;
- видел предмет;
- видел, что локация пустая.

Эта память может быть большой, но записи должны стареть и агрегироваться.

Пример:

```json
{
  "id": "mem_8f71",
  "memory_layer": "episodic",
  "kind": "item_seen",
  "turn": 3120,
  "location_id": "old_farm",
  "entity_ids": [],
  "item_type": "water",
  "summary": "Видел воду на Старой ферме",
  "importance": 0.45,
  "confidence": 0.9,
  "decay_rate": 0.02,
  "tags": ["item", "water", "resupply"],
  "expires_turn": 6000
}
```

### C. Semantic Memory

Обобщённые знания: “что я знаю о мире”.

Примеры:

```text
В Баре есть торговец.
Болото опасно во время выброса.
В Лаборатории X часто бывают документы.
Старый мост часто закрывается.
Сидорович покупает артефакты.
```

Semantic memory живёт дольше, чем episodic. Она создаётся из повторяющихся событий или важных наблюдений.

Пример:

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

### D. Spatial Memory

Карта мира глазами НПЦ.

Содержит:

- известные локации;
- известные связи;
- travel_time;
- опасность локаций;
- вероятность артефактов;
- вероятность предметов;
- известные торговцы;
- когда локация была последним раз проверена;
- была ли локация подтверждена пустой.

Пример:

```json
{
  "location_id": "swamp",
  "known": true,
  "last_visited_turn": 2200,
  "last_observed_turn": 2200,
  "danger_score": 0.72,
  "anomaly_score": 0.8,
  "artifact_probability": 0.55,
  "known_items": ["water", "bandage"],
  "confirmed_empty_until_turn": 3600,
  "confidence": 0.64
}
```

### E. Social Memory

Память об отношениях.

Содержит:

- кто помог;
- кто атаковал;
- кто дал информацию;
- кому можно доверять;
- кто принадлежит к какой фракции;
- кто является целью;
- кто опасен.

Пример:

```json
{
  "id": "soc_agent_ai_2",
  "memory_layer": "social",
  "subject_id": "agent_ai_2",
  "trust": 0.35,
  "fear": 0.6,
  "hostility": 0.2,
  "last_seen_location_id": "garbage",
  "last_seen_turn": 5100,
  "facts": [
    "дал информацию о медикаментах",
    "был замечен рядом с трупом сталкера"
  ]
}
```

### F. Threat Memory

Опасности должны жить отдельно, потому что они часто важнее обычных наблюдений.

Содержит:

- выбросы;
- аномалии;
- мутанты;
- засады;
- закрытые пути;
- локации, где НПЦ получил урон.

Пример:

```json
{
  "id": "threat_swamp_anomaly",
  "memory_layer": "threat",
  "kind": "anomaly_damage",
  "location_id": "swamp",
  "turn": 4200,
  "danger_score": 0.8,
  "summary": "Получил урон от аномалии на Болоте",
  "confidence": 0.95,
  "importance": 0.85,
  "decay_rate": 0.005
}
```

### G. Goal Memory

Память, связанная с долгосрочной целью.

Примеры:

- где искать документы;
- кто является целью убийства;
- сколько денег осталось до wealth goal;
- какие попытки уже провалились;
- какие маршруты к цели были бесполезны.

Goal memory должна жить дольше обычной episodic memory.

---

## 5.3. Унифицированный формат MemoryRecord

Даже если физически память хранится в разных индексах, базовый формат должен быть единым.

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
    emotional_weight: float # 0..1, for danger/help/betrayal
    novelty: float          # 0..1

    decay_rate: float
    min_retention_turns: int
    expires_turn: int | None

    access_count: int
    source: str             # seen, heard, inferred, traded, group_signal
    status: str             # active, stale, contradicted, archived
    supersedes: list[str]
    evidence_refs: list[str]
```

### Обязательные поля для поиска

Для производительности каждая запись должна иметь:

```text
id
layer
kind
tags
location_id
entity_ids
item_types
created_turn
importance
confidence
status
```

Без этих полей retrieval будет либо медленным, либо неточным.

---

## 5.4. Индексы памяти

Память должна индексироваться минимум так:

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

Это позволит быстро отвечать на вопросы:

```text
Где я видел воду?
Где ближайший известный торговец?
Какие локации опасны при выбросе?
Где я уже искал артефакты и ничего не нашёл?
Кто последний видел мою цель?
Какие сведения о Болоте ещё актуальны?
```

---

## 5.5. Retrieval: как НПЦ вспоминает

Память не должна вся передаваться в decision loop. Вместо этого каждый тик формируются запросы.

Примеры запросов:

```python
MemoryQuery(
    purpose="find_water",
    tags=["water", "drink", "resupply"],
    near_location=current_location,
    max_results=10,
)

MemoryQuery(
    purpose="avoid_emission",
    tags=["shelter", "safe_terrain", "emission"],
    near_location=current_location,
    max_results=5,
)

MemoryQuery(
    purpose="hunt_target",
    entity_ids=[kill_target_id],
    tags=["last_seen", "rumor", "intel"],
    max_results=8,
)
```

Результаты сортируются не только по свежести, а по общей полезности.

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

Для разных целей веса могут отличаться.

Например, для угроз важнее confidence/importance, а для предметов важнее recency/location.

---

## 5.6. Старение и удаление памяти

Нельзя просто удалять самые старые записи. Нужно учитывать тип записи.

### Быстро удалять

Эти записи можно хранить недолго:

| Тип | Почему можно удалить быстро |
|---|---|
| Повторяющиеся наблюдения одной и той же локации без изменений | Мало новой информации |
| “Я жду” / idle decisions | Почти нет ценности |
| Мелкие перемещения без событий | Можно агрегировать |
| Временные working observations | Нужны только сейчас |
| Низкоуверенные слухи без подтверждений | Быстро теряют ценность |

Пример TTL: 50–500 тиков.

### Хранить средне

| Тип | Почему |
|---|---|
| Видел предмет | Полезно, но предмет могли забрать |
| Видел мутанта | Опасность может переместиться |
| Видел другого сталкера | Last known location устаревает |
| Локация была пустой | После выброса или времени может измениться |
| Торговец был замечен | Полезно, но надо подтверждать |

Пример TTL: 1000–5000 тиков, с decay.

### Хранить долго

| Тип | Почему |
|---|---|
| Известная локация торговца | Ключевая инфраструктура |
| Опасные зоны, где агент получил урон | Важный опыт выживания |
| Успешные маршруты к безопасным локациям | Критично при выбросе |
| Социальные факты: враг/союзник/предатель | Долгосрочное влияние |
| Сведения о глобальной цели | Основная мотивация |
| Смерть союзника/атака/важный бой | Высокая эмоциональная значимость |

Пример TTL: 10000+ тиков или до явного опровержения.

### Хранить почти постоянно

| Тип | Почему |
|---|---|
| Базовая карта известных локаций | Фундамент навигации |
| Глобальная цель и прогресс | Сущность персонажа |
| Собственные важные достижения | История агента |
| Проверенные торговцы | Экономическая инфраструктура |
| Известные выходы из Зоны | Финальная цель |

---

## 5.7. Memory consolidation

Раз в N тиков или при переполнении памяти нужно выполнять consolidation.

Пример:

```text
100 episodic записей:
  “видел Сидоровича в Баре”
  “торговал с Сидоровичем в Баре”
  “купил воду у Сидоровича в Баре”

превращаются в semantic запись:
  “Сидорович — торговец, обычно находится в Баре, продаёт воду/еду/медицину”
```

После consolidation исходные записи можно:

- удалить;
- архивировать;
- оставить только последние 1–3 как evidence;
- заменить summary-записью.

Пример summary record:

```json
{
  "layer": "semantic",
  "kind": "trader_profile",
  "subject_id": "trader_0",
  "location_id": "bar",
  "summary": "Сидорович — надёжный торговец в Баре. У него часто есть вода, еда и медикаменты.",
  "confidence": 0.91,
  "importance": 0.95,
  "evidence_count": 17,
  "last_confirmed_turn": 8800
}
```

---

## 6. Новая схема принятия решений

### 6.1. Observe

НПЦ получает наблюдения только из доступного мира.

Наблюдения:

- текущая локация;
- предметы на земле;
- артефакты;
- другие агенты;
- торговцы;
- мутанты;
- выбросы;
- открытые/закрытые пути;
- результаты текущего действия;
- изменения собственного состояния.

```python
observations = observe_world(agent, state)
```

### 6.2. Update memory

Каждое наблюдение превращается в одну или несколько memory records.

Пример:

```text
Наблюдение: “на локации лежит вода”
  → episodic.item_seen
  → spatial.location_known_item += water

Наблюдение: “торговец рядом”
  → working.visible_trader
  → episodic.trader_seen
  → semantic.trader_location_known обновить confidence
```

### 6.3. Build beliefs

BeliefState строится из:

- self state;
- текущих observations;
- retrieval из памяти;
- активного плана;
- доступных маршрутов;
- угроз.

Пример:

```python
beliefs = BeliefState(
    self_state=...,
    current_location=...,
    known_traders=memory.retrieve("known_trader", near=current_location),
    known_water_sources=memory.retrieve("water"),
    known_shelters=memory.retrieve("safe_from_emission"),
    active_plan=agent.get("active_plan"),
)
```

### 6.4. Evaluate drives

Drives считаются системно.

Пример базовых drives:

```text
health
thirst
hunger
sleep
radiation
safety
emission_shelter
combat_survival
resupply_food
resupply_drink
resupply_medicine
resupply_ammo
resupply_weapon
resupply_armor
wealth
global_goal
social
curiosity
```

Важно: `resupply` лучше разбить на отдельные drives, а не держать всё в одном `reload_or_rearm`. Тогда исчезает необходимость заново угадывать в planner, чего именно не хватает.

Вместо:

```text
reload_or_rearm = 0.65
```

лучше:

```text
resupply.weapon = 0.65
resupply.armor = 0.00
resupply.ammo = 0.40
resupply.food = 0.55
resupply.drink = 0.00
resupply.medicine = 0.45
```

А общий `resupply` можно считать как max или weighted max.

### 6.5. Generate objectives

Каждый drive создаёт варианты objective.

Пример:

```text
thirst=0.8
  → DRINK_EXISTING_WATER if water in inventory
  → BUY_WATER_FROM_TRADER if known trader
  → TRAVEL_TO_REMEMBERED_WATER if memory has item_seen water
  → SEARCH_FOR_WATER_IN_SAFE_LOCATION
```

Пример для снаряжения:

```text
resupply.ammo=0.4
  → PICKUP_KNOWN_AMMO
  → BUY_COMPATIBLE_AMMO
  → SELL_ITEMS_TO_BUY_AMMO
  → FARM_ARTIFACTS_FOR_AMMO
```

### 6.6. Score objectives

Каждый objective получает score.

```text
objective_score =
    urgency              * 0.35
  + expected_value       * 0.25
  + success_probability  * 0.15
  + memory_confidence    * 0.10
  + goal_alignment       * 0.10
  - risk                 * risk_sensitivity
  - time_cost            * time_sensitivity
  - resource_cost        * resource_sensitivity
  - plan_switch_cost
```

`risk_sensitivity` зависит от `risk_tolerance`:

```text
risk_sensitivity = 1.0 - risk_tolerance
```

Осторожный НПЦ сильнее штрафует риск. Рискованный НПЦ легче идёт в аномалии.

### 6.7. Evaluate active plan

Активный план каждый тик оценивается как один из вариантов.

```python
current_plan_option = ObjectiveOption(
    key="continue_current_plan",
    expected_value=plan.remaining_value,
    risk=plan.remaining_risk,
    time_cost=plan.remaining_time,
    switch_cost=0,
    confidence=plan.confidence,
)
```

Параллельно оцениваются альтернативы.

```text
continue_current_plan_score = 0.62
best_new_objective_score    = 0.74
switch_cost                 = 0.10

0.74 - 0.10 = 0.64 > 0.62
→ можно переключиться
```

Если разница мала, НПЦ продолжает план. Это даёт инерцию поведения и убирает “дёрганье”.

---

## 7. Системная политика прерывания планов

В v3 не должно быть отдельного `if emission then cancel`. Выброс должен стать очень сильным objective с высоким urgency/time_pressure/risk.

Но для ясности можно описать результат через правила.

### 7.1. Виды решения по активному плану

```python
class PlanContinuityDecision(Enum):
    CONTINUE = "continue"
    ADAPT = "adapt"
    PAUSE = "pause"
    ABORT = "abort"
    COMPLETE = "complete"
```

### 7.2. Когда CONTINUE

Продолжать план, если:

- цель всё ещё валидна;
- риск не вырос сильно;
- нет альтернативы с существенно большей полезностью;
- ресурсное состояние ещё терпимо;
- текущий шаг не провален.

Пример:

```text
НПЦ идёт продавать артефакт.
Жажда выросла с 20 до 35.
План близок к завершению.
→ продолжить.
```

### 7.3. Когда ADAPT

Адаптировать план, если цель прежняя, но путь/шаг надо изменить.

Примеры:

```text
Идёт к торговцу, но путь закрылся.
→ построить другой маршрут, не менять objective.

Идёт купить воду, но увидел воду на земле.
→ заменить buy step на pickup/consume.

Идёт исследовать аномалию, но нашёл артефакт до полного исследования.
→ завершить explore раньше и перейти к sell_artifacts.
```

### 7.4. Когда PAUSE

Поставить план на паузу, если возникла краткосрочная задача, после которой старый план ещё актуален.

Примеры:

```text
Идёт к аномалии за артефактами.
Жажда стала 80, но рядом есть вода в инвентаре.
→ pause get_rich plan
→ consume water
→ resume get_rich plan
```

```text
Идёт продавать артефакт.
Начался бой с мутантом.
→ pause sell plan
→ resolve combat/flee
→ resume if alive and artifact still exists
```

### 7.5. Когда ABORT

Полностью отменить план, если:

- цель больше не имеет смысла;
- риск стал неприемлемым;
- ключевая память оказалась ложной;
- предмет/цель исчезли;
- глобальная цель изменилась;
- план слишком долго не даёт прогресса.

Пример:

```text
НПЦ шёл за водой, потому что помнил воду на ферме.
Пришёл: воды нет.
Запись item_seen помечается stale/contradicted.
План ABORT.
Новый objective: BUY_WATER_FROM_TRADER.
```

---

## 8. Примеры поведения

### Пример 1. НПЦ идёт за артефактами, но хочет пить

Состояние:

```text
active_plan: FIND_ARTIFACTS at swamp
thirst: 62
inventory.water: 0
known_trader: bar, distance 20
current step: travel_to swamp, remaining 8 ticks
```

Оценка:

```text
continue_find_artifacts = 0.58
buy_water               = 0.61
switch_cost             = 0.12
```

Решение:

```text
0.61 - 0.12 = 0.49 < 0.58
→ продолжить идти за артефактами
```

Через несколько тиков:

```text
thirst: 82
continue_find_artifacts = 0.52
buy_water               = 0.82
switch_cost             = 0.10
0.82 - 0.10 = 0.72 > 0.52
→ PAUSE текущий план
→ новый план: идти к торговцу за водой
```

### Пример 2. НПЦ идёт за патронами, но начался выброс

Состояние:

```text
active_plan: RESUPPLY_AMMO
current_location.terrain: plain
emission_warning: true
known_shelter: bunker, distance 5
```

Оценка:

```text
continue_resupply_ammo = 0.44
reach_shelter          = 0.98
switch_cost            = 0.05
```

Решение:

```text
0.98 - 0.05 > 0.44
→ PAUSE или ABORT resupply plan
→ objective REACH_SAFE_SHELTER
```

Это выглядит как “исключение”, но на самом деле это системная оценка риска.

### Пример 3. НПЦ без брони и без еды

Старый подход:

```text
reload_or_rearm = max(armor 0.70, food 0.55)
→ resupply
planner заново решает, что купить
```

Новый подход:

```text
resupply.armor = 0.70
resupply.food  = 0.55
```

Objective candidates:

```text
BUY_ARMOR score 0.62, risk low, cost high
BUY_FOOD  score 0.67, risk low, cost low, survival relevance high
```

Решение:

```text
Если голод низкий, купить броню.
Если голод растёт, сначала купить еду.
```

То есть порядок не жёсткий, а системный.

### Пример 4. НПЦ помнит 20 мест с водой

Memory retrieval не должен возвращать все 20. Он возвращает лучшие варианты.

Критерии:

```text
близко?
свежее наблюдение?
высокая confidence?
безопасный маршрут?
не было ли записи, что место уже пустое?
```

Результат:

```text
1. water at bar trader      score 0.91
2. water seen at old_farm   score 0.68
3. water rumor at swamp     score 0.31
```

Planner использует только top-N.

### Пример 5. НПЦ нашёл ложную память

```text
Память: “на ферме лежит аптечка”, confidence 0.7
НПЦ пришёл на ферму, аптечки нет.
```

Действия:

```text
1. Запись item_seen помечается contradicted.
2. Spatial memory фермы обновляется: no_medkit_confirmed_turn = current_turn.
3. Confidence похожих старых записей снижается.
4. План за аптечкой abort.
5. Генерируется новый objective: buy_medical_from_trader.
```

---

## 9. Планирование

### 9.1. Планировщик должен быть goal-oriented

Для MVP достаточно гибрида:

- utility scoring выбирает objective;
- HTN-like templates строят план;
- шаги имеют preconditions/effects;
- ActivePlan мониторит прогресс.

Пример templates:

```text
RESTORE_WATER:
  A. consume water from inventory
  B. pick up known water at current location
  C. travel to remembered water → pickup → consume
  D. travel to trader → buy water → consume if thirst high
  E. search safe location for consumables

RESUPPLY_AMMO:
  A. pickup compatible ammo at current location
  B. travel to remembered compatible ammo → pickup
  C. travel to trader → buy compatible ammo
  D. sell non-critical items → buy compatible ammo
  E. find artifacts → sell → buy ammo

FIND_ARTIFACTS:
  A. sell artifacts if carrying too many or near trader
  B. travel to best remembered anomaly
  C. explore current anomaly
  D. mark empty if nothing found
```

### 9.2. Один объект ItemNeed вместо дублирования

Чтобы не дублировать needs/planner, нужно ввести `ItemNeed`.

```python
@dataclass
class ItemNeed:
    key: str                    # food, drink, medicine, weapon, armor, ammo
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str]
    reason: str
```

`DriveEvaluator` создаёт `ItemNeed`, а planner использует его напрямую.

Пример:

```python
ItemNeed(
    key="ammo",
    desired_count=3,
    current_count=1,
    missing_count=2,
    urgency=0.40,
    compatible_item_types=frozenset({"ammo_545"}),
    reason="АК-74 требует 5.45, есть только 1 пачка из 3",
)
```

Тогда planner не должен заново выяснять калибр оружия.

---

## 10. Объяснение поведения для фронта

Фронт должен показывать не raw memory и не огромный JSON, а readable debug model.

### 10.1. NPC Thought Panel

Предлагаемые секции:

```text
1. Текущее состояние
2. Что НПЦ сейчас хочет
3. Что НПЦ сейчас делает
4. План по шагам
5. Почему выбран этот план
6. Какие альтернативы были отвергнуты
7. Какая память использована
8. Что может прервать план
9. Последние мысли/решения
```

### 10.2. Пример UI-представления

```text
NPC-3: “Шрам”

Состояние:
  HP: 72/100
  Голод: 35%
  Жажда: 68%
  Сонливость: 20%
  Деньги: 420
  Оружие: АК-74
  Патроны: 1/3 пачки
  Броня: нет

Главная долгосрочная цель:
  Разбогатеть: 420 / 75000

Текущая мысль:
  “Мне нужна вода, но я уже близко к торговцу. Сначала куплю воду, потом вернусь к поиску артефактов.”

Активный objective:
  RESTORE_WATER
  score: 0.82

Активный план:
  1. Дойти до Бара                         [выполняется, осталось 6 ходов]
  2. Купить воду у Сидоровича              [ожидает]
  3. Выпить воду, если жажда > 50           [ожидает]
  4. Вернуться к плану FIND_ARTIFACTS       [запланировано]

Почему выбран:
  + Жажда высокая: 68%
  + Из памяти известно, что в Баре есть торговец
  + До Бара близко: 6 ходов
  - Поиск артефактов отложен: риск обезвоживания растёт

Отвергнутые альтернативы:
  FIND_ARTIFACTS: score 0.58 — отложено из-за жажды
  RESUPPLY_AMMO: score 0.44 — не срочно
  BUY_ARMOR: score 0.38 — дорого и не срочно

Использованная память:
  - “Сидорович обычно находится в Баре” confidence 0.91
  - “В Баре недавно покупал воду” confidence 0.76
  - “Болото опасно во время выброса” confidence 0.84

Условия прерывания:
  - HP < 35
  - Жажда > 90 и нет воды
  - Выброс начался на опасной местности
  - Маршрут к Бару закрылся
```

### 10.3. Debug payload для фронта

```json
{
  "agent_id": "agent_ai_3",
  "display_name": "Шрам",
  "current_thought": "Мне нужна вода; ближайший надёжный источник — торговец в Баре.",
  "active_objective": {
    "key": "RESTORE_WATER",
    "score": 0.82,
    "source_drive": "thirst",
    "reason": "Жажда 68%, воды в инвентаре нет, торговец известен"
  },
  "drives": [
    {"key": "thirst", "urgency": 0.68, "rank": 1},
    {"key": "resupply_ammo", "urgency": 0.40, "rank": 2},
    {"key": "wealth", "urgency": 0.31, "rank": 3}
  ],
  "plan": {
    "id": "plan_901",
    "status": "active",
    "objective_key": "RESTORE_WATER",
    "steps": [
      {"index": 0, "kind": "travel_to_location", "label": "Идти в Бар", "status": "running", "remaining_ticks": 6},
      {"index": 1, "kind": "trade_buy_item", "label": "Купить воду", "status": "pending"},
      {"index": 2, "kind": "consume_item", "label": "Выпить воду", "status": "pending"}
    ]
  },
  "alternatives": [
    {"key": "FIND_ARTIFACTS", "score": 0.58, "decision": "paused", "reason": "Жажда выше безопасного уровня"},
    {"key": "RESUPPLY_AMMO", "score": 0.44, "decision": "rejected", "reason": "Есть 1 пачка патронов, не критично"}
  ],
  "memory_used": [
    {"id": "sem_trader_bar", "summary": "Сидорович обычно находится в Баре", "confidence": 0.91},
    {"id": "mem_buy_water_122", "summary": "Покупал воду в Баре", "confidence": 0.76}
  ],
  "interrupt_watchlist": [
    "emission_on_dangerous_terrain",
    "hp_below_35",
    "thirst_above_90",
    "route_blocked"
  ]
}
```

---

## 11. Хранение thought trace

Каждый тик можно хранить лёгкий trace последнего решения.

```python
agent["brain_trace"] = {
    "turn": world_turn,
    "thought": "Иду к торговцу за водой",
    "active_objective": ...,
    "top_drives": ...,
    "plan_summary": ...,
    "alternatives": ...,
    "memory_refs": ...,
}
```

Не нужно хранить trace за все тики в агенте. Старые trace можно писать в memory как compressed decision summaries.

Например:

```json
{
  "layer": "episodic",
  "kind": "decision_summary",
  "summary": "Отложил поиск артефактов и пошёл в Бар за водой из-за высокой жажды",
  "importance": 0.55,
  "tags": ["decision", "water", "plan_paused"]
}
```

---

## 12. Как мигрировать из текущей системы

### Этап 1. Не ломать existing behavior

Добавить новые структуры поверх текущих:

```text
agent["active_plan_v3"]
agent["brain_trace"]
agent["memory_v3"]
```

`schedule_action` временно оставить как runtime action.

### Этап 2. Вынести память

Создать модуль:

```text
backend/app/games/zone_stalkers/memory/
  models.py
  store.py
  indexing.py
  retrieval.py
  decay.py
  consolidation.py
  serializers.py
```

Старый `agent["memory"]` можно поддерживать как view/compat слой.

### Этап 3. Разбить NeedScores

Вместо одного `reload_or_rearm` ввести отдельные item needs:

```text
need.food_stock
need.drink_stock
need.medicine_stock
need.weapon
need.armor
need.ammo
```

Старый `reload_or_rearm` можно временно считать как:

```python
reload_or_rearm = max(
    food_stock,
    drink_stock,
    medicine_stock,
    weapon,
    armor,
    ammo,
)
```

### Этап 4. Ввести PlanMonitor

Даже до полной замены `scheduled_action` можно добавить:

```python
plan_monitor.assess_scheduled_action(agent, state, beliefs, drives)
```

Он будет возвращать:

```text
continue / pause / abort / adapt
```

Сначала можно поддержать только:

```text
выброс
критический HP
критическая жажда
критический голод
маршрут заблокирован
цель исчезла
```

Но важно, чтобы это было через общий score, а не через отдельные исключения.

### Этап 5. Добавить фронтовый debug payload

На backend отдавать `brain_trace`, а на frontend сделать панель:

```text
AgentProfileModal
  ├─ Current Thought
  ├─ Drives
  ├─ Active Objective
  ├─ Plan Timeline
  ├─ Alternatives
  ├─ Memory Used
  └─ Interrupt Watchlist
```

### Этап 6. Постепенно заменить legacy helper'ы

Executor может временно вызывать `_bot_schedule_travel`, `_bot_buy_from_trader`, `_bot_consume`, но decision ownership должен перейти в `NPCBrain`.

---

## 13. Основные инварианты v3

1. **НПЦ думает каждый тик.**  
   Активный план не отключает переоценку.

2. **План имеет инерцию.**  
   НПЦ не должен бросать цель при малейшем изменении score.

3. **Память — источник знаний.**  
   НПЦ не должен использовать всю карту как истину, кроме системных сигналов, доступных всем.

4. **Каждое решение объяснимо.**  
   У каждого выбранного objective должны быть reasons, score factors и memory refs.

5. **Долгие действия имеют checkpoints.**  
   Travel/explore/sleep не должны быть слепым ожиданием до конца.

6. **Нет дублирования item logic.**  
   То, какие предметы нужны, считается один раз и передаётся planner'у.

7. **Emergency — это высокая utility, а не отдельная магия.**  
   Выброс, бой, критический HP и жажда должны побеждать потому, что их risk/time_pressure огромны.

8. **Память стареет, но важный опыт остаётся.**  
   Удаление должно учитывать importance, confidence, emotional_weight, access_count и тип записи.

9. **Фронт показывает не JSON, а мысль.**  
   Игрок должен видеть понятное объяснение: “почему он это делает”.

---

## 14. Минимальный MVP v3

Если делать поэтапно, минимальный полезный MVP:

1. `BrainTrace` для фронта.
2. `ActivePlan` как wrapper над `scheduled_action`.
3. `PlanMonitor`, который каждый тик оценивает активное действие.
4. Разделение `reload_or_rearm` на item needs.
5. Индексированная память по location/item/entity/tag.
6. Retrieval top-N вместо сканирования всей памяти.
7. Memory decay + consolidation для старых записей.
8. UI-блок “Что НПЦ думает / делает / планирует”.

Даже без полного переписывания executor'ов это уже сделает систему намного понятнее.

---

## 15. Пример будущего полного тика

```python
def run_npc_brain_tick(agent_id: str, state: dict, world_turn: int) -> list[dict]:
    agent = state["agents"][agent_id]

    observations = observe_agent_world(agent_id, state)
    memory_result = memory_system.ingest(agent, observations, world_turn)

    retrieval_context = build_retrieval_context(agent, observations, state)
    relevant_memories = memory_system.retrieve(agent, retrieval_context)

    beliefs = build_belief_state(agent, state, observations, relevant_memories)
    drives = evaluate_drives(agent, beliefs)
    objectives = generate_objectives(agent, beliefs, drives)

    active_plan = agent.get("active_plan_v3")
    plan_assessment = assess_active_plan(active_plan, beliefs, objectives)

    decision = choose_decision(
        objectives=objectives,
        active_plan=active_plan,
        plan_assessment=plan_assessment,
        personality=agent.get("personality", {}),
    )

    plan = build_or_update_plan(decision, active_plan, beliefs)
    events = execute_plan_step(agent_id, agent, plan, state, world_turn)

    agent["active_plan_v3"] = serialize_plan(plan)
    agent["brain_trace"] = build_brain_trace(
        agent=agent,
        beliefs=beliefs,
        drives=drives,
        objectives=objectives,
        decision=decision,
        plan=plan,
        memories=relevant_memories,
    )

    return events
```

---

## 16. Итоговая формула поведения

НПЦ v3 — это не набор `if`-исключений, а агент с памятью, потребностями и планами:

```text
Наблюдаю мир
  → обновляю память
  → вспоминаю релевантное
  → оцениваю потребности
  → формирую цели
  → сравниваю старый план с новыми вариантами
  → продолжаю / адаптирую / ставлю на паузу / отменяю
  → исполняю следующий шаг
  → записываю объяснение решения
```

Такой подход должен сделать поведение:

- более системным;
- менее завязанным на исключения;
- лучше объяснимым;
- лучше масштабируемым по памяти;
- удобным для фронтовой визуализации;
- более живым и правдоподобным для симуляции Зоны.
