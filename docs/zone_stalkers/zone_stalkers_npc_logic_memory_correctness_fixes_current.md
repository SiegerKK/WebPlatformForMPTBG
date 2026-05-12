# Zone Stalkers — NPC Logic, Memory Semantics and Anti-Loop Fixes after CPU PR5

## Purpose

CPU PR5 significantly improved runtime performance by moving NPC memory to `memory_v3`, removing legacy memory, lowering the hot memory cap, and optimizing memory retrieval.

Observed performance improvement:

```text
Before:
  10 NPCs could drop Effective speed to x45 or lower.

After:
  10 NPCs run around x150–x200.
```

However, NPC debug exports show several semantic correctness problems:

```text
- memory_v3 is capped but often filled with low-value plan lifecycle noise;
- support objectives such as GET_MONEY_FOR_RESUPPLY can loop on the same location/action;
- story/debug timeline may be empty even when memory_v3 contains many records;
- scheduled action skip summaries lose objective/intent context;
- some dead NPC states can be inconsistent;
- WAIT_IN_SHELTER / emission logic can create repeated plan churn;
- objective_key may be UNKNOWN for known intents;
- dead agents may still receive semantic memory updates.
```

This document is a Copilot-ready implementation plan for fixing those issues systematically.

---

# 1. Important correction: do not force killers to attack blindly

A killer NPC with global goal `kill_stalker` may reasonably choose not to attack immediately if:

```text
- target is stronger;
- NPC has no armor;
- NPC lacks ammo / weapon / medicine;
- NPC has too little money for required resupply;
- survival or emission pressure is high.
```

So the problem is **not**:

```text
visible target ⇒ always attack
```

The correct rule is:

```text
visible target ⇒ explicit combat-readiness arbitration
```

If the NPC chooses not to attack, the system must explain why and ensure the support objective makes progress.

Valid behavior:

```text
target visible and co-located
target combat strength high
NPC poorly equipped / underfunded
⇒ choose PREPARE_FOR_HUNT or GET_MONEY_FOR_RESUPPLY
```

Invalid behavior:

```text
target visible and co-located
NPC chooses GET_MONEY_FOR_RESUPPLY
then loops forever exploring the same anomaly/location
and memory_v3 fills with plan lifecycle spam
and debug export cannot explain the decision
```

---

# 2. Observed issues to fix

## 2.1. Support objective loop

Example from killer NPC export:

```text
global_goal = kill_stalker
target visible_now = true
target co_located = true
target combat_strength = 1
current_goal = get_money_for_resupply
active_objective = GET_MONEY_FOR_RESUPPLY
scheduled_action = explore_anomaly_location
active_plan_v3 step = explore_location
location_id = current location
```

This may be valid as a temporary support objective, but only if it makes progress.

Problem:

```text
GET_MONEY_FOR_RESUPPLY can repeatedly select the same local exploration action without clear success/failure/exhaustion semantics.
```

## 2.2. memory_v3 polluted by ActivePlan lifecycle records

Observed memory composition:

```text
records_count = 500
goal = 391
active_plan_created = 173
active_plan_completed = 172
stalkers_seen = 61
objective_decision = 46
semantic = 17
```

This means `memory_v3` is technically capped but semantically polluted.

`memory_v3` should store knowledge and meaningful experiences, not the execution log of every active plan.

## 2.3. story_timeline/debug export can be empty

Observed:

```text
story_timeline = []
npc_brain.latest_event = null
npc_brain.latest_decision = null
recent_trace_events = []
```

while:

```text
memory_v3.records_count = 500
objective_decision records exist
target/hunt memory exists
```

After PR5, debug/export must not depend on legacy `agent["memory"]`.

## 2.4. scheduled action continuation loses context

Observed summary:

```json
{
  "objective_key": null,
  "intent_kind": null,
  "summary": "Продолжаю explore_anomaly_location — action_still_valid."
}
```

This hides critical information.

A good summary should preserve:

```text
current objective
intent kind
support objective reason
target visibility
combat readiness reason
interrupt checks
```

## 2.5. death state can be inconsistent

Observed in other NPC logs:

```text
is_alive = false
hp = 92 / 93
current_goal = emergency_shelter
adapter_intent = flee_emission
brain_runtime queued/invalidated
```

Death must be a clean terminal state.

## 2.6. WAIT_IN_SHELTER can churn every tick

If `WAIT_IN_SHELTER` has `valid_until_turn = world_turn`, Brain can re-run every tick during emission, creating repeated plans and memory noise.

## 2.7. objective_key = UNKNOWN for known intents

Known intents such as:

```text
flee_emission
wait_in_shelter
seek_water
seek_food
rest
```

should not produce `objective_key = UNKNOWN`.

## 2.8. Dead agents can receive new semantic memory

Memory consolidation/decay can create semantic records after death unless explicitly blocked.

---

# 3. Target architecture

## 3.1. Clear memory separation

Use separate systems for separate concerns:

```text
memory_v3:
  Canonical NPC working memory: facts, observations, meaningful experiences, target intel.

brain_trace:
  Recent reasoning/debug trace.

GameEvent / story timeline:
  Historical UI/debug timeline and event log.

active_plan_v3:
  Current execution plan.

scheduled_action:
  Current long-running action.
```

Do not use `memory_v3` as an execution log.

## 3.2. Support objectives are tactical, not replacements for global goals

For `global_goal = kill_stalker`:

```text
GET_MONEY_FOR_RESUPPLY
PREPARE_FOR_HUNT
RESUPPLY_AMMO
RESUPPLY_ARMOR
HEAL_SELF
```

are support objectives.

They are valid only while they make the global goal more achievable.

They must have:

```text
success condition
failure condition
cooldown / exhausted marker
max attempts
fallback
debug explanation
```

## 3.3. Scheduled actions must be interruptible by real strategic changes

`interruptible = true` means the action can be stopped if a more important condition appears.

The action continuation check must consider:

```text
target visible / target disappeared
combat started
emission danger
critical HP/thirst/hunger
support action exhausted
global goal completed
```

---

# 4. Fix A — Combat readiness arbitration for kill_stalker

## Goal

Do not force attack, but make the decision explicit and testable.

## Files to inspect

```text
backend/app/games/zone_stalkers/decision/needs.py
backend/app/games/zone_stalkers/decision/objectives/generator.py
backend/app/games/zone_stalkers/decision/objectives/selection.py
backend/app/games/zone_stalkers/decision/objectives/intent_adapter.py
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required helper

Add a central helper:

```python
def evaluate_kill_target_combat_readiness(
    *,
    agent: dict[str, Any],
    target_belief: Any,
    need_result: Any,
    context: Any,
) -> dict[str, Any]:
    ...
```

Return shape:

```python
{
    "combat_ready": bool,
    "should_engage_now": bool,
    "reasons": ["target_too_strong", "no_armor", "money_missing_for_resupply"],
    "target_visible_now": bool,
    "target_co_located": bool,
    "target_strength": float | None,
    "weapon_ready": bool,
    "ammo_ready": bool,
    "armor_ready": bool,
    "hp_ready": bool,
    "recommended_support_objective": "GET_MONEY_FOR_RESUPPLY" | "RESUPPLY_AMMO" | "HEAL_SELF" | None,
}
```

## Rules

If target is visible and co-located:

```text
if combat_ready:
  prefer ENGAGE_TARGET

else:
  choose explicit support objective with reason:
    - no_weapon → RESUPPLY_WEAPON / GET_MONEY_FOR_RESUPPLY
    - low_ammo → RESUPPLY_AMMO / GET_MONEY_FOR_RESUPPLY
    - no_armor + target strong → RESUPPLY_ARMOR / GET_MONEY_FOR_RESUPPLY
    - hp_low → HEAL_SELF
    - target_too_strong → PREPARE_FOR_HUNT / GET_MONEY_FOR_RESUPPLY
```

Do not hide this behind a generic `get_rich`.

## Acceptance criteria

```text
[ ] Visible target does not always force attack.
[ ] Visible target always triggers combat-readiness arbitration.
[ ] If NPC does not attack, debug state explains why.
[ ] GET_MONEY_FOR_RESUPPLY reason includes combat-readiness/support details.
[ ] Tests cover both attack-ready and not-ready cases.
```

## Tests

```python
def test_killer_attacks_visible_target_when_combat_ready(): ...
def test_killer_delays_attack_when_target_strong_and_no_armor(): ...
def test_killer_delays_attack_when_ammo_missing(): ...
def test_killer_support_objective_records_not_attacking_reason(): ...
```

---

# 5. Fix B — GET_MONEY_FOR_RESUPPLY must not loop forever

## Goal

Support objective must make progress or terminate.

## Files to inspect

```text
backend/app/games/zone_stalkers/decision/active_plan_composer.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/memory/memory_events.py
```

## Required model

For money/support objective, planner should choose one concrete method:

```text
sell safe item
sell artifact
buy required item if affordable
explore anomaly for artifacts
travel to known artifact location
loot known stash
ask trader / search intel
```

Each method must define:

```text
success condition
failure condition
cooldown / exhausted marker
max attempts per location
fallback method
```

## Required memory facts

Add memory records:

```text
money_source_exhausted
anomaly_search_exhausted
resupply_location_exhausted
artifact_search_failed
artifact_search_success
support_objective_progress
support_objective_failed
```

Example details:

```json
{
  "action_kind": "anomaly_search_exhausted",
  "objective_key": "GET_MONEY_FOR_RESUPPLY",
  "location_id": "loc_debug_62",
  "reason": "no_artifact_found_after_exploration",
  "cooldown_until_turn": 125300,
  "attempt_count": 3
}
```

## Required planner behavior

Before selecting `explore_anomaly_location` for `GET_MONEY_FOR_RESUPPLY`:

```python
if is_location_exhausted_for_money(agent, location_id, world_turn):
    choose another money source
```

If all money sources are exhausted:

```text
- downgrade support objective;
- reconsider attack risk;
- choose retreat/wait/search alternative;
- record explicit debug reason.
```

## Acceptance criteria

```text
[ ] Same anomaly/location is not selected indefinitely.
[ ] Failed exploration writes exhaustion/cooldown memory.
[ ] Planner avoids exhausted money-source locations.
[ ] Support objective either succeeds, fails, or changes strategy.
[ ] No infinite explore_anomaly_location loop.
```

## Tests

```python
def test_get_money_for_resupply_marks_anomaly_exhausted_after_failed_attempts(): ...
def test_get_money_for_resupply_avoids_exhausted_location(): ...
def test_get_money_for_resupply_switches_source_after_exhaustion(): ...
def test_get_money_for_resupply_reconsiders_hunt_when_no_money_sources_remain(): ...
```

---

# 6. Fix C — Scheduled action continuation must check interrupts and preserve context

## Problem

Current continuation summary can be too shallow:

```text
"Продолжаю explore_anomaly_location — action_still_valid."
```

with:

```text
objective_key = null
intent_kind = null
```

## Required helper

Add:

```python
def evaluate_scheduled_action_interrupts(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    context: Any,
    scheduled_action: dict[str, Any],
    world_turn: int,
) -> dict[str, Any] | None:
    ...
```

Interrupt candidates:

```text
critical_hp
critical_thirst
critical_hunger
emission_danger
target_visible_changed
target_visible_and_combat_ready
support_source_exhausted
global_goal_completed
combat_started
```

For support objectives:

```text
target visible but combat-not-ready
```

does not necessarily interrupt, but must be recorded as checked.

## Summary shape

When continuing scheduled action:

```json
{
  "turn": 125034,
  "objective_key": "GET_MONEY_FOR_RESUPPLY",
  "intent_kind": "get_rich",
  "summary": "Продолжаю explore_anomaly_location — action_still_valid",
  "skip_reason": "scheduled_action_still_valid",
  "scheduled_action_type": "explore_anomaly_location",
  "active_plan_id": "...",
  "support_objective_for": "kill_stalker",
  "target_visible_now": true,
  "target_co_located": true,
  "combat_ready": false,
  "not_attacking_reasons": ["target_too_strong", "no_armor", "money_missing_for_resupply"],
  "interrupts_checked": ["critical_hp", "emission", "support_source_exhausted", "target_visible"],
  "interrupt_triggered": null
}
```

## Acceptance criteria

```text
[ ] scheduled_action skip summary preserves objective_key.
[ ] scheduled_action skip summary preserves intent_kind.
[ ] target visibility/combat-readiness check is visible in debug.
[ ] interruptible action actually interrupts when a valid interrupt appears.
```

## Tests

```python
def test_scheduled_action_skip_summary_preserves_objective_and_intent(): ...
def test_scheduled_action_skip_summary_records_combat_readiness_reason(): ...
def test_support_source_exhausted_interrupts_explore_scheduled_action(): ...
def test_combat_ready_visible_target_interrupts_support_action(): ...
```

---

# 7. Fix D — memory_v3 must not store low-value ActivePlan lifecycle logs

## Problem

`memory_v3` is often dominated by:

```text
active_plan_created
active_plan_completed
active_plan_step_started
active_plan_step_completed
```

## Files

```text
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
```

## Required policy

Trace/debug only:

```text
active_plan_created
active_plan_step_started
active_plan_step_completed
active_plan_completed
```

Memory-worthy:

```text
active_plan_step_failed
active_plan_repair_requested
active_plan_repaired
active_plan_aborted
plan_monitor_abort
global_goal_completed
objective_decision
death
target_death_confirmed
support_objective_failed
support_source_exhausted
```

## Implementation

Add explicit policy:

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
    "plan_monitor_abort": "memory",
    "objective_decision": "memory_dedup",
    "global_goal_completed": "memory",
}
```

## Acceptance criteria

```text
[ ] active_plan_created is not written to memory_v3.
[ ] active_plan_completed is not written to memory_v3.
[ ] active_plan_step_started is not written to memory_v3.
[ ] active_plan_step_completed is not written to memory_v3.
[ ] important failures/aborts remain in memory_v3.
[ ] memory_v3 top_kinds no longer dominated by active_plan lifecycle.
```

## Tests

```python
def test_active_plan_created_is_trace_only_not_memory_v3(): ...
def test_active_plan_completed_is_trace_only_not_memory_v3(): ...
def test_active_plan_step_failed_is_memory_v3(): ...
def test_active_plan_aborted_is_memory_v3(): ...
def test_memory_v3_not_dominated_by_active_plan_lifecycle_long_run(): ...
```

---

# 8. Fix E — Make memory_v3 eviction semantically healthy

## Problem

Broad layer protection can protect low-value records if they are mapped to `goal`.

## Files

```text
backend/app/games/zone_stalkers/memory/store.py
```

## Required change

Do not protect all `goal` records equally.

Use record-level retention priority:

```python
def _retention_priority(raw: dict[str, Any]) -> int:
    kind = str(raw.get("kind") or "")
    layer = str(raw.get("layer") or "")
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    action_kind = str(details.get("action_kind") or kind)

    if kind in {
        "death",
        "combat_killed",
        "target_death_confirmed",
        "target_intel",
        "target_seen",
        "emission_warning",
        "emission_started",
        "global_goal_completed",
        "support_source_exhausted",
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

Hard cap stays hard:

```text
len(memory_v3.records) <= 500
```

## Acceptance criteria

```text
[ ] Threat/target/semantic records survive low-value lifecycle records.
[ ] active_plan lifecycle records are evicted first if any still exist.
[ ] hard cap remains enforced.
```

---

# 9. Fix F — Fix objective_key = UNKNOWN for known intents

## Files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/objectives/intent_adapter.py
```

## Required mapping

Known intent → canonical objective:

```python
_INTENT_TO_OBJECTIVE_KEY_FALLBACK = {
    "leave_zone": "LEAVE_ZONE",
    "flee_emission": "REACH_SAFE_SHELTER",
    "wait_in_shelter": "WAIT_IN_SHELTER",
    "seek_water": "RESTORE_WATER",
    "seek_food": "RESTORE_FOOD",
    "rest": "REST",
    "heal_self": "HEAL_SELF",
    "escape_danger": "ESCAPE_DANGER",
    "sell_artifacts": "SELL_ARTIFACTS",
    "get_rich": "FIND_ARTIFACTS",
    "hunt_target": "HUNT_TARGET",
    "resupply": "RESUPPLY",
}
```

If `RESUPPLY` is not a real objective, map by forced category:

```text
RESUPPLY_AMMO
RESUPPLY_ARMOR
RESUPPLY_WEAPON
RESUPPLY_FOOD
RESUPPLY_DRINK
RESUPPLY_MEDICINE
```

## Acceptance criteria

```text
[ ] flee_emission never records UNKNOWN objective.
[ ] wait_in_shelter never records UNKNOWN objective.
[ ] seek_water/seek_food/rest never record UNKNOWN objective.
[ ] normal objective_decision records have known objective_key.
```

---

# 10. Fix G — WAIT_IN_SHELTER stability

## Problem

WAIT_IN_SHELTER should not re-run Brain every tick during emission.

## Required behavior

When NPC is already in safe shelter during emission:

```text
- do not create new plan every tick;
- do not write objective_decision every tick;
- do not write plan lifecycle memory every tick;
- re-evaluate when emission ends or safety state changes.
```

## Implementation option

Use `emission_ends_turn` if available:

```python
if objective_key == "WAIT_IN_SHELTER":
    emission_ends_turn = state.get("emission_ends_turn")
    if emission_ends_turn:
        return max(world_turn + 1, min(int(emission_ends_turn), world_turn + 30))
    return world_turn + 5
```

If `_brain_valid_until_turn` lacks state, pass state into it.

## Acceptance criteria

```text
[ ] WAIT_IN_SHELTER valid_until is not equal to world_turn every tick.
[ ] WAIT_IN_SHELTER does not create plan churn.
[ ] Brain reruns when emission ends.
```

---

# 11. Fix H — Death lifecycle normalization

## Required invariant

```text
is_alive == false
⇒ hp == 0
⇒ scheduled_action == None
⇒ action_queue == []
⇒ active_plan_v3 == None
⇒ no queued Brain decision
```

## Helper

Implement:

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
) -> dict[str, Any] | None:
    ...
```

Apply to:

```text
emission death
combat death
starvation/thirst death
scripted death
```

## Brain cleanup

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

## Acceptance criteria

```text
[ ] Dead NPC has hp=0.
[ ] Dead NPC has no scheduled_action/action_queue/active_plan.
[ ] Dead NPC is not queued for Brain.
[ ] Death writes one meaningful memory record.
[ ] Death emits agent_died event.
```

---

# 12. Fix I — Dead agents must not receive new semantic memory

## Files

```text
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required behavior

At the start of memory decay/consolidation:

```python
if not agent.get("is_alive", True):
    return
```

or if decay must still happen:

```text
allow decay pass only,
do not create/update semantic records.
```

## Acceptance criteria

```text
[ ] Dead agents do not get new semantic records.
[ ] Dead agents do not update memory last_accessed_turn.
[ ] Dead agents do not run Brain or active_plan.
```

---

# 13. Fix J — Deduplicate repeated observations

## Problem

Repeated records such as:

```text
stalkers_seen
travel_hop
items_seen
no_tracks_found
no_witnesses
witness_source_exhausted
```

can flood memory.

## Required dedup signatures

```text
stalkers_seen:
  (kind, location_id, sorted(entity_ids or names))

items_seen:
  (kind, location_id, sorted(item_types))

travel_hop:
  (kind, from_location, to_location)

witness_source_exhausted:
  (kind, location_id, target_id)

no_tracks_found:
  (kind, location_id, target_id)
```

## Dedup window

```python
MEMORY_EVENT_DEDUP_WINDOW_TURNS = 30
```

If same signature exists within window:

```text
update existing record:
  times_seen += 1
  last_seen_turn = world_turn
  confidence = min(1.0, confidence + 0.02)

do not add a new record
```

Do not dedup critical events:

```text
death
combat_kill
target_death_confirmed
global_goal_completed
emission_started
```

## Acceptance criteria

```text
[ ] repeated stalkers_seen does not flood memory_v3.
[ ] repeated no_witnesses/no_tracks is consolidated.
[ ] important events remain individual.
```

---

# 14. Fix K — Story timeline/export from memory_v3

## Problem

After PR5, story timeline may be empty because export code no longer has legacy memory.

## Files

```text
frontend/src/games/zone_stalkers/ui/agent_profile/exportNpcHistory.ts
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/*
```

## Required behavior

Export should include:

```json
{
  "export_schema": "npc_history_v2",
  "story_events": [...],
  "memory_v3_summary": {...},
  "brain_trace": {...}
}
```

Do not use ambiguous root field:

```text
memory
```

for timeline data.

Build `story_events` from:

```text
memory_v3 objective_decision
memory_v3 target_seen / target_intel / death / combat / support failures
brain_trace recent events
GameEvent log if available
```

## Acceptance criteria

```text
[ ] story_timeline/story_events is not empty when memory_v3 has meaningful records.
[ ] objective decisions appear in export timeline.
[ ] target intel/seen appears in export timeline.
[ ] active_plan lifecycle can appear in debug trace but not canonical memory.
[ ] export schema updated to npc_history_v2 if field names change.
```

---

# 15. Fix L — Hunt/intel loops must respect exhausted sources

## Problem

NPC can repeatedly ask witnesses / gather intel despite having semantic memory like:

```text
semantic_no_witnesses
semantic_witness_source_exhausted
```

## Files

```text
backend/app/games/zone_stalkers/decision/active_plan_composer.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/hunt/*
```

## Required behavior

If memory has:

```text
witness_source_exhausted(location_id, target_id, cooldown_until_turn > world_turn)
```

planner must not choose:

```text
question_witnesses at same location
```

It should choose:

```text
ask trader
inspect tracks
travel to next likely location
verify another lead
wait for cooldown
```

## Acceptance criteria

```text
[ ] GATHER_INTEL does not repeat exhausted witness source.
[ ] exhausted source cooldown expires correctly.
[ ] planner chooses alternative step.
```

---

# 16. Debug/explainability improvements

## Required debug fields

For every decision or skip summary include:

```text
objective_key
intent_kind
global_goal
support_objective_for
scheduled_action_type
active_plan_id
target_visible_now
target_co_located
combat_ready
not_attacking_reasons
interrupts_checked
interrupt_triggered
```

If target is visible but NPC does not attack:

```text
must include explicit reason
```

Examples:

```text
no_armor
low_ammo
target_too_strong
hp_low
money_missing_for_resupply
support_source_not_exhausted
```

## Acceptance criteria

```text
[ ] It is clear why killer is not attacking.
[ ] It is clear why scheduled action continues.
[ ] It is clear when/why scheduled action is interrupted.
```

---

# 17. Tests to add

## 17.1. Combat/support arbitration

```python
def test_killer_attacks_visible_target_when_combat_ready(): ...
def test_killer_delays_attack_when_target_strong_and_no_armor(): ...
def test_killer_support_objective_records_not_attacking_reason(): ...
def test_get_money_for_resupply_is_support_objective_for_kill_stalker(): ...
```

## 17.2. Anti-loop support objective

```python
def test_get_money_for_resupply_marks_anomaly_exhausted_after_failed_attempts(): ...
def test_get_money_for_resupply_avoids_exhausted_location(): ...
def test_get_money_for_resupply_switches_source_after_exhaustion(): ...
```

## 17.3. Scheduled action skip

```python
def test_scheduled_action_skip_summary_preserves_objective_and_intent(): ...
def test_scheduled_action_skip_summary_records_combat_readiness_reason(): ...
def test_support_source_exhausted_interrupts_explore_scheduled_action(): ...
```

## 17.4. Memory policy

```python
def test_active_plan_created_is_trace_only_not_memory_v3(): ...
def test_active_plan_completed_is_trace_only_not_memory_v3(): ...
def test_active_plan_step_failed_is_memory_v3(): ...
def test_memory_v3_not_dominated_by_active_plan_lifecycle_long_run(): ...
```

## 17.5. Objective key mapping

```python
def test_flee_emission_memory_uses_reach_safe_shelter_not_unknown(): ...
def test_wait_in_shelter_memory_uses_wait_in_shelter_not_unknown(): ...
def test_seek_water_food_rest_do_not_record_unknown_objective(): ...
```

## 17.6. Death invariants

```python
def test_emission_death_sets_hp_zero_and_clears_runtime(): ...
def test_combat_death_sets_hp_zero_and_clears_runtime(): ...
def test_dead_agent_has_no_queued_brain_decision(): ...
def test_dead_agent_does_not_receive_new_semantic_memory(): ...
```

## 17.7. Export/story timeline

```typescript
test("npc history export builds story_events from memory_v3", ...)
test("objective decisions appear in story_events without legacy memory", ...)
test("export schema is npc_history_v2", ...)
```

---

# 18. Required validation scenario

Run:

```text
10 NPCs
x600 auto-run
several thousand turns
include killer NPC
include at least one emission
export full_debug/history
```

Check:

```text
[ ] Killer may choose not to attack stronger target, but reason is explicit.
[ ] Killer does not loop forever on one anomaly.
[ ] GET_MONEY_FOR_RESUPPLY has progress/failure/exhaustion.
[ ] memory_v3.records_count <= 500.
[ ] active_plan_created/completed are not top memory kinds.
[ ] story_events are populated.
[ ] dead NPCs have hp=0 and clean runtime.
[ ] no normal objective_decision has objective_key=UNKNOWN.
[ ] Effective speed remains improved, roughly x150–x200 for 10 NPCs or better.
```

---

# 19. Required grep checks

Do not reintroduce legacy memory:

```bash
git grep -n "agent.get("memory"" backend/app backend/tests || true
git grep -n "agent\["memory"\]" backend/app backend/tests || true
git grep -n "setdefault("memory"" backend/app backend/tests || true
git grep -n ""memory": \[\]" backend/app backend/tests || true
```

Expected:

```text
No runtime/test usage.
```

Also check low-value lifecycle memory:

```bash
git grep -n "active_plan_created" backend/app/games/zone_stalkers/memory backend/tests
git grep -n "active_plan_completed" backend/app/games/zone_stalkers/memory backend/tests
```

Expected:

```text
Only policy/tests/debug trace references, not normal memory writes.
```

---

# 20. Implementation order

Recommended order:

```text
1. Add memory event policy: stop low-value active_plan lifecycle records from memory_v3.
2. Add support-source exhaustion for GET_MONEY_FOR_RESUPPLY exploration.
3. Add scheduled action skip summary improvements.
4. Add combat-readiness explanation for kill_stalker support objectives.
5. Fix objective_key fallback mappings.
6. Fix WAIT_IN_SHELTER valid_until.
7. Add canonical death helper and migrate death paths.
8. Disable semantic consolidation for dead agents.
9. Add repeated observation dedup.
10. Fix frontend/export story timeline from memory_v3.
11. Add focused tests.
12. Run full backend non-e2e + Brain v3 E2E + frontend build.
13. Run 10-NPC manual validation.
```

---

# 21. Definition of Done

This cleanup is complete when:

```text
[ ] Not attacking a visible target can be correct, but always has explicit combat-readiness reason.
[ ] GET_MONEY_FOR_RESUPPLY cannot loop forever on the same location.
[ ] Anomaly/location exploration records success/failure/exhaustion.
[ ] Planner avoids exhausted support sources.
[ ] scheduled_action skip summaries preserve objective/intent.
[ ] memory_v3 no longer stores low-value active_plan lifecycle spam.
[ ] memory_v3 remains capped at 500.
[ ] meaningful semantic/threat/target records survive eviction.
[ ] known intents do not produce objective_key=UNKNOWN.
[ ] WAIT_IN_SHELTER does not cause per-tick Brain/plan churn.
[ ] dead NPC state is normalized.
[ ] dead NPCs do not receive new semantic memory.
[ ] story_events/debug timeline is populated from memory_v3/trace/events.
[ ] no legacy agent["memory"] returns.
[ ] backend tests are green.
[ ] Brain v3 E2E tests are green.
[ ] frontend build is green.
[ ] 10-NPC manual validation still shows improved Effective speed.
```

---

# 22. Summary for Copilot

Do not force all killers to attack visible targets.

The real issue is:

```text
NPC support behavior lacks progress/failure/exhaustion semantics,
memory_v3 stores execution noise,
debug output does not explain decisions,
and exports are not fully memory_v3-aware.
```

Fix the system so cautious behavior is:

```text
reasonable,
bounded,
explainable,
memory-clean,
and testable.
```
