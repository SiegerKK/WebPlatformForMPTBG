# Zone Stalkers — план мощной многоэтапной CPU-оптимизации

## Назначение документа

Этот документ описывает следующий отдельный PR после завершения текущей ветки:

```text
copilot/optimize-network-traffic-cpu-load
```

Текущий PR закрывает первый слой оптимизации:

```text
- zone_delta WebSocket payload;
- projections / zone-lite / debug-map-lite;
- compact WS messages;
- state_revision;
- debug subscriptions;
- map-static / map-dynamic endpoints;
- частичный gating memory decay / observations / brain_trace.
```

Следующий PR должен сфокусироваться **не на сети**, а на радикальном снижении CPU-нагрузки backend simulation loop.

Целевая задача:

```text
Снизить CPU-нагрузку tick/NPC simulation на 10×, а в крупных мирах потенциально на 50×–100×,
без ухудшения качества ИИ NPC.
```

Под “без ухудшения качества ИИ” здесь понимается:

```text
NPC принимают те же или эквивалентные решения,
но не пересчитывают их каждую минуту без причины.
```

---

# 1. Главная идея

Сейчас simulation loop всё ещё близок к модели:

```text
каждый игровой тик:
  скопировать весь state;
  пройти всех агентов;
  проверить scheduled actions;
  возможно обновить память;
  возможно обновить observations;
  возможно запустить Brain;
  построить delta сравнением old/new state;
  сохранить state.
```

Это плохо масштабируется.

Нужная модель:

```text
каждый игровой тик:
  обработать только due tasks;
  обработать только dirty/invalidated entities;
  пересчитать Brain только тем NPC, у которых есть причина думать;
  строить delta только из dirty-set;
  не копировать и не сканировать весь мир без необходимости.
```

То есть переход:

```text
polling all entities every tick
→ event-driven dirty-entity simulation
```

---

# 2. Что не входит в этот PR

Этот PR **не должен** повторять сетевые задачи текущей ветки.

Не делать здесь:

```text
- новую систему WebSocket zone_delta;
- frontend applyZoneDelta;
- projection endpoints;
- debug-map-lite;
- map-static/map-dynamic;
- compact tick fallback;
- lazy memory endpoints;
- scoped debug WebSocket subscriptions.
```

Они уже относятся к текущему PR.

Этот документ предполагает, что текущий PR уже слит или будет слит перед началом данной работы.

---

# 3. Целевые направления оптимизации

В PR должны войти совместимые между собой направления:

```text
1. CPU profiler with breakdown.
2. Dirty-set tracking.
3. Delta from dirty-set, not old/new full diff.
4. Remove or minimize full deepcopy(state).
5. Event-driven scheduled actions.
6. Lazy needs model.
7. Decision invalidation for NPC Brain.
8. AI decision budget / staggered thinking.
9. Pathfinding and nearest-target caches.
10. Memory indexes for decision/debug queries.
11. Static/runtime state separation.
12. Hot/cold state split.
```

Эти идеи не конфликтуют между собой. Они складываются в одну архитектуру.

---

# 4. Ожидаемый эффект

## 4.1. Conservative estimate

Для небольшого мира:

```text
10–15 NPC
50–70 locations
несколько traders
умеренный debug
```

Ожидаемый выигрыш:

```text
2×–5× CPU
```

## 4.2. Medium world estimate

Для мира:

```text
30–80 NPC
100–200 locations
активный auto-tick
много scheduled actions
```

Ожидаемый выигрыш:

```text
5×–20× CPU
```

## 4.3. Large world estimate

Для мира:

```text
100+ NPC
200+ locations
длинные travel/sleep/explore actions
большая память NPC
частые pathfinding queries
```

Ожидаемый выигрыш:

```text
10×–100× CPU
```

Максимальный выигрыш возможен, если большинство NPC большую часть времени находятся в long actions:

```text
travel
sleep
explore
wait
trade route
```

и их не нужно трогать каждый тик.

---

# 5. Этап 0 — обязательное CPU-профилирование

## 5.1. Почему сначала profiler

Нельзя оптимизировать вслепую. Нужно увидеть, где реально тратится CPU:

```text
deepcopy?
memory decay?
observations?
NPC Brain?
pathfinding?
event DB writes?
save state?
delta build?
JSON serialization?
debug trace?
```

Текущая ветка уже добавляет общий `tick_total_ms`, но этого недостаточно.

## 5.2. Добавить `TickProfiler`

Создать файл:

```text
backend/app/games/zone_stalkers/performance/tick_profiler.py
```

Пример API:

```python
from contextlib import contextmanager
from time import perf_counter
from typing import Any


class TickProfiler:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.sections: dict[str, float] = {}
        self.counters: dict[str, int] = {}

    @contextmanager
    def section(self, name: str):
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - started) * 1000.0
            self.sections[name] = self.sections.get(name, 0.0) + elapsed_ms

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections_ms": {k: round(v, 3) for k, v in self.sections.items()},
            "counters": dict(self.counters),
        }
```

## 5.3. Метрики, которые нужно собрать

В `tick_zone_map` и `ruleset.tick` добавить breakdown:

```text
load_state_ms
deepcopy_ms
migration_ms
memory_v3_ensure_ms
memory_decay_ms
scheduled_actions_ms
plan_monitor_ms
needs_update_ms
emission_ms
combat_ms
active_plan_runtime_ms
npc_brain_total_ms
npc_brain_decision_count
npc_brain_skipped_count
location_observations_ms
world_time_advance_ms
reset_flags_ms
event_db_write_ms
save_state_ms
delta_build_ms
state_size_bytes
runtime_state_size_bytes
dirty_agents_count
dirty_locations_count
dirty_traders_count
```

## 5.4. Performance endpoint

Расширить текущий endpoint:

```text
GET /zone-stalkers/debug/performance/{match_id}
```

Чтобы он возвращал последние N тиков с breakdown:

```json
{
  "tick_total_ms": 42.5,
  "sections_ms": {
    "deepcopy": 12.3,
    "scheduled_actions": 4.1,
    "npc_brain": 18.0,
    "delta_build": 2.5,
    "save_state": 4.0
  },
  "counters": {
    "agents_total": 20,
    "npc_brain_decision_count": 3,
    "npc_brain_skipped_count": 17,
    "dirty_agents_count": 4,
    "due_tasks_count": 2
  }
}
```

## 5.5. Acceptance criteria

```text
[ ] Можно увидеть breakdown tick CPU по секциям.
[ ] Метрики не ломают tick при ошибках.
[ ] Profiler можно отключить конфигом.
[ ] Есть endpoint для последних tick metrics.
[ ] Есть тест, что profiler не ломает tick result shape.
```

---

# 6. Этап 1 — Dirty-set tracking

## 6.1. Проблема

Сейчас compact delta строится через сравнение `old_state` и `new_state`:

```text
for every agent:
  compare hot fields

for every location:
  compare hot fields

for every trader:
  compare hot fields
```

Это лучше для сети, но по CPU всё ещё O(N agents + N locations + N traders).

## 6.2. Цель

Все изменения state должны помечать dirty entities.

Тогда delta builder будет работать так:

```text
for agent_id in dirty.agents:
  build compact agent patch

for location_id in dirty.locations:
  build compact location patch

for trader_id in dirty.traders:
  build compact trader patch
```

## 6.3. Структура dirty state

Не сохранять dirty-set в persistent state как обычные данные.

Использовать runtime-only объект:

```python
class TickRuntime:
    dirty_agents: set[str]
    dirty_locations: set[str]
    dirty_traders: set[str]
    dirty_state_fields: set[str]
    events: list[dict]
    profiler: TickProfiler
```

Если пока сложно внедрить runtime object, можно временно хранить:

```python
state["_runtime_dirty"] = {
    "agents": set(),
    "locations": set(),
    "traders": set(),
    "state": set(),
}
```

Перед сохранением state удалить `_runtime_dirty`.

## 6.4. Helper functions

Создать файл:

```text
backend/app/games/zone_stalkers/runtime/dirty.py
```

API:

```python
def mark_agent_dirty(runtime, agent_id: str) -> None:
    runtime.dirty_agents.add(agent_id)

def mark_location_dirty(runtime, location_id: str) -> None:
    runtime.dirty_locations.add(location_id)

def mark_trader_dirty(runtime, trader_id: str) -> None:
    runtime.dirty_traders.add(trader_id)

def mark_state_dirty(runtime, field: str) -> None:
    runtime.dirty_state_fields.add(field)
```

Для безопасной миграции можно сначала использовать wrapper-сеттеры только в основных hot paths:

```python
def set_agent_field(state, runtime, agent_id: str, key: str, value):
    agent = state["agents"][agent_id]
    if agent.get(key) != value:
        agent[key] = value
        mark_agent_dirty(runtime, agent_id)
```

## 6.5. Где помечать dirty

Минимальный список:

```text
agent:
  location_id
  hp
  hunger
  thirst
  sleepiness
  money
  inventory
  equipment
  is_alive
  has_left_zone
  scheduled_action
  active_plan_v3
  current_goal
  global_goal
  action_used

location:
  agents
  artifacts
  items
  anomaly_activity
  dominant_anomaly_type

trader:
  money
  inventory
  prices
  location_id
  is_alive

state:
  world_turn
  world_day
  world_hour
  world_minute
  emission_active
  emission_scheduled_turn
  emission_ends_turn
  game_over
  active_events
```

## 6.6. Delta from dirty-set

Добавить новый builder:

```text
backend/app/games/zone_stalkers/delta_dirty.py
```

или расширить текущий `delta.py`:

```python
def build_zone_delta_from_dirty(
    *,
    state: dict,
    runtime: TickRuntime,
    events: list[dict],
) -> dict:
    ...
```

Fallback оставить:

```python
if runtime dirty unavailable:
    build_zone_delta(old_state=..., new_state=...)
```

## 6.7. Acceptance criteria

```text
[ ] Delta builder может работать от dirty-set.
[ ] Старый old/new diff остаётся fallback.
[ ] Все основные state mutations помечают dirty.
[ ] Dirty-set удаляется перед save_context_state.
[ ] Тест: если изменился один agent, delta содержит только его.
[ ] Тест: если agent перешёл loc_A → loc_B, dirty содержит agent, loc_A, loc_B.
[ ] Тест: dirty-set не попадает в projection/full saved state.
```

---

# 7. Этап 2 — убрать полный `deepcopy(state)` из tick hot path

## 7.1. Проблема

В начале `tick_zone_map` сейчас есть:

```python
state = copy.deepcopy(state)
```

Это копирует весь мир:

```text
agents
locations
traders
memory
memory_v3
debug
brain_trace
inventory
events
combat state
```

Стоимость растёт вместе с размером мира, даже если в тике изменился один NPC.

## 7.2. Цель

Заменить full deepcopy на copy-on-write или in-place runtime mutation.

## 7.3. Предпочтительный вариант: copy-on-write runtime

Создать runtime wrapper:

```python
class ZoneTickRuntime:
    def __init__(self, state: dict):
        self.state = dict(state)  # shallow top-level copy
        self._agents_copied = False
        self._locations_copied = False
        self._traders_copied = False
        self._copied_agents: set[str] = set()
        self._copied_locations: set[str] = set()
        self._copied_traders: set[str] = set()
        self.dirty_agents: set[str] = set()
        self.dirty_locations: set[str] = set()
        self.dirty_traders: set[str] = set()
        self.dirty_state_fields: set[str] = set()
```

Methods:

```python
def agent(self, agent_id: str) -> dict:
    if not self._agents_copied:
        self.state["agents"] = dict(self.state.get("agents", {}))
        self._agents_copied = True
    if agent_id not in self._copied_agents:
        self.state["agents"][agent_id] = dict(self.state["agents"][agent_id])
        self._copied_agents.add(agent_id)
    return self.state["agents"][agent_id]
```

Equivalent methods:

```text
location(location_id)
trader(trader_id)
set_state_field(field, value)
```

## 7.4. Migration strategy

Do not rewrite all tick code at once.

Step-by-step:

```text
1. Keep old tick_zone_map signature.
2. Inside tick_zone_map create runtime = ZoneTickRuntime(state).
3. Replace direct mutations in hottest paths first:
   - scheduled action travel updates;
   - needs update;
   - death;
   - emission;
   - inventory/trade;
   - location agent moves.
4. Keep fallback direct mutation where not yet migrated.
5. At end return runtime.state.
```

But for correctness, direct mutation of nested dicts not copied by runtime can mutate old_state. During migration, be careful.

Safer intermediate variant:

```text
Keep deepcopy for first PR if needed,
but implement dirty-set first.
Then remove deepcopy in dedicated commit with focused tests.
```

Recommended commit order:

```text
Commit A: profiler.
Commit B: dirty-set with old deepcopy still present.
Commit C: copy-on-write runtime, tests.
```

## 7.5. Tests required

```python
def test_tick_does_not_mutate_input_state():
    old = make_state()
    old_copy = deepcopy(old)
    new_state, events = tick_zone_map(old)
    assert old == old_copy
    assert new_state is not old

def test_copy_on_write_changes_only_mutated_agent():
    ...

def test_dirty_set_matches_changed_entities():
    ...
```

## 7.6. Acceptance criteria

```text
[ ] tick_zone_map no longer performs full copy.deepcopy(state) on every tick.
[ ] Input state is not mutated.
[ ] Changed nested structures are copied before mutation.
[ ] Tests cover travel, needs, death, emission, trade.
[ ] tick_total_ms/deepcopy_ms confirms improvement.
```

---

# 8. Этап 3 — Event-driven scheduled actions

## 8.1. Проблема

Long actions currently require per-tick processing:

```text
travel:
  every tick decrement turns_remaining

sleep:
  every tick or interval update

explore:
  every tick countdown

wait:
  every tick countdown
```

Если 100 NPC идут по 30 минут, система трогает их 3000 раз, хотя meaningful event только один:

```text
arrival
```

## 8.2. Цель

Перевести long actions на модель:

```text
scheduled_action.started_turn
scheduled_action.ends_turn
scheduled_action.target_id
```

Каждый tick обрабатывать только actions, у которых:

```text
ends_turn <= world_turn
```

## 8.3. Action scheduler

Добавить в state runtime:

```json
{
  "scheduled_tasks": {
    "12345": [
      {
        "kind": "travel_arrival",
        "agent_id": "agent_1"
      }
    ]
  }
}
```

Но хранить ключи как строки, потому что JSON.

Helper API:

```python
def schedule_task(state, runtime, turn: int, task: dict) -> None:
    state.setdefault("scheduled_tasks", {}).setdefault(str(turn), []).append(task)
    mark_state_dirty(runtime, "scheduled_tasks")

def pop_due_tasks(state, runtime, world_turn: int) -> list[dict]:
    tasks = state.setdefault("scheduled_tasks", {}).pop(str(world_turn), [])
    if tasks:
        mark_state_dirty(runtime, "scheduled_tasks")
    return tasks
```

## 8.4. Travel example

Old scheduled_action:

```json
{
  "type": "travel",
  "turns_remaining": 12,
  "turns_total": 30,
  "target_id": "loc_B",
  "route": ["loc_A", "loc_X", "loc_B"]
}
```

New scheduled_action:

```json
{
  "type": "travel",
  "started_turn": 100,
  "ends_turn": 130,
  "turns_total": 30,
  "target_id": "loc_B",
  "route": ["loc_A", "loc_X", "loc_B"],
  "interruptible": true
}
```

UI computes:

```python
turns_remaining = max(0, ends_turn - world_turn)
```

## 8.5. Due task processing

At each tick:

```python
due_tasks = pop_due_tasks(state, runtime, world_turn)
for task in due_tasks:
    process_due_task(task)
```

Task types:

```text
travel_arrival
explore_complete
sleep_interval
sleep_complete
wait_complete
trade_complete
active_plan_step_complete
```

## 8.6. Interruptions

Quality must not degrade. Long actions still need interruption by urgent events.

Do not poll every NPC for every interruption. Instead, urgent events invalidate affected actions.

Examples:

### Emission warning

When emission warning starts:

```text
for alive agents not in safe location:
  interrupt current action if interruptible
  enqueue urgent decision: REACH_SAFE_SHELTER
```

This is O(N) only when emission warning happens, not every tick.

### Combat starts in location

When combat starts in location:

```text
for agents in that location:
  interrupt/alert
```

O(number of agents in location), not O(all agents).

### Critical need threshold crossed

Use lazy needs thresholds and scheduled threshold tasks.

If thirst will cross critical at turn 12340, schedule:

```json
{
  "kind": "need_threshold_crossed",
  "agent_id": "agent_1",
  "need": "thirst",
  "threshold": "critical"
}
```

## 8.7. Backward compatibility

During migration, support old scheduled_action shape:

```python
if scheduled_action has turns_remaining and no ends_turn:
    migrate to ends_turn = world_turn + turns_remaining
```

## 8.8. Acceptance criteria

```text
[ ] Travel no longer decrements turns_remaining every tick.
[ ] UI still shows progress via derived turns_remaining.
[ ] Arrival happens at correct turn.
[ ] Explore/sleep/wait complete at correct turn.
[ ] Emission can interrupt travel/sleep/explore.
[ ] Combat/location event can interrupt relevant agents.
[ ] No quality loss in NPC behavior tests.
[ ] scheduled_tasks does not grow unbounded.
```

---

# 9. Этап 4 — Lazy needs model

## 9.1. Проблема

Needs are updated by periodically looping through all agents:

```text
hunger += rate
thirst += rate
sleepiness += rate
```

This is O(N) even if no agent is active.

## 9.2. Цель

Store base value + last update turn:

```json
{
  "needs_state": {
    "hunger": {
      "base": 20,
      "updated_turn": 1000
    },
    "thirst": {
      "base": 30,
      "updated_turn": 1000
    },
    "sleepiness": {
      "base": 10,
      "updated_turn": 1000
    }
  }
}
```

Compute current value lazily:

```python
def get_need(agent, need_key, world_turn):
    state = agent["needs_state"][need_key]
    elapsed_hours = (world_turn - state["updated_turn"]) / 60
    return min(100, state["base"] + elapsed_hours * rate_per_hour)
```

## 9.3. Materialization

Write current values back only when:

```text
- agent consumes food/water;
- agent sleeps;
- agent takes damage from critical need;
- agent decision pipeline needs stable snapshot;
- projection/delta needs value;
- threshold crossed event fires.
```

## 9.4. Threshold tasks

When materializing or after consumption, schedule future threshold crossings:

```text
hunger soft threshold
hunger critical threshold
thirst soft threshold
thirst critical threshold
sleepiness soft threshold
sleepiness critical threshold
```

Example:

```python
def schedule_need_thresholds(agent_id, agent, world_turn):
    current_thirst = get_need(agent, "thirst", world_turn)
    if current_thirst < CRITICAL_THIRST_THRESHOLD:
        turns_until = compute_turns_until_threshold(...)
        schedule_task(world_turn + turns_until, {
            "kind": "need_threshold_crossed",
            "agent_id": agent_id,
            "need": "thirst",
            "threshold": "critical",
        })
```

## 9.5. Death/damage from critical needs

Instead of checking all agents hourly, schedule critical damage ticks only for affected agents.

When thirst crosses critical:

```text
schedule recurring need_damage task for this agent
```

When agent drinks:

```text
cancel or ignore stale need_damage tasks via token/revision
```

Use revision token:

```json
{
  "kind": "need_damage",
  "agent_id": "agent_1",
  "need": "thirst",
  "needs_revision": 17
}
```

If task revision != current agent needs revision, ignore.

## 9.6. Backward compatibility

Existing saves have:

```text
agent.hunger
agent.thirst
agent.sleepiness
```

Migration:

```python
if "needs_state" not in agent:
    agent["needs_state"] = {
        "hunger": {"base": agent.get("hunger", 0), "updated_turn": world_turn},
        ...
    }
```

Projection can still expose:

```text
hunger
thirst
sleepiness
```

as derived values.

## 9.7. Acceptance criteria

```text
[ ] No full-agent hourly needs loop required.
[ ] UI still sees hunger/thirst/sleepiness numbers.
[ ] Eating/drinking/sleeping works.
[ ] Starvation/thirst damage still happens.
[ ] Critical need can interrupt current action.
[ ] Old saves migrate.
[ ] Tests cover value calculation and threshold scheduling.
```

---

# 10. Этап 5 — NPC Brain decision invalidation

## 10.1. Проблема

NPC Brain should not run just because time passed.

A stable NPC in a long action or with a valid plan does not need full objective scoring every tick.

## 10.2. Цель

Introduce per-agent decision cache:

```json
{
  "brain_runtime": {
    "last_decision_turn": 1230,
    "valid_until_turn": 1260,
    "decision_revision": 10,
    "last_objective_key": "SELL_ARTIFACTS",
    "last_intent_kind": "sell_artifacts",
    "last_plan_key": "sell_artifacts:trader_1",
    "invalidated": false,
    "invalidators": []
  }
}
```

## 10.3. Invalidation reasons

Brain must run immediately when:

```text
agent arrived
plan completed
plan failed
scheduled action interrupted
inventory changed
money changed significantly
hp crossed threshold
hunger/thirst/sleepiness crossed soft/critical threshold
emission warning started
emission started
emission ended
combat started
enemy/target seen
target location intel received
trader unavailable
artifact found
location confirmed empty
global goal changed
```

## 10.4. Helper API

```python
def invalidate_brain(agent, runtime, reason: str, priority: str = "normal") -> None:
    br = agent.setdefault("brain_runtime", {})
    br["invalidated"] = True
    br.setdefault("invalidators", []).append({
        "reason": reason,
        "priority": priority,
    })
    mark_agent_dirty(runtime, agent["id"])
```

## 10.5. Decision eligibility

```python
def should_run_brain(agent, world_turn):
    br = agent.get("brain_runtime") or {}
    if br.get("invalidated"):
        return True
    if world_turn >= br.get("valid_until_turn", 0):
        return True
    return False
```

## 10.6. Validity duration

For ordinary economic decisions:

```text
valid_until_turn = world_turn + 5..15
```

For safe idle:

```text
valid_until_turn = world_turn + 15..60
```

For danger/combat/emission:

```text
valid_until_turn = world_turn
```

## 10.7. Quality preservation

This does not make NPC dumber. It only means:

```text
NPC keeps following its chosen plan until something relevant changes.
```

That is closer to realistic behavior than rethinking every minute.

## 10.8. Acceptance criteria

```text
[ ] NPC Brain runs only when invalidated or cache expired.
[ ] Urgent events invalidate immediately.
[ ] Economic NPC still finds/sells artifacts.
[ ] Hunter NPC still reacts to new intel/target sightings.
[ ] Emission response remains immediate.
[ ] Brain decision count per tick drops significantly.
```

---

# 11. Этап 6 — AI budget and staggered thinking

## 11.1. Проблема

Even with invalidation, many NPC can become invalidated at once:

```text
emission ended
all arrived
day changed
trader stock changed
large combat event
```

This causes CPU spikes.

## 11.2. Цель

Introduce decision queue and per-tick budget.

Config:

```yaml
ai_budget:
  enabled: true
  max_normal_decisions_per_tick: 5
  max_background_decisions_per_tick: 2
  urgent_decisions_ignore_budget: true
  max_decision_delay_turns: 10
```

## 11.3. Priorities

```text
urgent:
  combat
  emission
  critical HP
  critical thirst/hunger
  target co-located for hunter

high:
  arrived
  plan failed
  artifact found
  scheduled action completed

normal:
  resupply
  sell artifacts
  choose next anomaly

low:
  idle improvement
  social/non-critical
```

## 11.4. Decision queue

Runtime structure:

```json
{
  "decision_queue": [
    {
      "agent_id": "agent_1",
      "priority": "high",
      "reason": "arrived",
      "queued_turn": 1234
    }
  ]
}
```

Queue order:

```text
urgent > high > normal > low
then queued_turn ascending
```

## 11.5. Starvation prevention

If an agent waits too long:

```text
world_turn - queued_turn >= max_decision_delay_turns
```

it gets promoted.

## 11.6. Acceptance criteria

```text
[ ] Urgent decisions are immediate.
[ ] Normal decisions are capped per tick.
[ ] No NPC can be starved forever.
[ ] CPU spikes from simultaneous invalidation are reduced.
[ ] Simulation quality remains stable in 100-seed balance run.
```

---

# 12. Этап 7 — Pathfinding and nearest-object caches

## 12.1. Проблема

NPC repeatedly search paths and nearest entities:

```text
nearest trader
nearest shelter
nearest anomaly
route to target
route to trader
route to safe location
```

If each decision recomputes BFS/Dijkstra, CPU grows quickly.

## 12.2. Цель

Cache graph queries by `map_revision`.

## 12.3. Cache structure

```python
class PathfindingCache:
    map_revision: int
    shortest_path: dict[tuple[str, str], list[str]]
    shortest_distance: dict[tuple[str, str], int]
    nearest_trader: dict[str, str]
    nearest_shelter: dict[str, str]
    anomaly_candidates: dict[tuple[str, int], list[str]]
```

## 12.4. Invalidation

Clear cache when:

```text
map_revision changes
connection added/removed
location added/removed
terrain type changed
safe/unsafe status changed
trader added/removed/moved/died
```

## 12.5. Precompute option

For small/medium maps, precompute all-pairs shortest paths:

```text
Floyd-Warshall or repeated Dijkstra/BFS
```

For weighted travel_time graph, use Dijkstra from each node.

For 50–200 nodes, this is acceptable when map changes, not every tick.

## 12.6. Acceptance criteria

```text
[ ] Repeated route queries hit cache.
[ ] Cache invalidates on map_revision.
[ ] Pathfinding results match old logic.
[ ] Nearest trader/shelter/anomaly queries become O(1) or near O(1).
```

---

# 13. Этап 8 — Memory indexes

## 13.1. Проблема

Memory-heavy NPC and hunt debug can become expensive if code scans full memory arrays/records.

## 13.2. Цель

Build indexes at write time.

## 13.3. Index structure

In agent runtime:

```json
{
  "memory_index": {
    "revision": 0,
    "by_kind": {},
    "by_location": {},
    "by_target": {},
    "by_source_agent": {},
    "hunt_relevant": []
  }
}
```

Global/debug index:

```json
{
  "global_memory_index": {
    "hunt_by_location": {},
    "hunt_by_target": {},
    "hunt_by_hunter": {}
  }
}
```

## 13.4. Update on `_add_memory`

When adding memory:

```python
_add_memory(...)
update_agent_memory_index(...)
update_global_hunt_index_if_relevant(...)
```

Hunt-relevant kinds:

```text
target_seen
target_last_known_location
target_intel
intel_from_stalker
intel_from_trader
target_not_found
target_location_exhausted
witness_source_exhausted
no_tracks_found
no_witnesses
target_moved
target_route_observed
target_wounded
target_combat_noise
target_death_confirmed
hunt_failed
combat_initiated
combat_resolved
```

## 13.5. Bounds

Indexes must be bounded:

```yaml
memory_index:
  max_records_per_agent_kind: 100
  max_hunt_records_per_location: 100
  max_total_global_hunt_records: 5000
```

Drop oldest / lowest importance.

## 13.6. Query API

```python
query_memory(
    agent,
    kind=None,
    location_id=None,
    target_id=None,
    since_turn=None,
    limit=20,
)
```

Should use indexes when available, fallback scan otherwise.

## 13.7. Acceptance criteria

```text
[ ] Memory queries no longer scan full memory by default.
[ ] Index updates when memory is added.
[ ] Index stays bounded.
[ ] Fallback scan works for old saves.
[ ] Hunt debug endpoints use indexes if available.
```

---

# 14. Этап 9 — Static/runtime state separation

## 14.1. Проблема

State currently mixes:

```text
static map topology
dynamic positions
memory/debug
runtime scheduled tasks
economy state
```

This makes copying, diffing, saving and serializing more expensive.

## 14.2. Цель

Split:

```text
static_state:
  locations topology
  terrain
  regions
  connections
  debug_layout
  image_url
  item definitions if needed

runtime_state:
  world time
  agents runtime
  dynamic location occupancy
  artifacts/items on ground
  traders runtime
  mutants runtime
  scheduled tasks
  combat interactions
  economy runtime
  recent events
```

## 14.3. Practical implementation

Do not necessarily split DB schema in first step.

Within `state_blob`, separate keys:

```json
{
  "static": {
    "map_revision": 1,
    "locations": {}
  },
  "runtime": {
    "state_revision": 123,
    "world_turn": 123,
    "agents": {},
    "locations_dynamic": {}
  }
}
```

Or lighter migration:

```text
Keep existing shape,
but internally build runtime indexes and avoid copying static locations.
```

## 14.4. Compatibility layer

Existing code expects:

```python
state["locations"][loc_id]
```

So a full split is large.

Recommended approach:

```text
Step 1:
  add helper getters:
    get_location_static(state, loc_id)
    get_location_dynamic(state, loc_id)
    get_location_view(state, loc_id)

Step 2:
  migrate hot paths to helper getters.

Step 3:
  later split persistent shape.
```

## 14.5. Acceptance criteria

```text
[ ] Static map data is not copied every tick.
[ ] Dynamic occupancy/artifacts/items are updated separately.
[ ] Existing projections still work.
[ ] map_revision controls static invalidation.
```

---

# 15. Recommended PR structure

This is a large optimization. It should be split into commits or sub-PRs internally.

## Commit group 1 — instrumentation

```text
1. Add TickProfiler.
2. Add metrics breakdown.
3. Add tests.
```

## Commit group 2 — dirty-set

```text
4. Add TickRuntime/dirty helpers.
5. Mark dirty in major mutation paths.
6. Build delta from dirty-set.
7. Keep old diff fallback.
```

## Commit group 3 — copy-on-write

```text
8. Introduce copy-on-write runtime state wrapper.
9. Remove full deepcopy from tick_zone_map.
10. Add mutation isolation tests.
```

## Commit group 4 — event-driven actions

```text
11. Introduce scheduled_tasks.
12. Migrate travel to ends_turn/due task.
13. Migrate explore/wait/sleep.
14. Add interruption handling.
```

## Commit group 5 — lazy needs

```text
15. Add needs_state.
16. Add derived getters.
17. Add threshold tasks.
18. Remove full hourly needs loop.
```

## Commit group 6 — Brain invalidation and budget

```text
19. Add brain_runtime cache.
20. Add invalidation helpers.
21. Add decision_queue.
22. Add AI budget.
23. Ensure urgent decisions bypass budget.
```

## Commit group 7 — caches/indexes

```text
24. Add pathfinding cache.
25. Add nearest trader/shelter cache.
26. Add memory indexes.
27. Update hunt/debug to use indexes.
```

## Commit group 8 — cleanup and docs

```text
28. Remove obsolete per-tick loops.
29. Add migration docs.
30. Add performance report template.
```

---

# 16. Performance report template

Add a generated or manual report after PR:

```text
docs/zone_stalkers_optimization/cpu_optimization_results.md
```

Required sections:

```text
Before/after:
  tick_total_ms p50/p95/p99
  npc_brain_ms p50/p95
  deepcopy_ms
  delta_build_ms
  memory_decay_ms
  location_observations_ms
  decisions_per_tick
  due_tasks_per_tick
  dirty_agents_per_tick
  dirty_locations_per_tick

Scenarios:
  10 NPC / 50 locations / 1 day
  50 NPC / 100 locations / 3 days
  100 NPC / 200 locations / 3 days
  debug on/off
  auto_tick x100/x600
```

Example table:

```text
Scenario: 50 NPC, 100 locations, 3 game days

Metric                 Before      After      Gain
tick_total_ms p50      42 ms       6 ms       7.0×
tick_total_ms p95      88 ms       12 ms      7.3×
npc_brain_ms p50       20 ms       3 ms       6.7×
deepcopy_ms p50        12 ms       0.8 ms     15×
delta_build_ms p50     4 ms        0.3 ms     13×
```

---

# 17. Testing strategy

## 17.1. Unit tests

Add tests for:

```text
TickProfiler
DirtySet
DeltaFromDirty
CopyOnWriteRuntime
ScheduledTasks
LazyNeeds
BrainInvalidation
AIBudget
PathfindingCache
MemoryIndex
```

## 17.2. Regression tests

Existing behavior must still work:

```text
NPC can travel.
NPC arrives at correct location.
NPC can find artifacts.
NPC can sell artifacts.
NPC can buy food/water.
NPC reacts to emission warning.
NPC can die from emission.
NPC can die from thirst/hunger.
Hunter NPC reacts to target intel.
```

## 17.3. Simulation tests

Add or extend headless simulation:

```bash
python tools/simulate_balance.py --seeds 20 --days 3 --npc-count 20
```

Compare:

```text
survival rate
artifact sold count
death causes
median money
decision counts
```

Before/after should be statistically similar.

## 17.4. Performance tests

Add optional pytest markers:

```python
@pytest.mark.performance
def test_tick_100_npcs_under_budget():
    ...
```

Do not make CI flaky. Use generous thresholds.

---

# 18. Risk management

## 18.1. Biggest risks

```text
1. Removing deepcopy may introduce accidental input mutation.
2. Event-driven scheduled actions may miss interruptions.
3. Lazy needs may desync UI and decision logic.
4. Brain invalidation may skip needed decisions.
5. Dirty-set may miss changed entities in delta.
6. Memory indexes may become stale.
```

## 18.2. Mitigations

```text
- Keep old full-diff delta fallback initially.
- Keep debug assertions in development:
    dirty delta vs old/new diff must match.
- Add mutation isolation tests.
- Add forced periodic full brain reevaluation:
    e.g. every 60 turns per NPC.
- Add emergency invalidation paths.
- Add resync fallback if frontend detects revision mismatch.
- Add metrics counters for skipped decisions.
```

## 18.3. Debug assertion mode

Add config:

```yaml
performance_debug:
  verify_dirty_delta_against_full_diff: false
  verify_no_input_state_mutation: false
  force_periodic_brain_check_turns: 60
```

In debug/test mode:

```python
dirty_delta = build_zone_delta_from_dirty(...)
full_delta = build_zone_delta(old_state, new_state, ...)
assert_equivalent_delta(dirty_delta, full_delta)
```

This is expensive, so only tests/debug.

---

# 19. Configuration

Add config area:

```yaml
performance:
  profiler_enabled: true

  dirty_delta_enabled: true
  dirty_delta_verify_in_debug: false

  copy_on_write_enabled: true

  scheduled_tasks_enabled: true

  lazy_needs_enabled: true

  brain_invalidation_enabled: true
  force_periodic_brain_check_turns: 60

  ai_budget:
    enabled: true
    max_normal_decisions_per_tick: 5
    max_background_decisions_per_tick: 2
    urgent_decisions_ignore_budget: true
    max_decision_delay_turns: 10

  pathfinding_cache:
    enabled: true
    precompute_all_pairs: true

  memory_index:
    enabled: true
    max_records_per_agent_kind: 100
    max_hunt_records_per_location: 100
    max_total_global_hunt_records: 5000
```

For rollout, each subsystem should be toggleable.

---

# 20. Acceptance criteria for the whole PR

## Functional

```text
[ ] Existing gameplay still works.
[ ] NPCs travel, explore, trade, eat/drink/sleep, react to emissions.
[ ] NPC Brain decisions remain semantically equivalent.
[ ] Debug UI still receives correct zone_delta.
[ ] No revision/delta regressions.
[ ] Old saves migrate.
```

## Performance

```text
[ ] tick_total_ms p50 improves at least 3× in medium scenario.
[ ] tick_total_ms p95 improves at least 3× in medium scenario.
[ ] deepcopy_ms is near zero or eliminated.
[ ] delta_build_ms is proportional to dirty entity count, not world size.
[ ] npc_brain_decision_count per tick drops significantly.
[ ] scheduled action processing is proportional to due tasks, not all scheduled agents.
```

## Safety

```text
[ ] Emergency decisions bypass AI budget.
[ ] Emission warning still interrupts relevant NPCs.
[ ] Critical needs still cause decisions/death.
[ ] No NPC is stuck forever due to skipped decisions.
[ ] Dirty-set verification passes in tests.
```

---

# 21. Minimal viable version of this PR

If the full plan is too large, the MVP should include:

```text
1. TickProfiler breakdown.
2. Dirty-set tracking.
3. Delta from dirty-set.
4. Remove full deepcopy or isolate it to a smaller runtime copy.
5. Event-driven travel actions with ends_turn.
6. Brain invalidation for obvious cases:
   - arrived
   - plan failed/completed
   - inventory changed
   - emission warning
   - critical needs
7. AI budget for normal decisions.
```

This MVP alone can produce major CPU wins.

Lazy needs, memory indexes, full static/runtime split and all-pairs path cache can be subsequent commits/PRs if needed.

---

# 22. Suggested implementation order for Copilot

Follow this order exactly:

```text
1. Add TickProfiler and metrics breakdown.
2. Add TickRuntime with dirty-set only; keep old deepcopy.
3. Change delta builder to use dirty-set, fallback to old diff.
4. Add debug verification dirty-delta vs full-diff in tests.
5. Replace full deepcopy with copy-on-write runtime.
6. Migrate travel scheduled_action to ends_turn + scheduled_tasks.
7. Migrate explore/sleep/wait to scheduled_tasks.
8. Add interruption handling for emission/combat/critical needs.
9. Add lazy needs model behind feature flag.
10. Add brain invalidation helpers.
11. Add decision queue and AI budget.
12. Add pathfinding cache.
13. Add memory indexes.
14. Add performance report doc.
```

Do not start with AI budget before profiler/dirty/copy-on-write. Without profiler and dirty-set, it will be difficult to prove gains and debug regressions.

---

# 23. Final target architecture

```text
ZoneState
  static map data mostly cold
  runtime state hot

TickRuntime
  dirty sets
  due tasks
  profiler
  decision queue
  cache refs

Tick loop
  load runtime state
  process due tasks
  process global events due this turn
  process urgent invalidations
  run limited brain decisions
  update only dirty entities
  build delta from dirty-set
  save runtime state
```

The target tick should be proportional to:

```text
number of due tasks
+ number of dirty entities
+ number of invalidated NPCs
+ number of urgent world events
```

Not proportional to:

```text
all NPCs
+ all locations
+ full memory size
+ full state size
```

This is the architectural change required to reduce CPU by 1–2 orders of magnitude while preserving NPC intelligence.
