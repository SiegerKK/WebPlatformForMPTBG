# Zone Stalkers — NPC Logic and Memory Correctness Fixes after CPU PR5

## Purpose

This document describes a systematic cleanup after CPU PR5 memory optimization.

CPU PR5 improved performance significantly:

```text
Before:
  10 NPCs could drop Effective speed to x45 or lower.

After:
  10 NPCs are around x150–x200.
```

However, NPC logs now show semantic correctness issues in NPC logic and `memory_v3`.

This document is **not** another raw performance PR.

The goal is:

```text
Make NPC logic and memory semantically correct after the memory_v3-only architecture.
```

## Scope

In scope:

```text
1. Normalize agent death state.
2. Stop writing low-value ActivePlan lifecycle noise into memory_v3.
3. Fix UNKNOWN objective keys in memory records.
4. Fix WAIT_IN_SHELTER / emission loop behavior.
5. Prevent dead agents from receiving new semantic memory.
6. Deduplicate repeated observations.
7. Rename frontend/export legacy "memory" timeline field if it is not canonical memory.
8. Add tests to prevent regression.
```

Out of scope:

```text
- New large CPU optimization pass.
- Vector memory.
- External memory DB.
- Full Brain v4 rewrite.
- Balancing NPC economy/combat difficulty.
- Reintroducing legacy agent["memory"].
```

---

# 1. Observed problems from NPC logs

## 1.1. Dead NPCs can still have HP > 0

Observed examples:

```text
agent_debug_6:
  is_alive = false
  hp = 92
  current_goal = emergency_shelter
  adapter_intent = flee_emission

agent_debug_9:
  is_alive = false
  hp = 93
  current_goal = emergency_shelter
  adapter_intent = flee_emission
```

Memory contains death records such as:

```text
kind = death
cause = emission
summary = "Погиб от выброса ..."
```

This means death happened, but state was not normalized.

Correct invariant:

```text
is_alive == false
⇒ hp == 0
⇒ scheduled_action == None
⇒ action_queue == []
⇒ active_plan_v3 == None
⇒ current_goal == "dead" or None
⇒ no queued Brain decision
```

---

## 1.2. memory_v3 is capped but polluted by ActivePlan lifecycle noise

PR5 correctly caps `memory_v3` at 500 records, but logs show the cap is often filled mostly by:

```text
active_plan_created
active_plan_completed
active_plan_step_started
active_plan_step_completed
```

Example pattern:

```text
records_count = 500
goal records ≈ 460+
active_plan_created ≈ 200+
active_plan_completed ≈ 200+
semantic facts ≈ 20–30
threat records ≈ 0–5
```

This means `memory_v3` is technically bounded but semantically unhealthy.

`memory_v3` should not be a plan execution log.

Correct model:

```text
memory_v3:
  NPC knowledge, facts, important experiences, threats, target intel, learned locations

brain_trace / GameEvent / debug timeline:
  technical execution history and low-level plan lifecycle
```

---

## 1.3. Important decisions are recorded with objective_key = UNKNOWN

Observed cases:

```text
adapter_intent_kind = flee_emission
objective_key = UNKNOWN

adapter_intent_kind = wait_in_shelter
objective_key = UNKNOWN
```

This pollutes memory and makes debug history less useful.

Correct mapping should be:

```text
flee_emission      → REACH_SAFE_SHELTER
wait_in_shelter    → WAIT_IN_SHELTER
seek_water         → RESTORE_WATER
seek_food          → RESTORE_FOOD
rest               → REST
heal_self          → HEAL_SELF
escape_danger      → HEAL_SELF or ESCAPE_DANGER if that objective exists
leave_zone         → LEAVE_ZONE
```

---

## 1.4. WAIT_IN_SHELTER can cause repeated Brain/Plan churn

Current behavior appears to be:

```text
emission active
→ Brain chooses WAIT_IN_SHELTER
→ active plan is created
→ wait step completes quickly
→ active plan completed
→ next tick Brain chooses WAIT_IN_SHELTER again
→ memory_v3 gets active_plan_created/completed spam
```

A key cause is likely:

```python
if objective_key in {"ENGAGE_TARGET", "REACH_SAFE_SHELTER", "WAIT_IN_SHELTER"}:
    return world_turn
```

That makes `WAIT_IN_SHELTER` immediately expire every tick.

Correct behavior:

```text
WAIT_IN_SHELTER should stay valid until the emission ends
or at least for a short stable interval.
```

---

## 1.5. Dead agents can still receive semantic memory updates

A dead NPC can have:

```text
death record at turn N
semantic record created/updated at turn N + X
```

This likely happens because memory decay/consolidation runs for dead agents and creates semantic records from old episodic records.

Correct behavior:

```text
Dead agents should not receive new semantic memory.
```

Allowed:

```text
- keep existing memory
- optionally archive/decay old records if needed
```

Not allowed:

```text
- create new semantic facts after death
- update last_accessed_turn due to retrieval
- continue plan/brain memory writes after death
```

---

## 1.6. Export/debug JSON still exposes a root "memory" field

After PR5, canonical NPC memory is `memory_v3`.

If frontend exports still include:

```json
"memory": []
```

this is misleading.

It may be only a timeline/export field, but the name conflicts with the PR5 architecture.

Correct naming:

```text
story_events
recent_timeline_events
debug_timeline
```

Do not call it canonical `memory`.

---

# 2. Required architecture after this cleanup

## 2.1. Memory separation

Use three separate concepts:

```text
memory_v3:
  Long-ish working memory used by Brain reasoning.

brain_trace:
  Recent reasoning/debug trace.

GameEvent / story timeline:
  Historical event log for UI/export/debug.
```

Do not store every technical event in `memory_v3`.

## 2.2. Death lifecycle

All death causes must go through one canonical death helper.

Examples:

```text
emission death
starvation/thirst death
combat death
mutant death
scripted death
```

All should produce the same state invariants.

## 2.3. ActivePlan lifecycle

ActivePlan lifecycle events should be trace/debug events by default.

Only semantically meaningful plan outcomes should become memory records.

---

# 3. Fix 1 — Add canonical death helper

## Files to inspect

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/rules/world_rules.py
```

Search:

```bash
git grep -n "is_alive.*False" backend/app/games/zone_stalkers
git grep -n "agent_died" backend/app/games/zone_stalkers
git grep -n "combat_killed" backend/app/games/zone_stalkers
git grep -n "emission" backend/app/games/zone_stalkers/rules
```

## Implement helper

Add a helper in a suitable shared place.

Preferred:

```text
backend/app/games/zone_stalkers/rules/agent_lifecycle.py
```

or if smaller:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
```

Suggested API:

```python
def kill_agent(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    cause: str,
    location_id: str | None = None,
    add_memory: Callable[..., None] | None = None,
    emit_event: bool = True,
) -> dict[str, Any] | None:
    ...
```

Required state mutation:

```python
agent["is_alive"] = False
agent["hp"] = 0
agent["scheduled_action"] = None
agent["action_queue"] = []
agent["active_plan_v3"] = None
agent["current_goal"] = "dead"
```

Required Brain runtime cleanup:

```python
br = ensure_brain_runtime(agent, world_turn)
br["invalidated"] = False
br["invalidators"] = []
br["queued"] = False
br["queued_turn"] = None
br["queued_priority"] = None
br["last_skip_reason"] = "dead"
br["valid_until_turn"] = world_turn
```

Required Brain context cleanup:

```python
ctx = agent.get("brain_v3_context")
if isinstance(ctx, dict):
    ctx["intent_kind"] = None
    ctx["objective_key"] = None
    ctx["objective_score"] = 0
    ctx["objective_reason"] = f"dead:{cause}"
    ctx["adapter_intent"] = None
```

Required trace update:

```text
brain_trace.current_thought should indicate death.
It must not say "continue sleep", "continue travel", "wait in shelter", etc.
```

Required event:

```python
{
  "event_type": "agent_died",
  "payload": {
    "agent_id": agent_id,
    "cause": cause,
    "location_id": location_id,
    "world_turn": world_turn,
  }
}
```

Memory write:

```text
Write exactly one important death memory record.
Do not write follow-up ActivePlan lifecycle records after death.
```

## Acceptance criteria

```text
[ ] All death paths use the helper.
[ ] Dead agent always has hp = 0.
[ ] Dead agent has no scheduled_action.
[ ] Dead agent has no action_queue.
[ ] Dead agent has no active_plan_v3.
[ ] Dead agent has no queued Brain decision.
[ ] Death emits one agent_died event.
[ ] Death writes one meaningful death memory record.
[ ] Death does not trigger plan repair spam.
```

---

# 4. Fix 2 — Stop ActivePlan lifecycle noise from entering memory_v3

## Problem

These records dominate memory:

```text
active_plan_created
active_plan_completed
active_plan_step_started
active_plan_step_completed
```

## Files to inspect

```text
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required design

Move low-value lifecycle events out of `memory_v3`.

They should remain in:

```text
brain_trace
debug timeline
GameEvent if needed
```

but not in canonical NPC memory.

## Minimal implementation

In `memory_events.py`, add low-value lifecycle skip list:

```python
_SKIP_ACTION_KINDS: frozenset[str] = frozenset({
    "sleep_interval_applied",

    # ActivePlan low-value lifecycle events.
    "active_plan_created",
    "active_plan_step_started",
    "active_plan_step_completed",
    "active_plan_completed",
})
```

Keep these as memory records because they can matter semantically:

```text
active_plan_step_failed
active_plan_repair_requested
active_plan_repaired
active_plan_aborted
global_goal_completed
objective_decision
target_death_confirmed
plan_monitor_abort
```

But even for repair-related events, add deduplication if they repeat too frequently.

## Better implementation

Introduce classification:

```python
MEMORY_EVENT_POLICY = {
    "active_plan_created": "trace_only",
    "active_plan_step_started": "trace_only",
    "active_plan_step_completed": "trace_only",
    "active_plan_completed": "trace_only",

    "active_plan_step_failed": "memory",
    "active_plan_repair_requested": "memory_dedup",
    "active_plan_repaired": "memory_dedup",
    "active_plan_aborted": "memory",
    "objective_decision": "memory_dedup",
    "global_goal_completed": "memory",
}
```

Then in `write_memory_event_to_v3(...)`:

```python
policy = MEMORY_EVENT_POLICY.get(action_kind, "memory")
if policy == "trace_only":
    return
if policy == "memory_dedup" and recently_written_same_signature(...):
    return
```

## Acceptance criteria

```text
[ ] active_plan_created is not written to memory_v3.
[ ] active_plan_completed is not written to memory_v3.
[ ] active_plan_step_started is not written to memory_v3.
[ ] active_plan_step_completed is not written to memory_v3.
[ ] Brain trace still contains plan lifecycle details.
[ ] memory_v3 top_kinds is not dominated by active_plan lifecycle records.
```

---

# 5. Fix 3 — Make memory_v3 eviction semantically healthier

## Problem

`goal`, `threat`, and `semantic` layers are protected from eviction.

This accidentally protects hundreds of low-value `active_plan_created/completed` records if they are mapped to `goal`.

## Required change

Protection should be based on semantic importance, not broad layer alone.

Do not protect all `goal` records equally.

## Files

```text
backend/app/games/zone_stalkers/memory/store.py
```

## Current risk

```python
_PROTECTED_LAYERS = {"threat", "goal", "semantic"}
```

This is too broad.

## Suggested approach

Replace broad layer protection with record-level retention class.

Add helper:

```python
def _retention_priority(raw: dict[str, Any]) -> int:
    kind = str(raw.get("kind") or "")
    layer = str(raw.get("layer") or "")
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    action_kind = str(details.get("action_kind") or kind)

    if kind in {
        "combat_killed",
        "combat_kill",
        "target_death_confirmed",
        "target_intel",
        "emission_warning",
        "emission_started",
        "anomaly_detected",
        "global_goal_completed",
    }:
        return 100

    if layer == "semantic":
        return 80

    if kind == "objective_decision":
        return 60

    if action_kind.startswith("active_plan_"):
        return 10

    return 40
```

Then use this in `_eviction_sort_key`.

Important:

```text
Hard cap must remain hard.
len(memory_v3.records) <= 500 always.
```

## Acceptance criteria

```text
[ ] Threat records survive ordinary lifecycle records.
[ ] Semantic knowledge survives ordinary lifecycle records.
[ ] Objective decisions survive low-value plan lifecycle.
[ ] active_plan lifecycle records are evicted first if any still exist.
[ ] Hard cap remains enforced.
```

---

# 6. Fix 4 — Fix UNKNOWN objective keys

## Files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/objectives.py
backend/app/games/zone_stalkers/decision/adapter.py
```

Search:

```bash
git grep -n "UNKNOWN" backend/app/games/zone_stalkers
git grep -n "_INTENT_TO_OBJECTIVE_KEY_FALLBACK" backend/app/games/zone_stalkers
git grep -n "objective_key" backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required mapping

Extend fallback mapping:

```python
_INTENT_TO_OBJECTIVE_KEY_FALLBACK: Dict[str, str] = {
    "leave_zone": "LEAVE_ZONE",
    "flee_emission": "REACH_SAFE_SHELTER",
    "wait_in_shelter": "WAIT_IN_SHELTER",
    "seek_water": "RESTORE_WATER",
    "seek_food": "RESTORE_FOOD",
    "rest": "REST",
    "heal_self": "HEAL_SELF",
    "escape_danger": "HEAL_SELF",
    "sell_artifacts": "SELL_ARTIFACTS",
    "get_rich": "FIND_ARTIFACTS",
}
```

For `resupply`, prefer specific objective if available:

```python
def _objective_key_from_intent_and_context(intent_kind, context, item_needs):
    if intent_kind == "resupply":
        # choose RESUPPLY_FOOD / RESUPPLY_DRINK / RESUPPLY_AMMO / RESUPPLY_WEAPON
        # based on top item_need or selected objective.
        ...
```

## Rule

Do not write `objective_key = "UNKNOWN"` unless there is truly no objective concept.

For important intents:

```text
flee_emission
wait_in_shelter
seek_water
seek_food
rest
heal_self
leave_zone
```

`UNKNOWN` is not acceptable.

## Acceptance criteria

```text
[ ] flee_emission memory records use REACH_SAFE_SHELTER.
[ ] wait_in_shelter memory records use WAIT_IN_SHELTER.
[ ] seek_water uses RESTORE_WATER.
[ ] seek_food uses RESTORE_FOOD.
[ ] rest uses REST.
[ ] No normal objective_decision memory has objective_key=UNKNOWN.
```

---

# 7. Fix 5 — WAIT_IN_SHELTER should not re-run Brain every tick

## Problem

`WAIT_IN_SHELTER` appears to expire immediately, causing repeated Brain decisions and plan churn.

## Files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/brain_runtime.py
backend/app/games/zone_stalkers/decision/active_plan_composer.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
```

## Required behavior

When emission is active and NPC is already in shelter:

```text
- do not create a new plan every tick;
- do not write a new objective_decision every tick;
- do not write plan lifecycle memory records every tick;
- wait until emission ends or until state changes.
```

## Implementation option A — valid_until until emission end

Change `_brain_valid_until_turn(...)` to use state.

Current likely logic:

```python
def _brain_valid_until_turn(agent, world_turn):
    objective_key = ...
    if objective_key in {"ENGAGE_TARGET", "REACH_SAFE_SHELTER", "WAIT_IN_SHELTER"}:
        return world_turn
```

Change to:

```python
def _brain_valid_until_turn(agent, state, world_turn):
    objective_key = str((agent.get("brain_v3_context") or {}).get("objective_key") or "")

    if objective_key == "WAIT_IN_SHELTER":
        emission_ends_turn = state.get("emission_ends_turn")
        if emission_ends_turn is not None:
            return max(world_turn + 1, min(int(emission_ends_turn), world_turn + 30))
        return world_turn + 5

    if objective_key == "REACH_SAFE_SHELTER":
        # Re-evaluate frequently while still trying to reach shelter.
        return world_turn

    if objective_key == "ENGAGE_TARGET":
        return world_turn

    ...
```

Update callers:

```python
_post_brain_decision_runtime_update(agent, state, world_turn)
```

## Implementation option B — scheduled wait action

Represent `WAIT_IN_SHELTER` as a scheduled wait action:

```python
agent["scheduled_action"] = {
    "type": "wait",
    "reason": "emission",
    "started_turn": world_turn,
    "ends_turn": state["emission_ends_turn"],
    "turns_total": state["emission_ends_turn"] - world_turn,
    "turns_remaining": state["emission_ends_turn"] - world_turn,
    "interruptible": True,
}
```

Then Brain can skip until wait completes.

This is cleaner but larger.

## Acceptance criteria

```text
[ ] WAIT_IN_SHELTER does not create active_plan_created/completed every tick.
[ ] NPC remains in shelter during emission.
[ ] Brain reruns when emission ends.
[ ] Brain reruns if NPC becomes unsafe or needs become critical.
[ ] No memory_v3 spam during shelter waiting.
```

---

# 8. Fix 6 — Do not create semantic memory for dead agents

## Files

```text
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Problem

`decay_memory(...)` can create semantic records by consolidating old episodic records.

For dead agents, this creates new memory after death.

## Fix

At the top of `decay_memory(...)`:

```python
def decay_memory(agent: dict[str, Any], world_turn: int) -> None:
    if not agent.get("is_alive", True):
        return
    ...
```

Alternative if we still want archival decay:

```python
if not agent.get("is_alive", True):
    mem_v3 = ensure_memory_v3(agent)
    stats = mem_v3["stats"]
    last_decay = stats.get("last_decay_turn")
    if last_decay is None or (world_turn - last_decay) >= DECAY_CADENCE_TURNS:
        stats["last_decay_turn"] = world_turn
        _run_decay_pass(mem_v3, world_turn)
        mem_v3["stats"]["records_count"] = len(mem_v3["records"])
    return  # no consolidation
```

Preferred:

```text
No new semantic records after death.
```

## Acceptance criteria

```text
[ ] Dead agents do not get new semantic records.
[ ] Dead agents do not update last_accessed_turn via retrieval.
[ ] Dead agents do not run Brain.
[ ] Dead agents do not run active plan processing.
```

---

# 9. Fix 7 — Deduplicate repeated observations

## Problem

Repeated observations such as `stalkers_seen`, `travel_hop`, `items_seen` can flood memory.

Even with consolidation, source episodic records remain.

## Files

```text
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required behavior

Repeated low-value observation within a short window should update an existing semantic/aggregate record, not append another episodic record.

Dedup signature examples:

```text
stalkers_seen:
  (kind, location_id, sorted(entity_ids or names))

items_seen:
  (kind, location_id, sorted(item_types))

travel_hop:
  (kind, from_location, to_location)

trader_visited:
  (kind, trader_id, location_id)
```

## Suggested dedup window

```python
MEMORY_EVENT_DEDUP_WINDOW_TURNS = 30
```

## Implementation

Before adding new record:

```python
existing = find_recent_record_by_signature(agent, signature, world_turn, window=30)
if existing:
    update existing.details["times_seen"] += 1
    update existing.details["last_seen_turn"] = world_turn
    update existing.confidence = min(1.0, existing.confidence + 0.02)
    return
```

Do this for low-value repeated observations only.

Do not dedup:

```text
death
combat_kill
target_death_confirmed
global_goal_completed
critical threat
```

## Acceptance criteria

```text
[ ] Repeated same-location stalkers_seen does not create unbounded episodic records.
[ ] Repeated travel_hop does not dominate memory.
[ ] Important events are still recorded individually.
[ ] memory_v3 remains useful after 1000+ turns.
```

---

# 10. Fix 8 — Rename frontend/export "memory" timeline field

## Problem

After PR5, canonical memory is `memory_v3`.

Frontend export still uses a root or type field called `memory`, which confuses debugging.

## Files

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/exportNpcHistory.ts
frontend/src/games/zone_stalkers/ui/agent_profile/*
```

## Required behavior

If the exported field is a display timeline, rename it:

```text
memory → story_events
```

or:

```text
memory → recent_timeline_events
```

The export should clearly distinguish:

```text
memory_v3_summary:
  canonical memory stats

story_events:
  UI/debug timeline derived from trace/events/history
```

## Backward compatibility

No backward compatibility is required for local debug exports unless consumers depend on it.

If needed, bump schema:

```text
npc_history_v1 → npc_history_v2
```

Recommended:

```typescript
export_schema: 'npc_history_v2'
```

## Acceptance criteria

```text
[ ] Export does not imply legacy memory is canonical.
[ ] memory_v3_summary remains.
[ ] timeline data is named story_events or recent_timeline_events.
[ ] AgentProfile UI still works.
```

---

# 11. Tests to add

## 11.1. Death invariants

File:

```text
backend/tests/decision/v3/test_npc_death_invariants.py
```

Tests:

```python
def test_emission_death_sets_hp_to_zero_and_clears_runtime(): ...
def test_starvation_death_sets_hp_to_zero_and_clears_runtime(): ...
def test_combat_death_sets_hp_to_zero_and_clears_runtime(): ...
def test_dead_agent_has_no_queued_brain_decision(): ...
def test_dead_agent_has_no_active_plan_or_scheduled_action(): ...
```

---

## 11.2. Memory policy tests

File:

```text
backend/tests/decision/v3/test_memory_event_policy.py
```

Tests:

```python
def test_active_plan_created_is_trace_only_not_memory_v3(): ...
def test_active_plan_completed_is_trace_only_not_memory_v3(): ...
def test_active_plan_step_started_is_trace_only_not_memory_v3(): ...
def test_active_plan_step_completed_is_trace_only_not_memory_v3(): ...
def test_active_plan_failed_is_still_memory_v3(): ...
def test_plan_monitor_abort_is_still_memory_v3(): ...
def test_death_memory_is_still_memory_v3(): ...
```

---

## 11.3. Objective key tests

File:

```text
backend/tests/decision/v3/test_objective_key_mapping.py
```

Tests:

```python
def test_flee_emission_memory_uses_reach_safe_shelter_not_unknown(): ...
def test_wait_in_shelter_memory_uses_wait_in_shelter_not_unknown(): ...
def test_seek_water_memory_uses_restore_water_not_unknown(): ...
def test_seek_food_memory_uses_restore_food_not_unknown(): ...
def test_rest_memory_uses_rest_not_unknown(): ...
def test_no_normal_objective_decision_has_unknown_key(): ...
```

---

## 11.4. WAIT_IN_SHELTER stability tests

File:

```text
backend/tests/decision/v3/test_wait_in_shelter_stability.py
```

Tests:

```python
def test_wait_in_shelter_brain_valid_until_emission_end(): ...
def test_wait_in_shelter_does_not_recreate_plan_each_tick(): ...
def test_wait_in_shelter_does_not_spam_memory_v3(): ...
def test_wait_in_shelter_reruns_after_emission_ends(): ...
```

---

## 11.5. Dead agent memory tests

File:

```text
backend/tests/decision/v3/test_dead_agent_memory.py
```

Tests:

```python
def test_dead_agent_decay_does_not_create_semantic_record(): ...
def test_dead_agent_retrieval_does_not_track_access(): ...
def test_dead_agent_no_new_memory_after_death_tick(): ...
```

---

## 11.6. Memory composition regression test

File:

```text
backend/tests/decision/v3/test_memory_composition_long_run.py
```

Test shape, do not assert exact counts too tightly:

```python
def test_long_run_memory_not_dominated_by_active_plan_lifecycle():
    # Run one or several NPCs for enough turns.
    # Assert memory_v3.records_count <= 500.
    # Assert active_plan lifecycle kinds are below a small threshold.
    # Assert semantic/threat/objective records still exist.
```

Suggested assertions:

```python
assert records_count <= 500
assert count_kind("active_plan_created") == 0
assert count_kind("active_plan_completed") == 0
assert count_kind("active_plan_step_started") == 0
assert count_kind("active_plan_step_completed") == 0
assert count_layer("semantic") > 0
```

---

# 12. Manual validation scenario

After fixes, rerun the same kind of simulation.

## Scenario

```text
10 NPCs
auto-run x600
several thousand turns
include at least one emission
export full_debug + history for all NPCs
```

## Check

For every NPC:

```text
[ ] If is_alive=false, hp=0.
[ ] If is_alive=false, scheduled_action=None.
[ ] If is_alive=false, active_plan_v3 absent/None.
[ ] If is_alive=false, brain_runtime.queued=false.
[ ] No objective_decision has objective_key=UNKNOWN for normal intents.
[ ] memory_v3.records_count <= 500.
[ ] active_plan_created/completed do not dominate memory_v3.
[ ] WAIT_IN_SHELTER does not repeat every tick.
[ ] Dead agents do not receive new semantic records after death.
```

## Performance should not regress

Expected:

```text
10 NPCs should remain roughly in the improved x150–x200 effective range,
or at least not collapse back to x45.
```

Memory cleanup should usually improve performance further because fewer low-value records are written and retained.

---

# 13. Do not reintroduce legacy memory

Hard rule:

```text
Do not bring back agent["memory"].
```

Forbidden:

```python
agent.setdefault("memory", [])
agent["memory"].append(...)
agent.get("memory")
```

Run:

```bash
git grep -n "agent.get(\"memory\"" backend/app backend/tests || true
git grep -n "agent\\[\"memory\"\\]" backend/app backend/tests || true
git grep -n "setdefault(\"memory\"" backend/app backend/tests || true
git grep -n "\"memory\": \\[\\]" backend/app backend/tests || true
```

Expected:

```text
No runtime/test usage.
```

Frontend export may have renamed timeline fields, but should not claim canonical memory is `memory`.

---

# 14. Implementation order

Recommended order:

```text
1. Add kill_agent helper and migrate all death paths.
2. Add death invariant tests.
3. Change memory_events policy to skip trace-only ActivePlan lifecycle records.
4. Add memory event policy tests.
5. Fix objective_key fallback mapping.
6. Add objective key tests.
7. Fix WAIT_IN_SHELTER valid_until / wait runtime.
8. Add WAIT_IN_SHELTER stability tests.
9. Disable semantic consolidation for dead agents.
10. Add dead-agent memory tests.
11. Add dedup for repeated low-value observations.
12. Rename frontend/export timeline memory field.
13. Run full backend non-e2e + Brain v3 E2E.
14. Run manual 10-NPC simulation and inspect logs.
```

---

# 15. Required test commands

Run focused tests:

```bash
pytest backend/tests/decision/v3/test_npc_death_invariants.py -q
pytest backend/tests/decision/v3/test_memory_event_policy.py -q
pytest backend/tests/decision/v3/test_objective_key_mapping.py -q
pytest backend/tests/decision/v3/test_wait_in_shelter_stability.py -q
pytest backend/tests/decision/v3/test_dead_agent_memory.py -q
pytest backend/tests/decision/v3/test_memory_composition_long_run.py -q
```

Run existing PR5/Brain tests:

```bash
pytest backend/tests/decision/v3/test_memory_store.py -q
pytest backend/tests/decision/v3/test_memory_retrieval.py -q
pytest backend/tests/decision/v3/test_memory_retrieval_fast_path.py -q
pytest backend/tests/decision/v3/test_memory_v3_only_runtime.py -q
pytest backend/tests/decision/test_context_builder_memory_v3.py -q
```

Run Brain v3 E2E:

```bash
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
```

Run full non-e2e:

```bash
pytest backend/tests -k "not e2e" -q
```

Run frontend build if export schema is changed:

```bash
cd frontend
npm run build
```

---

# 16. Definition of Done

This cleanup is done when:

```text
[ ] Dead NPCs always have hp=0.
[ ] Dead NPCs have no active scheduled_action/action_queue/active_plan.
[ ] Dead NPCs are not queued for Brain decisions.
[ ] Dead NPCs do not receive new semantic memory after death.
[ ] active_plan_created/completed/step_started/step_completed are not stored in memory_v3.
[ ] Important plan failures/repairs/deaths still produce memory records.
[ ] Normal objective decisions do not use objective_key=UNKNOWN.
[ ] WAIT_IN_SHELTER does not rerun Brain every tick.
[ ] memory_v3 remains capped at 500.
[ ] memory_v3 is not dominated by ActivePlan lifecycle records.
[ ] Frontend export no longer uses ambiguous root "memory" for timeline data.
[ ] No legacy agent["memory"] runtime/test usage is reintroduced.
[ ] Backend tests are green.
[ ] Brain v3 E2E tests are green.
[ ] Frontend build is green.
[ ] 10-NPC manual run still shows improved Effective speed.
```

---

# 17. Summary for Copilot

The current system is faster, but memory semantics are wrong.

Do not optimize first.

Fix correctness:

```text
1. Death must be a clean terminal state.
2. memory_v3 must store knowledge, not plan lifecycle logs.
3. Objectives must not be UNKNOWN for known intents.
4. WAIT_IN_SHELTER must be stable across emission duration.
5. Dead agents must not keep thinking or learning.
```

Only after this cleanup should the project continue toward the 100-NPC target.
