# Zone Stalkers — Performance Optimization Plan v2 после PR6

> Основано на документе `zone_stalkers_performance_optimization_plan.md` и актуальном состоянии ветки `copilot/implement-pr-6-new-mechanics`.
>
> Цель:
>
> ```text
> 1. Проверить адекватность исходного optimization plan.
> 2. Адаптировать его под последние изменения PR6:
>    - HuntLead / TargetBelief.possible_locations;
>    - hunt_search_by_agent;
>    - location_hunt_traces;
>    - debug-map overlays;
>    - compact export hunt_search.
> 3. Зафиксировать pre-optimization correctness blockers.
> 4. Разбить оптимизацию на безопасные PR-этапы.
> ```

---

# 0. Executive summary

Исходный документ в целом правильный: главные направления выбраны верно.

```text
projection
→ static/dynamic split
→ WebSocket delta
→ lazy debug endpoints
→ CPU profiling
→ brain_trace/memory/observation throttling
→ decision invalidation
→ pathfinding cache
```

Но после PR6 нужно обязательно учесть новый слой данных:

```text
debug.hunt_search_by_agent
debug.location_hunt_traces
agent.brain_v3_context.hunt_target_belief
possible_locations
likely_routes
exhausted_locations
combat_hunt_events
```

Эти данные полезны для debug-карты, но если отправлять их на каждый tick всем клиентам, они станут новым источником сетевого и CPU-раздувания.

Главная правка к исходному плану:

```text
PR6 hunt/debug data must be treated as debug/warm/cold data,
not as always-hot game data.
```

---

# 1. Проверка текущих PR6-правок перед оптимизацией

## 1.1. Что сделано хорошо

В ветке PR6 уже есть хорошие изменения:

```text
- HuntLead model;
- TargetBelief.possible_locations / likely_routes / exhausted_locations;
- staged target_not_found suppression;
- source_agent_id берётся из details.source_agent_id / witness_id / trader_id;
- look_for_tracks больше не обязан читать реальную location_id цели напрямую;
- debug.hunt_search_by_agent;
- debug.location_hunt_traces;
- Location profile Hunt Traces;
- map overlay state;
- compact export hunt_search.
```

Это значит: PR6 уже добавил полноценный слой debug visibility для поиска цели.

## 1.2. Статус correctness gate по коду

После follow-up правок correctness gate закрыт: ниже перечислены уже реализованные пункты.

### 1.2.1. `search_target found target` останавливает `VERIFY_LEAD`

Ранее в runtime был риск:

```text
VERIFY_LEAD:
  search_target finds target
  → plan.advance()
  → look_for_tracks
  → question_witnesses
```

Сейчас действует явное runtime-правило:

```text
if objective in {VERIFY_LEAD, TRACK_TARGET, PURSUE_TARGET}
and step == search_target
and target_found:
  complete/abort current ActivePlan with reason target_found
  next decision should select ENGAGE_TARGET
```

Что закрывает баг-сценарий:

```text
нашёл цель
→ пошёл искать следы
→ пошёл спрашивать свидетелей
→ занялся другой целью
```

### 1.2.2. Есть explicit outcome от `search_target`

`_exec_search_target()` выставляет:

```python
step.payload["_target_found"] = True
step.payload["_hunt_step_outcome"] = "target_found"
step.payload["_target_id"] = target_id
step.payload["_target_location_id"] = current_location_id
```

и runtime использует это для раннего завершения hunt active plan.

### 1.2.3. Добавлен short-term recent target contact

Если после `target_seen` следующий decision tick уже не считает цель `visible_now`, TargetBelief должен всё равно помнить свежий контакт.

Добавлены поля:

```python
recently_seen: bool
recent_contact_turn: int | None
recent_contact_location_id: str | None
recent_contact_age: int | None
```

TTL:

```text
RECENT_TARGET_CONTACT_TURNS = 5–15
```

`ENGAGE_TARGET` генерируется при:

```text
visible_now
or co_located
or recently_seen at current location
```

### 1.2.4. `no_witnesses` exhaust/cooldown-ит источник

Ранее существовал риск loop:

```text
GATHER_INTEL
→ question_witnesses
→ no_witnesses
→ GATHER_INTEL
→ question_witnesses
```

Реализован memory event:

```text
witness_source_exhausted
```

Payload:

```json
{
  "action_kind": "witness_source_exhausted",
  "target_id": "...",
  "location_id": "...",
  "source_kind": "location_witnesses",
  "cooldown_until_turn": 12345
}
```

Objective/planner не повторяет тот же witness source до конца cooldown.

### 1.2.5. `possible_locations` не содержит zero-confidence locations

Ранее PR6 мог оставлять locations with:

```text
confidence = 0
probability = 0
```

inside `possible_locations`.

Сейчас:

```text
possible_locations:
  confidence > 0.05

rejected_locations:
  confidence = 0 / exhausted / fully suppressed
```

Это важно и для debug UI, и для размера payload.

### 1.2.6. Route hints игнорируют exhausted destinations

Если route destination exhausted:

```text
route.to_location_id in exhausted_locations
```

то применяется:

```text
drop route
```

or:

```text
route.confidence *= 0.1
```

Route hints не толкают NPC к exhausted/zero-confidence destinations.

### 1.2.7. Hunt source_refs предпочитают target lead memories

For hunt objectives:

```text
GATHER_INTEL
VERIFY_LEAD
TRACK_TARGET
ENGAGE_TARGET
CONFIRM_KILL
```

source refs should prefer:

```text
target_intel
target_seen
target_last_known_location
target_moved
target_route_observed
target_not_found
```

`active_plan_*` lifecycle records исключены из приоритетных source refs.

---

# 2. Оценка исходного optimization plan

## 2.1. Что в исходном плане правильно

Исходный документ правильно диагностирует две главные проблемы:

```text
1. Frontend получает слишком большой state слишком часто.
2. Backend tick делает слишком много полной работы каждый ход.
```

Также верно предложено разделение данных:

```text
static data
hot dynamic data
warm detailed data
cold debug/heavy data
```

И верная целевая модель:

```text
authoritative backend state
→ compact projection/delta
→ frontend local store
→ lazy endpoints for heavy details
```

Это правильная архитектура для симуляции с растущими memory/brain/debug данными.

## 2.2. Что нужно адаптировать после PR6

После PR6 появились новые debug-heavy fields:

```text
state.debug.hunt_search_by_agent
state.debug.location_hunt_traces
brain_v3_context.hunt_target_belief.possible_locations
brain_v3_context.hunt_target_belief.likely_routes
brain_v3_context.hunt_target_belief.exhausted_locations
```

Их нужно классифицировать так:

| Данные | Тип | Передача |
|---|---|---|
| best_location_id/confidence | warm/debug summary | debug projection / selected NPC |
| possible_locations top N | warm/debug summary | debug projection / selected NPC |
| likely_routes top N | warm/debug summary | debug projection / selected NPC |
| exhausted_locations | warm/debug summary | debug projection / selected NPC |
| location_hunt_traces | debug-heavy | only debug map mode / throttled |
| combat_hunt_events | debug-heavy | only debug mode / paginated |
| raw memory_v3 hunt records | cold | lazy endpoint |

---

# 3. Updated data classification after PR6

## 3.1. Hot dynamic data

These may be sent through lite projection / delta:

```text
world_turn
world time
agent.location_id
agent.hp
agent.hunger/thirst/sleepiness
agent.money
agent.is_alive
agent.has_left_zone
agent.scheduled_action summary
agent.active_plan_summary
locations_dynamic agents/items/artifacts counts
```

## 3.2. Warm debug summary

Send only in debug projection or selected-agent profile:

```text
agent.current_objective
agent.current_intent
active_plan_summary
hunt_search summary:
  target_id
  best_location_id
  best_location_confidence
  top 3–5 possible_locations
  top 3–5 likely_routes
  exhausted_locations
  lead_count
```

## 3.3. Cold/heavy debug

Never send during normal auto tick:

```text
memory
memory_v3.records
brain_trace.events
full active_plan_v3
objective ranking alternatives
full hunt traces by all locations
full combat_hunt_events
full location_hunt_traces
raw story timeline
full inventory for all NPCs
```

## 3.4. Static data

Send once or when `map_revision` changes:

```text
locations topology
connections
terrain
regions
debug_layout
item definitions
```

---

# 4. Updated network plan

## 4.1. Add projection modes

Recommended modes:

```text
game
debug-lite
debug-map
full
```

### game

For normal game UI.

Must exclude:

```text
memory
memory_v3
brain_trace
active_plan_v3 full
brain_v3_context full
state.debug.hunt_search_by_agent
state.debug.location_hunt_traces
debug layout if static map already loaded
```

May include:

```text
active_plan_summary
equipment_summary
inventory_summary
```

### debug-lite

For agent list / selected agent summary.

May include selected or summarized:

```text
brain_v3_context.latest_decision_summary
hunt_search top N
active_plan_summary
```

Must still exclude:

```text
full memory_v3 records
full brain_trace.events
full location_hunt_traces for all locations
```

### debug-map

For debug map page.

May include:

```text
locations_dynamic
agents lite
debug.hunt_search_by_agent summary
debug.location_hunt_traces summary
```

Should be throttled and bounded:

```text
max traces per location
max routes per location
max possible_locations per agent
freshness cutoff
```

### full

Manual only.

---

# 5. Updated WebSocket delta plan

## 5.1. Normal `zone_delta`

Should include hot dynamic changes only:

```text
world time
agent positions
scheduled_action changes
hp/needs/money
location membership counts
recent visible event summaries
```

Should not include:

```text
memory_v3
brain_trace.events
location_hunt_traces
full hunt_search_by_agent
```

## 5.2. Debug delta

Optional later:

```text
zone_debug_delta
```

For debug map only, throttled:

```text
max 2–4/sec
only when debug map open
only for selected hunter/target or visible map area
```

Do not implement this before basic `zone_delta`; it is optional.

---

# 6. Updated CPU optimization plan after PR6

## 6.1. New CPU risk: hunt debug aggregation

`build_hunt_debug_payload()` can be expensive if it scans:

```text
all agents
all memory_v3 records
all locations
all target beliefs
```

every tick.

Need rules:

```text
1. Build full hunt debug only if debug map is open or debug mode enabled.
2. For normal game projection, skip full location_hunt_traces.
3. Bound records by freshness/limit.
4. Cache per-agent hunt_search summary when no relevant memory/location changed.
```

## 6.2. Agent-level hunt belief caching

`TargetBelief` can be cached by signature:

```text
agent_id
target_id
world_turn bucket
agent.location_id
target visible/co-located signature
memory_v3_revision
debug_omniscient_targets
```

Recommended agent fields:

```json
{
  "memory_v3_revision": 123,
  "hunt_belief_cache": {
    "target_id": "agent_debug_0",
    "revision": 123,
    "world_turn": 4567,
    "belief": {...}
  }
}
```

Do not cache if:

```text
target visible now
combat just happened
memory changed this tick
location changed this tick
```

## 6.3. Location hunt traces indexing

Instead of scanning all memory every tick, later add incremental index:

```text
state.debug_indexes.location_hunt_trace_index
```

or:

```text
state.runtime.hunt_trace_index
```

Update it when `_add_memory()` writes hunt-relevant events:

```text
target_seen
target_not_found
target_moved
target_route_observed
target_intel
target_location_exhausted
```

This can be later optimization; first measure.

## 6.4. Memory decay/staleness after PR6

Do not decay/staleness update all hunt leads every tick.

Run:

```text
on decision
on arrival
on target memory write
periodically every N turns
```

This is especially important because PR6 added many trace records:

```text
target_not_found
no_tracks_found
no_witnesses
target_route_observed
target_moved
```

---

# 7. Updated performance telemetry

Add counters specifically for PR6:

```text
hunt_belief_build_ms
hunt_debug_payload_ms
hunt_trace_records_scanned
hunt_trace_records_returned
hunt_search_by_agent_size_bytes
location_hunt_traces_size_bytes
possible_locations_count
likely_routes_count
exhausted_locations_count
```

Add to state-size endpoint:

```json
{
  "debug_hunt_search_bytes": 120000,
  "location_hunt_traces_bytes": 90000,
  "brain_v3_context_bytes": 180000,
  "memory_v3_bytes": 600000
}
```

Add frontend perf panel:

```text
Hunt debug payload KB
Hunt traces locations count
Hunt overlay mode
Debug map refresh rate
```

---

# 8. Updated implementation roadmap

## Optimization PR 0 — correctness gate before optimization

Status: closed (реализовано в follow-up перед optimization PR 1).

Gameplay correctness issues:

```text
[x] search_target found target produces explicit outcome.
[x] VERIFY_LEAD/TRACK_TARGET stops after target_found.
[x] Next objective after target_found is ENGAGE_TARGET if combat-ready.
[x] Soft needs do not override visible/recent target contact.
[x] no_witnesses creates witness_source_exhausted/cooldown.
[x] possible_locations excludes zero-confidence entries or moves them to rejected_locations.
[x] route_hints ignore exhausted/zero-confidence destinations.
[x] hunt objective source_refs prefer target lead memories over active_plan lifecycle.
```

Tests:

```text
test_verify_lead_stops_after_search_target_finds_target
test_target_seen_next_objective_is_engage_target
test_soft_thirst_does_not_override_visible_kill_target
test_no_witnesses_exhausts_gather_intel_source
test_zero_confidence_locations_not_in_possible_locations
test_route_hints_ignore_exhausted_destination
test_verify_lead_source_refs_use_target_intel_not_active_plan_lifecycle
```

PR 0 больше не блокирует оптимизацию: можно переходить к PR 1.

---

## Optimization PR 1 — baseline measurement and state-size diagnostics

Implement:

```text
tick profiler
response size logging
state-size endpoint
performance endpoint
frontend Performance panel MVP
```

Must measure:

```text
tick_total_ms
deepcopy_ms
decision_total_ms
memory_v3_ms
hunt_belief_build_ms
hunt_debug_payload_ms
serialization_ms
state_size_bytes
zone_lite_size_bytes
debug_map_size_bytes
```

Acceptance:

```text
Can run 100 ticks and see where time/size goes.
Can compare full/debug/game projections.
```

---

## Optimization PR 2 — projection and stripping

Implement:

```text
zone-lite projection
projection=game/debug-map/full
heavy field stripping
lazy detail endpoints skeleton
```

Critical PR6 rule:

```text
game projection must not include:
  debug.hunt_search_by_agent
  debug.location_hunt_traces
  full brain_v3_context
  memory_v3
  brain_trace.events
```

Debug map projection may include bounded:

```text
hunt_search_by_agent
location_hunt_traces
```

Acceptance:

```text
zone-lite is 5–20x smaller than full state.
normal auto tick no longer transfers memory_v3/brain_trace/hunt_traces.
debug map still has Hunt Traces when debug projection is requested.
```

---

## Optimization PR 3 — static/dynamic map split

Implement:

```text
map_revision
state_revision
zone-map-static
zone-map-dynamic
frontend static map cache
```

PR6-specific note:

```text
debug_layout and static locations should not be resent every tick.
hunt traces are dynamic debug data, not static map data.
```

Acceptance:

```text
static map loads once.
dynamic state updates without resending locations topology.
map edit increments map_revision.
```

---

## Optimization PR 4 — WebSocket delta

Implement:

```text
ZoneDelta
base_revision/revision
frontend reducer
resync on mismatch
remove refresh-on-every-tick
```

Delta should include only changed hot fields.

PR6-specific note:

```text
Do not put full location_hunt_traces into normal zone_delta.
```

If needed later:

```text
zone_debug_delta for selected debug overlay only.
```

Acceptance:

```text
auto tick uses WS deltas, not full HTTP refresh.
average normal delta is small.
debug map can still manually/lazily fetch hunt traces.
```

---

## Optimization PR 5 — CPU quick wins

Implement:

```text
brain_trace off by default / selected agents only
memory decay/staleness periodic/on decision
observations event-driven
hot recent_events limit
hunt debug build only when debug projection requested
```

Acceptance:

```text
tick_total_ms decreases.
state size stops growing from traces/events.
scheduled travel NPCs are cheap.
```

---

## Optimization PR 6 — deep CPU scaling

Implement after measurement:

```text
copy-on-write or in-place tick
pathfinding cache by map_revision
decision invalidation flags
AI decision scheduler / max decisions per tick
hunt belief cache
location hunt trace index
```

Acceptance:

```text
50+ NPC scenario remains stable.
urgent events still immediate.
debug correctness tests pass.
```

---

# 9. Revised endpoint list

## 9.1. Snapshots/projections

```text
GET /contexts/{context_id}/zone-lite
GET /contexts/{context_id}/zone-lite?projection=game
GET /contexts/{context_id}/zone-lite?projection=debug-map
GET /contexts/{context_id}/zone-map-static
GET /contexts/{context_id}/zone-map-dynamic
GET /contexts/{context_id}/debug/full-state
```

## 9.2. Agent details

```text
GET /contexts/{context_id}/agents/{agent_id}/profile
GET /contexts/{context_id}/agents/{agent_id}/memory
GET /contexts/{context_id}/agents/{agent_id}/brain-trace
GET /contexts/{context_id}/agents/{agent_id}/active-plan
GET /contexts/{context_id}/agents/{agent_id}/inventory
```

## 9.3. Hunt/debug details

```text
GET /contexts/{context_id}/debug/hunt-search/agents/{agent_id}
GET /contexts/{context_id}/debug/hunt-search/locations/{location_id}
GET /contexts/{context_id}/debug/hunt-search/targets/{target_id}
```

## 9.4. Performance

```text
GET /contexts/{context_id}/debug/performance
GET /contexts/{context_id}/debug/state-size
POST /contexts/{context_id}/debug/performance/clear
POST /contexts/{context_id}/debug/benchmark-ticks
```

---

# 10. Updated acceptance criteria for optimization

## Network

For 10–20 NPC:

```text
normal game projection:
  no memory_v3
  no brain_trace.events
  no location_hunt_traces
  no full brain_v3_context
  average WS delta <= 1–10 KB after delta PR

debug map:
  bounded hunt_search payload
  static map cached
  heavy traces lazy/throttled
```

## CPU

```text
hunt_debug_payload_ms measured
hunt_belief_build_ms measured
brain_trace default off or bounded
memory decay not every tick
observations event-driven
pathfinding cached
deepcopy measured and later removed if significant
```

## Functionality

Must preserve:

```text
Brain v3 decisions
PR6 hunt search
debug map Hunt Traces
NPC profile Hunt Search
compact export hunt_search
agent profile memory/brain trace on demand
```

---

# 11. Risks added by PR6

## 11.1. Debug payload explosion

`location_hunt_traces` can grow with every:

```text
target_not_found
no_tracks_found
target_moved
target_route_observed
no_witnesses
```

Mitigation:

```text
limits
freshness cutoff
projection gating
lazy endpoints
```

## 11.2. CPU from all-agent memory scans

Building location traces by scanning all `memory_v3` records every tick may become expensive.

Mitigation:

```text
measure first
debug-only build
cache / incremental index later
```

## 11.3. Breaking debug map by stripping too much

If projections strip `brain_v3_context` without adding lazy/debug hunt endpoints, debug map loses PR6 visibility.

Mitigation:

```text
debug-map projection
hunt-search endpoints
frontend fallback messaging
```

---

# 12. Minimal first optimization PR

Do this first after PR6 correctness gate (gate already closed):

```text
1. Add tick profiler.
2. Add response-size logging.
3. Add /debug/state-size.
4. Add /debug/performance.
5. Add game/debug-map/full projection functions.
6. Ensure game projection strips:
   memory, memory_v3, brain_trace, full active_plan_v3,
   full brain_v3_context, debug.hunt_search_by_agent,
   debug.location_hunt_traces.
7. Ensure debug-map projection keeps bounded hunt_search/hunt_traces.
8. Add frontend Performance panel MVP.
```

This gives immediate visibility and reduces risk before larger delta/static split work.

---

# 13. Final recommendation

Do not start with WebSocket delta immediately.

Recommended order:

```text
0. PR6 correctness gate closed.
1. Measurement/profiling.
2. Projections/stripping.
3. Static/dynamic split.
4. WebSocket delta.
5. CPU quick wins.
6. Deep CPU scaling.
```

Reason:

```text
Delta is useful only after we know what should be in the projected state.
If we delta a bad/heavy state, we only make a complicated bad system.
```

The original optimization plan is directionally correct, but after PR6 the most important adaptation is:

```text
PR6 hunt/search debug data must be explicitly projection-gated,
bounded, measured, and lazy-loadable.
```
