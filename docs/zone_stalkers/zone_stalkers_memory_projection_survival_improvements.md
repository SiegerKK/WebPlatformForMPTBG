# Zone Stalkers — Memory Compression, Debug Projection and Survival Fallback Improvements

## Purpose

This PR follows CPU PR5 and the NPC logic/memory correctness fixes.

Recent NPC logs show that the previous fixes worked in several important areas:

```text
- death state is now normalized;
- active_plan_created / active_plan_completed no longer dominate memory_v3;
- npc_history_v2 story_events are populated;
- anomaly_search_exhausted / support-source exhaustion started working;
- memory_v3 cap remains enforced at 500 records.
```

However, the new logs reveal the next layer of systemic issues:

```text
1. memory_v3 is still always full and now dominated by repeated stalkers_seen observations.
2. full_debug does not include the story timeline even though history export does.
3. has_left_zone/global_goal_achieved agents still project active survival needs/objectives.
4. emergency survival fallback is weak: NPCs can sit in a trader bunker with critical thirst/hunger and still fail to recover.
5. stale scheduled_action can continue after current objective changes to REST/restore_needs.
6. memory/debug metrics are not yet good enough to quickly detect these problems.
```

This PR is about **simulation hygiene and observability**, not raw CPU optimization.

---

# 1. Observed issues from current logs

## 1.1. memory_v3 is full for every NPC

All inspected NPCs have:

```text
memory_v3.records_count = 500 / 500
```

The cap works, but the memory is always saturated.

This means eviction/compression policy is now critical for correctness: if low-value repeated records fill the cap, meaningful target/threat/semantic facts can be displaced.

---

## 1.2. stalkers_seen is the new dominant memory noise

Across the latest sample of 10 NPCs:

```text
total memory_v3 records: 5000
stalkers_seen records: 3093
share: 61.9%
```

Examples:

```text
Убийца 0:    stalkers_seen = 429 / 500
Сталкер #2:  stalkers_seen = 385 / 500
Сталкер #4:  stalkers_seen = 383 / 500
Сталкер #5:  stalkers_seen = 380 / 500
Сталкер #1:  stalkers_seen = 325 / 500
```

This is better than plan lifecycle spam, because social observations may be useful, but the current volume is still excessive.

`memory_v3` is still too much of an episodic observation log and not enough of a compact working memory.

---

## 1.3. semantic layer is too small

Across the same sample:

```text
episodic: 3874 records, 77.5%
goal:      808 records, 16.2%
semantic:  243 records,  4.9%
threat:     58 records,  1.2%
spatial:     9 records,  0.2%
social:      8 records,  0.2%
```

Expected direction:

```text
fewer repeated episodic observations
more compact semantic/social/spatial facts
```

---

## 1.4. full_debug misses story timeline

The history export now uses:

```text
export_schema = npc_history_v2
story_events = [...]
```

and story events are populated.

But `full_debug` exports still have empty or missing `story_events`.

This creates a projection gap:

```text
history JSON:
  good timeline

full_debug JSON:
  current state only, no story timeline
```

For diagnosis, `full_debug` should include at least a compact story timeline or reference the same projection logic used by history export.

---

## 1.5. has_left_zone terminal state is confusing

Example:

```text
Убийца 1:
  is_alive = true
  has_left_zone = true
  global_goal_achieved = true
  location_name = "🚪 Покинул Зону"
  hp = 2
  hunger = 100
  thirst = 100
  sleepiness = 100
  current_goal = restore_needs
  active_objective = RESTORE_WATER
```

This may be internally harmless if the agent is skipped by tick logic, but debug projection is misleading.

If an NPC has left the Zone and completed its global goal, UI/debug should not present it as an actively struggling NPC trying to restore needs.

---

## 1.6. Emergency survival fallback appears weak

Example:

```text
Убийца 0:
  location = Бункер торговца
  hp = 24
  hunger = 100
  thirst = 100
  sleepiness = 100
  money = 38
  current_goal = restore_needs
  active_objective = RESTORE_WATER
```

Likely issue:

```text
water costs more than available money,
but NPC does not reliably sell low-value inventory / ammo / bandage to buy water.
```

This can leave NPCs stuck in critical needs despite being at a trader hub.

---

## 1.7. Stale scheduled actions after objective changes

Examples:

```text
Сталкер #5:
  current_goal = restore_needs
  active_objective = REST
  scheduled_action = explore_anomaly_location
  active_plan_v3.objective_key = FIND_ARTIFACTS

Сталкер #6:
  current_goal = restore_needs
  active_objective = REST
  scheduled_action = explore_anomaly_location
  active_plan_v3.objective_key = FIND_ARTIFACTS
```

This may be valid only if:

```text
- REST is not actually urgent;
- sleepiness scale is inverted and 0 means exhausted;
- scheduled action has a safe completion window;
```

but as projected, it looks like a stale action.

The system needs a clear rule for when restore-needs objectives interrupt old exploration/travel actions.

---

# 2. Goals

## Functional goals

```text
1. Compress repeated social/location observations.
2. Increase semantic/social/spatial usefulness of memory_v3.
3. Include compact story timeline in full_debug.
4. Project left-zone agents as terminal/completed, not active restore-needs agents.
5. Add emergency survival trade fallback.
6. Make restore-needs interrupt behavior explicit and testable.
7. Add memory/debug stats so future logs are easier to inspect.
```

## Non-goals

```text
- Do not reintroduce legacy agent["memory"].
- Do not increase MEMORY_V3_MAX_RECORDS above 500.
- Do not remove memory_v3 retrieval optimization.
- Do not rewrite Brain v3.
- Do not force all NPCs to avoid the trader bunker.
- Do not force killers to attack if combat-readiness says not ready.
```

---

# 3. Fix A — Compress repeated stalkers_seen observations

## Problem

`stalkers_seen` dominates memory_v3.

Repeated sightings of the same people in the same location should not create hundreds of separate episodic records.

## Files to inspect

```text
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/decay.py
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/context_builder.py
```

## Required behavior

For repeated social observations:

```text
same location
same observed entity set or overlapping observed entity set
within dedup window
⇒ update an aggregate/semantic record
⇒ do not create another full episodic record
```

## Suggested record model

Instead of many records like:

```text
kind = stalkers_seen
location_id = loc_debug_61
entity_ids = [trader_sidor, agent_debug_0, agent_debug_7, ...]
created_turn = 4011

kind = stalkers_seen
location_id = loc_debug_61
entity_ids = [trader_sidor, agent_debug_0, agent_debug_7, ...]
created_turn = 4021
```

Use one compact record:

```json
{
  "kind": "semantic_stalkers_seen",
  "layer": "semantic",
  "location_id": "loc_debug_61",
  "entity_ids": ["trader_sidor", "agent_debug_0", "agent_debug_7"],
  "details": {
    "first_seen_turn": 4011,
    "last_seen_turn": 4381,
    "times_seen": 37,
    "seen_names": ["Гнидорович", "Сталкер #0", "Сталкер #7"],
    "unique_entity_count": 3,
    "last_observed_group_size": 3
  },
  "tags": ["social", "location_population", "stalkers_seen"]
}
```

## Dedup signature

```python
def social_observation_signature(raw: dict[str, Any]) -> tuple:
    return (
        "stalkers_seen",
        raw.get("location_id"),
        tuple(sorted(raw.get("entity_ids") or [])),
    )
```

For large groups, exact set matching may be too strict. Add a second mode:

```text
same location
entity overlap >= 70%
within dedup window
⇒ merge into existing aggregate
```

## Dedup window

Recommended initial values:

```python
STALKERS_SEEN_DEDUP_WINDOW_TURNS = 60
STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION = 5
```

## Memory policy

For `stalkers_seen`:

```text
- keep a few recent episodic samples per location;
- keep/update semantic aggregate;
- do not let stalkers_seen exceed a per-agent memory budget.
```

Suggested budget:

```python
MEMORY_V3_MAX_STALKERS_SEEN_RECORDS = 75
```

This can be soft budget used during trim.

## Acceptance criteria

```text
[ ] stalkers_seen no longer occupies >50% of memory_v3 in long runs.
[ ] repeated same-location sightings update semantic aggregate.
[ ] context_builder still sees known entities from memory_v3.
[ ] known traders/stalkers are not lost.
[ ] memory_v3.records_count remains <= 500.
```

## Tests

```python
def test_repeated_stalkers_seen_merges_into_semantic_record(): ...
def test_stalkers_seen_budget_limits_episodic_records(): ...
def test_context_builder_reads_known_entities_from_merged_stalkers_seen(): ...
def test_stalkers_seen_merge_preserves_last_seen_turn_and_times_seen(): ...
```

---

# 4. Fix B — Compress repeated travel_hop/location observations

## Problem

`travel_hop` is not as dominant as `stalkers_seen`, but it is still repeated in long runs.

## Required behavior

Repeated travel along the same route should update route familiarity rather than create endless episodic records.

## Suggested aggregate

```json
{
  "kind": "semantic_route_traveled",
  "layer": "spatial",
  "details": {
    "from_location_id": "loc_A",
    "to_location_id": "loc_B",
    "times_traveled": 12,
    "last_traveled_turn": 4410,
    "known_safe": true,
    "known_risky": false
  },
  "tags": ["route", "travel", "spatial"]
}
```

## Acceptance criteria

```text
[ ] repeated travel_hop does not grow unbounded.
[ ] route familiarity remains available to planning/context.
[ ] travel_hop does not dominate memory_v3.
```

## Tests

```python
def test_repeated_travel_hop_updates_route_semantic_memory(): ...
def test_route_semantic_memory_preserves_last_traveled_turn(): ...
```

---

# 5. Fix C — Add memory composition metrics to debug export

## Problem

Current manual analysis requires parsing JSON externally.

Every full_debug should expose memory composition directly.

## Required fields

Add to agent debug projection:

```json
{
  "memory_v3_stats": {
    "records_count": 500,
    "records_cap": 500,
    "cap_utilization": 1.0,
    "by_layer": {
      "episodic": 380,
      "goal": 80,
      "semantic": 30,
      "threat": 10
    },
    "top_kinds": [
      ["stalkers_seen", 120],
      ["objective_decision", 80]
    ],
    "semantic_ratio": 0.06,
    "episodic_ratio": 0.76,
    "stalkers_seen_ratio": 0.24,
    "travel_hop_ratio": 0.04,
    "last_record_turn": 4410,
    "oldest_record_turn": 61
  }
}
```

Also include warning flags:

```json
{
  "memory_health": {
    "is_at_cap": true,
    "stalkers_seen_dominates": true,
    "semantic_ratio_low": true,
    "top_kind": "stalkers_seen"
  }
}
```

## Acceptance criteria

```text
[ ] full_debug directly shows memory composition.
[ ] memory health flags identify noisy memory.
[ ] manual log review no longer requires custom parsing for basic stats.
```

## Tests

```python
def test_full_debug_includes_memory_v3_stats(): ...
def test_memory_health_flags_stalkers_seen_dominance(): ...
```

---

# 6. Fix D — Include compact story_events in full_debug

## Problem

`history` export has populated story events, but `full_debug` does not.

## Files to inspect

```text
frontend/src/games/zone_stalkers/ui/agent_profile/exportNpcHistory.ts
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/*
backend/app/games/zone_stalkers/*projection*
backend/app/games/zone_stalkers/*debug*
```

## Required behavior

`full_debug` should include a compact timeline:

```json
{
  "story_events": [
    {
      "turn": 4381,
      "kind": "objective_decision",
      "summary": "Выбрана цель FIND_ARTIFACTS...",
      "source": "memory_v3"
    }
  ],
  "story_events_count": 120,
  "story_events_truncated": true
}
```

Use the same projection logic as `npc_history_v2`.

## Size control

Do not dump all memory.

Use:

```python
FULL_DEBUG_STORY_EVENTS_LIMIT = 50
```

or frontend equivalent.

Recommended:

```text
- latest 50 story events;
- important events always included if recent:
  death, combat, emission, objective_decision, support_source_exhausted, global_goal_completed.
```

## Acceptance criteria

```text
[ ] full_debug includes story_events.
[ ] history and full_debug use consistent story projection.
[ ] story_events in full_debug are capped.
[ ] full_debug remains readable and not huge.
```

## Tests

```python
def test_full_debug_includes_compact_story_events(): ...
def test_full_debug_story_events_are_capped(): ...
def test_full_debug_story_events_come_from_memory_v3_without_legacy_memory(): ...
```

Frontend test if projection is frontend-only:

```typescript
test("full debug export includes compact story events", ...)
```

---

# 7. Fix E — Terminal projection for has_left_zone agents

## Problem

Agents that already left the Zone can still be displayed as if they are currently solving survival needs.

Example:

```text
has_left_zone = true
global_goal_achieved = true
location_name = "🚪 Покинул Зону"
current_goal = restore_needs
active_objective = RESTORE_WATER
hunger/thirst/sleepiness = 100
```

## Required behavior

If:

```python
agent.get("has_left_zone") is True
```

or location is terminal `left_zone`:

```text
- debug/UI projection should show terminal state;
- active survival objectives should be hidden or marked stale;
- Brain should not run;
- scheduled_action should be null;
```

## Projection rule

In debug/export projection:

```python
if agent.get("has_left_zone"):
    projected_current_goal = "left_zone"
    projected_active_objective = {
        "key": "LEFT_ZONE",
        "score": 0,
        "source": "terminal_state",
        "reason": "NPC покинул Зону"
    }
    projected_adapter_intent = None
```

Also include:

```json
{
  "terminal_state": {
    "kind": "left_zone",
    "global_goal_achieved": true,
    "left_zone_turn": 4412
  }
}
```

## Runtime rule

If not already guaranteed:

```text
has_left_zone agents should be skipped by Brain/tick action logic.
```

## Acceptance criteria

```text
[ ] left-zone agents do not display active RESTORE_WATER/RESTORE_FOOD objectives.
[ ] left-zone agents show terminal_state in full_debug/history.
[ ] left-zone agents are not scheduled for new actions.
[ ] old needs values may remain as final snapshot but are clearly inactive.
```

## Tests

```python
def test_left_zone_agent_projects_terminal_goal_not_restore_needs(): ...
def test_left_zone_agent_has_no_active_objective_in_debug_projection(): ...
def test_left_zone_agent_is_skipped_by_brain_runtime(): ...
```

---

# 8. Fix F — Emergency survival trade fallback

## Problem

NPC can be in trader bunker with critical thirst/hunger but fail to recover because it lacks enough cash.

Example:

```text
location = trader bunker
thirst = 100
hunger = 100
sleepiness = 100
hp = low
money = 38
water price likely > money
inventory has sellable items
```

## Required behavior

When survival need is critical and trader is available:

```text
1. Try to consume existing item.
2. If no item, try to buy item.
3. If not enough money, sell low-priority item.
4. Buy survival item.
5. Consume immediately.
```

## Files to inspect

```text
backend/app/games/zone_stalkers/decision/objectives/generator.py
backend/app/games/zone_stalkers/decision/active_plan_composer.py
backend/app/games/zone_stalkers/rules/trade_rules.py
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/rules/actions/*
```

## Emergency sell policy

Allowed to sell in emergency:

```text
- low-value artifact if not needed for immediate goal;
- excess ammo not compatible with equipped weapon;
- duplicate medicine beyond emergency reserve;
- bandage if thirst/starvation is immediately life-threatening;
- low-value loot.
```

Do not sell:

```text
- equipped weapon;
- only compatible ammo stack if combat/hunt goal active;
- equipped armor unless death is otherwise imminent;
- quest/critical target item.
```

## Suggested helper

```python
def plan_emergency_survival_trade(
    *,
    agent: dict[str, Any],
    need_kind: Literal["water", "food", "medicine"],
    trader: dict[str, Any],
    world_turn: int,
) -> list[PlanStep] | None:
    ...
```

Expected plan:

```text
sell_item
buy_item
consume_item
```

## Acceptance criteria

```text
[ ] NPC with critical thirst at trader and insufficient money can sell low-priority item.
[ ] NPC buys water after selling.
[ ] NPC consumes water immediately.
[ ] Critical thirst loop does not repair/abort forever.
[ ] NPC does not sell equipped weapon/armor as first resort.
```

## Tests

```python
def test_critical_thirst_at_trader_sells_low_priority_item_to_buy_water(): ...
def test_critical_hunger_at_trader_sells_low_priority_item_to_buy_food(): ...
def test_emergency_trade_does_not_sell_equipped_weapon(): ...
def test_emergency_trade_consumes_item_after_buying(): ...
def test_restore_water_does_not_loop_when_money_initially_insufficient(): ...
```

---

# 9. Fix G — Restore-needs interrupt semantics for stale scheduled actions

## Problem

Current objective may become REST/RESTORE_WATER/RESTORE_FOOD while scheduled_action continues old exploration/travel.

Example:

```text
active_objective = REST
scheduled_action = explore_anomaly_location
active_plan_v3.objective_key = FIND_ARTIFACTS
```

## Required behavior

Define explicit interruption thresholds.

## Thresholds

Hard interrupts:

```text
thirst >= 90
hunger >= 90
hp <= 30
emission immediate
```

Soft interrupts:

```text
sleepiness >= configured high threshold
thirst >= 70
hunger >= 70
```

For hard interrupts:

```text
cancel old scheduled_action immediately
abort/pause active_plan_v3
start restore-needs objective
```

For soft interrupts:

```text
allow current short action to complete if remaining <= small threshold
otherwise interrupt
```

## Sleepiness scale clarification

The logs show possible ambiguity:

```text
sleepiness = 0
active_objective = REST
reason = "Сильная усталость"
```

Before implementing rules, confirm scale:

```text
Option A:
  sleepiness 0 = not sleepy, 100 = very sleepy

Option B:
  sleepiness 0 = exhausted, 100 = rested
```

The field name strongly suggests Option A, but behavior may use Option B.

## Required action

Add explicit debug field:

```json
{
  "sleep_need": {
    "raw_sleepiness": 0,
    "interpreted_fatigue": 100,
    "scale": "sleepiness_low_means_tired"
  }
}
```

or rename internally/projection:

```text
sleepiness → rest_level
fatigue → actual need
```

Do not change gameplay semantics blindly until scale is confirmed.

## Acceptance criteria

```text
[ ] critical thirst/hunger/hp interrupts old exploration action.
[ ] soft rest need does not always interrupt short actions.
[ ] debug projection explains sleepiness/fatigue scale.
[ ] no confusing REST objective with unexplained sleepiness=0.
```

## Tests

```python
def test_critical_thirst_interrupts_explore_anomaly_action(): ...
def test_soft_rest_need_allows_short_action_to_finish(): ...
def test_sleepiness_projection_explains_scale(): ...
```

---

# 10. Fix H — Improve planner use of support_source_exhausted

## Problem

The anti-loop fix works but can trigger late:

```text
plan chooses travel
plan_monitor later aborts because support source is exhausted
```

Better behavior:

```text
planner should not choose exhausted source in the first place.
```

## Required behavior

Before creating plan steps:

```python
if source_exhausted(agent, source_id, objective_key, world_turn):
    skip source
```

Apply to:

```text
FIND_ARTIFACTS
GET_MONEY_FOR_RESUPPLY
RESUPPLY_* if source is trader/location-specific
GATHER_INTEL if witness/tracks exhausted
```

## Acceptance criteria

```text
[ ] planner filters exhausted sources before plan creation.
[ ] plan_monitor remains safety net, not primary filter.
[ ] action_aborted/support_source_exhausted decreases in long run.
```

## Tests

```python
def test_planner_does_not_select_exhausted_artifact_location(): ...
def test_plan_monitor_support_source_exhausted_is_safety_net_only(): ...
```

---

# 11. Add aggregate memory statistics command/test utility

## Goal

Make it easy to reproduce the current manual memory analysis.

## Suggested script

Add dev utility:

```text
scripts/zone_stalkers/analyze_npc_memory_exports.py
```

Input:

```text
folder with *_full_debug*.json and *_history*.json
```

Output:

```text
- per-agent table;
- aggregate layer counts;
- aggregate kind counts;
- top noisy kinds;
- terminal state anomalies;
- dead state anomalies;
- full_debug/history projection gaps.
```

Example command:

```bash
python scripts/zone_stalkers/analyze_npc_memory_exports.py ./debug_exports
```

## Acceptance criteria

```text
[ ] Script parses exported NPC JSON files.
[ ] Script reports memory_v3 composition.
[ ] Script reports agents at cap.
[ ] Script reports top kinds by count.
[ ] Script reports left_zone but active objective anomalies.
[ ] Script reports dead but hp>0 anomalies.
```

This is a dev tool, not production runtime.

---

# 12. Validation scenario

Run the same kind of simulation again.

## Scenario

```text
10 NPCs
x600 auto-run
4000+ turns
include emissions
export full_debug + history for all NPCs
```

## Expected checks

```text
[ ] memory_v3.records_count <= 500 for all NPCs.
[ ] stalkers_seen is not >50% of memory for most NPCs.
[ ] semantic_ratio increases compared with current logs.
[ ] full_debug includes story_events.
[ ] left_zone agents show terminal_state and no active restore-needs objective.
[ ] critical thirst/hunger at trader can resolve via emergency trade.
[ ] stale exploration actions are interrupted by critical survival needs.
[ ] support_source_exhausted abort frequency decreases.
[ ] death state remains normalized.
```

---

# 13. Required test groups

Add focused tests:

```bash
pytest backend/tests/decision/v3/test_memory_observation_compression.py -q
pytest backend/tests/decision/v3/test_debug_projection_story_events.py -q
pytest backend/tests/decision/v3/test_left_zone_terminal_projection.py -q
pytest backend/tests/decision/v3/test_emergency_survival_trade.py -q
pytest backend/tests/decision/v3/test_restore_needs_interrupts.py -q
pytest backend/tests/decision/v3/test_support_source_preplanning.py -q
```

Run existing suites:

```bash
pytest backend/tests/decision/v3/test_memory_store.py -q
pytest backend/tests/decision/v3/test_memory_retrieval.py -q
pytest backend/tests/decision/v3/test_memory_v3_only_runtime.py -q
pytest backend/tests/decision/test_context_builder_memory_v3.py -q
pytest backend/tests -k "not e2e" -q
```

Run E2E:

```bash
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
```

Run frontend build if projection/export changes:

```bash
cd frontend
npm run build
```

---

# 14. Definition of Done

This PR is complete when:

```text
[ ] repeated stalkers_seen records are compressed/aggregated;
[ ] stalkers_seen no longer dominates memory_v3 in long runs;
[ ] travel_hop has route/familiarity aggregation;
[ ] full_debug contains compact story_events;
[ ] full_debug contains memory_v3_stats and memory_health;
[ ] left-zone agents project as terminal/completed;
[ ] emergency survival trade can resolve critical thirst/hunger at trader;
[ ] old explore/travel actions are interrupted by critical restore-needs objectives;
[ ] sleepiness/fatigue scale is explicit in debug projection;
[ ] planner filters exhausted sources before plan creation;
[ ] dev memory export analyzer exists;
[ ] no legacy agent["memory"] returns;
[ ] backend tests are green;
[ ] Brain v3 E2E is green;
[ ] frontend build is green;
[ ] new 10-NPC validation shows cleaner memory composition.
```

---

# 15. Summary for Copilot

The previous PR fixed the first memory pollution layer.

The next problem is:

```text
memory_v3 is still full,
but now mostly with repeated social observations.
```

The next PR should make NPC memory more semantic and make debug exports more useful.

Main priorities:

```text
1. Compress stalkers_seen.
2. Include story_events in full_debug.
3. Project left-zone agents as terminal.
4. Add emergency survival trade fallback.
5. Interrupt stale scheduled actions on critical restore-needs.
6. Add memory composition stats and analyzer.
```

Do not reintroduce legacy memory and do not raise the 500-record cap.
