/**
 * MemoryTimeline — enhanced memory timeline.
 * Special rendering for objective_decision and v2_decision entries.
 * Spec section 10.
 */
import React from 'react';
import {
  type MemEntry,
  MEM_ICONS,
  MEM_COLORS,
  _INTENT_META,
  TIME_LABEL,
  turnToTime,
  pct,
} from './exportNpcHistory';

interface MemoryTimelineProps {
  memory: MemEntry[];
}

export function MemoryTimeline({ memory }: MemoryTimelineProps) {
  if (memory.length === 0) return null;

  return (
    <div style={st.list}>
      {[...memory].reverse().map((m, i) => {
        const effects = m.effects ?? {};
        const actionKind = typeof effects.action_kind === 'string' ? effects.action_kind : null;

        // Resolve time
        const t = m.world_day !== undefined
          ? { world_day: m.world_day, world_hour: m.world_hour ?? 0, world_minute: m.world_minute ?? 0 }
          : turnToTime(m.world_turn);
        const timeLabel = `День ${t.world_day} · ${TIME_LABEL(t.world_hour, t.world_minute)}`;

        // Objective decision (v3 brain)
        if (actionKind === 'objective_decision') {
          return (
            <ObjectiveDecisionEntry key={i} m={m} effects={effects} timeLabel={timeLabel} />
          );
        }

        // Legacy v2 decision
        if (actionKind === 'v2_decision') {
          return (
            <V2DecisionEntry key={i} m={m} effects={effects} timeLabel={timeLabel} />
          );
        }

        // Default rendering
        return (
          <DefaultEntry key={i} m={m} timeLabel={timeLabel} />
        );
      })}
    </div>
  );
}

// ─── Objective Decision Entry ─────────────────────────────────────────────────

function ObjectiveDecisionEntry({
  m,
  effects,
  timeLabel,
}: {
  m: MemEntry;
  effects: Record<string, unknown>;
  timeLabel: string;
}) {
  const objectiveKey = typeof effects.objective_key === 'string' ? effects.objective_key : null;
  const objectiveScore = typeof effects.objective_score === 'number' ? effects.objective_score : null;
  const objectiveSource = typeof effects.objective_source === 'string' ? effects.objective_source : null;
  const objectiveReason = typeof effects.objective_reason === 'string' ? effects.objective_reason : null;
  const adapterIntent = typeof effects.adapter_intent_kind === 'string' ? effects.adapter_intent_kind : (typeof effects.intent_kind === 'string' ? effects.intent_kind : null);
  const planStep = typeof effects.plan_step === 'string' ? effects.plan_step : (typeof effects.scheduled_action_type === 'string' ? effects.scheduled_action_type : null);

  return (
    <div style={{ ...st.entry, borderLeft: '3px solid #6366f1' }}>
      <div style={st.meta}>
        <span style={{ ...st.type, color: '#818cf8' }}>
          🧠 objective decision
        </span>
        <span style={st.when}>{timeLabel}</span>
      </div>
      {objectiveKey && (
        <div style={st.objKeyRow}>
          <span style={st.objKey}>
            Цель: {objectiveKey.replace(/_/g, ' ').toUpperCase()}
          </span>
          {objectiveScore != null && (
            <span style={st.objScore}>{pct(objectiveScore)}</span>
          )}
        </div>
      )}
      {objectiveSource && (
        <div style={st.objMeta}>Source: {objectiveSource}</div>
      )}
      {objectiveReason && (
        <div style={st.objReason}>{objectiveReason}</div>
      )}
      {adapterIntent && (
        <div style={st.objAdapter}>Adapter: {adapterIntent}</div>
      )}
      {planStep && (
        <div style={st.objPlanStep}>Plan step: {planStep}</div>
      )}
      {!!m.summary && <div style={st.summary}>{m.summary}</div>}
    </div>
  );
}

// ─── V2 Decision Entry ────────────────────────────────────────────────────────

function V2DecisionEntry({
  m,
  effects,
  timeLabel,
}: {
  m: MemEntry;
  effects: Record<string, unknown>;
  timeLabel: string;
}) {
  const intentKind = typeof effects.intent_kind === 'string' ? effects.intent_kind : null;
  const intentScore = typeof effects.intent_score === 'number' ? effects.intent_score : null;
  const intentMeta = intentKind ? (_INTENT_META[intentKind] ?? null) : null;

  return (
    <div style={{ ...st.entry, borderLeft: '3px solid #475569' }}>
      <div style={st.meta}>
        <span style={{ ...st.type, color: '#64748b' }}>
          📋 legacy intent decision
        </span>
        <div style={st.legacyRight}>
          <span style={st.legacyBadge}>legacy</span>
          <span style={st.when}>{timeLabel}</span>
        </div>
      </div>
      {intentMeta && (
        <div style={st.title}>
          {intentMeta.icon} {intentMeta.label}
        </div>
      )}
      {intentKind && !intentMeta && (
        <div style={st.title}>intent: {intentKind}</div>
      )}
      {intentScore != null && (
        <div style={st.objScore}>score: {pct(intentScore)}</div>
      )}
      {!!m.summary && <div style={st.summary}>{m.summary}</div>}
    </div>
  );
}

// ─── Default Entry ────────────────────────────────────────────────────────────

function DefaultEntry({ m, timeLabel }: { m: MemEntry; timeLabel: string }) {
  const effects = m.effects ?? {};
  const color = MEM_COLORS[m.type] ?? '#94a3b8';
  const icon = MEM_ICONS[m.type] ?? '📝';
  const subLabel = effects.action_kind ? ` · ${effects.action_kind}` : '';

  const decisionIntentKind =
    m.type === 'decision' && typeof effects.intent_kind === 'string'
      ? effects.intent_kind
      : null;
  const intentMeta = decisionIntentKind ? (_INTENT_META[decisionIntentKind] ?? null) : null;
  const decisionScore =
    m.type === 'decision' && typeof effects.intent_score === 'number'
      ? effects.intent_score
      : null;

  return (
    <div style={{ ...st.entry, borderLeft: `3px solid ${color}` }}>
      <div style={st.meta}>
        <span style={{ ...st.type, color }}>
          {icon} {m.type}{subLabel}
        </span>
        <span style={st.when}>{timeLabel}</span>
      </div>
      <div style={st.title}>{m.title}</div>
      {intentMeta && (
        <div style={st.intentRow}>
          <span style={st.intentText}>{intentMeta.icon} {intentMeta.label}</span>
          {decisionScore != null && (
            <span style={st.intentScore}>· {pct(decisionScore)}</span>
          )}
        </div>
      )}
      {!!m.summary && <div style={st.summary}>{m.summary}</div>}
    </div>
  );
}

const st: Record<string, React.CSSProperties> = {
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    maxHeight: 480,
    overflowY: 'auto',
  },
  entry: {
    background: '#0f172a',
    borderRadius: 6,
    padding: '0.5rem',
    paddingLeft: '0.6rem',
  },
  meta: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 3,
    flexWrap: 'wrap' as const,
    gap: 4,
  },
  type: {
    fontWeight: 600,
    fontSize: '0.72rem',
  },
  when: {
    color: '#475569',
    fontSize: '0.7rem',
  },
  title: {
    color: '#f8fafc',
    fontWeight: 600,
    fontSize: '0.82rem',
    marginBottom: 2,
  },
  summary: {
    color: '#94a3b8',
    fontSize: '0.78rem',
    lineHeight: 1.5,
  },
  // Objective decision styles
  objKeyRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap' as const,
  },
  objKey: {
    color: '#a5b4fc',
    fontWeight: 700,
    fontSize: '0.82rem',
    letterSpacing: '0.02em',
  },
  objScore: {
    color: '#818cf8',
    fontSize: '0.72rem',
    fontVariantNumeric: 'tabular-nums' as const,
  },
  objMeta: {
    color: '#475569',
    fontSize: '0.68rem',
    marginTop: 2,
  },
  objReason: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    lineHeight: 1.4,
    marginTop: 2,
  },
  objAdapter: {
    color: '#6366f1',
    fontSize: '0.7rem',
    marginTop: 2,
  },
  objPlanStep: {
    color: '#475569',
    fontSize: '0.7rem',
  },
  // Legacy v2 styles
  legacyRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
  },
  legacyBadge: {
    color: '#78350f',
    border: '1px solid #78350f',
    borderRadius: 4,
    fontSize: '0.58rem',
    padding: '0px 4px',
    lineHeight: 1.6,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  // Intent row (default entry)
  intentRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
  },
  intentText: {
    fontSize: '0.78rem',
  },
  intentScore: {
    fontSize: '0.72rem',
    color: '#64748b',
  },
};
