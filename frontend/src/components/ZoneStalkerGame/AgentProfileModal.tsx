/**
 * AgentProfileModal — reusable modal showing a full Zone Stalkers agent profile.
 *
 * Import `AgentForProfile` to build a compatible agent object.
 * Closing: clicking the semi-transparent overlay or the ✕ button calls `onClose`.
 */
import React from 'react';

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
  risk_tolerance?: number;
  reputation?: number;
  memory?: Array<{
    world_turn: number;
    world_day: number;
    world_hour: number;
    type: string;
    title: string;
    summary: string;
  }>;
}

interface Props {
  agent: AgentForProfile;
  locationName: string;
  onClose: () => void;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const HOUR_LABEL = (h: number) => `${String(h).padStart(2, '0')}:00`;

const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore: '🔍',
  sleep: '😴',
  event: '📖',
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function AgentProfileModal({ agent, locationName, onClose }: Props) {
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
              {agent.scheduled_action.type} — {agent.scheduled_action.turns_remaining}ч осталось
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
                      День {m.world_day} · {HOUR_LABEL(m.world_hour)}
                    </span>
                  </div>
                  <div style={s.memoryTitle}>{m.title}</div>
                  <div style={s.memorySummary}>{m.summary}</div>
                </div>
              ))}
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
};
