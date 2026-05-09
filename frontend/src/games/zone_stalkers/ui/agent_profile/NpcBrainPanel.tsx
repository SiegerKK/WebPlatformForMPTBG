/**
 * NpcBrainPanel — "NPC Brain v3 — Current Decision" section.
 * Spec section 4.
 */
import React from 'react';
import type { BrainTrace, BrainTraceEvent } from '../AgentProfileModal';
import { pct, formatObjectiveKey, traceTimeLabel, schedRemaining } from './exportNpcHistory';

interface NpcBrainPanelProps {
  brainTrace: BrainTrace | null | undefined;
  latestEvent: BrainTraceEvent | null;
  scheduledAction: {
    type: string;
    turns_remaining: number;
    turns_total: number;
    target_id: string;
    final_target_id?: string;
  } | null;
}

export function NpcBrainPanel({ brainTrace, latestEvent, scheduledAction }: NpcBrainPanelProps) {
  if (!brainTrace) return null;

  const hasObjective = latestEvent?.active_objective != null;
  const isPlanMonitorOnly = !hasObjective && latestEvent?.mode === 'plan_monitor';
  const isLegacyDecision = !hasObjective && latestEvent?.mode === 'decision';

  return (
    <div style={st.brainCard}>
      {/* Current thought */}
      {brainTrace.current_thought && (
        <div style={st.thoughtBlock}>
          <span style={st.thoughtLabel}>Текущая мысль:</span>
          <span style={st.thoughtVal}>{brainTrace.current_thought}</span>
        </div>
      )}

      {/* Plan-monitor only — no new decision */}
      {isPlanMonitorOnly && (
        <div style={st.continuationNote}>
          ⏳ Сейчас нет нового решения, NPC продолжает действие
        </div>
      )}

      {/* Legacy decision — no objective */}
      {isLegacyDecision && (
        <div style={st.legacyWarning}>
          ⚠️ Legacy decision event — objective отсутствует
          {latestEvent?.intent_kind && (
            <span style={st.legacyIntent}> · intent: {latestEvent.intent_kind}</span>
          )}
        </div>
      )}

      {/* Objective-first decision */}
      {hasObjective && latestEvent && (
        <>
          {/* Active objective */}
          <div style={st.objectiveBlock}>
            <div style={st.objectiveHeader}>
              <span style={st.objectivePrimary}>Активная цель:</span>
            </div>
            <div style={st.objectiveKeyRow}>
              <span style={st.objectiveKey}>
                {formatObjectiveKey(latestEvent.active_objective!.key)}
              </span>
              <span style={st.objectiveScore}>{pct(latestEvent.active_objective!.score)}</span>
            </div>
            {latestEvent.active_objective!.source && (
              <div style={st.objectiveMeta}>
                Источник: {latestEvent.active_objective!.source}
              </div>
            )}
            {latestEvent.active_objective!.reason && (
              <div style={st.objectiveReason}>{latestEvent.active_objective!.reason}</div>
            )}
          </div>

          {/* Execution info (adapter + scheduled action) */}
          <div style={st.executionBlock}>
            <div style={st.execTitle}>Исполнение:</div>
            {latestEvent.intent_kind && (
              <div style={st.execRow}>
                <span style={st.execKey}>adapter intent:</span>
                <span style={st.execVal}>{latestEvent.intent_kind}</span>
                {latestEvent.intent_score != null && (
                  <span style={st.execScore}>{pct(latestEvent.intent_score)}</span>
                )}
              </div>
            )}
            {scheduledAction && (
              <div style={st.execRow}>
                <span style={st.execKey}>scheduled:</span>
                <span style={st.execVal}>
                  {scheduledAction.type} — {schedRemaining(scheduledAction.type, scheduledAction.turns_remaining)}{' '}
                  ({scheduledAction.turns_remaining}/{scheduledAction.turns_total})
                </span>
              </div>
            )}
          </div>

          {/* Reason / summary */}
          {latestEvent.reason && (
            <div style={st.reasonBlock}>{latestEvent.reason}</div>
          )}
        </>
      )}

      {/* Timestamp */}
      {latestEvent && (
        <div style={st.timeRow}>
          {traceTimeLabel(latestEvent.turn, latestEvent.world_time)}
        </div>
      )}
    </div>
  );
}

const st: Record<string, React.CSSProperties> = {
  brainCard: {
    background: '#0d1f3c',
    borderRadius: 8,
    borderLeft: '3px solid #6366f1',
    padding: '0.65rem 0.75rem',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.45rem',
  },
  thoughtBlock: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  thoughtLabel: {
    color: '#64748b',
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  thoughtVal: {
    color: '#c7d2fe',
    fontSize: '0.82rem',
    lineHeight: 1.5,
    fontStyle: 'italic',
  },
  continuationNote: {
    color: '#94a3b8',
    fontSize: '0.78rem',
    padding: '0.3rem 0',
  },
  legacyWarning: {
    color: '#f59e0b',
    fontSize: '0.75rem',
    background: '#1c1400',
    border: '1px solid #78350f',
    borderRadius: 5,
    padding: '0.3rem 0.5rem',
  },
  legacyIntent: {
    color: '#94a3b8',
  },
  objectiveBlock: {
    background: '#0f172a',
    borderRadius: 6,
    padding: '0.45rem 0.55rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  objectiveHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  objectivePrimary: {
    color: '#94a3b8',
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
  },
  objectiveKeyRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap' as const,
  },
  objectiveKey: {
    color: '#a5f3fc',
    fontWeight: 700,
    fontSize: '0.88rem',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.03em',
  },
  objectiveScore: {
    color: '#38bdf8',
    fontSize: '0.75rem',
    fontWeight: 600,
    fontVariantNumeric: 'tabular-nums' as const,
  },
  objectiveMeta: {
    color: '#64748b',
    fontSize: '0.7rem',
  },
  objectiveReason: {
    color: '#94a3b8',
    fontSize: '0.75rem',
    lineHeight: 1.45,
  },
  executionBlock: {
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
    paddingTop: 2,
  },
  execTitle: {
    color: '#64748b',
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
  },
  execRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  execKey: {
    color: '#475569',
    fontSize: '0.72rem',
    flexShrink: 0,
  },
  execVal: {
    color: '#cbd5e1',
    fontSize: '0.72rem',
    flex: 1,
  },
  execScore: {
    color: '#64748b',
    fontSize: '0.68rem',
    fontVariantNumeric: 'tabular-nums' as const,
  },
  reasonBlock: {
    color: '#94a3b8',
    fontSize: '0.75rem',
    lineHeight: 1.45,
    fontStyle: 'italic',
    borderTop: '1px solid #1e293b',
    paddingTop: 4,
  },
  timeRow: {
    color: '#334155',
    fontSize: '0.68rem',
    textAlign: 'right' as const,
  },
};
