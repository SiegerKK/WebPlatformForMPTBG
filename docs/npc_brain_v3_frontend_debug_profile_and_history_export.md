# NPC Brain v3 — Frontend Debug UX and Compact NPC History Export

> Branch context: after PR 4 (`copilot/implement-pr-4-npc-brain-v3`)  
> Scope:
>
> 1. Доработать фронт профиля NPC так, чтобы новая система принятия решений читалась отдельно и понятно.
> 2. Оставить текущий широкий JSON-export для глубокого дебага.
> 3. Добавить второй export: компактная история NPC для чтения, анализа и передачи в Copilot/ChatGPT.
>
> Главная проблема сейчас:
>
> ```text
> AgentProfileModal уже знает про active_objective / objective_scores / memory_used,
> но UI всё ещё смешивает:
> - старую legacy memory;
> - intent-first wording;
> - objective-first decisions;
> - memory_v3 stats;
> - raw brain_trace.
> ```
>
> В итоге в профиле NPC получается мешанка из старой и новой систем.

---

# 1. Current state

## 1.1. Что уже есть

`AgentProfileModal.tsx` уже содержит типы:

```text
BrainTrace
BrainTraceEvent
BrainTraceObjectiveInfo
BrainTraceMemoryUsed
AgentForProfile.memory
AgentForProfile.brain_trace
AgentForProfile.active_plan_v3
AgentForProfile.memory_v3.stats
```

Также профиль уже умеет:

```text
- показывать базовые характеристики NPC;
- показывать inventory/equipment;
- показывать current_goal/global_goal;
- показывать legacy memory;
- подгружать full legacy memory через getAgentMemory(...);
- экспортировать JSON одной кнопкой;
- частично отображать brain_trace/objectives.
```

## 1.2. Что сейчас не так

Проблемы:

```text
1. New NPC Brain v3 data не выделены в отдельную читаемую секцию.
2. Objective-first decisions визуально смешаны с legacy intent/memory entries.
3. Пользователь видит и "intent", и "objective", но не понимает, что является причиной решения, а что adapter.
4. memory_v3 почти не раскрыта: есть stats, но нет понятного summary.
5. brain_trace показывает технические события, но не собирает "почему NPC так решил" в одну карточку.
6. Экспорт только один: широкий JSON dump.
7. Full dump после PR 3/4 стал большим из-за legacy memory + memory_v3.records + memory_v3.indexes.
8. Нет компактного "история NPC для чтения" export.
```

---

# 2. Target UX

Профиль NPC должен отвечать на вопросы:

```text
Что NPC сейчас делает?
Почему он это делает?
Какая цель выбрана?
Какие альтернативы были отвергнуты?
Какие потребности влияли?
Какая память использовалась?
Что будет дальше?
Какая история важных действий у NPC?
```

Не нужно заставлять пользователя читать raw JSON.

---

# 3. New UI structure inside AgentProfileModal

Рекомендуемый порядок секций:

```text
1. Header / status / export buttons
2. Current state
3. NPC Brain v3 — Current Decision
4. NPC Brain v3 — Objective Ranking
5. NPC Brain v3 — Needs & Constraints
6. NPC Brain v3 — Memory Used
7. Current Runtime Action
8. Goals
9. Equipment / Inventory
10. Memory Timeline
11. Memory v3 Summary
12. Raw Debug collapsible section
```

Главная новая секция:

```text
NPC Brain v3 — Что он сейчас думает
```

---

# 4. Section: NPC Brain v3 — Current Decision

## 4.1. Purpose

Показать главный вывод новой системы принятия решений.

## 4.2. Data source

Use:

```text
agent.brain_trace.current_thought
latest decision event from agent.brain_trace.events
latest event.active_objective
latest event.intent_kind
latest event.reason
agent._v2_context.objective_key if available
agent.current_goal
agent.scheduled_action
```

## 4.3. Layout

Example:

```text
🧠 NPC Brain v3

Текущая мысль:
Продолжаю explore_anomaly_location — action_still_valid.

Активная цель:
FIND_ARTIFACTS
Источник: global_goal
Score: 24%
Причина: Глобальная цель: get_rich

Исполнение:
adapter intent: get_rich
планировщик: explore_anomaly_location
scheduled action: explore_anomaly_location, осталось 19/30
```

## 4.4. Behavior

If latest event has `active_objective`:

```text
show objective as primary
show intent as adapter/execution detail
```

If no `active_objective` but has intent:

```text
show legacy mode warning:
"Legacy decision event — objective отсутствует"
```

If only plan monitor event:

```text
show:
"Сейчас нет нового решения, NPC продолжает действие"
```

---

# 5. Section: Objective Ranking

## 5.1. Purpose

Показать почему выбранная цель победила.

## 5.2. Data source

Use latest decision event:

```text
event.active_objective
event.objective_scores
event.alternatives
```

## 5.3. Layout

Example:

```text
🏆 Рейтинг целей

✅ GET_MONEY_FOR_RESUPPLY — 35%
   Не хватает денег для обязательного пополнения

2. SELL_ARTIFACTS — 31%
   rejected: нет артефакта

3. RESTORE_WATER — 22%
   rejected: ниже soft threshold

4. FIND_ARTIFACTS — 18%
   rejected: ниже score
```

## 5.4. Display rules

```text
- active objective always first;
- show score as percent;
- show source;
- show reason;
- show decision label:
  selected / rejected / continue_current / plan_unavailable;
- max visible by default: 5;
- optional "show all" if more.
```

---

# 6. Section: Needs & Constraints

## 6.1. Purpose

Показать, какие потребности и ограничения влияли на решение.

## 6.2. Data source

Latest decision event:

```text
event.immediate_needs
event.item_needs
event.liquidity
event.combat_readiness
```

## 6.3. Layout

Example:

```text
⚠️ Immediate needs
- none

🎒 Item needs
- weapon: 65% — Нет оружия
- ammo: 40% — Недостаточно патронов
- drink: 35% — Недостаточный запас воды

💰 Liquidity
- missing: 1460 RU
- safe sale options: 0
- planner allowed: get_money_first

⚔ Combat readiness
- ready: false
- blockers: no_weapon, low_ammo
```

## 6.4. Important distinction

Separate visually:

```text
Immediate needs = survival / immediate blocking
Item needs = reserve/equipment gaps
Soft needs = hunger/thirst/sleep pressure
Liquidity = money constraints
```

This avoids old confusion where all needs looked equally critical.

---

# 7. Section: Memory Used

## 7.1. Purpose

Показать только память, которая реально повлияла на последнее решение.

## 7.2. Data source

Latest decision event:

```text
event.memory_used
```

Do not dump full memory here.

## 7.3. Layout

Example:

```text
🧩 Использованная память

find_trader:
  Торговец в Бункере торговца
  kind: trader_location_known
  confidence: 90%

find_water:
  В Локации Б есть вода
  kind: water_source_known
  confidence: 80%
```

## 7.4. Rules

```text
- Max 5 entries.
- Group by used_for.
- Show confidence.
- Show kind.
- Show summary.
- If memory_used empty:
  "В этом решении память не использовалась напрямую."
```

Do not mix with general memory timeline.

---

# 8. Section: Current Runtime Action

## 8.1. Purpose

Показать что NPC реально делает сейчас, отдельно от "почему".

## 8.2. Data source

```text
agent.scheduled_action
agent.action_queue
agent.active_plan_v3
```

## 8.3. Layout before PR 5

Before ActivePlan:

```text
⏳ Runtime action

scheduled_action:
  explore_anomaly_location
  target: Поле электр
  progress: 11/30
  remaining: 19 turns
```

## 8.4. Layout after PR 5

When `active_plan_v3` exists:

```text
🧭 ActivePlan v3

Objective:
  FIND_ARTIFACTS

Status:
  active

Steps:
  ✅ travel_to_location
  🔄 explore_anomaly_location
  ⏳ pickup_artifact
  ⏳ sell_artifacts
```

## 8.5. Rule

Do not show `scheduled_action` as the "goal".

It is execution state only.

---

# 9. Section: Goals

Current section can stay, but it should distinguish:

```text
Global goal:
  get_rich

Current goal:
  get_rich / get_money_for_resupply

Active objective:
  FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY
```

Recommended labels:

```text
Глобальная цель
Текущая высокая цель
Активная objective
Исполнительный intent
```

This removes confusion between old `current_goal`, new objective and adapter intent.

---

# 10. Section: Memory Timeline

## 10.1. Purpose

Legacy memory timeline remains useful.

But it should be reformatted to make objective decisions readable.

## 10.2. Current issue

Legacy memory entries now include:

```text
action_kind = objective_decision
objective_key
adapter_intent_kind
intent_kind
```

If rendered as old intent memory, it becomes noisy.

## 10.3. Required display rules

For memory entry:

```text
effects.action_kind === "objective_decision"
```

Render as:

```text
🧠 Цель GET_MONEY_FOR_RESUPPLY
Score: 35%
Source: item_need
Reason: Не хватает денег для обязательного пополнения
Adapter: get_rich
Plan step: travel_to_location
```

Do not render old intent as primary.

For old entry:

```text
effects.action_kind === "v2_decision"
```

Render as legacy:

```text
Legacy intent decision
intent: seek_water
score: 36%
```

Add small badge:

```text
legacy
```

---

# 11. Section: Memory v3 Summary

## 11.1. Purpose

Show memory_v3 as storage state, not full dump.

## 11.2. Data source

```text
agent.memory_v3.stats
agent.memory_v3.schema_version
optional indexes counts
```

Current `AgentForProfile.memory_v3` type only includes stats. Extend type minimally:

```ts
memory_v3?: {
  schema_version?: number;
  stats?: {
    records_count?: number;
    active?: number;
    stale?: number;
    archived?: number;
  };
  records?: Record<string, {
    id: string;
    kind: string;
    layer: string;
    summary: string;
    confidence: number;
    importance: number;
    last_accessed_turn?: number | null;
    tags?: string[];
  }>;
  indexes?: Record<string, Record<string, string[]>>;
} | null;
```

## 11.3. Layout

Example:

```text
🧠 Memory v3

Records: 267
Active: 120
Stale: 130
Archived: 17

Top layers:
- episodic: 95
- semantic: 40
- spatial: 52
- threat: 8

Recently accessed:
- trader_location_known — Торговец в Бункере торговца
- water_source_known — В Локации Б есть вода
```

## 11.4. Important

Do not render all `memory_v3.records` by default.

This is too large.

---

# 12. Raw Debug collapsible section

Add a collapsed section:

```text
🧾 Raw debug
```

Inside:

```text
- raw brain_trace JSON
- raw _v2_context
- raw active_plan_v3
- raw memory_v3 stats
```

Do not show this expanded by default.

---

# 13. Export buttons

## 13.1. Current state

Header currently has one export button:

```text
📥
```

It exports:

```ts
const exportData = { ...agent, memory: fetchedMemory ?? agent.memory ?? [] };
JSON.stringify(exportData, null, 2)
```

This should remain.

## 13.2. Required new layout

Header buttons:

```text
📦 Full debug JSON
📘 NPC story JSON
✕ Close
```

or compact icons:

```text
📦 = полный дамп
📘 = история NPC
```

Tooltips:

```text
"Полный debug export"
"Компактная история NPC"
```

---

# 14. Export 1 — Full Debug JSON

Keep current behavior.

Purpose:

```text
deep debug / reproduce state / inspect raw memory_v3 indexes
```

Filename:

```text
stalker_<name>_full_debug.json
```

Data:

```ts
{
  ...agent,
  memory: fetchedMemory ?? agent.memory ?? []
}
```

No trimming.

---

# 15. Export 2 — Compact NPC History JSON

## 15.1. Purpose

Readable export for analysis.

Should answer:

```text
who is the NPC?
what is current state?
what did he decide recently?
what important events happened?
what memory influenced decisions?
what is he doing now?
```

Without huge indexes and all raw memory.

## 15.2. Filename

```text
stalker_<name>_history.json
```

## 15.3. Shape

```ts
type CompactNpcHistoryExport = {
  export_schema: "npc_history_v1";
  exported_at: string;

  agent: {
    id: string;
    name: string;
    faction?: string;
    is_alive: boolean;
    location_id: string;
    location_name?: string;

    hp: number;
    hunger: number;
    thirst: number;
    sleepiness: number;
    radiation: number;
    money: number;

    global_goal?: string;
    current_goal?: string | null;
    active_objective?: BrainTraceObjectiveInfo | null;
    adapter_intent?: {
      kind?: string | null;
      score?: number | null;
    } | null;

    scheduled_action?: unknown;
    active_plan_v3?: unknown;
  };

  equipment: Record<string, AgentInventoryItem | null>;
  inventory_summary: Array<{
    type: string;
    name: string;
    count: number;
    total_value: number;
  }>;

  npc_brain: {
    current_thought?: string;
    latest_decision?: {
      turn: number;
      world_time?: string;
      active_objective?: BrainTraceObjectiveInfo;
      adapter_intent?: {
        kind?: string | null;
        score?: number | null;
      };
      objective_scores?: BrainTraceObjectiveInfo[];
      alternatives?: BrainTraceObjectiveInfo[];
      immediate_needs?: BrainTraceNeed[];
      item_needs?: BrainTraceNeed[];
      liquidity?: unknown;
      memory_used?: BrainTraceMemoryUsed[];
      summary: string;
    } | null;
    recent_trace_events: BrainTraceEvent[];
  };

  story_timeline: Array<{
    turn: number;
    time_label: string;
    category: "decision" | "action" | "observation" | "system";
    title: string;
    summary?: string;
    objective_key?: string;
    adapter_intent_kind?: string;
    action_kind?: string;
    location_id?: string;
    item_type?: string;
    artifact_type?: string;
    money_delta?: number;
  }>;

  memory_v3_summary?: {
    records_count?: number;
    active?: number;
    stale?: number;
    archived?: number;
    top_layers?: Array<{ layer: string; count: number }>;
    top_kinds?: Array<{ kind: string; count: number }>;
    recently_accessed?: Array<{
      id: string;
      kind: string;
      layer: string;
      summary: string;
      confidence?: number;
      last_accessed_turn?: number | null;
    }>;
  };
};
```

## 15.4. Timeline filtering

Do not include all memory entries.

Recommended:

```text
last 80 legacy memory entries
+ all objective_decision entries from last 200 memory entries
+ all death/emission/artifact/trade/combat entries from last 200 entries
```

Or simpler first version:

```ts
const STORY_TIMELINE_LIMIT = 120;
const storyTimeline = displayMemory
  .slice(-STORY_TIMELINE_LIMIT)
  .map(toCompactTimelineEntry);
```

## 15.5. Inventory summary

Group by type:

```text
water x2
bread x1
ammo_12gauge x3
bandage x2
```

Do not dump every item id unless needed.

## 15.6. Exclude from compact export

Exclude:

```text
memory_v3.records full map
memory_v3.indexes
full legacy memory
full brain_trace raw JSON beyond recent 5–10 events
raw state blob
large location registry
```

---

# 16. Helper functions to add in AgentProfileModal.tsx

Add near helpers:

```ts
const getLatestDecisionEvent = (trace?: BrainTrace | null): BrainTraceEvent | null => {
  if (!trace?.events?.length) return null;
  const decisions = trace.events.filter((ev) => ev.mode === 'decision');
  return decisions.length ? decisions[decisions.length - 1] : trace.events[trace.events.length - 1];
};
```

```ts
const formatObjectiveKey = (key?: string | null): string => {
  if (!key) return '—';
  return key.replace(/_/g, ' ');
};
```

```ts
const pct = (value?: number | null): string => {
  if (value == null || Number.isNaN(value)) return '—';
  return `${Math.round(value * 100)}%`;
};
```

```ts
const summarizeInventory = (items: AgentInventoryItem[]) => {
  const grouped = new Map<string, { type: string; name: string; count: number; total_value: number }>();
  for (const item of items) {
    const current = grouped.get(item.type) ?? {
      type: item.type,
      name: item.name,
      count: 0,
      total_value: 0,
    };
    current.count += 1;
    current.total_value += item.value ?? 0;
    grouped.set(item.type, current);
  }
  return [...grouped.values()].sort((a, b) => a.type.localeCompare(b.type));
};
```

```ts
const toCompactTimelineEntry = (m: MemEntry) => {
  const effects = m.effects ?? {};
  const actionKind = typeof effects.action_kind === 'string' ? effects.action_kind : undefined;
  return {
    turn: m.world_turn,
    time_label: traceTimeLabel(
      m.world_turn,
      m.world_day != null
        ? { world_day: m.world_day, world_hour: m.world_hour ?? 0, world_minute: m.world_minute ?? 0 }
        : undefined,
    ),
    category: m.type as 'decision' | 'action' | 'observation' | 'system',
    title: m.title,
    summary: m.summary,
    action_kind: actionKind,
    objective_key: typeof effects.objective_key === 'string' ? effects.objective_key : undefined,
    adapter_intent_kind: typeof effects.adapter_intent_kind === 'string' ? effects.adapter_intent_kind : undefined,
    location_id: typeof effects.location_id === 'string'
      ? effects.location_id
      : typeof effects.target_location_id === 'string'
        ? effects.target_location_id
        : undefined,
    item_type: typeof effects.item_type === 'string' ? effects.item_type : undefined,
    artifact_type: typeof effects.artifact_type === 'string' ? effects.artifact_type : undefined,
    money_delta: typeof effects.money_gained === 'number' ? effects.money_gained : undefined,
  };
};
```

```ts
const buildCompactNpcHistoryExport = (
  agent: AgentForProfile,
  displayMemory: MemEntry[],
  locationName: string,
): CompactNpcHistoryExport => {
  const latestDecision = getLatestDecisionEvent(agent.brain_trace);
  const recentTraceEvents = (agent.brain_trace?.events ?? []).slice(-10);
  const storyTimeline = displayMemory.slice(-120).map(toCompactTimelineEntry);

  return {
    export_schema: 'npc_history_v1',
    exported_at: new Date().toISOString(),
    agent: {
      id: agent.id,
      name: agent.name,
      faction: agent.faction,
      is_alive: agent.is_alive,
      location_id: agent.location_id,
      location_name: locationName,
      hp: agent.hp,
      hunger: agent.hunger,
      thirst: agent.thirst,
      sleepiness: agent.sleepiness,
      radiation: agent.radiation,
      money: agent.money,
      global_goal: agent.global_goal,
      current_goal: agent.current_goal,
      active_objective: latestDecision?.active_objective ?? null,
      adapter_intent: latestDecision
        ? { kind: latestDecision.intent_kind, score: latestDecision.intent_score }
        : null,
      scheduled_action: agent.scheduled_action,
      active_plan_v3: agent.active_plan_v3,
    },
    equipment: agent.equipment,
    inventory_summary: summarizeInventory(agent.inventory),
    npc_brain: {
      current_thought: agent.brain_trace?.current_thought,
      latest_decision: latestDecision
        ? {
            turn: latestDecision.turn,
            world_time: traceTimeLabel(latestDecision.turn, latestDecision.world_time),
            active_objective: latestDecision.active_objective,
            adapter_intent: {
              kind: latestDecision.intent_kind,
              score: latestDecision.intent_score,
            },
            objective_scores: latestDecision.objective_scores,
            alternatives: latestDecision.alternatives,
            immediate_needs: latestDecision.immediate_needs,
            item_needs: latestDecision.item_needs,
            liquidity: latestDecision.liquidity,
            memory_used: latestDecision.memory_used,
            summary: latestDecision.summary,
          }
        : null,
      recent_trace_events: recentTraceEvents,
    },
    story_timeline: storyTimeline,
    memory_v3_summary: buildMemoryV3Summary(agent.memory_v3),
  };
};
```

Need also add:

```ts
const downloadJson = (filename: string, data: unknown) => {
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};
```

Then buttons use this.

---

# 17. Memory v3 summary helper

If frontend receives only stats, show only stats.

If it receives records/indexes, compute summary:

```ts
const buildMemoryV3Summary = (memoryV3: AgentForProfile['memory_v3']) => {
  if (!memoryV3) return undefined;
  const stats = memoryV3.stats ?? {};
  const records = memoryV3.records ? Object.values(memoryV3.records) : [];

  const byLayer = new Map<string, number>();
  const byKind = new Map<string, number>();

  for (const rec of records) {
    byLayer.set(rec.layer, (byLayer.get(rec.layer) ?? 0) + 1);
    byKind.set(rec.kind, (byKind.get(rec.kind) ?? 0) + 1);
  }

  return {
    ...stats,
    top_layers: [...byLayer.entries()]
      .map(([layer, count]) => ({ layer, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8),
    top_kinds: [...byKind.entries()]
      .map(([kind, count]) => ({ kind, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10),
    recently_accessed: records
      .filter((rec) => rec.last_accessed_turn != null)
      .sort((a, b) => Number(b.last_accessed_turn ?? 0) - Number(a.last_accessed_turn ?? 0))
      .slice(0, 10)
      .map((rec) => ({
        id: rec.id,
        kind: rec.kind,
        layer: rec.layer,
        summary: rec.summary,
        confidence: rec.confidence,
        last_accessed_turn: rec.last_accessed_turn,
      })),
  };
};
```

---

# 18. UI components to add

To avoid bloating AgentProfileModal even more, ideally split into components:

```text
frontend/src/games/zone_stalkers/ui/agent_profile/NpcBrainPanel.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/ObjectiveRankingPanel.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/NeedsPanel.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/MemoryUsedPanel.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/RuntimeActionPanel.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/MemoryTimeline.tsx
frontend/src/games/zone_stalkers/ui/agent_profile/exportNpcHistory.ts
```

If that is too much for one PR, at least put helpers below the current helpers and keep JSX sections separated by comments.

Recommended:

```text
Do split into files now.
AgentProfileModal.tsx is already large.
```

---

# 19. Style requirements

Keep current style object approach unless project already uses CSS modules nearby.

Add styles:

```ts
brainCard
objectiveBadge
objectiveSelected
objectiveRejected
objectiveScoreBar
needChip
memoryUsedCard
exportBtnDebug
exportBtnStory
rawDebugBox
smallMono
```

Do not introduce external UI library.

---

# 20. Acceptance tests / manual checks

No automated frontend tests may exist. Use manual checks.

## 20.1. NPC with objective decision

Given agent has latest brain_trace decision with active_objective:

Expected:

```text
NPC Brain v3 panel shows objective as primary.
Intent appears only as adapter.
Objective ranking visible.
Needs visible.
Memory used visible if present.
```

## 20.2. NPC with only plan monitor

Given latest trace event is plan_monitor:

Expected:

```text
Panel says NPC continues scheduled action.
No fake "new decision" is displayed.
Scheduled action progress visible.
```

## 20.3. NPC with old legacy memory only

Expected:

```text
UI still works.
Shows legacy badge.
Does not crash if active_objective missing.
```

## 20.4. Full debug export

Expected:

```text
Button 📦 downloads full raw agent JSON.
Filename: stalker_<name>_full_debug.json.
Includes full memory.
Includes memory_v3 as received.
```

## 20.5. Compact story export

Expected:

```text
Button 📘 downloads compact readable JSON.
Filename: stalker_<name>_history.json.
Does not include full memory_v3.indexes.
Does not include full memory_v3.records.
Contains story_timeline, latest_decision, inventory_summary.
```

---

# 21. Backend/API considerations

Current frontend already calls:

```text
contextsApi.getAgentMemory(contextId, agent.id)
```

This fetches full legacy memory on demand.

For this frontend-only task, no backend changes are required.

Optional future backend endpoints:

```text
GET /contexts/{contextId}/agents/{agentId}/brain-summary
GET /contexts/{contextId}/agents/{agentId}/history-export
GET /contexts/{contextId}/agents/{agentId}/memory-v3/summary
```

Do not add these now unless frontend payload becomes too heavy.

---

# 22. What not to do

Do not remove full debug export.

Do not hide legacy memory completely.

Do not show full memory_v3 indexes in the profile by default.

Do not make compact export depend on backend.

Do not present adapter intent as primary reason.

Do not mix `objective_decision` and `v2_decision` under the same visual label.

---

# 23. Definition of done

This task is complete when:

```text
[ ] Agent profile has a readable "NPC Brain v3" decision section.
[ ] Latest active_objective is primary.
[ ] adapter intent is secondary.
[ ] Objective ranking is visible.
[ ] Needs/liquidity/combat readiness are visible.
[ ] memory_used is visible and separate from memory timeline.
[ ] Runtime scheduled_action is separate from decision reason.
[ ] Legacy memory timeline renders objective_decision specially.
[ ] Full debug export still exists.
[ ] Compact NPC history export exists.
[ ] Compact export excludes full memory_v3 records/indexes.
[ ] UI handles missing brain_trace/objective fields safely.
```

---

# 24. Final mental model

The profile should no longer say:

```text
NPC chose intent seek_food.
```

It should say:

```text
NPC chose objective RESTORE_FOOD.
Why: hunger above soft threshold.
Execution bridge: intent seek_food.
Plan: consume bread.
Memory used: remembered food source.
```

And for exports:

```text
Full debug export = raw truth for developers.
Compact story export = readable NPC life history.
```
