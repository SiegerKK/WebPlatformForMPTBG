/**
 * NeedsPanel — "NPC Brain v3 — Needs & Constraints" section.
 * Spec section 6.
 */
import React from 'react';
import type { BrainTraceEvent } from '../AgentProfileModal';
import { pct } from './exportNpcHistory';

interface NeedsPanelProps {
  latestEvent: BrainTraceEvent | null;
}

export function NeedsPanel({ latestEvent }: NeedsPanelProps) {
  if (!latestEvent) return null;

  const immediateNeeds = latestEvent.immediate_needs ?? [];
  const itemNeeds = latestEvent.item_needs ?? [];
  const liquidity = latestEvent.liquidity ?? null;

  const hasAnything = immediateNeeds.length > 0 || itemNeeds.length > 0 || liquidity != null;
  if (!hasAnything) return null;

  return (
    <div style={st.container}>
      {/* Immediate needs */}
      <div style={st.group}>
        <div style={st.groupLabel}>⚠️ Immediate needs</div>
        {immediateNeeds.length === 0 ? (
          <div style={st.empty}>— нет</div>
        ) : (
          <div style={st.chips}>
            {immediateNeeds.map((n, i) => (
              <div key={`im-${n.key}-${i}`} style={{ ...st.needChip, ...needColor(n.urgency) }}>
                <span style={st.needKey}>{n.key}</span>
                <span style={st.needUrg}>{pct(n.urgency)}</span>
                {n.reason && <span style={st.needReason}>{n.reason}</span>}
                {n.selected_item_type && (
                  <span style={st.needItem}>→ {n.selected_item_type}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Item needs */}
      {itemNeeds.length > 0 && (
        <div style={st.group}>
          <div style={st.groupLabel}>🎒 Item needs</div>
          <div style={st.chips}>
            {itemNeeds.map((n, i) => (
              <div key={`it-${n.key}-${i}`} style={{ ...st.needChip, ...needColor(n.urgency) }}>
                <span style={st.needKey}>{n.key}</span>
                <span style={st.needUrg}>{pct(n.urgency)}</span>
                {n.reason && <span style={st.needReason}>{n.reason}</span>}
                {typeof n.missing_count === 'number' && n.missing_count > 0 && (
                  <span style={st.needItem}>−{n.missing_count} шт</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Liquidity */}
      {liquidity && (
        <div style={st.group}>
          <div style={st.groupLabel}>💰 Liquidity</div>
          <div style={st.liquidRow}>
            {typeof liquidity.money_missing === 'number' && liquidity.money_missing > 0 && (
              <span style={st.liquidBad}>не хватает {liquidity.money_missing} RU</span>
            )}
            <span style={st.liquidItem}>
              safe: {liquidity.safe_sale_options ?? 0}
            </span>
            <span style={st.liquidItem}>
              risky: {liquidity.risky_sale_options ?? 0}
            </span>
            <span style={st.liquidItem}>
              emergency: {liquidity.emergency_sale_options ?? 0}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Derive a chip background tint from urgency (0–1). */
function needColor(urgency: number): React.CSSProperties {
  if (urgency >= 0.8) return { borderColor: '#7f1d1d', background: '#1c0a0a' };
  if (urgency >= 0.5) return { borderColor: '#78350f', background: '#1c1000' };
  return { borderColor: '#1e3a5f', background: '#0c1624' };
}

const st: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  group: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  groupLabel: {
    color: '#94a3b8',
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  empty: {
    color: '#334155',
    fontSize: '0.72rem',
  },
  chips: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  needChip: {
    border: '1px solid #1e3a5f',
    background: '#0c1624',
    borderRadius: 5,
    padding: '0.25rem 0.45rem',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  needKey: {
    color: '#cbd5e1',
    fontSize: '0.75rem',
    fontWeight: 600,
  },
  needUrg: {
    color: '#f59e0b',
    fontSize: '0.7rem',
    fontVariantNumeric: 'tabular-nums' as const,
    fontWeight: 600,
  },
  needReason: {
    color: '#64748b',
    fontSize: '0.68rem',
    flex: 1,
  },
  needItem: {
    color: '#818cf8',
    fontSize: '0.68rem',
    flexShrink: 0,
  },
  liquidRow: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap' as const,
    alignItems: 'center',
  },
  liquidBad: {
    color: '#ef4444',
    fontSize: '0.75rem',
    fontWeight: 600,
  },
  liquidItem: {
    color: '#64748b',
    fontSize: '0.72rem',
    background: '#0f172a',
    borderRadius: 4,
    padding: '0.1rem 0.35rem',
    border: '1px solid #1e293b',
  },
};
