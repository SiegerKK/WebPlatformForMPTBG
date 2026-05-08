/**
 * AgentProfileModal — reusable modal showing a full Zone Stalkers agent profile.
 *
 * Import `AgentForProfile` to build a compatible agent object.
 * Closing: clicking the semi-transparent overlay or the ✕ button calls `onClose`.
 */
import React, { useState, useEffect } from 'react';
import { contextsApi } from '../../../api/client';

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentInventoryItem {
  id: string;
  type: string;
  name: string;
  weight?: number;
  value?: number;
}

type BrainTraceMode = 'plan_monitor' | 'decision' | 'system';
type BrainTraceDecision = 'continue' | 'abort' | 'new_intent' | 'no_op';

type BrainTraceNeed = {
  key: string;
  urgency: number;
  selected_item_type?: string | null;
  missing_count?: number;
  reason?: string;
};

type BrainTraceEvent = {
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
};

type BrainTrace = {
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
    /** Ultimate travel destination (may differ from target_id for multi-hop routes). */
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
  risk_tolerance?: number;
  reputation?: number;
  has_left_zone?: boolean;
  global_goal_achieved?: boolean;
  kill_target_id?: string | null;
  memory?: Array<{
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
}

interface Props {
  agent: AgentForProfile;
  locationName: string;
  onClose: () => void;
  /** Location registry used to resolve travel destination names and regions. */
  locations?: Record<string, { name: string; region?: string }>;
  /** When provided (debug mode), bot agents show a "preview decision" panel. */
  sendCommand?: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
  /**
   * Zone-map context ID.  When provided, the modal fetches agent memory
   * on-demand via the API (``GET /contexts/{contextId}/agents/{id}/memory``)
   * because memory is no longer included in the ``getTree`` state_blob.
   */
  contextId?: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const TIME_LABEL = (h: number, m: number) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;

/** 1 game turn = 1 real minute (mirrors MINUTES_PER_TURN in tick_rules.py) */
const MINUTES_PER_TURN = 1;
const TURNS_PER_HOUR = 60 / MINUTES_PER_TURN;

/** All available item types with display names — mirrors ITEM_TYPES in items.py */
const ALL_ITEM_OPTIONS: Array<{ value: string; label: string }> = [
  // Medical
  { value: 'bandage',       label: 'Бинт' },
  { value: 'medkit',        label: 'Аптечка' },
  { value: 'army_medkit',   label: 'Военная аптечка' },
  { value: 'stimpack',      label: 'Стимпак' },
  { value: 'morphine',      label: 'Морфин' },
  { value: 'antirad',       label: 'Антирад' },
  { value: 'rad_cure',      label: 'Рад-Пурге' },
  // Weapons
  { value: 'pistol',        label: 'Пистолет ПМ' },
  { value: 'shotgun',       label: 'Обрез ТОЗ-34' },
  { value: 'ak74',          label: 'АК-74' },
  { value: 'pkm',           label: 'ПКМ (пулемёт)' },
  { value: 'svu_svd',       label: 'СВД (снайперская)' },
  // Armor
  { value: 'leather_jacket',label: 'Кожаная куртка' },
  { value: 'stalker_suit',  label: 'Комбинезон сталкера' },
  { value: 'combat_armor',  label: 'Боевой бронежилет' },
  { value: 'seva_suit',     label: 'Костюм СЕВА' },
  { value: 'exoskeleton',   label: 'Экзоскелет' },
  // Ammo
  { value: 'ammo_9mm',      label: 'Патроны 9х18' },
  { value: 'ammo_12gauge',  label: 'Дробь 12 кал.' },
  { value: 'ammo_545',      label: 'Патроны 5.45х39' },
  { value: 'ammo_762',      label: 'Патроны 7.62х54R' },
  // Consumables
  { value: 'bread',         label: 'Буханка хлеба' },
  { value: 'canned_food',   label: 'Тушёнка' },
  { value: 'military_ration',label: 'Сухой паёк' },
  { value: 'water',         label: 'Вода (0.5л)' },
  { value: 'purified_water',label: 'Очищенная вода (1л)' },
  { value: 'energy_drink',  label: 'Энергетик' },
  { value: 'vodka',         label: 'Водка' },
  { value: 'glucose',       label: 'Раствор глюкозы' },
  // Detectors
  { value: 'echo_detector', label: 'Детектор «Эхо»' },
  { value: 'bear_detector', label: 'Детектор «Медведь»' },
  { value: 'veles_detector',label: 'Детектор «Велес»' },
];

/**
 * Derive game day/hour/minute from world_turn.
 * The game starts at turn 1 = day 1, 06:00.
 * Mirrors the time-advancement logic in tick_rules.py.
 */
const turnToTime = (worldTurn: number): { world_day: number; world_hour: number; world_minute: number } => {
  const totalMinutes = 6 * 60 + (worldTurn - 1) * MINUTES_PER_TURN;
  return {
    world_day: 1 + Math.floor(totalMinutes / (24 * 60)),
    world_hour: Math.floor(totalMinutes / 60) % 24,
    world_minute: totalMinutes % 60,
  };
};

const traceTimeLabel = (
  worldTurn: number,
  worldTime?: { world_day: number; world_hour: number; world_minute: number },
): string => {
  const worldTimeData = worldTime ?? turnToTime(worldTurn);
  return `День ${worldTimeData.world_day} · ${TIME_LABEL(worldTimeData.world_hour, worldTimeData.world_minute)}`;
};

/**
 * Format a scheduled-action countdown for display.
 * For sleep, turns are converted to hours; everything else shows minutes.
 */
const schedRemaining = (type: string, turns: number): string => {
  if (type === 'sleep') {
    return `${Math.ceil(turns / TURNS_PER_HOUR)} ч осталось`;
  }
  return `${turns * MINUTES_PER_TURN} мин осталось`;
};

const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore_anomaly_location: '🔍',
  sleep: '😴',
  event: '📖',
};

// Memory entry type → icon and colour
const MEM_ICONS: Record<string, string> = {
  decision: '🧠',
  action: '⚡',
  observation: '👁️',
  // legacy types kept for backwards compat with old save data
  travel: '🚶',
  explore_anomaly_location: '🔍',
  sleep: '😴',
  pickup: '🎁',
  trade_sell: '💰',
};

const MEM_COLORS: Record<string, string> = {
  decision: '#818cf8',   // indigo – the "thought"
  action: '#34d399',     // emerald – the "deed"
  observation: '#fbbf24', // amber – the "sight"
  travel: '#34d399',
  explore_anomaly_location: '#34d399',
  sleep: '#34d399',
  pickup: '#34d399',
  trade_sell: '#34d399',
};

// Map raw backend current_goal IDs to human-readable Russian labels.
const CURRENT_GOAL_LABELS: Record<string, string> = {
  gather_resources:             'Сбор ресурсов',
  goal_get_rich:                'Нажива',
  goal_get_rich_seek_artifacts: 'Ищу артефакты',
  sell_artifacts:               'Продаю артефакты',
  flee_to_safety:               'Бегство',

  upgrade_equipment:            'Апгрейд снаряжения',
  get_weapon:                   'Ищу оружие',
  get_armor:                    'Ищу броню',
  get_ammo:                     'Ищу патроны',
  flee_emission:                'Убегаю от выброса',
};

/** Return a display label for a current_goal identifier. */
const currentGoalLabel = (raw: string): string =>
  CURRENT_GOAL_LABELS[raw] ?? raw.replace(/_/g, ' ');

export default function AgentProfileModal({ agent, locationName, onClose, locations, sendCommand, contextId }: Props) {
  // Initialise immediately with the client-side hint so the panel renders on
  // first paint without needing a button click.
  const [moneyEdit, setMoneyEdit] = React.useState<string | null>(null);
  const [moneySaving, setMoneySaving] = React.useState(false);
  // Threshold editor state
  const [thresholdEdit, setThresholdEdit] = React.useState<string | null>(null);
  const [thresholdSaving, setThresholdSaving] = React.useState(false);
  // Inventory management state
  const [addItemType, setAddItemType] = React.useState<string>('bandage');
  const [addItemSaving, setAddItemSaving] = React.useState(false);
  const [removingItemId, setRemovingItemId] = React.useState<string | null>(null);
  // On-demand memory: fetched from API when contextId is provided
  // (state_blob no longer carries memory to save bandwidth).
  type MemEntry = NonNullable<AgentForProfile['memory']>[number];
  const [fetchedMemory, setFetchedMemory] = useState<MemEntry[] | null>(null);
  const fetchMemory = React.useCallback(() => {
    if (!contextId) return;
    contextsApi.getAgentMemory(contextId, agent.id)
      .then((res) => setFetchedMemory(res.data as MemEntry[]))
      .catch(() => { /* non-fatal */ });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, contextId]);
  useEffect(() => {
    setFetchedMemory(null); // reset when agent changes so we never show stale data
    fetchMemory();
  }, [fetchMemory]);
  // While the modal is open, poll for new memory entries every 5 seconds
  // so live games update without requiring the user to close and reopen the profile.
  // (5 s is a good balance: fast enough for active ticking, low overhead when paused.)
  useEffect(() => {
    if (!contextId) return;
    const id = setInterval(fetchMemory, 5000);
    return () => clearInterval(id);
  }, [fetchMemory, contextId]);
  // Prefer freshly-fetched memory, fall back to whatever was passed in agent.memory.
  const displayMemory: MemEntry[] = fetchedMemory ?? (agent.memory ?? []);

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

  return (
    <div
      style={s.overlay}
      onMouseDown={onClose}
    >
      <div
        style={s.modal}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <div style={s.header}>
          <div style={s.titleRow}>
            <span style={s.avatar}>{agent.controller.kind === 'human' ? '👤' : '🤖'}</span>
            <div>
              <div style={s.name}>{agent.name}</div>
              <div style={s.subtitle}>
                {agent.faction} ·{' '}
                {agent.controller.kind === 'human' ? 'Игрок' : 'ИИ'} ·{' '}
                {agent.is_alive ? '🟢 Жив' : '💀 Погиб'}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
            <button
              style={s.closeBtn}
              title="Экспорт в JSON"
              onClick={() => {
                // memory is stripped from the state_blob to save bandwidth and
                // loaded separately; merge it back in before exporting so the
                // JSON contains the full agent state.
                // fetchedMemory is null only while the initial fetch is still
                // in-flight (or when contextId is absent). In that case fall back
                // to agent.memory (always [] after stripping) so the file is still
                // valid JSON — the caller can retry after the UI finishes loading.
                const exportData = { ...agent, memory: fetchedMemory ?? agent.memory ?? [] };
                const json = JSON.stringify(exportData, null, 2);
                const blob = new Blob([json], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `stalker_${agent.name.replace(/\s+/g, '_')}.json`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >📥</button>
            <button style={s.closeBtn} onClick={onClose} title="Закрыть">✕</button>
          </div>
        </div>

        <Section label="📍 Местоположение">
          <div style={s.sectionVal}>{locationName}</div>
          {agent.scheduled_action && (
            <div style={s.schedLine}>
              {(() => {
                const sa = agent.scheduled_action;
                const icon = SCHED_ICONS[sa.type] ?? '⏳';
                const time = schedRemaining(sa.type, sa.turns_remaining);
                if (sa.type === 'travel' && locations) {
                  const destId = sa.final_target_id ?? sa.target_id;
                  const destLoc = locations[destId];
                  const destLabel = destLoc
                    ? destLoc.region
                      ? `${destLoc.name} (${destLoc.region})`
                      : destLoc.name
                    : destId;
                  return `${icon} В пути → ${destLabel} — ${time}`;
                }
                return `${icon} ${sa.type} — ${time}`;
              })()}
            </div>
          )}
        </Section>

        {/* ── Vital stats ── */}
        <Section label="📊 Характеристики">
          {[
            { label: '❤️ HP', val: agent.hp, max: agent.max_hp, pct: agent.max_hp > 0 ? agent.hp / agent.max_hp : 0, color: agent.hp / agent.max_hp > 0.5 ? '#22c55e' : agent.hp / agent.max_hp > 0.25 ? '#f59e0b' : '#ef4444' },
            { label: '☢ Рад', val: agent.radiation, max: 100, pct: Math.min(agent.radiation, 100) / 100, color: '#a855f7' },
            { label: '🍖 Голод', val: agent.hunger, max: 100, pct: agent.hunger / 100, color: agent.hunger > 75 ? '#ef4444' : agent.hunger > 50 ? '#f59e0b' : '#22c55e' },
            { label: '💧 Жажда', val: agent.thirst, max: 100, pct: agent.thirst / 100, color: agent.thirst > 75 ? '#ef4444' : agent.thirst > 50 ? '#f59e0b' : '#3b82f6' },
            { label: '😴 Сон', val: agent.sleepiness, max: 100, pct: agent.sleepiness / 100, color: agent.sleepiness > 75 ? '#ef4444' : agent.sleepiness > 50 ? '#f59e0b' : '#64748b' },
          ].map(({ label, val, max, pct, color }) => (
            <div key={label} style={s.statRow}>
              <span style={s.statLabel}>{label}</span>
              <div style={s.barBg}>
                <div style={{ ...s.barFill, width: `${Math.max(0, Math.min(100, pct * 100))}%`, background: color }} />
              </div>
              <span style={s.statVal}>{val}/{max}</span>
            </div>
          ))}
          {sendCommand ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
              <span style={s.moneyLabel}>💰</span>
              {moneyEdit !== null ? (
                <>
                  <input
                    type="number"
                    value={moneyEdit}
                    onChange={(e) => setMoneyEdit(e.target.value)}
                    onBlur={handleMoneySave}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleMoneySave(); if (e.key === 'Escape') setMoneyEdit(null); }}
                    style={s.moneyInput}
                    autoFocus
                    disabled={moneySaving}
                  />
                  <span style={s.moneyRu}>RU</span>
                  <button style={s.moneySaveBtn} onClick={handleMoneySave} disabled={moneySaving} title="Сохранить">
                    {moneySaving ? '…' : '💾'}
                  </button>
                </>
              ) : (
                <>
                  <span
                    style={s.moneyLineEditable}
                    onClick={() => setMoneyEdit(String(agent.money))}
                    title="Нажмите для редактирования"
                  >
                    {agent.money} RU
                  </span>
                  <span style={s.moneyEditHint}>✏️</span>
                </>
              )}
            </div>
          ) : (
            <div style={s.moneyLine}>💰 {agent.money} RU</div>
          )}
          {agent.reputation != null && (
            <div style={s.repLine}>⭐ Репутация: {agent.reputation}</div>
          )}
        </Section>

        {/* ── Skills & development ── */}
        {agent.skill_combat != null && (
          <Section label="🎯 Навыки">
            <div style={s.skillGrid}>
              {[
                { label: '⚔ Бой', val: agent.skill_combat ?? 1 },
                { label: '🔭 Сталкер', val: agent.skill_stalker ?? 1 },
                { label: '💼 Торговля', val: agent.skill_trade ?? 1 },
                { label: '💊 Медицина', val: agent.skill_medicine ?? 1 },
                { label: '🗣 Общение', val: agent.skill_social ?? 1 },
              ].map(({ label, val }) => (
                <div key={label} style={s.skillChip}>
                  <span style={s.skillLabel}>{label}</span>
                  <span style={s.skillVal}>Ур {val}</span>
                </div>
              ))}
            </div>
            {agent.experience != null && (
              <div style={s.xpLine}>
                📈 Опыт: {agent.experience} XP
                {agent.risk_tolerance != null && ` · Риск: ${agent.risk_tolerance}`}
              </div>
            )}
            {agent.global_goal && (
              <div style={s.goalLine}>
                🎯 Цель: <strong>{agent.global_goal}</strong>
                {agent.current_goal && (
                   <span style={s.subgoal}> → {currentGoalLabel(agent.current_goal)}</span>
                )}
              </div>
            )}
            {agent.global_goal === 'kill_stalker' && agent.kill_target_id && (
              <div style={{ color: '#f87171', fontSize: '0.75rem', marginTop: 2 }}>
                🎯 Цель устранения: <strong>{agent.kill_target_id}</strong>
              </div>
            )}
            {/* ── Material threshold editor ── */}
            {agent.material_threshold != null && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                <span style={{ color: '#94a3b8', fontSize: '0.78rem' }}>💼 Порог богатства:</span>
                {sendCommand && thresholdEdit !== null ? (
                  <>
                    <input
                      type="number"
                      min={3000}
                      max={10000}
                      value={thresholdEdit}
                      onChange={(e) => setThresholdEdit(e.target.value)}
                      onBlur={handleThresholdSave}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleThresholdSave(); if (e.key === 'Escape') setThresholdEdit(null); }}
                      style={s.moneyInput}
                      autoFocus
                      disabled={thresholdSaving}
                    />
                    <span style={s.moneyRu}>RU</span>
                    <button style={s.moneySaveBtn} onClick={handleThresholdSave} disabled={thresholdSaving} title="Сохранить">
                      {thresholdSaving ? '…' : '💾'}
                    </button>
                  </>
                ) : (
                  <>
                    <span
                      style={sendCommand ? s.moneyLineEditable : { color: '#e2e8f0', fontSize: '0.82rem' }}
                      onClick={() => sendCommand && setThresholdEdit(String(agent.material_threshold))}
                      title={sendCommand ? 'Нажмите для редактирования (3000–10000)' : undefined}
                    >
                      {agent.material_threshold} RU
                    </span>
                    {sendCommand && <span style={s.moneyEditHint}>✏️</span>}
                  </>
                )}
              </div>
            )}
          </Section>
        )}

        {/* ── Equipment ── */}
        <Section label="🔫 Снаряжение">
          {Object.keys(agent.equipment).length === 0 ? (
            <span style={s.empty}>Нет снаряжения</span>
          ) : (
            Object.entries(agent.equipment).map(([slot, item]) => {
              const slotLabel: Record<string, string> = {
                weapon: '🔫 Оружие',
                armor: '🛡️ Броня',
                detector: '📡 Детектор',
              };
              const label = slotLabel[slot] ?? slot;
              return (
                <div key={slot} style={{ ...s.equipRow, alignItems: 'center' }}>
                  <span style={s.equipSlot}>{label}</span>
                  <span style={item ? s.equipItem : s.equipEmpty}>
                    {item ? item.name : '— пусто —'}
                  </span>
                  {item?.value != null && (
                    <span style={s.equipVal}>{item.value} RU</span>
                  )}
                  {sendCommand && item?.id && (
                    <button
                      onClick={() => handleRemoveItem(item.id)}
                      disabled={removingItemId === item.id}
                      title="Снять с экипировки"
                      style={s.removeItemBtn}
                    >
                      {removingItemId === item.id ? '…' : '✕'}
                    </button>
                  )}
                </div>
              );
            })
          )}
        </Section>

        {/* ── Inventory ── */}
        <Section label={`🎒 Инвентарь (${agent.inventory.length})`}>
          {agent.inventory.length === 0 ? (
            <span style={s.empty}>Пусто</span>
          ) : (
            agent.inventory.map((item) => (
              <div key={item.id} style={{ ...s.invRow, alignItems: 'center' }}>
                <span style={s.invName}>{item.name}</span>
                {item.weight != null && (
                  <span style={s.invWeight}>{item.weight} кг</span>
                )}
                {item.value != null && (
                  <span style={s.invVal}>{item.value} RU</span>
                )}
                {sendCommand && (
                  <button
                    onClick={() => handleRemoveItem(item.id)}
                    disabled={removingItemId === item.id}
                    title="Удалить предмет"
                    style={s.removeItemBtn}
                  >
                    {removingItemId === item.id ? '…' : '✕'}
                  </button>
                )}
              </div>
            ))
          )}
          {/* ── Add Item panel (debug only) ── */}
          {sendCommand && (
            <div style={s.addItemRow}>
              <select
                value={addItemType}
                onChange={(e) => setAddItemType(e.target.value)}
                style={s.addItemSelect}
              >
                {ALL_ITEM_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <button
                onClick={handleAddItem}
                disabled={addItemSaving}
                style={s.addItemBtn}
                title="Добавить предмет в инвентарь"
              >
                {addItemSaving ? '…' : '+ Добавить'}
              </button>
            </div>
          )}
        </Section>

        {/* ── Memory ── */}
        {displayMemory.length > 0 && (
          <Section label={`🧠 Память (${displayMemory.length})`}>
            <div style={s.memoryList}>
              {[...displayMemory].reverse().map((m, i) => {
                const icon = MEM_ICONS[m.type] ?? '📝';
                const color = MEM_COLORS[m.type] ?? '#94a3b8';
                const subLabel = m.effects?.action_kind
                  ? ` · ${m.effects.action_kind}`
                  : '';
                const t = m.world_day !== undefined
                  ? { world_day: m.world_day, world_hour: m.world_hour ?? 0, world_minute: m.world_minute ?? 0 }
                  : turnToTime(m.world_turn);
                const decisionIntentKind = m.type === 'decision' && m.effects?.intent_kind
                  ? (m.effects.intent_kind as string)
                  : null;
                const decisionIntentMeta = decisionIntentKind ? (_INTENT_META[decisionIntentKind] ?? null) : null;
                const decisionScore = m.type === 'decision' && typeof m.effects?.intent_score === 'number'
                  ? m.effects.intent_score as number
                  : null;
                return (
                  <div key={i} style={{ ...s.memoryEntry, borderLeft: `3px solid ${color}` }}>
                    <div style={s.memoryMeta}>
                      <span style={{ ...s.memoryType, color }}>
                        {icon} {m.type}{subLabel}
                      </span>
                      <span style={s.memoryWhen}>
                        День {t.world_day} · {TIME_LABEL(t.world_hour, t.world_minute)}
                      </span>
                    </div>
                    <div style={s.memoryTitle}>{m.title}</div>
                    {decisionIntentMeta && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                        <span style={{ fontSize: '0.78rem' }}>{decisionIntentMeta.icon} {decisionIntentMeta.label}</span>
                        {decisionScore !== null && (
                          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>· {Math.round(decisionScore * 100)}%</span>
                        )}
                      </div>
                    )}
                    {!!m.summary && (
                      <div style={s.memorySummary}>{m.summary}</div>
                    )}
                  </div>
                );
              })}
            </div>
          </Section>
        )}

        {agent.brain_trace && (
          <Section label="🧠 Brain Trace (v3)">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={s.memoryMeta}>
                <span style={s.memoryType}>
                  schema v{agent.brain_trace.schema_version} · mode: {agent.brain_trace.mode}
                </span>
                <span style={s.memoryWhen}>
                  {traceTimeLabel(agent.brain_trace.turn, agent.brain_trace.world_time)}
                </span>
              </div>
              <div style={{ fontSize: '0.82rem', color: '#cbd5e1' }}>
                <strong>Мысль:</strong> {agent.brain_trace.current_thought}
              </div>
              {agent.active_plan_v3 && (
                <div style={s.memoryEntry}>
                  <div style={s.memoryMeta}>
                    <span style={s.memoryType}>🗺 Активный план</span>
                  </div>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#cbd5e1', fontSize: '0.72rem' }}>
                    {JSON.stringify(agent.active_plan_v3, null, 2)}
                  </pre>
                </div>
              )}
              {agent.brain_trace.top_drives && agent.brain_trace.top_drives.length > 0 && (
                <div style={s.memoryEntry}>
                  <div style={s.memoryMeta}>
                    <span style={s.memoryType}>📈 Топ драйвы</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {agent.brain_trace.top_drives.map((d) => (
                      <div key={`${d.key}-${d.rank}`} style={{ color: '#cbd5e1', fontSize: '0.75rem' }}>
                        #{d.rank} {d.key}: {Math.round(d.value * 100)}%
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {agent.brain_trace.events.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {[...agent.brain_trace.events].slice(-5).map((ev, idx) => (
                    <div key={`${ev.turn}-${idx}`} style={s.memoryEntry}>
                      <div style={s.memoryMeta}>
                        <span style={s.memoryType}>
                          [{ev.mode}] {ev.decision}
                        </span>
                        <span style={s.memoryWhen}>{traceTimeLabel(ev.turn, ev.world_time)}</span>
                      </div>
                      <div style={s.memorySummary}>{ev.summary}</div>
                      {ev.reason && (
                        <div style={{ color: '#94a3b8', fontSize: '0.72rem' }}>Причина: {ev.reason}</div>
                      )}
                      {ev.scheduled_action_type && (
                        <div style={{ color: '#94a3b8', fontSize: '0.72rem' }}>
                          Действие: {ev.scheduled_action_type}
                        </div>
                      )}
                      {ev.intent_kind && (
                        <div style={{ color: '#94a3b8', fontSize: '0.72rem' }}>
                          Intent: {ev.intent_kind}
                          {typeof ev.intent_score === 'number' ? ` (${Math.round(ev.intent_score * 100)}%)` : ''}
                        </div>
                      )}
                      {ev.dominant_pressure && (
                        <div style={{ color: '#94a3b8', fontSize: '0.72rem' }}>
                          Pressure: {ev.dominant_pressure.key} = {Math.round(ev.dominant_pressure.value)}
                        </div>
                      )}
                      {ev.immediate_needs && ev.immediate_needs.length > 0 && (
                        <div style={{ color: '#cbd5e1', fontSize: '0.72rem', marginTop: 4 }}>
                          <strong>Срочные нужды:</strong>
                          {ev.immediate_needs.map((n, i) => (
                            <div key={`im-${n.key}-${i}`} style={{ color: '#94a3b8' }}>
                              • {n.key} ({Math.round((n.urgency ?? 0) * 100)}%){n.selected_item_type ? ` → ${n.selected_item_type}` : ''}
                            </div>
                          ))}
                        </div>
                      )}
                      {ev.item_needs && ev.item_needs.length > 0 && (
                        <div style={{ color: '#cbd5e1', fontSize: '0.72rem', marginTop: 4 }}>
                          <strong>Запасы/снаряжение:</strong>
                          {ev.item_needs.map((n, i) => (
                            <div key={`it-${n.key}-${i}`} style={{ color: '#94a3b8' }}>
                              • {n.key} ({Math.round((n.urgency ?? 0) * 100)}%){typeof n.missing_count === 'number' ? `, не хватает: ${n.missing_count}` : ''}
                            </div>
                          ))}
                        </div>
                      )}
                      {ev.liquidity && (
                        <div style={{ color: '#cbd5e1', fontSize: '0.72rem', marginTop: 4 }}>
                          <strong>Деньги/ликвидность:</strong>
                          <div style={{ color: '#94a3b8' }}>
                            safe: {ev.liquidity.safe_sale_options ?? 0}, risky: {ev.liquidity.risky_sale_options ?? 0}, emergency: {ev.liquidity.emergency_sale_options ?? 0}
                            {typeof ev.liquidity.money_missing === 'number' ? `, не хватает: ${ev.liquidity.money_missing}` : ''}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Section>
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
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 8,
  },
  titleRow: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  avatar: { fontSize: '2.5rem', flexShrink: 0 },
  name: { color: '#f8fafc', fontWeight: 700, fontSize: '1.15rem' },
  subtitle: { color: '#64748b', fontSize: '0.8rem', marginTop: 2 },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#64748b',
    cursor: 'pointer',
    fontSize: '1.1rem',
    padding: '0.15rem',
    lineHeight: 1,
    flexShrink: 0,
  },
  section: {
    background: '#1e293b',
    borderRadius: 8,
    padding: '0.75rem',
    border: '1px solid #334155',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.45rem',
  },
  sectionLabel: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 2,
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
  moneyLineEditable: {
    color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem',
    cursor: 'pointer', borderBottom: '1px dashed #fbbf24',
  },
  moneyEditHint: { color: '#64748b', fontSize: '0.72rem', cursor: 'pointer' },
  moneyInput: {
    background: '#0f172a', border: '1px solid #fbbf24', borderRadius: 5,
    color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem',
    padding: '0.1rem 0.35rem', width: 100,
  },
  moneyRu: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem' },
  moneySaveBtn: {
    background: 'transparent', border: '1px solid #fbbf24',
    borderRadius: 5, color: '#fbbf24', cursor: 'pointer',
    fontSize: '0.8rem', padding: '0.1rem 0.3rem', lineHeight: 1,
  },
  repLine: { color: '#a78bfa', fontSize: '0.8rem' },
  skillGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
    gap: 6,
  },
  skillChip: {
    background: '#0f172a',
    borderRadius: 6,
    padding: '0.3rem 0.5rem',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  skillLabel: { color: '#94a3b8', fontSize: '0.72rem' },
  skillVal: { color: '#f8fafc', fontWeight: 700, fontSize: '0.75rem' },
  xpLine: { color: '#64748b', fontSize: '0.75rem', marginTop: 2 },
  goalLine: { color: '#94a3b8', fontSize: '0.8rem', marginTop: 4 },
  subgoal: { color: '#60a5fa', fontSize: '0.78rem' },
  equipRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem' },
  equipSlot: {
    color: '#64748b',
    fontSize: '0.7rem',
    width: 56,
    flexShrink: 0,
    textTransform: 'capitalize',
  },
  equipItem: { color: '#cbd5e1', flex: 1 },
  equipEmpty: { color: '#334155', flex: 1 },
  equipVal: { color: '#64748b', fontSize: '0.7rem' },
  invRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: '0.82rem',
    borderBottom: '1px solid #0f172a',
    paddingBottom: 3,
  },
  invName: { color: '#cbd5e1', flex: 1 },
  invWeight: { color: '#475569', fontSize: '0.7rem' },
  invVal: { color: '#64748b', fontSize: '0.7rem' },
  removeItemBtn: {
    background: 'transparent',
    border: '1px solid #334155',
    borderRadius: 4,
    color: '#ef4444',
    cursor: 'pointer',
    fontSize: '0.68rem',
    padding: '1px 5px',
    lineHeight: '1.2',
    flexShrink: 0,
  },
  addItemRow: {
    display: 'flex',
    gap: 6,
    alignItems: 'center',
    marginTop: 6,
    paddingTop: 6,
    borderTop: '1px solid #1e293b',
  },
  addItemSelect: {
    flex: 1,
    background: '#1e293b',
    color: '#cbd5e1',
    border: '1px solid #334155',
    borderRadius: 4,
    fontSize: '0.75rem',
    padding: '3px 6px',
  },
  addItemBtn: {
    background: '#14532d',
    border: '1px solid #16a34a',
    borderRadius: 4,
    color: '#86efac',
    cursor: 'pointer',
    fontSize: '0.75rem',
    padding: '3px 8px',
    whiteSpace: 'nowrap' as const,
  },
  empty: { color: '#334155', fontSize: '0.72rem' },
  memoryList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    maxHeight: 480,
    overflowY: 'auto',
  },
  memoryEntry: { background: '#0f172a', borderRadius: 6, padding: '0.5rem', paddingLeft: '0.6rem' },
  memoryMeta: { display: 'flex', justifyContent: 'space-between', marginBottom: 3 },
  memoryType: { color: '#94a3b8', fontWeight: 600, fontSize: '0.72rem' },
  memoryWhen: { color: '#475569', fontSize: '0.7rem' },
  memoryTitle: { color: '#f8fafc', fontWeight: 600, fontSize: '0.82rem', marginBottom: 2 },
  memorySummary: { color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.5 },
  memoryFieldLabel: { color: '#64748b', fontWeight: 600 },
  // ── Bot decision preview ──
  decisionRow: { display: 'flex', gap: 8, alignItems: 'flex-start' },
  decisionLabel: { color: '#64748b', fontSize: '0.75rem', minWidth: 68, flexShrink: 0, paddingTop: 1 },
  decisionVal: { color: '#e2e8f0', fontSize: '0.78rem', lineHeight: 1.5 },
  decisionChosen: {
    border: '1px solid #22c55e',
    borderRadius: 8,
    padding: '0.5rem 0.75rem',
    background: '#052e16',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 4,
  },
  decisionChosenAction: {
    color: '#86efac',
    fontWeight: 700,
    fontSize: '0.85rem',
  },
  decisionChosenReason: {
    color: '#94a3b8',
    fontSize: '0.75rem',
  },
  decisionChosenGoal: {
    color: '#475569',
    fontSize: '0.7rem',
  },
  decisionPreviewBtn: {
    display: 'none', // kept for backwards compat but no longer shown
  },
  decisionToggleBtn: {
    display: 'none', // kept for backwards compat but no longer shown
  },
  decisionRefreshBtn: {
    background: 'transparent',
    border: '1px solid #334155',
    color: '#64748b',
    borderRadius: 6,
    padding: '0.2rem 0.55rem',
    fontSize: '0.72rem',
    cursor: 'pointer',
    alignSelf: 'flex-start',
    marginTop: 4,
  },
  // ── v2 Decision Architecture styles ──
  v2NeedRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '0.15rem 0',
  },
  v2NeedLabel: {
    color: '#94a3b8',
    fontSize: '0.7rem',
    width: 112,
    flexShrink: 0,
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  v2BarBg: {
    flex: 1,
    height: 5,
    background: '#0f172a',
    borderRadius: 3,
    overflow: 'hidden',
  },
  v2ScoreVal: {
    color: '#64748b',
    fontSize: '0.68rem',
    width: 34,
    textAlign: 'right' as const,
  },
  v2IntentBox: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 3,
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 7,
    padding: '0.45rem 0.6rem',
    marginTop: 4,
  },
  v2IntentHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  v2IntentIcon: {
    fontSize: '0.95rem',
    flexShrink: 0,
  },
  v2IntentLabel: {
    color: '#f8fafc',
    fontWeight: 700,
    fontSize: '0.82rem',
    flex: 1,
  },
  v2IntentScore: {
    fontSize: '0.7rem',
    color: '#64748b',
    fontVariantNumeric: 'tabular-nums' as const,
  },
  v2IntentReason: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    lineHeight: 1.45,
    marginTop: 1,
  },
  v2PlanLine: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    marginTop: 2,
    fontSize: '0.7rem',
    color: '#475569',
  },
  v2SourceBadge: {
    fontSize: '0.6rem',
    color: '#6366f1',
    border: '1px solid #6366f1',
    borderRadius: 4,
    padding: '0px 4px',
    flexShrink: 0,
  },
  v2RefreshBtn: {
    background: 'transparent',
    border: '1px solid #334155',
    color: '#64748b',
    borderRadius: 6,
    padding: '0.2rem 0.55rem',
    fontSize: '0.72rem',
    cursor: 'pointer',
    alignSelf: 'flex-start',
    marginTop: 6,
  },
};

// ─── Client-side decision hint (approximates backend _describe_bot_decision_tree) ─
// Used to show an immediate result on mount before the backend responds.
// Known artifact item types (mirrors Python ARTIFACT_ITEM_TYPES in tick_rules.py).
const _ARTIFACT_TYPES = new Set([
  'artifact', 'fireball', 'jellyfish', 'moonlight', 'soul', 'gravi',
  'goldfish', 'night_star', 'stone_blood', 'spring',
]);

// ─── v2 Decision Architecture ─────────────────────────────────────────────────

/** Human-readable labels for each NeedScore drive. */
const _NEED_META: Record<string, { icon: string; label: string }> = {
  survive_now:          { icon: '🚨', label: 'Выживание (крит.)' },
  heal_self:            { icon: '💊', label: 'Лечение' },
  eat:                  { icon: '🍖', label: 'Голод' },
  drink:                { icon: '💧', label: 'Жажда' },
  sleep:                { icon: '😴', label: 'Усталость' },
  reload_or_rearm:      { icon: '🔫', label: 'Снаряжение' },
  avoid_emission:       { icon: '⚡', label: 'Выброс' },
  get_rich:             { icon: '💰', label: 'Накопление' },
  hunt_target:          { icon: '🎯', label: 'Охота' },
  unravel_zone_mystery: { icon: '📜', label: 'Тайна Зоны' },
  leave_zone:           { icon: '🚪', label: 'Покинуть Зону' },
  trade:                { icon: '🏪', label: 'Торговля' },
  negotiate:            { icon: '🗣', label: 'Общение' },
  maintain_group:       { icon: '📋', label: 'Группа' },
  help_ally:            { icon: '🤝', label: 'Помощь' },
  join_group:           { icon: '👥', label: 'Влиться в группу' },
};

/** Human-readable labels for each intent kind. */
const _INTENT_META: Record<string, { icon: string; label: string }> = {
  escape_danger:        { icon: '🚨', label: 'Бегство (критический HP)' },
  heal_self:            { icon: '💊', label: 'Срочное лечение' },
  flee_emission:        { icon: '⚡', label: 'Бегство от выброса' },
  wait_in_shelter:      { icon: '🏚️', label: 'Укрытие от выброса' },
  seek_water:           { icon: '💧', label: 'Поиск воды' },
  seek_food:            { icon: '🍖', label: 'Поиск еды' },
  rest:                 { icon: '😴', label: 'Отдых (сон)' },
  resupply:             { icon: '🔫', label: 'Получить снаряжение' },
  sell_artifacts:       { icon: '💎', label: 'Продажа артефактов' },
  trade:                { icon: '🏪', label: 'Торговля' },
  upgrade_equipment:    { icon: '⬆️', label: 'Апгрейд снаряжения' },
  loot:                 { icon: '🎁', label: 'Мародёрство' },
  explore:              { icon: '🔭', label: 'Исследование' },
  get_rich:             { icon: '💰', label: 'Накопление богатства' },
  hunt_target:          { icon: '🎯', label: 'Охота на цель' },
  search_information:   { icon: '📜', label: 'Поиск информации' },
  leave_zone:           { icon: '🚪', label: 'Покинуть Зону' },
  negotiate:            { icon: '🗣️', label: 'Переговоры' },
  assist_ally:          { icon: '🤝', label: 'Помощь союзнику' },
  form_group:           { icon: '👥', label: 'Создать группу' },
  follow_group_plan:    { icon: '📋', label: 'Следовать плану группы' },
  maintain_group:       { icon: '🔗', label: 'Сохранить группу' },
  idle:                 { icon: '💤', label: 'Ожидание' },
};
/** Human-readable Russian labels for plan step kinds. */
const _STEP_LABEL_RU: Record<string, string> = {
  travel_to_location:        'Путешествие',
  sleep_for_hours:           'Сон',
  explore_location:          'Исследование',
  trade_buy_item:            'Покупка',
  trade_sell_item:           'Продажа',
  consume_item:              'Употребление предмета',
  equip_item:                'Экипировка',
  pickup_item:               'Подбор предмета',
  heal_self:                 'Лечение',
  ask_for_intel:             'Запрос информации',
  wait:                      'Ожидание',
  legacy_scheduled_action:   'Запланированное действие',
};

// ─── AgentCreateModal ─────────────────────────────────────────────────────────

export interface AgentCreateProps {
  onClose: () => void;
  onSave: (name: string, faction: string, globalGoal: string, isTrader: boolean, killTargetId?: string) => Promise<void>;
  /** When true, the modal opens with the Trader checkbox pre-checked. */
  defaultIsTrader?: boolean;
  /** Optional list of agents to populate the kill-target selector. */
  agents?: Record<string, { id: string; name: string; is_alive?: boolean }>;
}

const FACTION_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'loner',    label: 'Одиночка' },
  { value: 'military', label: 'Военные'  },
  { value: 'duty',     label: 'Долг'     },
  { value: 'freedom',  label: 'Свобода'  },
];

const GLOBAL_GOAL_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'get_rich',              label: 'Разбогатеть'       },
  { value: 'unravel_zone_mystery',  label: 'Разгадать тайну Зоны' },
  { value: 'kill_stalker',          label: 'Устранить сталкера' },
];

export function AgentCreateModal({ onClose, onSave, defaultIsTrader = false, agents }: AgentCreateProps) {
  const [name,          setName]          = useState('');
  const [faction,       setFaction]       = useState('loner');
  const [globalGoal,    setGlobalGoal]    = useState('get_rich');
  const [isTrader,      setIsTrader]      = useState(defaultIsTrader);
  const [killTargetId,  setKillTargetId]  = useState('');
  const [saving,        setSaving]        = useState(false);
  const [err,           setErr]           = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await onSave(name.trim(), faction, globalGoal, isTrader,
                   globalGoal === 'kill_stalker' ? killTargetId.trim() : undefined);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Ошибка создания');
      setSaving(false);
    }
  };

  return (
    <div style={s.overlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={s.header}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: '1.5rem' }}>{isTrader ? '🏪' : '👤'}</span>
            <span style={s.name}>{isTrader ? 'Создать торговца' : 'Создать сталкера'}</span>
          </div>
          <button style={s.closeBtn} onClick={onClose} title="Закрыть">✕</button>
        </div>

        {/* Trader toggle */}
        <div style={{ ...s.section, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={isTrader}
              onChange={(e) => setIsTrader(e.target.checked)}
              style={{ accentColor: '#f59e0b', width: 16, height: 16 }}
            />
            <span style={{ ...cs.traderLabel, color: isTrader ? '#f59e0b' : '#94a3b8' }}>
              🏪 Торговец (фиксирован на локации, покупает при наличии средств)
            </span>
          </label>
        </div>

        {/* Name */}
        <div style={s.section}>
          <div style={s.sectionLabel}>Имя персонажа</div>
          <input
            style={cs.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Имя персонажа (пусто = случайное)"
            autoFocus
          />
        </div>

        {/* Faction — hidden for traders (traders are neutral) */}
        {!isTrader && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Фракция</div>
            <select
              style={cs.input}
              value={faction}
              onChange={(e) => setFaction(e.target.value)}
            >
              {FACTION_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
        )}

        {/* Global goal — hidden for traders */}
        {!isTrader && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Глобальная цель</div>
            <select
              style={cs.input}
              value={globalGoal}
              onChange={(e) => setGlobalGoal(e.target.value)}
            >
              {GLOBAL_GOAL_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
        )}

        {/* Kill target — shown only when globalGoal === 'kill_stalker' */}
        {!isTrader && globalGoal === 'kill_stalker' && (
          <div style={s.section}>
            <div style={s.sectionLabel}>Цель устранения</div>
            {agents && Object.keys(agents).length > 0 ? (
              <select
                style={cs.input}
                value={killTargetId}
                onChange={(e) => setKillTargetId(e.target.value)}
              >
                <option value="">— выбрать сталкера —</option>
                {Object.values(agents).map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name}{ag.is_alive === false ? ' †' : ''} ({ag.id})
                  </option>
                ))}
              </select>
            ) : (
              <input
                style={cs.input}
                value={killTargetId}
                onChange={(e) => setKillTargetId(e.target.value)}
                placeholder="ID сталкера-цели (напр. agent_0)"
              />
            )}
          </div>
        )}

        {err && (
          <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: -4 }}>{err}</div>
        )}

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
          <button style={cs.cancelBtn} onClick={onClose} disabled={saving}>
            Отмена
          </button>
          <button style={cs.saveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? '…' : isTrader ? 'Создать торговца' : 'Создать'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Styles local to AgentCreateModal (avoid polluting `s`)
const cs: Record<string, React.CSSProperties> = {
  input: {
    width: '100%',
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 6,
    color: '#f1f5f9',
    fontSize: '0.82rem',
    padding: '0.4rem 0.55rem',
    boxSizing: 'border-box',
  },
  traderLabel: {
    fontSize: '0.78rem',
    fontWeight: 500,
    userSelect: 'none',
  },
  saveBtn: {
    padding: '0.4rem 1rem',
    background: '#1d4ed8',
    color: '#fff',
    border: 'none',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.8rem',
    fontWeight: 600,
  },
  cancelBtn: {
    padding: '0.4rem 0.8rem',
    background: '#1e293b',
    color: '#94a3b8',
    border: '1px solid #334155',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.8rem',
  },
};
