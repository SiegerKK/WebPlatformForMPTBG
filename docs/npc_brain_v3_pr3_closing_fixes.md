# NPC Brain v3 — PR 3 Closing Fixes

> Branch: `copilot/npc-brain-v3-pr-3`  
> Base: `copilot/npc-brain-v3-pr-2`  
> Purpose: финальный список правок, которые нужно внести перед закрытием PR 3.
>
> Важно: PR 3 не должен реализовывать полноценный Objective-based decision layer.  
> Это задача PR 4.
>
> PR 3 должен сделать память рабочим инфраструктурным слоем:
>
> ```text
> legacy memory
> → memory_v3 live bridge
> → indexed retrieval
> → BeliefState adapter
> → минимальные безопасные lookup'и в текущем planner
> → brain_trace.memory_used
> ```
>
> То есть:
>
> ```text
> PR 3 = память живёт, индексируется, извлекается и минимально используется.
> PR 4 = память участвует в Objective scoring и выборе целей.
> PR 5 = память участвует в ActivePlan repair/pause/resume.
> ```

---

# 1. Current PR 3 status

Текущая ветка PR 3 уже добавила правильную основу:

```text
backend/app/games/zone_stalkers/memory/models.py
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/retrieval.py
backend/app/games/zone_stalkers/memory/legacy_bridge.py
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/decision/beliefs.py

backend/tests/decision/v3/test_memory_store.py
backend/tests/decision/v3/test_memory_retrieval.py
backend/tests/decision/v3/test_legacy_memory_bridge.py
backend/tests/decision/v3/test_memory_decay.py
backend/tests/decision/v3/test_belief_state_adapter.py
backend/tests/decision/v3/test_brain_trace_memory_used.py
```

Уже реализовано:

```text
- MemoryRecord;
- MemoryQuery;
- MemoryStore indexes;
- retrieval scoring;
- lazy import legacy memory;
- decay/consolidation skeleton;
- BeliefState adapter;
- brain_trace.memory_used function signatures;
- frontend memory stats/display skeleton.
```

Это хорошее направление.

Но PR 3 пока нельзя считать закрытым, пока `memory_v3` не обновляется live и не используется реальным decision pipeline хотя бы в минимальных безопасных местах.

---

# 2. Boundary clarification: what PR 3 should and should not do

## 2.1. PR 3 should do

PR 3 должен:

```text
1. Создать memory_v3.
2. Поддерживать индексы:
   - by_layer
   - by_kind
   - by_location
   - by_entity
   - by_item_type
   - by_tag

3. Мостить новые legacy memory entries в memory_v3.
4. Делать lazy import старой memory при пустой memory_v3.
5. Делать deterministic retrieval top-N.
6. Строить BeliefState adapter из AgentContext + MemoryStore.
7. Подключить BeliefState к текущему decision pipeline минимально.
8. Показывать memory_used в brain_trace.
9. Не ломать legacy agent["memory"].
```

## 2.2. PR 3 should NOT do

PR 3 не должен:

```text
- вводить Objective scoring;
- решать, какая цель победила, через память;
- делать ActivePlan;
- делать pause/resume/repair на основе памяти;
- реализовывать TargetBelief как полноценную охотничью модель;
- реализовывать CombatReadiness сверх того, что уже заложено в PR 2;
- делать social/reputation consequences;
- добавлять Redis/PostgreSQL/vector search.
```

Эти вещи относятся к PR 4 / PR 5 / post-PR5.

---

# 3. Mandatory fix A — live bridge from `_add_memory()` to `memory_v3`

## Problem

`legacy_bridge.py` содержит правильную функцию:

```python
bridge_legacy_entry_to_memory_v3(...)
```

и в комментариях заявляет, что она должна вызываться после каждого `_add_memory()`.

Но сейчас PR 3 в основном делает:

```text
tick start:
  ensure_memory_v3
  lazy import if memory_v3 empty
  decay
```

Этого недостаточно.

Если новые legacy memory entries не мостятся live:

```text
memory_v3 импортирует старые записи один раз,
а новые события этого NPC после import не попадают в memory_v3.
```

Тогда `memory_v3` быстро становится устаревшим.

## Required change

В центральном `_add_memory()` в `tick_rules.py` после фактического добавления новой legacy записи нужно вызвать bridge:

```python
from app.games.zone_stalkers.memory.legacy_bridge import bridge_legacy_entry_to_memory_v3

bridge_legacy_entry_to_memory_v3(
    agent_id=agent_id,
    agent=agent,
    legacy_entry=entry,
    world_turn=world_turn,
)
```

Если `_add_memory()` сейчас не принимает `agent_id`, добавить optional параметр:

```python
def _add_memory(
    agent: dict,
    world_turn: int,
    state: dict,
    memory_type: str,
    title: str,
    effects: dict,
    summary: str | None = None,
    *,
    agent_id: str | None = None,
) -> None:
    ...
```

В местах вызова, где доступен `agent_id`, передавать его.

Если где-то `agent_id` недоступен:

```python
agent_id = agent_id or agent.get("id") or agent.get("agent_id") or agent.get("name", "unknown")
```

Но для tick loop лучше передавать реальный ключ агента из `state["agents"]`.

## Merge behavior

Если `_add_memory()` не добавляет новую запись, а только merge'ит существующую, то для PR 3 MVP допустимо:

```text
bridge only newly appended entries.
```

Не нужно в этом PR решать идеальный two-way update legacy merge ↔ memory_v3.

## Tests

Добавить:

```python
def test_add_memory_bridges_new_legacy_entry_to_memory_v3():
    ...
```

Ожидание:

```text
_add_memory(... action_kind="trade_buy", item_type="bread")
→ legacy agent["memory"] updated
→ agent["memory_v3"]["records"] contains kind="item_bought"
```

Добавить:

```python
def test_add_memory_does_not_bridge_sleep_interval_applied():
    ...
```

Ожидание:

```text
action_kind="sleep_interval_applied"
→ legacy memory may exist if needed
→ memory_v3 does not get standalone MemoryRecord
```

## Priority

```text
BLOCKER for closing PR 3
```

---

# 4. Mandatory fix B — real `agent_id` in MemoryRecord

## Problem

Current bridge may use:

```python
agent.get("name", agent.get("agent_id", "unknown"))
```

This can store display name as `MemoryRecord.agent_id`.

Wrong:

```text
agent_id = "Поцик 1"
```

Correct:

```text
agent_id = "npc_123" / actual key from state["agents"]
```

This matters because future memory retrieval, debug and entity linking should use stable IDs.

## Required change

Update signatures:

```python
def bridge_legacy_entry_to_memory_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    legacy_entry: dict[str, Any],
    world_turn: int,
) -> None:
    ...
```

```python
def _map_legacy_to_record(
    *,
    agent_id: str,
    agent: dict[str, Any],
    entry: dict[str, Any],
    world_turn: int,
) -> MemoryRecord | None:
    ...
```

Then:

```python
MemoryRecord(
    agent_id=agent_id,
    ...
)
```

For lazy import:

```python
import_legacy_memory(agent, agent_id, world_turn)
```

already has `agent_id`, so it should pass it through to `_map_legacy_to_record`.

## Tests

Update legacy bridge tests:

```python
assert rec["agent_id"] == "bot1"
```

Do not accept agent display name.

## Priority

```text
HIGH
```

---

# 5. Mandatory fix C — populate `entity_ids` and `by_entity` index

## Problem

`MemoryRecord` already has:

```text
entity_ids
```

and MemoryStore has:

```text
indexes.by_entity
```

But bridge currently does not consistently fill `entity_ids`.

This is important not only for future hunt logic, but also for traders, combat and social memory.

## Required change

In legacy bridge mapping, extract stable entity ids from common effect keys:

```python
entity_ids: list[str] = []

for key in (
    "agent_id",
    "target_id",
    "target_agent_id",
    "trader_id",
    "killer_id",
    "victim_id",
    "source_agent_id",
    "other_agent_id",
):
    value = effects.get(key)
    if value:
        entity_ids.append(str(value))

entity_ids_tuple = tuple(dict.fromkeys(entity_ids))
```

Then:

```python
MemoryRecord(..., entity_ids=entity_ids_tuple)
```

Do not put human-readable names into `entity_ids`.

Names may remain in `details`.

## Tests

Add:

```python
def test_legacy_bridge_indexes_trader_entity_id():
    ...
```

Input:

```python
effects = {
    "action_kind": "trade_buy",
    "trader_id": "trader_1",
    "item_type": "bread",
}
```

Assert:

```python
assert "trader_1" in rec["entity_ids"]
assert rec_id in memory_v3["indexes"]["by_entity"]["trader_1"]
```

Add:

```python
def test_legacy_bridge_indexes_target_entity_id():
    ...
```

Input:

```python
effects = {
    "action_kind": "target_seen",
    "target_id": "agent_target_1",
}
```

Assert:

```python
assert "agent_target_1" in rec["entity_ids"]
```

## Priority

```text
HIGH
```

---

# 6. Mandatory fix D — minimal real BeliefState integration

## Problem

`BeliefState` exists and unit tests build it manually.

But PR 3 contract requires more than an isolated adapter:

```text
Planner uses MemoryStore for at least trader/item/threat lookups.
brain_trace.memory_used shows used memories.
```

PR 3 does not need Objective scoring, but it should prove that memory can influence current legacy planner safely.

## Required minimal behavior

Inside `_run_bot_decision_v2_inner()` or equivalent:

```python
ctx = build_agent_context(...)
belief_state = build_belief_state(ctx, agent, world_turn)
```

Then use `belief_state` in safe lookup fallbacks:

```text
find_trader
find_food
find_water
avoid_threat
sell_artifacts
```

Recommended minimal scope:

### 6.1. Trader lookup

If `ctx.known_traders` has no usable trader location, use:

```python
find_trader_location_from_beliefs(belief_state, agent, world_turn)
```

### 6.2. Food/water lookup

If inventory/trader direct path fails and planner needs a known source, use:

```python
find_food_source_from_beliefs(...)
find_water_source_from_beliefs(...)
```

### 6.3. Threat lookup

Use `belief_state.known_threats` only as debug/context in PR 3. Do not overbuild avoidance scoring yet.

## `memory_used`

When a BeliefState helper returns a memory-backed result, produce memory_used item:

```python
{
    "id": memory_id,
    "kind": kind,
    "summary": summary,
    "confidence": confidence,
    "used_for": "find_trader" | "find_food" | "find_water" | "avoid_threat" | "sell_artifacts",
}
```

Pass it into:

```python
write_decision_brain_trace_from_v2(..., memory_used=memory_used)
```

## Required tests

Add an integration test:

```python
def test_decision_pipeline_uses_memory_v3_trader_lookup_and_writes_memory_used():
    ...
```

Setup:

```text
- no visible trader in current AgentContext;
- memory_v3 has trader_location_known at loc_trader;
- NPC needs resupply / sell_artifacts;
```

Expected:

```text
- decision/plan uses remembered trader location;
- brain_trace.events[-1].memory_used contains used_for="find_trader";
```

Add:

```python
def test_decision_pipeline_uses_memory_v3_water_source_when_no_visible_water():
    ...
```

Expected:

```text
- remembered water source can be used as target;
- memory_used contains used_for="find_water".
```

## Priority

```text
HIGH
```

---

# 7. Mandatory fix E — update `last_accessed_turn` on retrieval

## Problem

`MemoryRecord.last_accessed_turn` exists, but retrieval currently returns records without updating stored record access time.

That makes the field meaningless for:

```text
decay
debug
retention
future memory stats
```

## Required change

In `retrieve_memory()`:

```python
selected_ids = [record.id for ... top scored results ...]

for rid in selected_ids:
    if rid in records_raw:
        records_raw[rid]["last_accessed_turn"] = world_turn

return [MemoryRecord.from_dict(records_raw[rid]) for rid in selected_ids]
```

## Tests

Add:

```python
def test_retrieve_memory_updates_last_accessed_turn():
    ...
```

Expected:

```python
retrieve_memory(agent, query, world_turn=123)
assert agent["memory_v3"]["records"][record_id]["last_accessed_turn"] == 123
```

## Priority

```text
MEDIUM/HIGH
```

---

# 8. Mandatory fix F — PR 2 carry-over: non-critical seek_food/seek_water must not become long wait

## Problem

Latest post-PR2 NPC log showed:

```text
seek_food 41%
plan_step = wait
```

and:

```text
seek_water 34%
plan_step = wait
```

This happened after PR 2 soft-consume threshold fix.

The logic is now:

```text
hunger/thirst not high enough to consume item
but high enough to win legacy intent selection
→ planner refuses consume
→ plan becomes wait
```

That is bad because it blocks useful behavior.

## Required fix

Suppress legacy `eat` / `drink` candidates below soft consume threshold unless there is an actual `ImmediateNeed`.

Suggested constants:

```python
SOFT_CONSUME_FOOD_THRESHOLD = 50
SOFT_CONSUME_DRINK_THRESHOLD = 40
```

### In `select_intent()`

When building candidate list:

```python
has_food_immediate = any(
    n.key == "eat_now" and n.trigger_context in ("survival", "rest_preparation")
    for n in need_result.immediate_needs
)

has_drink_immediate = any(
    n.key == "drink_now" and n.trigger_context in ("survival", "rest_preparation")
    for n in need_result.immediate_needs
)

if kind == "eat" and not has_food_immediate and hunger < SOFT_CONSUME_FOOD_THRESHOLD:
    skip candidate

if kind == "drink" and not has_drink_immediate and thirst < SOFT_CONSUME_DRINK_THRESHOLD:
    skip candidate
```

Do not mutate `NeedScores`; just filter candidates.

Critical cases must still work:

```text
critical hunger → seek_food
critical thirst → seek_water
rest_preparation hunger/thirst → prepare_sleep_food/drink
```

## Tests

Add:

```python
def test_noncritical_thirst_below_soft_threshold_does_not_select_seek_water_if_get_rich_available():
    ...
```

Add:

```python
def test_noncritical_hunger_below_soft_threshold_does_not_select_seek_food_if_get_rich_available():
    ...
```

Add:

```python
def test_critical_thirst_still_selects_seek_water():
    ...
```

Add:

```python
def test_rest_preparation_thirst_still_allows_sleep_preparation_drink():
    ...
```

## Priority

```text
HIGH
```

This is technically a PR 2 carry-over, but since PR 3 is now the active branch, apply it here before closing PR 3.

---

# 9. Pre-PR5 hunt prerequisites check

The file `docs/npc_brain_v3_pre_pr5_hunt_prerequisites.md` is not currently present in this PR 3 branch.

Still, its PR 3-relevant requirements should be handled before PR 3 closes.

## 9.1. Already covered or mostly covered

### Memory layers

`memory/models.py` already defines:

```text
working
episodic
semantic
spatial
social
threat
goal
```

This satisfies the basic layer requirement.

### Entity index structure

MemoryStore already has:

```text
indexes.by_entity
```

But this is only useful after bridge fills `entity_ids`.

So:

```text
structure exists;
live data population still needs fix C.
```

### brain_trace.memory_used shape

`brain_trace.py` now has `memory_used` parameter and caps memory_used to 5 entries.

This is good.

## 9.2. Missing PR 3 hunt prerequisite: target-related memory kinds

Add support for these memory kinds in PR 3 bridge/memory taxonomy:

```text
target_seen
target_last_known_location
target_not_found
target_route_observed
target_equipment_seen
target_combat_strength_observed
target_death_confirmed
target_intel
```

This does NOT mean implementing TargetBelief or hunt logic now.

It only means MemoryStore can store/retrieve these records when future PRs start writing them.

### Required mapping

In `legacy_bridge.py`, add mappings:

```python
_ACTION_KIND_MAP.update({
    "target_seen": (
        LAYER_SOCIAL,
        "target_seen",
        ("target", "tracking", "social"),
    ),
    "target_last_known_location": (
        LAYER_SPATIAL,
        "target_last_known_location",
        ("target", "tracking", "spatial"),
    ),
    "target_not_found": (
        LAYER_SPATIAL,
        "target_not_found",
        ("target", "tracking", "negative_observation"),
    ),
    "target_route_observed": (
        LAYER_SPATIAL,
        "target_route_observed",
        ("target", "route", "tracking"),
    ),
    "target_equipment_seen": (
        LAYER_THREAT,
        "target_equipment_seen",
        ("target", "equipment", "combat"),
    ),
    "target_combat_strength_observed": (
        LAYER_THREAT,
        "target_combat_strength_observed",
        ("target", "combat", "threat"),
    ),
    "target_death_confirmed": (
        LAYER_THREAT,
        "target_death_confirmed",
        ("target", "death", "confirmed"),
    ),
    "target_intel": (
        LAYER_SOCIAL,
        "target_intel",
        ("target", "intel", "social"),
    ),
})
```

### Required tests

Add:

```python
def test_target_seen_memory_kind_supported_and_indexed_by_entity():
    ...
```

Add:

```python
def test_target_not_found_memory_kind_supported():
    ...
```

Add:

```python
def test_target_death_confirmed_memory_kind_supported():
    ...
```

Expected:

```text
- correct kind;
- correct layer;
- tags contain target/tracking/death etc.;
- target_id appears in entity_ids;
- by_entity index contains target id.
```

## 9.3. Missing PR 3 hunt prerequisite: target-related `used_for` values

`memory_used.used_for` should allow future hunt contexts:

```text
locate_target
track_target
prepare_for_hunt
engage_target
confirm_kill
```

No strict enum is required if `used_for` is free-form.

But add tests to ensure these shapes are accepted/displayed:

```python
def test_brain_trace_memory_used_accepts_target_used_for_values():
    ...
```

## 9.4. Missing PR 3 hunt prerequisite: retention guidance

In `decay.py`, high-value target memories should be protected by layer/importance.

For PR 3 MVP, do not overcomplicate. Just ensure:

```text
target_death_confirmed
target_equipment_seen
target_combat_strength_observed
```

are mapped to `threat` or high importance, so existing decay protection keeps them longer.

For `target_seen` / `target_not_found`, medium retention is enough.

## 9.5. Do NOT implement in PR 3

Do not implement yet:

```text
TargetBelief
CombatReadiness changes beyond PR 2
HUNT_TARGET objective decomposition
track/ambush/intercept objectives
plan repair when target moves
social revenge/reputation
```

Those remain for PR 4/PR 5/post-PR5.

---

# 10. Optional fix G — consolidation index consistency

## Problem

`decay.py` consolidation updates existing semantic records in-place and may mutate indexed fields like `tags`.

If indexed fields change but indexes are not updated, indexes can become stale.

## Minimal fix

Either:

### Option A

When updating existing semantic record, do not mutate indexed fields:

```text
do not change tags/location_id/entity_ids/item_types
```

Only update:

```text
confidence
importance
summary
details
```

### Option B

If mutating indexed fields, deindex and reindex.

Recommended for PR 3 simplicity:

```text
Option A.
```

## Priority

```text
Optional / Medium
```

---

# 11. Optional fix H — memory order for merged legacy entries

## Problem

Older logs showed merged legacy memories whose `world_turn` was updated while their list position stayed old.

This can make UI/debug order confusing.

## Recommended fix

For legacy merge:

```text
do not rewrite world_turn of existing memory entry.
```

Instead update:

```text
effects.last_seen_turn
effects.times_seen
status
```

## Priority

```text
Optional / Low
```

PR 3 MemoryStore makes this less important, but it is still a nice cleanup.

---

# 12. Final PR 3 closing checklist

Before closing PR 3:

```text
[ ] _add_memory live-bridges new entries into memory_v3.
[ ] sleep_interval_applied is not stored as standalone MemoryRecord.
[ ] MemoryRecord.agent_id uses real state agent id.
[ ] entity_ids are populated from effects and by_entity index works.
[ ] target_* memory kinds are supported as PR 3 hunt prerequisite.
[ ] retrieve_memory updates last_accessed_turn.
[ ] BeliefState is built in the real bot decision pipeline.
[ ] Planner uses BeliefState/MemoryStore for at least one real memory-backed lookup:
    - find_trader
    - find_food
    - find_water
    - avoid_threat
    - sell_artifacts
[ ] brain_trace.memory_used appears for memory-backed decisions.
[ ] target-related memory_used.used_for values are accepted.
[ ] non-critical seek_food/seek_water below soft threshold no longer turns into wait.
[ ] PR 1 tests still pass.
[ ] PR 2 tests still pass.
[ ] PR 3 tests pass.
```

---

# 13. Recommended implementation order

1. Add `agent_id` parameter to bridge and `_add_memory`.
2. Wire `_add_memory()` live bridge.
3. Populate `entity_ids`.
4. Add target_* memory mappings and tests.
5. Update `retrieve_memory()` to write `last_accessed_turn`.
6. Build `BeliefState` in real decision pipeline.
7. Add one memory-backed planner lookup with `memory_used`.
8. Apply non-critical `seek_food/seek_water` wait fix.
9. Add/adjust tests.
10. Optional: consolidation index cleanup.

---

# 14. Definition of “ready to close PR 3”

PR 3 is ready when this is true:

```text
MemoryStore is not just a set of classes.
It is live, indexed, retrieved, minimally used, and visible in brain_trace.
```

But this should still remain true:

```text
PR 3 does not choose goals through memory.
PR 3 only gives PR 4 a clean BeliefState + memory retrieval layer.
```

