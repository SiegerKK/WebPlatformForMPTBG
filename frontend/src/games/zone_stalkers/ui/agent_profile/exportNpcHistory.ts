/**
 * exportNpcHistory.ts
 *
 * Shared utilities, constants and export helpers for the NPC Brain v3 profile UX.
 * Imported by AgentProfileModal.tsx and all agent_profile/ sub-components.
 */

import type {
  AgentForProfile,
  BrainTrace,
  BrainTraceEvent,
  BrainTraceObjectiveInfo,
  BrainTraceMemoryUsed,
  BrainTraceNeed,
  AgentInventoryItem,
} from '../AgentProfileModal';

// ─── Re-export types for sub-component convenience ───────────────────────────

export type { BrainTrace, BrainTraceEvent, BrainTraceObjectiveInfo, BrainTraceMemoryUsed, BrainTraceNeed };

export type MemEntry = NonNullable<AgentForProfile['story_events']>[number];

// ─── CompactNpcHistoryExport ─────────────────────────────────────────────────

export type CompactNpcHistoryExport = {
  export_schema: 'npc_history_v2';
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
    material_threshold?: number;
    wealth_goal_target?: number;
    global_goal_achieved?: boolean;
    global_goal?: string;
    current_goal?: string | null;
    active_objective?: BrainTraceObjectiveInfo | null;
    adapter_intent?: { kind?: string | null; score?: number | null } | null;
    scheduled_action?: unknown;
    active_plan_v3?: unknown;
    brain_v3_context?: AgentForProfile['brain_v3_context'];
    legacy_context_used?: boolean;
    wealth_progress?: {
      money: number;
      liquid_wealth: number;
      material_threshold?: number;
      material_threshold_passed?: boolean;
      wealth_goal_target?: number;
      wealth_goal_reached?: boolean;
      global_goal_achieved?: boolean;
    };
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
    latest_event?: BrainTraceEvent | null;
    latest_decision?: {
      turn: number;
      world_time?: string;
      active_objective?: BrainTraceObjectiveInfo;
      adapter_intent?: { kind?: string | null; score?: number | null };
      objective_scores?: BrainTraceObjectiveInfo[];
      alternatives?: BrainTraceObjectiveInfo[];
      immediate_needs?: BrainTraceNeed[];
      item_needs?: BrainTraceNeed[];
      liquidity?: unknown;
      memory_used?: BrainTraceMemoryUsed[];
      summary: string;
    } | null;
    current_objective?: BrainTraceObjectiveInfo | null;
    current_runtime?: {
      mode: 'active_plan' | 'scheduled_action' | 'idle';
      scheduled_action?: unknown;
      active_plan_v3?: unknown;
      latest_plan_monitor?: BrainTraceEvent | null;
    };
    recent_trace_events: BrainTraceEvent[];
  };

  story_events: CompactTimelineEntry[];

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
  hunt_search?: {
    target_id?: string;
    target_name?: string;
    best_location_id?: string | null;
    best_location_confidence?: number;
    possible_locations?: Array<{
      location_id: string;
      probability: number;
      confidence: number;
      freshness: number;
      reason: string;
      source_refs: string[];
    }>;
    likely_routes?: Array<{
      from_location_id: string | null;
      to_location_id: string | null;
      confidence: number;
      freshness: number;
      reason: string;
      source_refs: string[];
    }>;
    exhausted_locations?: string[];
    lead_count?: number;
  };
};

export type CompactTimelineEntry = {
  turn: number;
  time_label: string;
  category: 'decision' | 'action' | 'observation' | 'system';
  title: string;
  summary?: string;
  objective_key?: string;
  adapter_intent_kind?: string;
  action_kind?: string;
  location_id?: string;
  item_type?: string;
  artifact_type?: string;
  money_delta?: number;
};

// ─── Time helpers ─────────────────────────────────────────────────────────────

export const MINUTES_PER_TURN = 1;
export const TURNS_PER_HOUR = 60 / MINUTES_PER_TURN;

export const TIME_LABEL = (h: number, m: number): string =>
  `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;

export const turnToTime = (
  worldTurn: number,
): { world_day: number; world_hour: number; world_minute: number } => {
  const totalMinutes = 6 * 60 + (worldTurn - 1) * MINUTES_PER_TURN;
  return {
    world_day: 1 + Math.floor(totalMinutes / (24 * 60)),
    world_hour: Math.floor(totalMinutes / 60) % 24,
    world_minute: totalMinutes % 60,
  };
};

export const traceTimeLabel = (
  worldTurn: number,
  worldTime?: { world_day: number; world_hour: number; world_minute: number },
): string => {
  const t = worldTime ?? turnToTime(worldTurn);
  return `День ${t.world_day} · ${TIME_LABEL(t.world_hour, t.world_minute)}`;
};

export const schedRemaining = (type: string, turns: number): string => {
  if (type === 'sleep') {
    return `${Math.ceil(turns / TURNS_PER_HOUR)} ч осталось`;
  }
  return `${turns * MINUTES_PER_TURN} мин осталось`;
};

// ─── Display constants ────────────────────────────────────────────────────────

export const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore_anomaly_location: '🔍',
  sleep: '😴',
  event: '📖',
};

export const MEM_ICONS: Record<string, string> = {
  decision: '🧠',
  action: '⚡',
  observation: '👁️',
  travel: '🚶',
  explore_anomaly_location: '🔍',
  sleep: '😴',
  pickup: '🎁',
  trade_sell: '💰',
};

export const MEM_COLORS: Record<string, string> = {
  decision: '#818cf8',
  action: '#34d399',
  observation: '#fbbf24',
  travel: '#34d399',
  explore_anomaly_location: '#34d399',
  sleep: '#34d399',
  pickup: '#34d399',
  trade_sell: '#34d399',
};

export const CURRENT_GOAL_LABELS: Record<string, string> = {
  gather_resources: 'Сбор ресурсов',
  goal_get_rich: 'Нажива',
  goal_get_rich_seek_artifacts: 'Ищу артефакты',
  sell_artifacts: 'Продаю артефакты',
  flee_to_safety: 'Бегство',
  upgrade_equipment: 'Апгрейд снаряжения',
  get_weapon: 'Ищу оружие',
  get_armor: 'Ищу броню',
  get_ammo: 'Ищу патроны',
  flee_emission: 'Убегаю от выброса',
};

export const currentGoalLabel = (raw: string): string =>
  CURRENT_GOAL_LABELS[raw] ?? raw.replace(/_/g, ' ');

export const _INTENT_META: Record<string, { icon: string; label: string }> = {
  escape_danger: { icon: '🚨', label: 'Бегство (критический HP)' },
  heal_self: { icon: '💊', label: 'Срочное лечение' },
  flee_emission: { icon: '⚡', label: 'Бегство от выброса' },
  wait_in_shelter: { icon: '🏚️', label: 'Укрытие от выброса' },
  seek_water: { icon: '💧', label: 'Поиск воды' },
  seek_food: { icon: '🍖', label: 'Поиск еды' },
  rest: { icon: '😴', label: 'Отдых (сон)' },
  resupply: { icon: '🔫', label: 'Получить снаряжение' },
  sell_artifacts: { icon: '💎', label: 'Продажа артефактов' },
  trade: { icon: '🏪', label: 'Торговля' },
  upgrade_equipment: { icon: '⬆️', label: 'Апгрейд снаряжения' },
  loot: { icon: '🎁', label: 'Мародёрство' },
  explore: { icon: '🔭', label: 'Исследование' },
  get_rich: { icon: '💰', label: 'Накопление богатства' },
  hunt_target: { icon: '🎯', label: 'Охота на цель' },
  search_information: { icon: '📜', label: 'Поиск информации' },
  leave_zone: { icon: '🚪', label: 'Покинуть Зону' },
  negotiate: { icon: '🗣️', label: 'Переговоры' },
  assist_ally: { icon: '🤝', label: 'Помощь союзнику' },
  form_group: { icon: '👥', label: 'Создать группу' },
  follow_group_plan: { icon: '📋', label: 'Следовать плану группы' },
  maintain_group: { icon: '🔗', label: 'Сохранить группу' },
  idle: { icon: '💤', label: 'Ожидание' },
};

// ─── Export helper functions ──────────────────────────────────────────────────

export const getLatestTraceEvent = (trace?: BrainTrace | null): BrainTraceEvent | null => {
  if (!trace?.events?.length) return null;
  return trace.events[trace.events.length - 1] ?? null;
};

export const getLatestDecisionEvent = (trace?: BrainTrace | null): BrainTraceEvent | null => {
  if (!trace?.events?.length) return null;
  const decisions = trace.events.filter((ev) => ev.mode === 'decision');
  return decisions.length ? decisions[decisions.length - 1] : null;
};

export const formatObjectiveKey = (key?: string | null): string => {
  if (!key) return '—';
  return key.replace(/_/g, ' ');
};

export const pct = (value?: number | null): string => {
  if (value == null || Number.isNaN(value)) return '—';
  return `${Math.round(value * 100)}%`;
};

export const summarizeInventory = (items: AgentInventoryItem[]) => {
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

export const getLiquidWealth = (agent: AgentForProfile): number =>
  Number(agent.money ?? 0) + summarizeInventory(agent.inventory).reduce((acc, item) => acc + Number(item.total_value ?? 0), 0);

export const toCompactTimelineEntry = (m: MemEntry) => {
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
    adapter_intent_kind:
      typeof effects.adapter_intent_kind === 'string' ? effects.adapter_intent_kind : undefined,
    location_id:
      typeof effects.location_id === 'string'
        ? effects.location_id
        : typeof effects.target_location_id === 'string'
        ? effects.target_location_id
        : undefined,
    item_type: typeof effects.item_type === 'string' ? effects.item_type : undefined,
    artifact_type: typeof effects.artifact_type === 'string' ? effects.artifact_type : undefined,
    money_delta: typeof effects.money_gained === 'number' ? effects.money_gained : undefined,
  };
};

export const getCurrentObjectiveFromAgent = (
  agent: AgentForProfile,
  latestDecision: BrainTraceEvent | null,
  storyTimeline: CompactTimelineEntry[],
  options?: { includeLegacyContext?: boolean },
): BrainTraceObjectiveInfo | null => {
  if (latestDecision?.active_objective) return latestDecision.active_objective;

  let brainContext = agent.brain_v3_context;
  if (!brainContext && options?.includeLegacyContext) {
    brainContext = agent._v2_context;
  }
  const objectiveKey = brainContext?.objective_key;
  if (objectiveKey) {
    return {
      key: objectiveKey,
      score: brainContext?.objective_score ?? brainContext?.intent_score ?? 0,
      source: 'current_context',
      reason: brainContext?.objective_reason ?? brainContext?.intent_reason ?? undefined,
    };
  }

  const lastObjectiveMemory = [...storyTimeline]
    .reverse()
    .find((entry) => entry.objective_key);

  if (lastObjectiveMemory?.objective_key) {
    return {
      key: lastObjectiveMemory.objective_key,
      score: 0,
      source: 'last_objective_decision',
      reason: lastObjectiveMemory.summary,
    };
  }

  return null;
};

export const buildMemoryV3Summary = (memoryV3: AgentForProfile['memory_v3']) => {
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

const buildStoryEventsFromMemoryV3 = (agent: AgentForProfile): MemEntry[] => {
  const records = agent.memory_v3?.records ? Object.values(agent.memory_v3.records) : [];
  const memoryDerived: MemEntry[] = records
    .slice()
    .sort((a, b) => Number(a.created_turn ?? 0) - Number(b.created_turn ?? 0))
    .map((rec) => ({
      world_turn: Number(rec.created_turn ?? 0),
      type: typeof rec.details?.memory_type === 'string' ? rec.details.memory_type : 'observation',
      title: rec.summary || rec.kind,
      summary: rec.summary,
      effects: {
        ...(rec.details ?? {}),
        action_kind: typeof rec.details?.action_kind === 'string' ? rec.details.action_kind : rec.kind,
        location_id: rec.location_id,
      },
    }));

  const traceDerived: MemEntry[] = (agent.brain_trace?.events ?? []).map((event) => ({
    world_turn: Number(event.turn ?? 0),
    world_day: event.world_time?.world_day,
    world_hour: event.world_time?.world_hour,
    world_minute: event.world_time?.world_minute,
    type: event.mode === 'decision' ? 'decision' : 'system',
    title:
      event.mode === 'decision'
        ? '🧠 Brain decision'
        : event.mode === 'active_plan_monitor'
        ? '🛰️ Plan monitor'
        : '📌 Trace event',
    summary: event.summary,
    effects: {
      action_kind: typeof event.decision === 'string' ? event.decision : event.mode,
      objective_key:
        typeof event.active_objective?.key === 'string'
          ? event.active_objective.key
          : typeof event.objective_key === 'string'
          ? event.objective_key
          : undefined,
      adapter_intent_kind:
        typeof event.adapter_intent?.kind === 'string'
          ? event.adapter_intent.kind
          : typeof event.intent_kind === 'string'
          ? event.intent_kind
          : undefined,
      scheduled_action_type:
        typeof event.scheduled_action_type === 'string' ? event.scheduled_action_type : undefined,
    },
  }));

  return [...memoryDerived, ...traceDerived]
    .sort((a, b) => Number(a.world_turn ?? 0) - Number(b.world_turn ?? 0))
    .slice(-120);
};

export const buildStoryEvents = (
  agent: AgentForProfile,
  displayMemory: MemEntry[],
): MemEntry[] => {
  if (displayMemory.length > 0) return displayMemory;
  if ((agent.story_events ?? []).length > 0) return agent.story_events ?? [];
  return buildStoryEventsFromMemoryV3(agent);
};

export const buildCompactNpcHistoryExport = (
  agent: AgentForProfile,
  displayMemory: MemEntry[],
  locationName: string,
): CompactNpcHistoryExport => {
  const storyEventsSource = buildStoryEvents(agent, displayMemory);
  const latestEvent = getLatestTraceEvent(agent.brain_trace);
  const latestDecision = getLatestDecisionEvent(agent.brain_trace);
  const recentTraceEvents = (agent.brain_trace?.events ?? []).slice(-10);
  const storyTimeline = storyEventsSource
    .slice(-120)
    .map(toCompactTimelineEntry)
    .filter((entry) => entry.action_kind !== 'active_plan_step_started');
  const legacyContextUsed = !agent.brain_v3_context && !!agent._v2_context;
  const currentObjective = getCurrentObjectiveFromAgent(agent, latestDecision, storyTimeline, {
    includeLegacyContext: legacyContextUsed,
  });
  const latestPlanMonitor = latestEvent?.mode === 'active_plan_monitor' ? latestEvent : null;
  const adapterContext = agent.brain_v3_context ?? (legacyContextUsed ? agent._v2_context : null);
  const adapterIntentKind = latestDecision?.adapter_intent?.kind
    ?? latestDecision?.intent_kind
    ?? agent.brain_v3_context?.adapter_intent?.kind
    ?? agent.brain_v3_context?.intent_kind
    ?? adapterContext?.intent_kind;
  const adapterIntentScore = latestDecision?.adapter_intent?.score
    ?? latestDecision?.intent_score
    ?? agent.brain_v3_context?.adapter_intent?.score
    ?? agent.brain_v3_context?.intent_score
    ?? adapterContext?.intent_score;
  const liquidWealth = getLiquidWealth(agent);
  const materialThreshold = typeof agent.material_threshold === 'number' ? agent.material_threshold : undefined;
  const wealthGoalTarget = typeof agent.wealth_goal_target === 'number' ? agent.wealth_goal_target : undefined;
  const wealthProgress = {
    money: Number(agent.money ?? 0),
    liquid_wealth: liquidWealth,
    material_threshold: materialThreshold,
    material_threshold_passed: materialThreshold != null ? liquidWealth >= materialThreshold : undefined,
    wealth_goal_target: wealthGoalTarget,
    wealth_goal_reached: wealthGoalTarget != null ? liquidWealth >= wealthGoalTarget : undefined,
    global_goal_achieved: Boolean(agent.global_goal_achieved),
  };
  const huntBelief = agent.brain_v3_context?.hunt_target_belief;
  const targetName = huntBelief?.target_id
    ? (storyEventsSource
        .slice()
        .reverse()
        .find((mem) => {
          const effects = mem.effects ?? {};
          const actionKind = typeof effects.action_kind === 'string' ? effects.action_kind : '';
          if (actionKind !== 'target_seen' && actionKind !== 'target_intel') return false;
          const targetId = typeof effects.target_id === 'string'
            ? effects.target_id
            : typeof effects.target_agent_id === 'string'
            ? effects.target_agent_id
            : '';
          return targetId === huntBelief.target_id;
        })
        ?.effects?.target_name as string | undefined)
    : undefined;

  return {
    export_schema: 'npc_history_v2',
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
      material_threshold: agent.material_threshold,
      wealth_goal_target: agent.wealth_goal_target,
      global_goal_achieved: agent.global_goal_achieved,
      global_goal: agent.global_goal,
      current_goal: agent.current_goal,
      active_objective: currentObjective,
      adapter_intent: adapterIntentKind != null
        ? { kind: adapterIntentKind, score: adapterIntentScore }
        : null,
      scheduled_action: agent.scheduled_action,
      active_plan_v3: agent.active_plan_v3,
      brain_v3_context: agent.brain_v3_context ?? (legacyContextUsed ? agent._v2_context ?? null : null),
      legacy_context_used: legacyContextUsed,
      wealth_progress: wealthProgress,
    },
    equipment: agent.equipment,
    inventory_summary: summarizeInventory(agent.inventory),
    npc_brain: {
      current_thought: agent.brain_trace?.current_thought,
      latest_event: latestEvent,
      latest_decision: latestDecision
        ? {
            turn: latestDecision.turn,
            world_time: traceTimeLabel(latestDecision.turn, latestDecision.world_time),
            active_objective: latestDecision.active_objective,
            adapter_intent: {
              kind: latestDecision.adapter_intent?.kind ?? latestDecision.intent_kind,
              score: latestDecision.adapter_intent?.score ?? latestDecision.intent_score,
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
      current_objective: currentObjective,
      current_runtime: agent.scheduled_action != null
        ? {
            mode: 'scheduled_action',
            scheduled_action: agent.scheduled_action,
            latest_plan_monitor: latestPlanMonitor,
          }
        : agent.active_plan_v3 != null
        ? {
            mode: 'active_plan',
            active_plan_v3: agent.active_plan_v3,
            latest_plan_monitor: latestPlanMonitor,
          }
        : {
            mode: 'idle',
            latest_plan_monitor: latestPlanMonitor,
          },
      recent_trace_events: recentTraceEvents,
    },
    story_events: storyTimeline,
    memory_v3_summary: buildMemoryV3Summary(agent.memory_v3),
    hunt_search: huntBelief
      ? {
          target_id: huntBelief.target_id,
          target_name: targetName,
          best_location_id: huntBelief.best_location_id,
          best_location_confidence: huntBelief.best_location_confidence,
          possible_locations: (huntBelief.possible_locations ?? []).slice(0, 5),
          likely_routes: (huntBelief.likely_routes ?? []).slice(0, 5),
          exhausted_locations: huntBelief.exhausted_locations ?? [],
          lead_count: huntBelief.lead_count,
        }
      : undefined,
  };
};

export const downloadJson = (filename: string, data: unknown): void => {
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};
