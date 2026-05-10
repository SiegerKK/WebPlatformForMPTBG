# CPU Optimization PR 4 — Brain Invalidation and AI Decision Budget

> Base:
>
> ```text
> after CPU Optimization PR 3 — Event-Driven Actions and Lazy Needs
> ```
>
> Goal:
>
> ```text
> Stop running full NPC Brain decisions unless something relevant changed.
> Limit non-urgent AI decisions per tick to prevent CPU spikes.
> ```
>
> This PR must preserve NPC intelligence and responsiveness to urgent events.

---

# 1. Problem

NPC Brain v3 can be expensive:

```text
belief state
memory retrieval
objective generation
objective scoring
active plan composition
debug trace
```

A stable NPC does not need to rethink every tick if it is:

```text
- traveling;
- sleeping;
- executing a valid active plan;
- waiting for scheduled action completion;
- not seeing new threats/opportunities;
- not crossing need thresholds.
```

Target behavior:

```text
NPC keeps following its chosen plan until invalidated or cache expires.
Urgent events bypass all budgets.
```

---

# 2. Scope

## In scope

```text
1. brain_runtime per agent.
2. Brain invalidation helpers.
3. should_run_brain().
4. valid_until_turn.
5. decision queue.
6. per-tick AI budget.
7. urgent bypass.
8. debug visibility for skipped/cached decisions.
9. tests and E2E.
```

## Out of scope

Do not do here:

```text
- change objective scoring semantics;
- rewrite hunt mechanics;
- rewrite active plan system;
- change combat mechanics;
- remove memory model.
```

---

# 3. brain_runtime state

Add per agent:

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
    "invalidators": [],
    "queued": false,
    "queued_turn": null,
    "queued_priority": null,
    "last_skip_reason": null
  }
}
```

---

# 4. Invalidation helpers

## 4.1. Add file

```text
backend/app/games/zone_stalkers/decision/brain_runtime.py
```

## 4.2. API

```python
def ensure_brain_runtime(agent: dict, world_turn: int) -> dict:
    ...

def invalidate_brain(
    agent: dict,
    runtime,
    *,
    reason: str,
    priority: str = "normal",
    world_turn: int | None = None,
) -> None:
    br = ensure_brain_runtime(agent, world_turn or 0)
    br["invalidated"] = True
    br.setdefault("invalidators", []).append({
        "reason": reason,
        "priority": priority,
        "world_turn": world_turn,
    })
    if runtime:
        runtime.mark_agent_dirty(agent["id"])

def clear_brain_invalidators(agent: dict) -> None:
    ...

def should_run_brain(agent: dict, world_turn: int) -> tuple[bool, str]:
    ...
```

## 4.3. should_run_brain

```python
def should_run_brain(agent: dict, world_turn: int) -> tuple[bool, str]:
    br = ensure_brain_runtime(agent, world_turn)

    if not agent.get("is_alive", True):
        return False, "dead"

    if agent.get("has_left_zone"):
        return False, "left_zone"

    if br.get("invalidated"):
        return True, "invalidated"

    if world_turn >= int(br.get("valid_until_turn") or 0):
        return True, "expired"

    if not agent.get("active_plan_v3") and not agent.get("scheduled_action"):
        return True, "no_plan_or_action"

    return False, "cached_until_valid"
```

---

# 5. Invalidation reasons

Brain must be invalidated immediately for:

## Urgent

```text
combat_started
combat_damage_taken
critical_hp
critical_thirst
critical_hunger
emission_warning_started
emission_started
target_co_located
target_seen
scheduled_action_interrupted
```

## High

```text
agent_arrived
plan_completed
plan_failed
active_plan_step_failed
inventory_changed
money_changed_significantly
artifact_found
target_intel_received
target_not_found
target_location_exhausted
trader_unavailable
```

## Normal

```text
need_soft_threshold_crossed
sleepiness_soft_threshold_crossed
new_location_observed
trade_opportunity_changed
```

## Low

```text
idle_refresh
social_opportunity
periodic_reconsideration
```

---

# 6. Where to call invalidation

Add invalidation calls in:

```text
travel arrival / scheduled task completion
active plan completed/failed
executor writes target_seen
executor writes target_not_found
question_witnesses receives target intel
inventory/equipment changes
money changes
hp changes
critical need threshold task
emission warning/start/end
combat events
artifact pickup
trade result
agent global_goal/current_goal change
```

Important hunt-specific invalidators:

```text
target_seen:
  urgent/high, should lead to ENGAGE_TARGET

target_intel:
  high for hunter

target_not_found:
  high/normal for hunter, should recompute hunt belief

witness_source_exhausted:
  normal/high for hunter if current plan was GATHER_INTEL

target_location_exhausted:
  high for hunter
```

---

# 7. Validity duration

After a successful decision, set validity window.

Suggested:

```text
combat / danger:
  valid_until_turn = world_turn

target visible / hunter engage:
  valid_until_turn = world_turn

active hunt search:
  valid_until_turn = world_turn + 1..3

economic plan:
  valid_until_turn = world_turn + 10..30

long travel/sleep/explore:
  valid_until_turn = scheduled_action.ends_turn
  or min(ends_turn, world_turn + 60)

idle:
  valid_until_turn = world_turn + 30..120
```

Do not let validity suppress urgent invalidators.

---

# 8. Decision queue and AI budget

## 8.1. Runtime structure

In state runtime or state:

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

This can be runtime-only if decisions are evaluated inside one tick. If queue persists across ticks, keep it in state with bounded size.

## 8.2. Budget config

```json
{
  "ai_budget": {
    "enabled": true,
    "max_normal_decisions_per_tick": 5,
    "max_background_decisions_per_tick": 2,
    "urgent_decisions_ignore_budget": true,
    "max_decision_delay_turns": 10
  }
}
```

Defaults:

```text
enabled = true
max_normal_decisions_per_tick = 5
max_background_decisions_per_tick = 2
urgent bypass = true
```

## 8.3. Priority order

```text
urgent > high > normal > low
then queued_turn ascending
```

## 8.4. Starvation prevention

If:

```text
world_turn - queued_turn >= max_decision_delay_turns
```

promote priority.

## 8.5. Urgent bypass

The following must never wait:

```text
combat
emission
critical HP
critical thirst/hunger
target co-located for hunter
target_seen
```

They run immediately even if budget is exhausted.

---

# 9. Main decision loop changes

Current pattern may be close to:

```text
for every agent:
  maybe run brain
```

New pattern:

```python
for agent_id, agent in agents.items():
    should_run, reason = should_run_brain(agent, world_turn)
    if not should_run:
        br["last_skip_reason"] = reason
        profiler.inc("npc_brain_skipped_count")
        continue

    priority = determine_decision_priority(agent)
    enqueue_or_run(agent_id, priority, reason)
```

Then:

```python
run urgent immediately
run high within budget or elevated budget
run normal up to max_normal_decisions_per_tick
run low/background up to max_background_decisions_per_tick
```

When brain decision runs:

```text
clear invalidated
clear invalidators
set last_decision_turn
set valid_until_turn
set last_objective_key
set last_intent_kind
set last_plan_key
increment decision_revision
```

---

# 10. Debug/UI visibility

NPC profile should show:

```text
Brain Runtime
  last_decision_turn
  valid_until_turn
  invalidated
  invalidators
  queued
  queued_priority
  queued_turn
  last_skip_reason
  decision_revision
```

This is important so skipped decisions do not look like the NPC is unresponsive.

Debug export should include compact brain_runtime.

---

# 11. Tests

Add:

```text
backend/tests/decision/v3/test_brain_invalidation.py
backend/tests/decision/v3/test_ai_budget.py
```

## Required tests

```python
def test_brain_skips_when_valid_plan_not_invalidated():
    ...

def test_plan_completed_invalidates_brain():
    ...

def test_target_seen_invalidates_and_runs_immediately():
    ...

def test_emission_warning_invalidates_all_exposed_agents():
    ...

def test_critical_need_bypasses_budget():
    ...

def test_normal_decisions_are_budgeted():
    ...

def test_urgent_decisions_ignore_budget():
    ...

def test_agent_is_not_starved_by_budget():
    ...

def test_hunter_reacts_to_new_target_intel_even_with_cache():
    ...

def test_get_rich_npc_still_progresses_with_decision_cache():
    ...
```

E2E:

```text
get_rich scenario
kill_target scenario
emission survival
sleep/rest/resupply
```

---

# 12. Manual acceptance

Run:

```text
1. 20 NPC mostly traveling.
   Expected: brain_decision_count per tick low.

2. Hunter receives target intel.
   Expected: immediate invalidation and hunt decision.

3. Hunter sees target.
   Expected: immediate ENGAGE_TARGET.

4. Emission warning.
   Expected: exposed agents react immediately.

5. Many agents arrive same tick.
   Expected: urgent/high handled; normal decisions spread by budget.

6. Debug profile.
   Expected: brain_runtime explains skipped/cached decisions.
```

---

# 13. Acceptance criteria

```text
[ ] Brain does not run every tick for stable agents.
[ ] Invalidators cause immediate reconsideration.
[ ] Urgent decisions bypass budget.
[ ] Normal decisions are capped per tick.
[ ] No starvation.
[ ] Hunt behavior remains correct.
[ ] Get-rich behavior remains correct.
[ ] Emission/combat/critical needs remain responsive.
[ ] NPC profile exposes brain_runtime.
[ ] Profiler shows reduced npc_brain_total_ms and decision_count.
```

---

# 14. Risks

## Risk: NPC becomes unresponsive

Mitigation:

```text
complete invalidator list
urgent bypass
short validity windows for risky objectives
E2E tests
```

## Risk: hunter misses target due to cached decision

Mitigation:

```text
target_seen and target_co_located are urgent invalidators
target_intel is high invalidator
```

## Risk: budget delays too much

Mitigation:

```text
max_decision_delay_turns
priority promotion
urgent bypass
debug visibility
```

---

# 15. Definition of done

This PR is done when the simulation still behaves intelligently, but profiler shows:

```text
npc_brain_decision_count << agents_total
npc_brain_skipped_count high for stable agents
npc_brain_total_ms reduced
urgent decisions still immediate
```

This is the PR that makes Brain v3 scalable.
