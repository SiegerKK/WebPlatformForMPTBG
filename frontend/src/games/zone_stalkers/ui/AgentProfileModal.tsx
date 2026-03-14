/**
 * AgentProfileModal — reusable modal showing a full Zone Stalkers agent profile.
 *
 * Import `AgentForProfile` to build a compatible agent object.
 * Closing: clicking the semi-transparent overlay or the ✕ button calls `onClose`.
 */
import React, { useState } from 'react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentInventoryItem {
  id: string;
  type: string;
  name: string;
  weight?: number;
  value?: number;
}

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
  memory?: Array<{
    world_turn: number;
    world_day: number;
    world_hour: number;
    world_minute?: number;
    type: string;
    title: string;
    summary: string;
  }>;
}

interface Props {
  agent: AgentForProfile;
  locationName: string;
  onClose: () => void;
  /** When provided (debug mode), bot agents show a "preview decision" panel. */
  sendCommand?: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const TIME_LABEL = (h: number, m: number) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;

const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore: '🔍',
  sleep: '😴',
  event: '📖',
};

// ─── Main component ───────────────────────────────────────────────────────────

// ─── Decision preview type ───────────────────────────────────────────────────

type DecisionPreview = {
  goal: string;
  action: string;
  reason: string;
  layers?: Array<{ name: string; skipped: boolean; action: string; reason: string }>;
};

export default function AgentProfileModal({ agent, locationName, onClose, sendCommand }: Props) {
  // Initialise immediately with the client-side hint so the panel renders on
  // first paint without needing a button click.
  const [decisionPreview, setDecisionPreview] = React.useState<DecisionPreview | null>(() => {
    if (sendCommand && agent.controller.kind === 'bot') {
      return _clientSideDecisionHint(agent);
    }
    return null;
  });
  const [loadingDecision, setLoadingDecision] = React.useState(false);
  const [showAllLayers, setShowAllLayers] = React.useState(false);

  // Fire the backend preview command whenever the displayed agent changes.
  // The result is discarded here — we rely on the client-side hint for
  // immediate display; the backend call keeps server-side state in sync.
  React.useEffect(() => {
    if (!sendCommand || agent.controller.kind !== 'bot') return;
    sendCommand('debug_preview_bot_decision', { agent_id: agent.id }).catch(() => {});
  }, [agent.id, agent.controller.kind]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePreviewDecision = async () => {
    if (!sendCommand) return;
    setLoadingDecision(true);
    try {
      await sendCommand('debug_preview_bot_decision', { agent_id: agent.id });
      setDecisionPreview(_clientSideDecisionHint(agent));
    } finally {
      setLoadingDecision(false);
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
          <button style={s.closeBtn} onClick={onClose} title="Закрыть">✕</button>
        </div>

        {/* ── Location & current action ── */}
        <Section label="📍 Местоположение">
          <div style={s.sectionVal}>{locationName}</div>
          {agent.scheduled_action && (
            <div style={s.schedLine}>
              {SCHED_ICONS[agent.scheduled_action.type] ?? '⏳'}{' '}
              {agent.scheduled_action.type === 'travel'
                ? `travel — ${agent.scheduled_action.turns_remaining} мин осталось`
                : agent.scheduled_action.type === 'sleep'
                  ? `sleep — ${Math.ceil(agent.scheduled_action.turns_remaining / 60)} ч осталось`
                  : `${agent.scheduled_action.type} — ${agent.scheduled_action.turns_remaining} мин осталось`
              }
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
          <div style={s.moneyLine}>💰 {agent.money} RU</div>
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
                  <span style={s.subgoal}> → {agent.current_goal}</span>
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
            Object.entries(agent.equipment).map(([slot, item]) => (
              <div key={slot} style={s.equipRow}>
                <span style={s.equipSlot}>{slot}</span>
                <span style={item ? s.equipItem : s.equipEmpty}>
                  {item ? item.name : '—'}
                </span>
                {item?.value != null && (
                  <span style={s.equipVal}>{item.value} RU</span>
                )}
              </div>
            ))
          )}
        </Section>

        {/* ── Inventory ── */}
        <Section label={`🎒 Инвентарь (${agent.inventory.length})`}>
          {agent.inventory.length === 0 ? (
            <span style={s.empty}>Пусто</span>
          ) : (
            agent.inventory.map((item) => (
              <div key={item.id} style={s.invRow}>
                <span style={s.invName}>{item.name}</span>
                {item.weight != null && (
                  <span style={s.invWeight}>{item.weight} кг</span>
                )}
                {item.value != null && (
                  <span style={s.invVal}>{item.value} RU</span>
                )}
              </div>
            ))
          )}
        </Section>

        {/* ── Memory ── */}
        {agent.memory && agent.memory.length > 0 && (
          <Section label={`🧠 Память (${agent.memory.length})`}>
            <div style={s.memoryList}>
              {[...agent.memory].reverse().slice(0, 8).map((m, i) => (
                <div key={i} style={s.memoryEntry}>
                  <div style={s.memoryMeta}>
                    <span style={s.memoryType}>
                      {SCHED_ICONS[m.type] ?? '📝'} {m.type}
                    </span>
                    <span style={s.memoryWhen}>
                      День {m.world_day} · {TIME_LABEL(m.world_hour, m.world_minute ?? 0)}
                    </span>
                  </div>
                  <div style={s.memoryTitle}>{m.title}</div>
                  <div style={s.memorySummary}>{m.summary}</div>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* ── Bot decision preview (debug, bots only) ── */}
        {sendCommand && agent.controller.kind === 'bot' && (
          <Section label="🤖 Решение в этом ходу">
            {decisionPreview ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {/* Chosen action — highlighted in green */}
                <div style={s.decisionChosen}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span>✅</span>
                    <span style={s.decisionChosenAction}>{decisionPreview.action}</span>
                  </div>
                  <div style={s.decisionChosenReason}>{decisionPreview.reason}</div>
                  <div style={s.decisionChosenGoal}>🎯 Цель: {decisionPreview.goal}</div>
                </div>

                {/* Collapsible all-layers list */}
                {decisionPreview.layers && decisionPreview.layers.length > 0 && (
                  <div>
                    <button
                      style={s.decisionToggleBtn}
                      onClick={() => setShowAllLayers(!showAllLayers)}
                    >
                      {showAllLayers ? '▲ Скрыть варианты' : '▼ Все варианты'}
                    </button>
                    {showAllLayers && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                        {decisionPreview.layers.map((layer, i) => (
                          <div
                            key={i}
                            style={{
                              display: 'flex',
                              gap: 6,
                              alignItems: 'flex-start',
                              padding: '0.3rem 0.5rem',
                              borderRadius: 6,
                              background: layer.skipped ? 'transparent' : '#052e16',
                              border: layer.skipped ? '1px solid #1e293b' : '1px solid #22c55e',
                              opacity: layer.skipped ? 0.6 : 1,
                            }}
                          >
                            <span style={{ flexShrink: 0, fontSize: '0.8rem' }}>
                              {layer.skipped ? '⏭' : '✅'}
                            </span>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                              <span style={{
                                color: layer.skipped ? '#64748b' : '#86efac',
                                fontSize: '0.75rem',
                                fontWeight: 600,
                              }}>
                                {layer.name} → {layer.action}
                              </span>
                              <span style={{ color: '#475569', fontSize: '0.68rem' }}>
                                {layer.reason}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <button
                  style={s.decisionRefreshBtn}
                  onClick={handlePreviewDecision}
                  disabled={loadingDecision}
                >
                  {loadingDecision ? '…' : '🔄 Обновить'}
                </button>
              </div>
            ) : (
              <button
                style={s.decisionPreviewBtn}
                onClick={handlePreviewDecision}
                disabled={loadingDecision}
              >
                {loadingDecision ? '⏳ Анализ…' : '🔍 Предсказать решение'}
              </button>
            )}
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
  empty: { color: '#334155', fontSize: '0.72rem' },
  memoryList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    maxHeight: 260,
    overflowY: 'auto',
  },
  memoryEntry: { background: '#0f172a', borderRadius: 6, padding: '0.5rem' },
  memoryMeta: { display: 'flex', justifyContent: 'space-between', marginBottom: 3 },
  memoryType: { color: '#94a3b8', fontWeight: 600, fontSize: '0.72rem' },
  memoryWhen: { color: '#475569', fontSize: '0.7rem' },
  memoryTitle: { color: '#f8fafc', fontWeight: 600, fontSize: '0.82rem', marginBottom: 2 },
  memorySummary: { color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.5 },
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
  decisionToggleBtn: {
    background: 'transparent',
    border: '1px solid #334155',
    color: '#64748b',
    borderRadius: 6,
    padding: '0.2rem 0.55rem',
    fontSize: '0.72rem',
    cursor: 'pointer',
  },
  decisionPreviewBtn: {
    background: '#1e3a5f',
    border: '1px solid #3b82f6',
    color: '#93c5fd',
    borderRadius: 7,
    padding: '0.35rem 0.85rem',
    fontSize: '0.8rem',
    cursor: 'pointer',
    alignSelf: 'flex-start',
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
};

// ─── Client-side decision hint (approximates backend _describe_bot_decision_tree) ─
// Used to show an immediate result on mount before the backend responds.
// Known artifact item types (mirrors Python ARTIFACT_ITEM_TYPES in tick_rules.py).
const _ARTIFACT_TYPES = new Set([
  'artifact', 'fireball', 'jellyfish', 'moonlight', 'soul', 'gravi',
  'goldfish', 'night_star', 'stone_blood', 'spring',
]);

/**
 * Client-side approximation of the backend `_describe_bot_decision_tree` logic.
 *
 * Evaluates the same 7-layer priority tree using agent fields available in the
 * frontend state, returning an immediate result that is displayed before (or
 * instead of) a backend round-trip.  The backend version is authoritative;
 * this function is used only for instantaneous UI feedback.
 *
 * @param agent - The bot agent whose decision should be previewed.
 * @returns A `DecisionPreview` with `goal`, `action`, `reason`, and the full
 *          `layers` array (each layer marked `skipped: true` if its condition
 *          was not met).
 */
function _clientSideDecisionHint(agent: AgentForProfile): DecisionPreview {
  const hp = agent.hp;
  const hunger = agent.hunger;
  const thirst = agent.thirst;
  const sleepiness = agent.sleepiness;
  const wealth = agent.money + agent.inventory.reduce((s, i) => s + (i.value ?? 0), 0);
  const threshold = agent.material_threshold ?? 1000;
  const goal = agent.current_goal ?? '—';
  const scheduled = agent.scheduled_action;
  const globalGoal = agent.global_goal ?? 'survive';
  const artifactCount = agent.inventory.filter((i) => _ARTIFACT_TYPES.has(i.type)).length;

  type Layer = { name: string; skipped: boolean; action: string; reason: string };
  const layers: Layer[] = [];

  // Layer 1: EMERGENCY: HP критический
  const cond1 = hp <= 30;
  layers.push({
    name: 'EMERGENCY: HP критический',
    skipped: !cond1,
    action: 'Лечение/бегство',
    reason: cond1 ? `HP = ${hp} (порог ≤30)` : `HP = ${hp}, выше критического`,
  });

  // Layer 2: EMERGENCY: Голод
  const cond2 = hunger >= 70;
  layers.push({
    name: 'EMERGENCY: Голод',
    skipped: !cond2,
    action: 'Поесть',
    reason: cond2 ? `Голод = ${hunger} (порог ≥70)` : `Голод = ${hunger}, терпимо`,
  });

  // Layer 3: EMERGENCY: Жажда
  const cond3 = thirst >= 70;
  layers.push({
    name: 'EMERGENCY: Жажда',
    skipped: !cond3,
    action: 'Попить',
    reason: cond3 ? `Жажда = ${thirst} (порог ≥70)` : `Жажда = ${thirst}, терпимо`,
  });

  // Layer 4: ВЫЖИВАНИЕ: Сон
  const cond4 = sleepiness >= 75;
  layers.push({
    name: 'ВЫЖИВАНИЕ: Сон',
    skipped: !cond4,
    action: 'Спать 6ч',
    reason: cond4 ? `Усталость = ${sleepiness} (порог ≥75)` : `Усталость = ${sleepiness}, норма`,
  });

  // Layer 5: ТОРГОВЛЯ: Продать артефакты
  // Note: client-side can't check for a trader at the location, so we only check inventory.
  const cond5 = artifactCount > 0;
  layers.push({
    name: 'ТОРГОВЛЯ: Продать артефакты',
    skipped: !cond5,
    action: 'Продать артефакты',
    reason: cond5
      ? `${artifactCount} артефактов (наличие торговца неизвестно)`
      : 'Нет артефактов в инвентаре',
  });

  // Layer 6: ЦЕЛЬ: Накопить богатство
  const cond6 = wealth < threshold;
  layers.push({
    name: 'ЦЕЛЬ: Накопить богатство',
    skipped: !cond6,
    action: 'Собирать ресурсы',
    reason: cond6
      ? `Богатство ${wealth} < порог ${threshold}`
      : `Богатство ${wealth} ≥ порог ${threshold}`,
  });

  // Layer 7: ЦЕЛЬ: Глобальная цель
  const cond7 = wealth >= threshold;
  layers.push({
    name: 'ЦЕЛЬ: Глобальная цель',
    skipped: !cond7,
    action: `Преследование цели «${globalGoal}»`,
    reason: cond7
      ? `Богатство ${wealth} ≥ порог ${threshold}, цель: ${globalGoal}`
      : `Богатство ${wealth} < порог ${threshold}`,
  });

  // Determine chosen action/reason (priority: scheduled_action > stat conditions)
  let action = 'Бездействие';
  let reason = '—';

  if (scheduled) {
    const t = scheduled.type;
    if (t === 'travel') {
      action = `Движение (${scheduled.turns_remaining} мин осталось)`;
      reason = 'Запланированное перемещение';
    } else if (t === 'sleep') {
      action = 'Спать';
      reason = 'Запланированный отдых';
    } else if (t === 'explore') {
      action = 'Исследование';
      reason = 'Запланированное исследование';
    }
  } else if (cond1) {
    action = 'Лечение или бегство';
    reason = `HP критически низкий (${hp})`;
  } else if (cond2) {
    action = 'Поиск еды';
    reason = `Голод ${hunger}/100`;
  } else if (cond3) {
    action = 'Поиск воды';
    reason = `Жажда ${thirst}/100`;
  } else if (cond4) {
    action = 'Спать 6 часов';
    reason = `Усталость ${sleepiness}/100`;
  } else if (cond5) {
    action = 'Продажа или путь к торговцу';
    reason = `${artifactCount} артефактов в инвентаре`;
  } else if (cond6) {
    action = 'Сбор ресурсов';
    reason = `Богатство ${wealth} < порог ${threshold}`;
  } else {
    action = 'Преследование глобальной цели';
    reason = `Цель: ${globalGoal}`;
  }

  return { goal, action, reason, layers };
}

// ─── AgentCreateModal ─────────────────────────────────────────────────────────

export interface AgentCreateProps {
  onClose: () => void;
  onSave: (name: string, faction: string, globalGoal: string, isTrader: boolean) => Promise<void>;
  /** When true, the modal opens with the Trader checkbox pre-checked. */
  defaultIsTrader?: boolean;
}

const FACTION_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'loner',    label: 'Одиночка' },
  { value: 'military', label: 'Военные'  },
  { value: 'duty',     label: 'Долг'     },
  { value: 'freedom',  label: 'Свобода'  },
];

export function AgentCreateModal({ onClose, onSave, defaultIsTrader = false }: AgentCreateProps) {
  const [name,       setName]       = useState('');
  const [faction,    setFaction]    = useState('loner');
  const [globalGoal, setGlobalGoal] = useState('');
  const [isTrader,   setIsTrader]   = useState(defaultIsTrader);
  const [saving,     setSaving]     = useState(false);
  const [err,        setErr]        = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await onSave(name.trim(), faction, globalGoal.trim(), isTrader);
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
            <textarea
              style={{ ...cs.input, minHeight: 64, resize: 'vertical' as const }}
              value={globalGoal}
              onChange={(e) => setGlobalGoal(e.target.value)}
              placeholder="Глобальная цель в Зоне…"
            />
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
