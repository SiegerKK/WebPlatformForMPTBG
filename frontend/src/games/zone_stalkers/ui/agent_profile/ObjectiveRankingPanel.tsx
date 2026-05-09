/**
 * ObjectiveRankingPanel — "NPC Brain v3 — Objective Ranking" section.
 * Spec section 5.
 */
import React, { useState } from 'react';
import type { BrainTraceEvent } from '../AgentProfileModal';
import { pct, formatObjectiveKey } from './exportNpcHistory';

interface ObjectiveRankingPanelProps {
  latestEvent: BrainTraceEvent | null;
}

const MAX_VISIBLE = 5;

export function ObjectiveRankingPanel({ latestEvent }: ObjectiveRankingPanelProps) {
  const [showAll, setShowAll] = useState(false);

  if (!latestEvent) return null;

  const activeObj = latestEvent.active_objective ?? null;
  const scores = latestEvent.objective_scores ?? [];
  const alternatives = latestEvent.alternatives ?? [];

  // Build ranked list: active first, then scores, then alternatives (deduped)
  type RankedItem = {
    key: string;
    score: number;
    source?: string;
    reason?: string;
    decision?: string;
    isActive: boolean;
  };

  const ranked: RankedItem[] = [];
  const seen = new Set<string>();

  if (activeObj) {
    ranked.push({
      key: activeObj.key,
      score: activeObj.score,
      source: activeObj.source,
      reason: activeObj.reason,
      decision: activeObj.decision ?? 'selected',
      isActive: true,
    });
    seen.add(activeObj.key);
  }

  for (const o of scores) {
    if (!seen.has(o.key)) {
      ranked.push({
        key: o.key,
        score: o.score,
        source: o.source,
        reason: o.reason,
        decision: o.decision ?? 'rejected',
        isActive: false,
      });
      seen.add(o.key);
    }
  }

  for (const o of alternatives) {
    if (!seen.has(o.key)) {
      ranked.push({
        key: o.key,
        score: o.score,
        source: o.source,
        reason: o.reason,
        decision: o.decision ?? 'rejected',
        isActive: false,
      });
      seen.add(o.key);
    }
  }

  if (ranked.length === 0) return null;

  const visible = showAll ? ranked : ranked.slice(0, MAX_VISIBLE);
  const hasMore = ranked.length > MAX_VISIBLE && !showAll;

  return (
    <div style={st.container}>
      {visible.map((item, idx) => {
        const isSelected = item.isActive || item.decision === 'selected' || item.decision === 'continue_current';
        const scorePct = Math.max(0, Math.min(100, Math.round((item.score ?? 0) * 100)));

        return (
          <div key={`${item.key}-${idx}`} style={{ ...st.row, ...(isSelected ? st.rowSelected : st.rowRejected) }}>
            <div style={st.topLine}>
              {/* Rank indicator */}
              <span style={isSelected ? st.rankActive : st.rankNormal}>
                {isSelected ? '✅' : `${idx + 1}.`}
              </span>
              {/* Objective key */}
              <span style={isSelected ? st.keyActive : st.keyNormal}>
                {formatObjectiveKey(item.key).toUpperCase()}
              </span>
              {/* Score */}
              <span style={st.score}>{pct(item.score)}</span>
              {/* Decision badge */}
              {item.decision && item.decision !== 'selected' && (
                <span style={st.decisionBadge}>{item.decision}</span>
              )}
              {isSelected && (
                <span style={st.selectedBadge}>выбрано</span>
              )}
            </div>

            {/* Score bar */}
            <div style={st.barBg}>
              <div
                style={{
                  ...st.barFill,
                  width: `${scorePct}%`,
                  background: isSelected ? '#22c55e' : '#334155',
                }}
              />
            </div>

            {/* Source */}
            {item.source && (
              <div style={st.meta}>источник: {item.source}</div>
            )}

            {/* Reason */}
            {item.reason && (
              <div style={st.reason}>{item.reason}</div>
            )}
          </div>
        );
      })}

      {hasMore && (
        <button style={st.showAllBtn} onClick={() => setShowAll(true)}>
          Показать все ({ranked.length - MAX_VISIBLE} скрыто)
        </button>
      )}
      {showAll && ranked.length > MAX_VISIBLE && (
        <button style={st.showAllBtn} onClick={() => setShowAll(false)}>
          Свернуть
        </button>
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
  row: {
    borderRadius: 6,
    padding: '0.4rem 0.55rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  rowSelected: {
    background: '#052e16',
    border: '1px solid #166534',
  },
  rowRejected: {
    background: '#0f172a',
    border: '1px solid #1e293b',
  },
  topLine: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  rankActive: {
    fontSize: '0.82rem',
    flexShrink: 0,
  },
  rankNormal: {
    color: '#475569',
    fontSize: '0.72rem',
    flexShrink: 0,
    minWidth: 18,
  },
  keyActive: {
    color: '#86efac',
    fontWeight: 700,
    fontSize: '0.8rem',
    flex: 1,
    letterSpacing: '0.02em',
  },
  keyNormal: {
    color: '#64748b',
    fontWeight: 600,
    fontSize: '0.78rem',
    flex: 1,
    letterSpacing: '0.02em',
  },
  score: {
    color: '#475569',
    fontSize: '0.7rem',
    fontVariantNumeric: 'tabular-nums' as const,
    flexShrink: 0,
  },
  decisionBadge: {
    color: '#ef4444',
    fontSize: '0.6rem',
    border: '1px solid #7f1d1d',
    borderRadius: 4,
    padding: '0px 4px',
    flexShrink: 0,
    lineHeight: 1.5,
  },
  selectedBadge: {
    color: '#22c55e',
    fontSize: '0.6rem',
    border: '1px solid #166534',
    borderRadius: 4,
    padding: '0px 4px',
    flexShrink: 0,
    lineHeight: 1.5,
  },
  barBg: {
    height: 3,
    background: '#0f172a',
    borderRadius: 2,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.3s',
  },
  meta: {
    color: '#475569',
    fontSize: '0.68rem',
  },
  reason: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    lineHeight: 1.4,
  },
  showAllBtn: {
    background: 'transparent',
    border: '1px solid #334155',
    color: '#64748b',
    borderRadius: 5,
    padding: '0.2rem 0.5rem',
    fontSize: '0.7rem',
    cursor: 'pointer',
    alignSelf: 'flex-start' as const,
    marginTop: 2,
  },
};
