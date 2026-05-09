/**
 * MemoryUsedPanel — "Memory Used" section.
 * Shows only memory that directly influenced the latest decision.
 * Spec section 7.
 */
import React from 'react';
import type { BrainTraceEvent } from '../AgentProfileModal';
import { pct } from './exportNpcHistory';

interface MemoryUsedPanelProps {
  latestEvent: BrainTraceEvent | null;
}

const MAX_ENTRIES = 5;

export function MemoryUsedPanel({ latestEvent }: MemoryUsedPanelProps) {
  const memoryUsed = latestEvent?.memory_used ?? [];

  if (memoryUsed.length === 0) {
    return (
      <div style={st.empty}>
        В этом решении память не использовалась напрямую.
      </div>
    );
  }

  return (
    <div style={st.container}>
      {memoryUsed.slice(0, MAX_ENTRIES).map((mu, i) => (
        <div key={`${mu.id}-${i}`} style={st.card}>
          <div style={st.cardTop}>
            <span style={st.usedFor}>{mu.used_for || mu.kind}</span>
            <span style={st.kind}>{mu.kind}</span>
            <span style={st.conf}>{pct(mu.confidence)}</span>
          </div>
          <div style={st.summary}>{mu.summary}</div>
        </div>
      ))}
      {memoryUsed.length > MAX_ENTRIES && (
        <div style={st.overflow}>+{memoryUsed.length - MAX_ENTRIES} ещё</div>
      )}
    </div>
  );
}

const st: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 5,
  },
  empty: {
    color: '#475569',
    fontSize: '0.75rem',
    fontStyle: 'italic',
  },
  card: {
    background: '#0f172a',
    borderRadius: 6,
    borderLeft: '3px solid #312e81',
    padding: '0.35rem 0.5rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  cardTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  usedFor: {
    color: '#a5b4fc',
    fontWeight: 700,
    fontSize: '0.75rem',
    flex: 1,
  },
  kind: {
    color: '#475569',
    fontSize: '0.68rem',
    border: '1px solid #1e293b',
    borderRadius: 4,
    padding: '0px 5px',
    lineHeight: 1.6,
    flexShrink: 0,
  },
  conf: {
    color: '#64748b',
    fontSize: '0.68rem',
    fontVariantNumeric: 'tabular-nums' as const,
    flexShrink: 0,
  },
  summary: {
    color: '#94a3b8',
    fontSize: '0.75rem',
    lineHeight: 1.45,
  },
  overflow: {
    color: '#475569',
    fontSize: '0.7rem',
    fontStyle: 'italic',
  },
};
