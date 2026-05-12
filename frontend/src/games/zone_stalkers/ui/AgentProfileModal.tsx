/**
 * AgentProfileModal — reusable modal showing a full Zone Stalkers agent profile.
 *
 * Import `AgentForProfile` to build a compatible agent object.
 * Closing: clicking the semi-transparent overlay or the ✕ button calls `onClose`.
 */
import React, { useState, useEffect } from 'react';
import { contextsApi } from '../../../api/client';
import {
  currentGoalLabel,
  downloadJson,
  buildCompactNpcHistoryExport,
  getCurrentObjectiveFromAgent,
  getLatestTraceEvent,
  getLatestDecisionEvent,
  toCompactTimelineEntry,
  formatObjectiveKey,
  pct,
} from './agent_profile/exportNpcHistory';
import type { MemEntry } from './agent_profile/exportNpcHistory';
import { NpcBrainPanel } from './agent_profile/NpcBrainPanel';
import { ObjectiveRankingPanel } from './agent_profile/ObjectiveRankingPanel';
import { NeedsPanel } from './agent_profile/NeedsPanel';
import { MemoryUsedPanel } from './agent_profile/MemoryUsedPanel';
import { RuntimeActionPanel } from './agent_profile/RuntimeActionPanel';
import { MemoryTimeline } from './agent_profile/MemoryTimeline';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AgentInventoryItem {
  id: string;
  type: string;
  name: string;
  weight?: number;
  value?: number;
}

export type BrainTraceMode = 'active_plan_monitor' | 'active_plan' | 'decision' | 'system' | 'legacy_decision';
export type BrainTraceDecision =
  | 'continue'
  | 'abort'
  | 'objective_decision'
  | 'legacy_new_intent'
  | 'no_op'
  | 'active_plan_created'
  | 'active_plan_step_started'
  | 'active_plan_step_completed'
  | 'active_plan_step_failed'
  | 'active_plan_repair_requested'
  | 'active_plan_repaired'
  | 'active_plan_aborted'
  | 'active_plan_completed';

export type BrainTraceNeed = {
  key: string;
  urgency: number;
  selected_item_type?: string | null;
  missing_count?: number;
  reason?: string;
};

export type BrainTraceMemoryUsed = {
  id: string;
  kind: string;
  summary: string;
  confidence: number;
  used_for: string;
};

export type BrainTraceObjectiveInfo = {
  key: string;
  score: number;
  source?: string;
  reason?: string;
  decision?: string;
};

export type BrainTraceEvent = {
  turn: number;
  world_time?: { world_day: number; world_hour: number; world_minute: number };
  mode: BrainTraceMode;
  decision: BrainTraceDecision;
  summary: string;
  reason?: string;
  scheduled_action_type?: string | null;
  intent_kind?: string | null;
  intent_score?: number | null;
  dominant_pressure?: { key: string; value: number } | null;
  immediate_needs?: BrainTraceNeed[];
  item_needs?: BrainTraceNeed[];
  liquidity?: { safe_sale_options?: number; risky_sale_options?: number; emergency_sale_options?: number; money_missing?: number } | null;
  memory_used?: BrainTraceMemoryUsed[];
  active_objective?: BrainTraceObjectiveInfo;
  objective_scores?: BrainTraceObjectiveInfo[];
  alternatives?: BrainTraceObjectiveInfo[];
  adapter_intent?: { kind?: string | null; score?: number | null } | null;
  active_plan_runtime?: {
    active_plan_id?: string | null;
    objective_key?: string | null;
    status?: string | null;
    current_step_index?: number | null;
    current_step_kind?: string | null;
    steps_count?: number | null;
    repair_count?: number | null;
  } | null;
};

export type BrainTrace = {
  schema_version: 1;
  turn: number;
  world_time?: { world_day: number; world_hour: number; world_minute: number };
  mode: BrainTraceMode;
  current_thought: string;
  events: BrainTraceEvent[];
  active_plan?: unknown;
  top_drives?: Array<{ key: string; value: number; rank: number }>;
};

export interface AgentForProfile {
  id: string;
  name: string;
  location_id: string;
  hp: number;
  max_hp: number;
  radiation: number;
  hunger: number;
  thirst: number;
  sleepiness: number;
  money: number;
  faction: string;
  inventory: AgentInventoryItem[];
  equipment: Record<string, AgentInventoryItem | null>;
  is_alive: boolean;
  action_used: boolean;
  scheduled_action: {
    type: string;
    turns_remaining: number;
    turns_total: number;
    target_id: string;
    final_target_id?: string;
  } | null;
  controller: { kind: string; participant_id?: string | null };
  experience?: number;
  skill_combat?: number;
  skill_stalker?: number;
  skill_trade?: number;
  skill_medicine?: number;
  skill_social?: number;
  global_goal?: string;
  current_goal?: string | null;
  material_threshold?: number;
  wealth_goal_target?: number;
  risk_tolerance?: number;
  reputation?: number;
  has_left_zone?: boolean;
  global_goal_achieved?: boolean;
  kill_target_id?: string | null;
  story_events?: Array<{
    world_turn: number;
    world_day?: number;
    world_hour?: number;
    world_minute?: number;
    type: string;
    title: string;
    summary?: string;
    effects?: Record<string, unknown>;
  }>;
  brain_trace?: BrainTrace | null;
  active_plan_v3?: unknown;
  brain_v3_context?: {
    objective_key?: string | null;
    objective_score?: number | null;
    objective_reason?: string | null;
    intent_kind?: string | null;
    intent_score?: number | null;
    intent_reason?: string | null;
    adapter_intent?: { kind?: string | null; score?: number | null; reason?: string | null } | null;
    hunt_target_belief?: {
      target_id: string;
      is_known: boolean;
      is_alive: boolean | null;
      last_known_location_id: string | null;
      location_confidence: number;
      best_location_id: string | null;
      best_location_confidence: number;
      last_seen_turn: number | null;
      visible_now: boolean;
      co_located: boolean;
      equipment_known: boolean;
      combat_strength: number | null;
      combat_strength_confidence: number;
      possible_locations: Array<{
        location_id: string;
        probability: number;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      likely_routes: Array<{
        from_location_id: string | null;
        to_location_id: string | null;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      exhausted_locations: string[];
      lead_count: number;
      route_hints: string[];
      source_refs: string[];
    } | null;
  } | null;
  _v2_context?: {
    objective_key?: string | null;
    objective_score?: number | null;
    objective_reason?: string | null;
    intent_kind?: string | null;
    intent_score?: number | null;
    intent_reason?: string | null;
  } | null;
  /** Extended type: may include records and indexes in full-debug mode. */
  memory_v3?: {
    schema_version?: number;
    stats?: { records_count?: number; active?: number; stale?: number; archived?: number };
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
}

interface Props {
  agent: AgentForProfile;
  locationName: string;
  onClose: () => void;
  locations?: Record<string, { name: string; region?: string }>;
  sendCommand?: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
  contextId?: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const ALL_ITEM_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'bandage',        label: 'Бинт' },
  { value: 'medkit',         label: 'Аптечка' },
  { value: 'army_medkit',    label: 'Военная аптечка' },
  { value: 'stimpack',       label: 'Стимпак' },
  { value: 'morphine',       label: 'Морфин' },
  { value: 'antirad',        label: 'Антирад' },
  { value: 'rad_cure',       label: 'Рад-Пурге' },
  { value: 'pistol',         label: 'Пистолет ПМ' },
  { value: 'shotgun',        label: 'Обрез ТОЗ-34' },
  { value: 'ak74',           label: 'АК-74' },
  { value: 'pkm',            label: 'ПКМ (пулемёт)' },
  { value: 'svu_svd',        label: 'СВД (снайперская)' },
  { value: 'leather_jacket', label: 'Кожаная куртка' },
  { value: 'stalker_suit',   label: 'Комбинезон сталкера' },
  { value: 'combat_armor',   label: 'Боевой бронежилет' },
  { value: 'seva_suit',      label: 'Костюм СЕВА' },
  { value: 'exoskeleton',    label: 'Экзоскелет' },
  { value: 'ammo_9mm',       label: 'Патроны 9х18' },
  { value: 'ammo_12gauge',   label: 'Дробь 12 кал.' },
  { value: 'ammo_545',       label: 'Патроны 5.45х39' },
  { value: 'ammo_762',       label: 'Патроны 7.62х54R' },
  { value: 'bread',          label: 'Буханка хлеба' },
  { value: 'canned_food',    label: 'Тушёнка' },
  { value: 'military_ration',label: 'Сухой паёк' },
  { value: 'water',          label: 'Вода (0.5л)' },
  { value: 'purified_water', label: 'Очищенная вода (1л)' },
  { value: 'energy_drink',   label: 'Энергетик' },
  { value: 'vodka',          label: 'Водка' },
  { value: 'glucose',        label: 'Раствор глюкозы' },
  { value: 'echo_detector',  label: 'Детектор «Эхо»' },
  { value: 'bear_detector',  label: 'Детектор «Медведь»' },
  { value: 'veles_detector', label: 'Детектор «Велес»' },
];

// ─── Main Component ───────────────────────────────────────────────────────────

export default function AgentProfileModal({ agent, locationName, onClose, locations, sendCommand, contextId }: Props) {
  const [moneyEdit, setMoneyEdit] = React.useState<string | null>(null);
  const [moneySaving, setMoneySaving] = React.useState(false);
  const [thresholdEdit, setThresholdEdit] = React.useState<string | null>(null);
  const [thresholdSaving, setThresholdSaving] = React.useState(false);
  const [addItemType, setAddItemType] = React.useState<string>('bandage');
  const [addItemSaving, setAddItemSaving] = React.useState(false);
  const [removingItemId, setRemovingItemId] = React.useState<string | null>(null);
  const [rawDebugOpen, setRawDebugOpen] = useState(false);

  const [fetchedMemory, setFetchedMemory] = useState<MemEntry[] | null>(null);
  const fetchMemory = React.useCallback(() => {
    if (!contextId) return;
    contextsApi.getAgentMemory(contextId, agent.id)
      .then((res) => setFetchedMemory(res.data as MemEntry[]))
      .catch(() => { /* non-fatal */ });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, contextId]);

  useEffect(() => {
    setFetchedMemory(null);
    fetchMemory();
  }, [fetchMemory]);

  useEffect(() => {
    if (!contextId) return;
    const id = setInterval(fetchMemory, 5000);
    return () => clearInterval(id);
  }, [fetchMemory, contextId]);

  const displayMemory: MemEntry[] = fetchedMemory ?? (agent.story_events ?? []);

  const storyTimelineForObjective = displayMemory.slice(-120).map(toCompactTimelineEntry);
  const latestTraceEvent = getLatestTraceEvent(agent.brain_trace);
  const latestDecisionEvent = getLatestDecisionEvent(agent.brain_trace);
  const currentObjective = getCurrentObjectiveFromAgent(agent, latestDecisionEvent, storyTimelineForObjective);
  const liquidWealth = agent.money + agent.inventory.reduce((acc, item) => acc + Number(item.value ?? 0), 0);
  const materialThresholdPassed = agent.material_threshold != null ? liquidWealth >= agent.material_threshold : null;
  const wealthGoalReached = agent.wealth_goal_target != null ? liquidWealth >= agent.wealth_goal_target : null;

  const handleAddItem = async () => {
    if (!sendCommand || !addItemType) return;
    setAddItemSaving(true);
    try {
      await sendCommand('debug_add_item', { agent_id: agent.id, item_type: addItemType });
    } finally {
      setAddItemSaving(false);
    }
  };

  const handleRemoveItem = async (itemId: string) => {
    if (!sendCommand) return;
    setRemovingItemId(itemId);
    try {
      await sendCommand('debug_remove_item', { agent_id: agent.id, item_id: itemId });
    } finally {
      setRemovingItemId(null);
    }
  };

  const handleMoneySave = async () => {
    if (!sendCommand || moneyEdit === null) return;
    const parsed = parseInt(moneyEdit, 10);
    if (isNaN(parsed)) { setMoneyEdit(null); return; }
    setMoneySaving(true);
    try {
      await sendCommand('debug_set_agent_money', { agent_id: agent.id, amount: parsed });
    } finally {
      setMoneySaving(false);
      setMoneyEdit(null);
    }
  };

  const handleThresholdSave = async () => {
    if (!sendCommand || thresholdEdit === null) return;
    const raw = parseInt(thresholdEdit, 10);
    if (isNaN(raw)) { setThresholdEdit(null); return; }
    const parsed = Math.max(3000, Math.min(10000, raw));
    setThresholdSaving(true);
    try {
      await sendCommand('debug_set_agent_threshold', { agent_id: agent.id, amount: parsed });
    } finally {
      setThresholdSaving(false);
      setThresholdEdit(null);
    }
  };

  // ── Exports ──────────────────────────────────────────────────────────────
  const handleFullDebugExport = () => {
    const exportData = { ...agent, story_events: fetchedMemory ?? agent.story_events ?? [] };
    downloadJson(`stalker_${agent.name.replace(/\s+/g, '_')}_full_debug.json`, exportData);
  };

  const handleStoryExport = () => {
    const compact = buildCompactNpcHistoryExport(agent, displayMemory, locationName);
    downloadJson(`stalker_${agent.name.replace(/\s+/g, '_')}_history.json`, compact);
  };

  // ── Memory v3 summary helpers ─────────────────────────────────────────────
  const mv3 = agent.memory_v3;
  const mv3Records = mv3?.records ? Object.values(mv3.records) : [];
  const hasFullMv3 = mv3Records.length > 0;

  const mv3LayerCounts = hasFullMv3
    ? mv3Records.reduce<Record<string, number>>((acc, rec) => {
        acc[rec.layer] = (acc[rec.layer] ?? 0) + 1;
        return acc;
      }, {})
    : null;

  const mv3RecentlyAccessed = hasFullMv3
    ? mv3Records
        .filter((r) => r.last_accessed_turn != null)
        .sort((a, b) => Number(b.last_accessed_turn ?? 0) - Number(a.last_accessed_turn ?? 0))
        .slice(0, 5)
    : null;

  return (
    <div style={s.overlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>

        {/* ── 1. Header / export buttons ── */}
        <div style={s.header}>
          <div style={s.titleRow}>
            <span style={s.avatar}>{agent.controller.kind === 'human' ? '👤' : '🤖'}</span>
            <div>
              <div style={s.name}>{agent.name}</div>
              <div style={s.subtitle}>
                {agent.faction} · {agent.controller.kind === 'human' ? 'Игрок' : 'ИИ'} · {agent.is_alive ? '🟢 Жив' : '💀 Погиб'}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
            <button style={s.exportBtnDebug} title="Полный debug export" onClick={handleFullDebugExport}>📦</button>
            <button style={s.exportBtnStory} title="Компактная история NPC" onClick={handleStoryExport}>📘</button>
            <button style={s.closeBtn} onClick={onClose} title="Закрыть">✕</button>
          </div>
        </div>

        {/* ── 2. Current state — Location ── */}
        <Section label="📍 Местоположение">
          <div style={s.sectionVal}>{locationName}</div>
        </Section>

        {/* ── 2. Current state — Vital stats ── */}
        <Section label="📊 Характеристики">
          {[
            { label: '❤️ HP',    val: agent.hp,         max: agent.max_hp, p: agent.max_hp > 0 ? agent.hp / agent.max_hp : 0, color: agent.hp / agent.max_hp > 0.5 ? '#22c55e' : agent.hp / agent.max_hp > 0.25 ? '#f59e0b' : '#ef4444' },
            { label: '☢ Рад',   val: agent.radiation,  max: 100, p: Math.min(agent.radiation, 100) / 100, color: '#a855f7' },
            { label: '🍖 Голод', val: agent.hunger,     max: 100, p: agent.hunger / 100,     color: agent.hunger > 75 ? '#ef4444' : agent.hunger > 50 ? '#f59e0b' : '#22c55e' },
            { label: '💧 Жажда', val: agent.thirst,     max: 100, p: agent.thirst / 100,     color: agent.thirst > 75 ? '#ef4444' : agent.thirst > 50 ? '#f59e0b' : '#3b82f6' },
            { label: '😴 Сон',   val: agent.sleepiness, max: 100, p: agent.sleepiness / 100, color: agent.sleepiness > 75 ? '#ef4444' : agent.sleepiness > 50 ? '#f59e0b' : '#64748b' },
          ].map(({ label, val, max, p, color }) => (
            <div key={label} style={s.statRow}>
              <span style={s.statLabel}>{label}</span>
              <div style={s.barBg}>
                <div style={{ ...s.barFill, width: `${Math.max(0, Math.min(100, p * 100))}%`, background: color }} />
              </div>
              <span style={s.statVal}>{val}/{max}</span>
            </div>
          ))}
          {sendCommand ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
              <span style={s.moneyLabel}>💰</span>
              {moneyEdit !== null ? (
                <>
                  <input type="number" value={moneyEdit} onChange={(e) => setMoneyEdit(e.target.value)}
                    onBlur={handleMoneySave} onKeyDown={(e) => { if (e.key === 'Enter') handleMoneySave(); if (e.key === 'Escape') setMoneyEdit(null); }}
                    style={s.moneyInput} autoFocus disabled={moneySaving} />
                  <span style={s.moneyRu}>RU</span>
                  <button style={s.moneySaveBtn} onClick={handleMoneySave} disabled={moneySaving} title="Сохранить">{moneySaving ? '…' : '💾'}</button>
                </>
              ) : (
                <>
                  <span style={s.moneyLineEditable} onClick={() => setMoneyEdit(String(agent.money))} title="Нажмите для редактирования">{agent.money} RU</span>
                  <span style={s.moneyEditHint}>✏️</span>
                </>
              )}
            </div>
          ) : (
            <div style={s.moneyLine}>💰 {agent.money} RU</div>
          )}
          {agent.reputation != null && <div style={s.repLine}>⭐ Репутация: {agent.reputation}</div>}
        </Section>

        {/* ── 2. Current state — Skills ── */}
        {agent.skill_combat != null && (
          <Section label="🎯 Навыки">
            <div style={s.skillGrid}>
              {[
                { label: '⚔ Бой',      val: agent.skill_combat ?? 1 },
                { label: '🔭 Сталкер',  val: agent.skill_stalker ?? 1 },
                { label: '💼 Торговля', val: agent.skill_trade ?? 1 },
                { label: '💊 Медицина', val: agent.skill_medicine ?? 1 },
                { label: '🗣 Общение',  val: agent.skill_social ?? 1 },
              ].map(({ label, val }) => (
                <div key={label} style={s.skillChip}>
                  <span style={s.skillLabel}>{label}</span>
                  <span style={s.skillVal}>Ур {val}</span>
                </div>
              ))}
            </div>
            {agent.experience != null && (
              <div style={s.xpLine}>📈 Опыт: {agent.experience} XP{agent.risk_tolerance != null && ` · Риск: ${agent.risk_tolerance}`}</div>
            )}
            {agent.material_threshold != null && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                <span style={{ color: '#94a3b8', fontSize: '0.78rem' }}>💼 Порог богатства:</span>
                {sendCommand && thresholdEdit !== null ? (
                  <>
                    <input type="number" min={3000} max={10000} value={thresholdEdit}
                      onChange={(e) => setThresholdEdit(e.target.value)}
                      onBlur={handleThresholdSave}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleThresholdSave(); if (e.key === 'Escape') setThresholdEdit(null); }}
                      style={s.moneyInput} autoFocus disabled={thresholdSaving} />
                    <span style={s.moneyRu}>RU</span>
                    <button style={s.moneySaveBtn} onClick={handleThresholdSave} disabled={thresholdSaving} title="Сохранить">{thresholdSaving ? '…' : '💾'}</button>
                  </>
                ) : (
                  <>
                    <span
                      style={sendCommand ? s.moneyLineEditable : { color: '#e2e8f0', fontSize: '0.82rem' }}
                      onClick={() => sendCommand && setThresholdEdit(String(agent.material_threshold))}
                      title={sendCommand ? 'Нажмите для редактирования (3000–10000)' : undefined}
                    >{agent.material_threshold} RU</span>
                    {sendCommand && <span style={s.moneyEditHint}>✏️</span>}
                  </>
                )}
              </div>
            )}
          </Section>
        )}

        {/* ── 3. NPC Brain v3 — Current Decision ── */}
        {agent.brain_trace && (
          <Section label="🧠 NPC Brain v3 — Текущее решение">
            <NpcBrainPanel
              brainTrace={agent.brain_trace}
              latestTraceEvent={latestTraceEvent}
              latestDecisionEvent={latestDecisionEvent}
              currentObjective={currentObjective}
              scheduledAction={agent.scheduled_action}
              huntTargetBelief={agent.brain_v3_context?.hunt_target_belief ?? null}
            />
          </Section>
        )}

        {/* ── 4. NPC Brain v3 — Objective Ranking ── */}
        {latestDecisionEvent != null && (
          (latestDecisionEvent.objective_scores?.length || latestDecisionEvent.alternatives?.length || latestDecisionEvent.active_objective != null)
        ) && (
          <Section label="🏆 Рейтинг целей">
            <ObjectiveRankingPanel latestEvent={latestDecisionEvent} />
          </Section>
        )}

        {/* ── 5. NPC Brain v3 — Needs & Constraints ── */}
        {latestDecisionEvent != null && (
          (latestDecisionEvent.immediate_needs?.length || latestDecisionEvent.item_needs?.length || latestDecisionEvent.liquidity != null)
        ) && (
          <Section label="⚠️ Потребности и ограничения">
            <NeedsPanel latestEvent={latestDecisionEvent} />
          </Section>
        )}

        {/* ── 6. NPC Brain v3 — Memory Used ── */}
        {latestDecisionEvent != null && (
          <Section label="🧩 Использованная память">
            <MemoryUsedPanel latestEvent={latestDecisionEvent} />
          </Section>
        )}

        {/* ── 7. Current Runtime Action ── */}
        {(agent.scheduled_action != null || agent.active_plan_v3 != null) && (
          <Section label="⏳ Текущее действие">
            <RuntimeActionPanel scheduledAction={agent.scheduled_action} activePlanV3={agent.active_plan_v3} locations={locations} />
          </Section>
        )}

        {/* ── 8. Goals ── */}
        {(agent.global_goal || agent.current_goal || currentObjective || latestDecisionEvent?.adapter_intent?.kind || latestDecisionEvent?.intent_kind || agent.brain_v3_context?.adapter_intent?.kind || agent.brain_v3_context?.intent_kind || agent.material_threshold != null || agent.wealth_goal_target != null || agent.global_goal_achieved != null) && (
          <Section label="🎯 Цели">
            {agent.global_goal && (
              <div style={s.goalRow}>
                <span style={s.goalLabel}>Глобальная цель:</span>
                <span style={s.goalVal}>{agent.global_goal}</span>
              </div>
            )}
            {agent.current_goal && (
              <div style={s.goalRow}>
                <span style={s.goalLabel}>Текущая высокая цель:</span>
                <span style={s.goalVal}>{currentGoalLabel(agent.current_goal)}</span>
              </div>
            )}
            {(agent.material_threshold != null || agent.wealth_goal_target != null) && (
              <>
                <div style={s.goalRow}>
                  <span style={s.goalLabel}>Liquid wealth:</span>
                  <span style={s.goalVal}>{liquidWealth} RU</span>
                </div>
                {agent.material_threshold != null && (
                  <div style={s.goalRow}>
                    <span style={s.goalLabel}>Material threshold:</span>
                    <span style={s.goalVal}>
                      {agent.material_threshold} RU {materialThresholdPassed == null ? '' : materialThresholdPassed ? '— passed' : '— not passed'}
                    </span>
                  </div>
                )}
                {agent.wealth_goal_target != null && (
                  <div style={s.goalRow}>
                    <span style={s.goalLabel}>Wealth goal target:</span>
                    <span style={s.goalVal}>
                      {agent.wealth_goal_target} RU {wealthGoalReached == null ? '' : wealthGoalReached ? '— reached' : '— not reached'}
                    </span>
                  </div>
                )}
              </>
            )}
            {agent.global_goal_achieved != null && (
              <div style={s.goalRow}>
                <span style={s.goalLabel}>Global goal completed:</span>
                <span style={s.goalVal}>
                  {agent.global_goal_achieved ? 'yes' : 'no'}
                  {agent.global_goal_achieved ? ' · next objective: LEAVE_ZONE' : ''}
                </span>
              </div>
            )}
            {currentObjective && (
              <div style={s.goalRow}>
                <span style={s.goalLabel}>Активная objective:</span>
                <span style={{ ...s.goalVal, color: '#a5f3fc', fontWeight: 700 }}>
                  {formatObjectiveKey(currentObjective.key).toUpperCase()}
                  {currentObjective.score != null && (
                    <span style={{ color: '#64748b', fontWeight: 400, marginLeft: 6 }}>{pct(currentObjective.score)}</span>
                  )}
                </span>
              </div>
            )}
            {(latestDecisionEvent?.adapter_intent?.kind || latestDecisionEvent?.intent_kind || agent.brain_v3_context?.adapter_intent?.kind || agent.brain_v3_context?.intent_kind) && (
              <div style={s.goalRow}>
                <span style={s.goalLabel}>Исполнительный adapter:</span>
                <span style={{ ...s.goalVal, color: '#818cf8' }}>
                  {latestDecisionEvent?.adapter_intent?.kind
                    ?? latestDecisionEvent?.intent_kind
                    ?? agent.brain_v3_context?.adapter_intent?.kind
                    ?? agent.brain_v3_context?.intent_kind}
                </span>
              </div>
            )}
            {agent.global_goal === 'kill_stalker' && agent.kill_target_id && (
              <div style={{ color: '#f87171', fontSize: '0.75rem', marginTop: 2 }}>
                🎯 Цель устранения: <strong>{agent.kill_target_id}</strong>
              </div>
            )}
          </Section>
        )}

        {/* ── 9. Equipment ── */}
        <Section label="🔫 Снаряжение">
          {Object.keys(agent.equipment).length === 0 ? (
            <span style={s.empty}>Нет снаряжения</span>
          ) : (
            Object.entries(agent.equipment).map(([slot, item]) => {
              const slotLabel: Record<string, string> = { weapon: '🔫 Оружие', armor: '🛡️ Броня', detector: '📡 Детектор' };
              return (
                <div key={slot} style={{ ...s.equipRow, alignItems: 'center' }}>
                  <span style={s.equipSlot}>{slotLabel[slot] ?? slot}</span>
                  <span style={item ? s.equipItem : s.equipEmpty}>{item ? item.name : '— пусто —'}</span>
                  {item?.value != null && <span style={s.equipVal}>{item.value} RU</span>}
                  {sendCommand && item?.id && (
                    <button onClick={() => handleRemoveItem(item.id)} disabled={removingItemId === item.id} title="Снять с экипировки" style={s.removeItemBtn}>
                      {removingItemId === item.id ? '…' : '✕'}
                    </button>
                  )}
                </div>
              );
            })
          )}
        </Section>

        {/* ── 10. Inventory ── */}
        <Section label={`🎒 Инвентарь (${agent.inventory.length})`}>
          {agent.inventory.length === 0 ? (
            <span style={s.empty}>Пусто</span>
          ) : (
            agent.inventory.map((item) => (
              <div key={item.id} style={{ ...s.invRow, alignItems: 'center' }}>
                <span style={s.invName}>{item.name}</span>
                {item.weight != null && <span style={s.invWeight}>{item.weight} кг</span>}
                {item.value != null && <span style={s.invVal}>{item.value} RU</span>}
                {sendCommand && (
                  <button onClick={() => handleRemoveItem(item.id)} disabled={removingItemId === item.id} title="Удалить предмет" style={s.removeItemBtn}>
                    {removingItemId === item.id ? '…' : '✕'}
                  </button>
                )}
              </div>
            ))
          )}
          {sendCommand && (
            <div style={s.addItemRow}>
              <select value={addItemType} onChange={(e) => setAddItemType(e.target.value)} style={s.addItemSelect}>
                {ALL_ITEM_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
              </select>
              <button onClick={handleAddItem} disabled={addItemSaving} style={s.addItemBtn} title="Добавить предмет в инвентарь">
                {addItemSaving ? '…' : '+ Добавить'}
              </button>
            </div>
          )}
        </Section>

        {/* ── 11. Memory Timeline ── */}
        {displayMemory.length > 0 && (
          <Section label={`📖 История (${displayMemory.length})`}>
            <MemoryTimeline memory={displayMemory} />
          </Section>
        )}

        {/* ── 12. Memory v3 Summary ── */}
        {mv3 && mv3.stats && (
          <Section label="🗂️ Память v3">
            <div style={s.smallMono}>
              Записей: {mv3.stats.records_count ?? 0} · Активных: {mv3.stats.active ?? 0} · Устаревших: {mv3.stats.stale ?? 0} · Архив: {mv3.stats.archived ?? 0}
            </div>
            {mv3LayerCounts && (
              <div style={{ marginTop: 4 }}>
                <div style={s.mv3SubLabel}>Слои:</div>
                <div style={s.mv3LayerList}>
                  {Object.entries(mv3LayerCounts).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([layer, count]) => (
                    <span key={layer} style={s.mv3LayerChip}>{layer}: {count}</span>
                  ))}
                </div>
              </div>
            )}
            {mv3RecentlyAccessed && mv3RecentlyAccessed.length > 0 && (
              <div style={{ marginTop: 4 }}>
                <div style={s.mv3SubLabel}>Недавно использованные:</div>
                {mv3RecentlyAccessed.map((rec) => (
                  <div key={rec.id} style={s.mv3RecRow}>
                    <span style={s.mv3RecKind}>{rec.kind}</span>
                    <span style={s.mv3RecSummary}>{rec.summary}</span>
                  </div>
                ))}
              </div>
            )}
          </Section>
        )}

        {/* ── 13. Raw Debug collapsible ── */}
        {agent.brain_trace && (
          <div style={s.section}>
            <button style={s.rawDebugToggle} onClick={() => setRawDebugOpen((v) => !v)}>
              {rawDebugOpen ? '▼' : '▶'} 🧾 Raw debug
            </button>
            {rawDebugOpen && (
              <div style={s.rawDebugBox}>
                {agent.brain_trace && (
                  <>
                    <div style={s.rawDebugLabel}>brain_trace</div>
                    <pre style={s.smallMono}>{JSON.stringify(agent.brain_trace, null, 2)}</pre>
                  </>
                )}
                {agent.active_plan_v3 != null && (
                  <>
                    <div style={{ ...s.rawDebugLabel, marginTop: 8 }}>active_plan_v3</div>
                    <pre style={s.smallMono}>{JSON.stringify(agent.active_plan_v3, null, 2)}</pre>
                  </>
                )}
                {mv3 && (
                  <>
                    <div style={{ ...s.rawDebugLabel, marginTop: 8 }}>memory_v3 (stats)</div>
                    <pre style={s.smallMono}>{JSON.stringify({ schema_version: mv3.schema_version, stats: mv3.stats }, null, 2)}</pre>
                  </>
                )}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}

// ─── Section helper ───────────────────────────────────────────────────────────

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={s.section}>
      <div style={s.sectionLabel}>{label}</div>
      {children}
    </div>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.75)',
    zIndex: 2000,
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'center',
    padding: '1.5rem 1rem',
    overflowY: 'auto',
  },
  modal: {
    background: '#0f172a',
    borderRadius: 14,
    border: '1px solid #334155',
    padding: '1.5rem',
    width: '100%',
    maxWidth: 560,
    display: 'flex',
    flexDirection: 'column',
    gap: '1rem',
    marginBottom: '1.5rem',
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
  titleRow: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  avatar: { fontSize: '2.5rem', flexShrink: 0 },
  name: { color: '#f8fafc', fontWeight: 700, fontSize: '1.15rem' },
  subtitle: { color: '#64748b', fontSize: '0.8rem', marginTop: 2 },
  closeBtn: {
    background: 'transparent', border: 'none', color: '#64748b',
    cursor: 'pointer', fontSize: '1.1rem', padding: '0.15rem', lineHeight: 1, flexShrink: 0,
  },
  // ── New export buttons (spec section 19) ──
  exportBtnDebug: {
    background: '#1e293b', border: '1px solid #334155', color: '#94a3b8',
    cursor: 'pointer', fontSize: '1rem', padding: '0.2rem 0.35rem',
    lineHeight: 1, borderRadius: 6, flexShrink: 0,
  },
  exportBtnStory: {
    background: '#1e3a5f', border: '1px solid #3b82f6', color: '#93c5fd',
    cursor: 'pointer', fontSize: '1rem', padding: '0.2rem 0.35rem',
    lineHeight: 1, borderRadius: 6, flexShrink: 0,
  },
  section: {
    background: '#1e293b', borderRadius: 8, padding: '0.75rem',
    border: '1px solid #334155', display: 'flex', flexDirection: 'column', gap: '0.45rem',
  },
  sectionLabel: {
    color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700,
    textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2,
  },
  sectionVal: { color: '#cbd5e1', fontSize: '0.9rem' },
  schedLine: { color: '#a78bfa', fontSize: '0.8rem', fontStyle: 'italic' },
  statRow: { display: 'flex', alignItems: 'center', gap: 8 },
  statLabel: { color: '#94a3b8', fontSize: '0.72rem', width: 70, flexShrink: 0 },
  barBg: { flex: 1, height: 6, background: '#0f172a', borderRadius: 3, overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 3, transition: 'width 0.3s' },
  statVal: { color: '#94a3b8', fontSize: '0.7rem', width: 42, textAlign: 'right' },
  moneyLine: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem', marginTop: 4 },
  moneyLabel: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem' },
  moneyLineEditable: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem', cursor: 'pointer', borderBottom: '1px dashed #fbbf24' },
  moneyEditHint: { color: '#64748b', fontSize: '0.72rem', cursor: 'pointer' },
  moneyInput: { background: '#0f172a', border: '1px solid #fbbf24', borderRadius: 5, color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem', padding: '0.1rem 0.35rem', width: 100 },
  moneyRu: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem' },
  moneySaveBtn: { background: 'transparent', border: '1px solid #fbbf24', borderRadius: 5, color: '#fbbf24', cursor: 'pointer', fontSize: '0.8rem', padding: '0.1rem 0.3rem', lineHeight: 1 },
  repLine: { color: '#a78bfa', fontSize: '0.8rem' },
  skillGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 6 },
  skillChip: { background: '#0f172a', borderRadius: 6, padding: '0.3rem 0.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  skillLabel: { color: '#94a3b8', fontSize: '0.72rem' },
  skillVal: { color: '#f8fafc', fontWeight: 700, fontSize: '0.75rem' },
  xpLine: { color: '#64748b', fontSize: '0.75rem', marginTop: 2 },
  goalLine: { color: '#94a3b8', fontSize: '0.8rem', marginTop: 4 },
  subgoal: { color: '#60a5fa', fontSize: '0.78rem' },
  // ── Goals section ──
  goalRow: { display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' as const },
  goalLabel: { color: '#64748b', fontSize: '0.72rem', flexShrink: 0, minWidth: 160 },
  goalVal: { color: '#cbd5e1', fontSize: '0.82rem' },
  // ── Equipment ──
  equipRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem' },
  equipSlot: { color: '#64748b', fontSize: '0.7rem', width: 56, flexShrink: 0, textTransform: 'capitalize' },
  equipItem: { color: '#cbd5e1', flex: 1 },
  equipEmpty: { color: '#334155', flex: 1 },
  equipVal: { color: '#64748b', fontSize: '0.7rem' },
  // ── Inventory ──
  invRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem', borderBottom: '1px solid #0f172a', paddingBottom: 3 },
  invName: { color: '#cbd5e1', flex: 1 },
  invWeight: { color: '#475569', fontSize: '0.7rem' },
  invVal: { color: '#64748b', fontSize: '0.7rem' },
  removeItemBtn: { background: 'transparent', border: '1px solid #334155', borderRadius: 4, color: '#ef4444', cursor: 'pointer', fontSize: '0.68rem', padding: '1px 5px', lineHeight: '1.2', flexShrink: 0 },
  addItemRow: { display: 'flex', gap: 6, alignItems: 'center', marginTop: 6, paddingTop: 6, borderTop: '1px solid #1e293b' },
  addItemSelect: { flex: 1, background: '#1e293b', color: '#cbd5e1', border: '1px solid #334155', borderRadius: 4, fontSize: '0.75rem', padding: '3px 6px' },
  addItemBtn: { background: '#14532d', border: '1px solid #16a34a', borderRadius: 4, color: '#86efac', cursor: 'pointer', fontSize: '0.75rem', padding: '3px 8px', whiteSpace: 'nowrap' as const },
  empty: { color: '#334155', fontSize: '0.72rem' },
  // ── Memory v3 ──
  mv3SubLabel: { color: '#64748b', fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.04em', marginBottom: 3 },
  mv3LayerList: { display: 'flex', flexWrap: 'wrap' as const, gap: 4 },
  mv3LayerChip: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 4, padding: '0.1rem 0.4rem', color: '#64748b', fontSize: '0.7rem' },
  mv3RecRow: { display: 'flex', gap: 6, alignItems: 'baseline', paddingTop: 2 },
  mv3RecKind: { color: '#6366f1', fontSize: '0.68rem', flexShrink: 0, minWidth: 120 },
  mv3RecSummary: { color: '#64748b', fontSize: '0.7rem', lineHeight: 1.4 },
  // ── Raw Debug (spec section 19) ──
  rawDebugToggle: { background: 'transparent', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '0.75rem', padding: 0, textAlign: 'left' as const, fontWeight: 600, letterSpacing: '0.03em' },
  rawDebugBox: { background: '#0a0f1a', border: '1px solid #1e293b', borderRadius: 6, padding: '0.5rem', marginTop: 4, maxHeight: 360, overflowY: 'auto' },
  rawDebugLabel: { color: '#475569', fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.05em', marginBottom: 2 },
  // ── Spec section 19 new styles ──
  brainCard: { background: '#0d1f3c', borderRadius: 8, borderLeft: '3px solid #6366f1', padding: '0.65rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.45rem' },
  objectiveBadge: { background: '#1e3a5f', border: '1px solid #3b82f6', borderRadius: 4, padding: '0.1rem 0.5rem', color: '#93c5fd', fontSize: '0.7rem', fontWeight: 600 },
  objectiveSelected: { background: '#052e16', border: '1px solid #166534', borderRadius: 6, padding: '0.35rem 0.5rem' },
  objectiveRejected: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, padding: '0.35rem 0.5rem' },
  objectiveScoreBar: { height: 3, background: '#0f172a', borderRadius: 2, overflow: 'hidden' },
  needChip: { border: '1px solid #1e3a5f', background: '#0c1624', borderRadius: 5, padding: '0.25rem 0.45rem', display: 'flex', alignItems: 'center', gap: 6 },
  memoryUsedCard: { background: '#0f172a', borderRadius: 6, borderLeft: '3px solid #312e81', padding: '0.35rem 0.5rem', display: 'flex', flexDirection: 'column', gap: 3 },
  smallMono: { fontFamily: 'monospace', fontSize: '0.7rem', color: '#64748b', whiteSpace: 'pre-wrap', margin: 0, lineHeight: 1.5 },
  // ── Legacy compatibility ──
  decisionRow: { display: 'flex', gap: 8, alignItems: 'flex-start' },
  decisionLabel: { color: '#64748b', fontSize: '0.75rem', minWidth: 68, flexShrink: 0, paddingTop: 1 },
  decisionVal: { color: '#e2e8f0', fontSize: '0.78rem', lineHeight: 1.5 },
  decisionChosen: { border: '1px solid #22c55e', borderRadius: 8, padding: '0.5rem 0.75rem', background: '#052e16', display: 'flex', flexDirection: 'column' as const, gap: 4 },
  decisionChosenAction: { color: '#86efac', fontWeight: 700, fontSize: '0.85rem' },
  decisionChosenReason: { color: '#94a3b8', fontSize: '0.75rem' },
  decisionChosenGoal: { color: '#475569', fontSize: '0.7rem' },
  decisionPreviewBtn: { display: 'none' },
  decisionToggleBtn: { display: 'none' },
  decisionRefreshBtn: { background: 'transparent', border: '1px solid #334155', color: '#64748b', borderRadius: 6, padding: '0.2rem 0.55rem', fontSize: '0.72rem', cursor: 'pointer', alignSelf: 'flex-start', marginTop: 4 },
  v2NeedRow: { display: 'flex', alignItems: 'center', gap: 6, padding: '0.15rem 0' },
  v2NeedLabel: { color: '#94a3b8', fontSize: '0.7rem', width: 112, flexShrink: 0, whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' },
  v2BarBg: { flex: 1, height: 5, background: '#0f172a', borderRadius: 3, overflow: 'hidden' },
  v2ScoreVal: { color: '#64748b', fontSize: '0.68rem', width: 34, textAlign: 'right' as const },
  v2IntentBox: { display: 'flex', flexDirection: 'column' as const, gap: 3, background: '#0f172a', border: '1px solid #334155', borderRadius: 7, padding: '0.45rem 0.6rem', marginTop: 4 },
  v2IntentHeader: { display: 'flex', alignItems: 'center', gap: 6 },
  v2IntentIcon: { fontSize: '0.95rem', flexShrink: 0 },
  v2IntentLabel: { color: '#f8fafc', fontWeight: 700, fontSize: '0.82rem', flex: 1 },
  v2IntentScore: { fontSize: '0.7rem', color: '#64748b', fontVariantNumeric: 'tabular-nums' as const },
  v2IntentReason: { color: '#94a3b8', fontSize: '0.72rem', lineHeight: 1.45, marginTop: 1 },
  v2PlanLine: { display: 'flex', alignItems: 'center', gap: 5, marginTop: 2, fontSize: '0.7rem', color: '#475569' },
  v2SourceBadge: { fontSize: '0.6rem', color: '#6366f1', border: '1px solid #6366f1', borderRadius: 4, padding: '0px 4px', flexShrink: 0 },
  v2RefreshBtn: { background: 'transparent', border: '1px solid #334155', color: '#64748b', borderRadius: 6, padding: '0.2rem 0.55rem', fontSize: '0.72rem', cursor: 'pointer', alignSelf: 'flex-start', marginTop: 6 },
  memoryList: { display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 480, overflowY: 'auto' },
  memoryEntry: { background: '#0f172a', borderRadius: 6, padding: '0.5rem', paddingLeft: '0.6rem' },
  memoryMeta: { display: 'flex', justifyContent: 'space-between', marginBottom: 3 },
  memoryType: { color: '#94a3b8', fontWeight: 600, fontSize: '0.72rem' },
  memoryWhen: { color: '#475569', fontSize: '0.7rem' },
  memoryTitle: { color: '#f8fafc', fontWeight: 600, fontSize: '0.82rem', marginBottom: 2 },
  memorySummary: { color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.5 },
  memoryFieldLabel: { color: '#64748b', fontWeight: 600 },
};

// ─── AgentCreateModal ─────────────────────────────────────────────────────────

export interface AgentCreateProps {
  onClose: () => void;
  onSave: (name: string, faction: string, globalGoal: string, isTrader: boolean, killTargetId?: string) => Promise<void>;
  defaultIsTrader?: boolean;
  agents?: Record<string, { id: string; name: string; is_alive?: boolean }>;
}

const FACTION_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'loner',    label: 'Одиночка' },
  { value: 'military', label: 'Военные'  },
  { value: 'duty',     label: 'Долг'     },
  { value: 'freedom',  label: 'Свобода'  },
];

const GLOBAL_GOAL_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'get_rich',             label: 'Разбогатеть'          },
  { value: 'unravel_zone_mystery', label: 'Разгадать тайну Зоны' },
  { value: 'kill_stalker',         label: 'Устранить сталкера'   },
];

export function AgentCreateModal({ onClose, onSave, defaultIsTrader = false, agents }: AgentCreateProps) {
  const [name,         setName]         = useState('');
  const [faction,      setFaction]      = useState('loner');
  const [globalGoal,   setGlobalGoal]   = useState('get_rich');
  const [isTrader,     setIsTrader]     = useState(defaultIsTrader);
  const [killTargetId, setKillTargetId] = useState('');
  const [saving,       setSaving]       = useState(false);
  const [err,          setErr]          = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await onSave(name.trim(), faction, globalGoal, isTrader, globalGoal === 'kill_stalker' ? killTargetId.trim() : undefined);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Ошибка создания');
      setSaving(false);
    }
  };

  return (
    <div style={s.overlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div style={s.header}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: '1.5rem' }}>{isTrader ? '🏪' : '👤'}</span>
            <span style={s.name}>{isTrader ? 'Создать торговца' : 'Создать сталкера'}</span>
          </div>
          <button style={s.closeBtn} onClick={onClose} title="Закрыть">✕</button>
        </div>

        <div style={{ ...s.section, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={isTrader} onChange={(e) => setIsTrader(e.target.checked)} style={{ accentColor: '#f59e0b', width: 16, height: 16 }} />
            <span style={{ ...cs.traderLabel, color: isTrader ? '#f59e0b' : '#94a3b8' }}>
              🏪 Торговец (фиксирован на локации, покупает при наличии средств)
            </span>
          </label>
        </div>

        <div style={s.section}>
          <div style={s.sectionLabel}>Имя персонажа</div>
          <input style={cs.input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя персонажа (пусто = случайное)" autoFocus />
        </div>

        {!isTrader && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Фракция</div>
            <select style={cs.input} value={faction} onChange={(e) => setFaction(e.target.value)}>
              {FACTION_OPTIONS.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        )}

        {!isTrader && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Глобальная цель</div>
            <select style={cs.input} value={globalGoal} onChange={(e) => setGlobalGoal(e.target.value)}>
              {GLOBAL_GOAL_OPTIONS.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        )}

        {!isTrader && globalGoal === 'kill_stalker' && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Цель устранения</div>
            {agents && Object.keys(agents).length > 0 ? (
              <select style={cs.input} value={killTargetId} onChange={(e) => setKillTargetId(e.target.value)}>
                <option value="">— выбрать сталкера —</option>
                {Object.values(agents).map((ag) => (
                  <option key={ag.id} value={ag.id}>{ag.name}{ag.is_alive === false ? ' †' : ''} ({ag.id})</option>
                ))}
              </select>
            ) : (
              <input style={cs.input} value={killTargetId} onChange={(e) => setKillTargetId(e.target.value)} placeholder="ID сталкера-цели (напр. agent_0)" />
            )}
          </div>
        )}

        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: -4 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
          <button style={cs.cancelBtn} onClick={onClose} disabled={saving}>Отмена</button>
          <button style={cs.saveBtn} onClick={handleSubmit} disabled={saving}>{saving ? '…' : isTrader ? 'Создать торговца' : 'Создать'}</button>
        </div>
      </div>
    </div>
  );
}

const cs: Record<string, React.CSSProperties> = {
  input: { width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#f1f5f9', fontSize: '0.82rem', padding: '0.4rem 0.55rem', boxSizing: 'border-box' },
  traderLabel: { fontSize: '0.78rem', fontWeight: 500, userSelect: 'none' },
  saveBtn: { padding: '0.4rem 1rem', background: '#1d4ed8', color: '#fff', border: 'none', borderRadius: 7, cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 },
  cancelBtn: { padding: '0.4rem 0.8rem', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 7, cursor: 'pointer', fontSize: '0.8rem' },
};
