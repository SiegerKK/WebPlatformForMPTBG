# NPC Brain v3 — PR 3 Implementation Contract

> Проект: `zone_stalkers`  
> Предыдущие этапы:
> - PR 1: `PlanMonitor`, `brain_trace`, sleep partial effects, survival-safe rest.
> - PR 2: `ImmediateNeed`, `ItemNeed`, liquidity, survival-mode purchasing.
>
> Цель PR 3: ввести `MemoryStore v3` и `BeliefState`-adapter, чтобы NPC начал принимать решения через структурированную память, а не через неявное сканирование `agent["memory"]` и частично всеведущий `state`.

---

## 1. Важное уточнение: после PR 3 мы полностью переходим на новую схему?

Короткий ответ:

```text
Нет, не полностью.
```

После PR 3 у нас будет уже очень сильная основа новой схемы:

```text
PR 1: action monitoring + trace
PR 2: explicit needs + liquidity
PR 3: structured memory + belief state adapter
```

Но полный переход на новую схему требует ещё минимум одного этапа:

```text
PR 4: Objective model + Objective scoring + Plan continuity
```

И, возможно:

```text
PR 5: ActivePlan becomes source of truth instead of scheduled_action
```

То есть PR 3 — это переход от “NPC смотрит в state и память хаотично” к “NPC строит belief из памяти”.  
Но ещё не полный `NPCBrain.tick`.

---

## 2. Цель PR 3

PR 3 должен сделать память настоящей системной механикой.

После PR 3 NPC должен:

1. Хранить память не только как плоский список `agent["memory"]`.
2. Иметь структурированный `memory_v3`.
3. Быстро искать релевантные записи по:
   - location;
   - entity;
   - item type;
   - tag;
   - memory kind;
   - layer.
4. Получать top-N memory results для decision pipeline.
5. Строить `BeliefState` adapter на базе:
   - текущих observations;
   - legacy `AgentContext`;
   - `memory_v3` retrieval.
6. Показывать во frontend/debug, какая память повлияла на решение.
7. Не ломать старую `agent["memory"]`.

---

## 3. Non-goals for PR 3

PR 3 НЕ делает:

- Redis;
- PostgreSQL memory tables;
- vector search;
- semantic embeddings;
- LLM reasoning;
- полный `ObjectiveGenerator`;
- замену `NeedScores`;
- замену `scheduled_action`;
- full `ActivePlan`;
- социальную дипломатию;
- групповую память;
- долгую консолидацию на тысячи записей;
- сложную forgetting model с ML.

PR 3 — это MVP структурированной памяти в существующем JSON/state.

---

## 4. Новая структура `memory_v3`

В агенте:

```python
agent["memory_v3"] = {
    "schema_version": 1,
    "records": {},
    "indexes": {
        "by_layer": {},
        "by_kind": {},
        "by_location": {},
        "by_entity": {},
        "by_item_type": {},
        "by_tag": {},
    },
    "stats": {
        "records_count": 0,
        "last_decay_turn": None,
        "last_consolidation_turn": None,
    },
}
```

### Backward compatibility

Если `memory_v3` отсутствует:

```python
ensure_memory_v3(agent)
```

создаёт пустую структуру.

Legacy `agent["memory"]` остаётся.

---

## 5. `MemoryRecord`

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/memory/models.py
```

Модель:

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: str
    agent_id: str
    layer: str
    kind: str
    created_turn: int
    last_accessed_turn: int | None

    summary: str
    details: dict

    location_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    item_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    importance: float = 0.5
    confidence: float = 1.0
    emotional_weight: float = 0.0
    decay_rate: float = 0.01

    status: str = "active"  # active, stale, contradicted, archived
    source: str = "observed" # observed, inferred, heard, legacy_import
    evidence_refs: tuple[str, ...] = ()
```

---

## 6. Memory layers

PR 3 фиксирует только базовые layers:

```text
working
episodic
semantic
spatial
social
threat
goal
```

### `working`

Краткоживущие записи текущей ситуации.

TTL: 1–20 тиков.

### `episodic`

События:

```text
видел предмет
купил предмет
прервал сон
нашёл артефакт
был ранен
пришёл в локацию
```

### `semantic`

Стабильные знания:

```text
торговец обычно в Бункере
Сидорович продаёт воду
Болото опасно
```

### `spatial`

Знания о локациях:

```text
known trader at location
known item at location
confirmed empty
danger score
```

### `social`

Отношения с агентами.

В PR 3 можно только создать layer, без сложной логики.

### `threat`

Опасности:

```text
emission danger
anomaly damage
combat ambush
dangerous terrain
```

### `goal`

Память, связанная с глобальной целью.

---

## 7. MemoryStore API

Рекомендуемые файлы:

```text
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/retrieval.py
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/memory/legacy_bridge.py
```

### Core API

```python
def ensure_memory_v3(agent: dict) -> dict:
    ...

def add_memory_record(agent: dict, record: MemoryRecord) -> None:
    ...

def retrieve_memory(
    agent: dict,
    query: MemoryQuery,
    world_turn: int,
) -> list[MemoryRecord]:
    ...

def mark_memory_stale(agent: dict, memory_id: str, reason: str) -> None:
    ...

def decay_memory(agent: dict, world_turn: int) -> None:
    ...
```

---

## 8. `MemoryQuery`

```python
@dataclass(frozen=True)
class MemoryQuery:
    purpose: str
    layers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    location_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    item_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    max_results: int = 10
    include_stale: bool = False
```

Hard cap:

```python
max_results <= 50
```

---

## 9. Retrieval scoring

PR 3 retrieval should be simple and deterministic.

```text
score =
    tag_match * 0.25
  + kind_match * 0.20
  + location_match * 0.20
  + confidence * 0.15
  + importance * 0.10
  + recency * 0.10
  - stale_penalty
```

Tie-breaker:

```python
(-score, -created_turn, record.id)
```

No random.

---

## 10. Legacy bridge

PR 3 must not abandon existing `agent["memory"]`.

### Ingest legacy memory into `memory_v3`

When a new legacy memory is added via `_add_memory(...)`, also create a `MemoryRecord`.

Mapping:

```text
legacy type observation → layer episodic or threat
legacy type decision    → layer episodic
legacy type action      → layer episodic
```

Examples:

```text
action_kind = trade_buy
→ kind = item_bought
→ tags = ["trade", "item", item_type]

action_kind = plan_monitor_abort
→ kind = action_aborted
→ tags = ["plan_monitor", "scheduled_action", reason]

action_kind = emission_imminent
→ layer = threat
→ kind = emission_warning
→ tags = ["emission", "danger"]
```

### Do not fully migrate historical memory in PR 3

Historical migration can be lazy:

```text
when memory_v3 empty:
  import last N legacy records
```

N default:

```python
LEGACY_MEMORY_IMPORT_LIMIT = 200
```

---

## 11. BeliefState adapter

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/beliefs.py
```

Модель:

```python
@dataclass(frozen=True)
class BeliefState:
    agent_id: str
    location_id: str
    current_location: dict
    visible_entities: tuple[dict, ...]
    known_traders: tuple[dict, ...]
    known_items: tuple[dict, ...]
    known_threats: tuple[dict, ...]
    relevant_memories: tuple[dict, ...]
    confidence_summary: dict
```

### Важно

В PR 3 `BeliefState` — adapter, а не replacement.

```text
AgentContext remains.
BeliefState is built from AgentContext + MemoryStore retrieval.
Planner can start using BeliefState where safe.
```

---

## 12. Retrieval contexts for planner

PR 3 должен подключить retrieval хотя бы к этим задачам:

### 12.1. Find trader

```python
MemoryQuery(
    purpose="find_trader",
    layers=("semantic", "spatial", "episodic"),
    tags=("trader", "trade"),
    max_results=5,
)
```

### 12.2. Find food / water

```python
MemoryQuery(
    purpose="find_food",
    tags=("food", "item"),
    item_types=FOOD_ITEM_TYPES,
    max_results=10,
)
```

```python
MemoryQuery(
    purpose="find_water",
    tags=("drink", "water", "item"),
    item_types=DRINK_ITEM_TYPES,
    max_results=10,
)
```

### 12.3. Avoid threat

```python
MemoryQuery(
    purpose="avoid_threat",
    layers=("threat", "spatial"),
    tags=("danger",),
    max_results=10,
)
```

### 12.4. Sell artifacts

```python
MemoryQuery(
    purpose="sell_artifacts",
    tags=("trader", "artifact_buyer"),
    max_results=5,
)
```

---

## 13. Memory decay

PR 3 should implement simple decay, not full consolidation.

### Decay cadence

```text
run decay every 100 turns per agent
```

Store:

```python
memory_v3["stats"]["last_decay_turn"]
```

### Decay behavior

For each record:

```text
effective_score = importance + confidence + emotional_weight + recency_bonus
```

If record is low-value and old:

```text
status = archived
```

Do not physically delete immediately unless over cap.

### Caps

```python
MEMORY_V3_MAX_RECORDS = 5000
MEMORY_V3_IMPORT_LEGACY_LIMIT = 200
MEMORY_V3_RETRIEVAL_MAX_RESULTS = 50
```

If over cap:

```text
delete/archive lowest-score records first
but never delete high-importance threat/goal/semantic records
```

---

## 14. Memory consolidation

PR 3 should only do minimal consolidation.

Example:

If same trader seen at same location many times:

```text
episodic trader_seen × many
→ semantic trader_location_known
```

Do not implement complex summarization.

Rule:

```text
if same kind + same subject/location observed >= 3 times:
    create/update semantic record
```

---

## 15. BrainTrace integration

Add optional field:

```json
{
  "memory_used": [
    {
      "id": "mem_123",
      "kind": "trader_location_known",
      "summary": "Гнидорович обычно находится в Бункере торговца",
      "confidence": 0.92
    }
  ]
}
```

Limit:

```text
memory_used <= 5
```

Do not dump full memory.

---

## 16. Frontend scope

`AgentProfileModal` should show:

```text
Память, использованная решением:
  - Гнидорович обычно находится в Бункере торговца (confidence 92%)
  - В Бункере недавно покупал воду (confidence 76%)
```

Do not build full memory browser in PR 3.

Optional debug section:

```text
Memory stats:
  records: 420
  active: 300
  stale: 80
  archived: 40
```

---

## 17. Expected files

### New backend package

```text
backend/app/games/zone_stalkers/memory/__init__.py
backend/app/games/zone_stalkers/memory/models.py
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/retrieval.py
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/memory/legacy_bridge.py
```

### New decision files

```text
backend/app/games/zone_stalkers/decision/beliefs.py
```

### Changed backend files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/context_builder.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
```

### Tests

```text
backend/tests/decision/v3/test_memory_store.py
backend/tests/decision/v3/test_memory_retrieval.py
backend/tests/decision/v3/test_memory_decay.py
backend/tests/decision/v3/test_legacy_memory_bridge.py
backend/tests/decision/v3/test_belief_state_adapter.py
backend/tests/decision/v3/test_brain_trace_memory_used.py
```

### Frontend

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

---

## 18. Test plan

### MemoryStore

```text
ensure_memory_v3 creates empty structure
add_memory_record updates records and indexes
retrieve by tag returns expected records
retrieve by location returns expected records
retrieve by item_type returns expected records
```

### Retrieval

```text
fresh high-confidence memory ranks above stale memory
threat memory persists longer than low-value observation
max_results cap is enforced
results are deterministic
```

### Legacy bridge

```text
_add_memory trade_buy creates legacy memory and memory_v3 record
_add_memory emission_imminent creates threat record
plan_monitor_abort creates memory_v3 record with tags
```

### Decay

```text
low-importance old episodic records become archived
semantic trader record remains active
threat record remains active longer
```

### BeliefState

```text
known trader appears in BeliefState from memory_v3
known water source appears in BeliefState
stale/contradicted record excluded unless include_stale=True
```

### Planner integration

```text
seek_water can use remembered water source via MemoryStore
sell_artifacts can use remembered trader
avoid threat can use threat memory
```

### BrainTrace

```text
brain_trace.memory_used length <= 5
memory_used contains summary/confidence/kind
```

---

## 19. Definition of Done

PR 3 is done when:

- [ ] `memory_v3` structure exists and is backward compatible.
- [ ] `MemoryRecord` and `MemoryQuery` models exist.
- [ ] Memory indexes work for layer/kind/location/entity/item/tag.
- [ ] Legacy `_add_memory` writes or bridges into `memory_v3`.
- [ ] Retrieval top-N is deterministic and capped.
- [ ] Basic decay/archive works.
- [ ] Minimal consolidation for repeated trader/location facts exists.
- [ ] `BeliefState` adapter exists.
- [ ] Planner uses MemoryStore for at least trader/item/threat lookups.
- [ ] `brain_trace.memory_used` shows used memories.
- [ ] Frontend displays memory-used summary.
- [ ] Old `agent["memory"]` still works.
- [ ] No Redis/PostgreSQL dependency is introduced.

---

## 20. What remains after PR 3

After PR 3, the project has:

```text
observability
active action monitoring
partial sleep/progress
explicit needs
liquidity
structured memory
belief adapter
```

But it still does not have full v3 decision ownership.

Remaining work:

```text
PR 4 — Objective model and scoring:
  - Objective
  - ObjectiveOption
  - goal alignment
  - risk/time/resource scoring
  - rejected alternatives in brain_trace
  - continue vs switch score

PR 5 — ActivePlan ownership:
  - ActivePlan becomes source of truth
  - scheduled_action becomes runtime executor detail
  - pause/adapt implemented
  - plan repair generalized
```

---

## 21. Final position

PR 3 is the point where NPC starts to become a memory-based agent.

But the full new scheme is complete only when:

```text
BeliefState + ImmediateNeed/ItemNeed + Objective scoring + ActivePlan continuity
```

are all connected.

So the realistic roadmap:

```text
PR 1: monitor/action observability
PR 2: needs/economy
PR 3: memory/beliefs
PR 4: objectives/scoring
PR 5: active plan ownership
```

After PR 3 we should stop adding special cases and move to `Objective` as the central decision model.
