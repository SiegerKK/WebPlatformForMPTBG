/**
 * RuntimeActionPanel — "Current Runtime Action" section.
 * Shows scheduled_action + active_plan_v3 separately from decision reasoning.
 * Spec section 8.
 */
import React from 'react';
import { schedRemaining, SCHED_ICONS } from './exportNpcHistory';

interface ScheduledAction {
  type: string;
  turns_remaining: number;
  turns_total: number;
  target_id: string;
  final_target_id?: string;
}

interface RuntimeActionPanelProps {
  scheduledAction: ScheduledAction | null;
  activePlanV3: unknown;
  locations?: Record<string, { name: string; region?: string }>;
}

/** Safely read a property from an unknown value. */
function safeGet<T>(obj: unknown, key: string): T | undefined {
  if (obj != null && typeof obj === 'object' && key in (obj as Record<string, unknown>)) {
    return (obj as Record<string, unknown>)[key] as T;
  }
  return undefined;
}

export function RuntimeActionPanel({ scheduledAction, activePlanV3, locations }: RuntimeActionPanelProps) {
  const hasPlan = activePlanV3 != null;

  if (!scheduledAction && !hasPlan) return null;

  return (
    <div style={st.container}>
      {/* Scheduled action */}
      {scheduledAction && (
        <div style={st.schedBlock}>
          <div style={st.schedHeader}>
            <span style={st.schedIcon}>{SCHED_ICONS[scheduledAction.type] ?? '⏳'}</span>
            <span style={st.schedType}>{scheduledAction.type}</span>
            <span style={st.schedProgress}>
              {scheduledAction.turns_remaining}/{scheduledAction.turns_total}
            </span>
            <span style={st.schedRemaining}>
              {schedRemaining(scheduledAction.type, scheduledAction.turns_remaining)}
            </span>
          </div>

          {/* Travel destination */}
          {scheduledAction.type === 'travel' && locations && (
            (() => {
              const destId = scheduledAction.final_target_id ?? scheduledAction.target_id;
              const destLoc = locations[destId];
              const destLabel = destLoc
                ? destLoc.region
                  ? `${destLoc.name} (${destLoc.region})`
                  : destLoc.name
                : destId;
              return (
                <div style={st.schedDest}>
                  → {destLabel}
                </div>
              );
            })()
          )}

          {/* Non-travel target */}
          {scheduledAction.type !== 'travel' && scheduledAction.target_id && (
            <div style={st.schedDest}>target: {scheduledAction.target_id}</div>
          )}

          {/* Progress bar */}
          <div style={st.barBg}>
            <div
              style={{
                ...st.barFill,
                width: `${scheduledAction.turns_total > 0
                  ? Math.round(
                      ((scheduledAction.turns_total - scheduledAction.turns_remaining) /
                        scheduledAction.turns_total) *
                        100,
                    )
                  : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Active plan v3 */}
      {hasPlan && (
        <div style={st.planBlock}>
          <div style={st.planTitle}>🧭 ActivePlan v3</div>
          <ActivePlanDisplay plan={activePlanV3} />
        </div>
      )}
    </div>
  );
}

function ActivePlanDisplay({ plan }: { plan: unknown }) {
  const objectiveKey = safeGet<string>(plan, 'objective_key');
  const status = safeGet<string>(plan, 'status');
  const steps = safeGet<unknown[]>(plan, 'steps');

  const STEP_ICONS: Record<string, string> = {
    done: '✅',
    completed: '✅',
    active: '🔄',
    in_progress: '🔄',
    pending: '⏳',
    failed: '❌',
    skipped: '⏭️',
  };

  if (!objectiveKey && !steps) {
    return (
      <pre style={st.planRaw}>
        {JSON.stringify(plan, null, 2)}
      </pre>
    );
  }

  return (
    <div style={st.planContent}>
      {objectiveKey && (
        <div style={st.planRow}>
          <span style={st.planLabel}>Objective:</span>
          <span style={st.planVal}>{objectiveKey.replace(/_/g, ' ').toUpperCase()}</span>
        </div>
      )}
      {status && (
        <div style={st.planRow}>
          <span style={st.planLabel}>Status:</span>
          <span style={st.planVal}>{status}</span>
        </div>
      )}
      {Array.isArray(steps) && steps.length > 0 && (
        <div style={st.steps}>
          {steps.map((step, i) => {
            const stepType = safeGet<string>(step, 'type') ?? safeGet<string>(step, 'action_type') ?? String(i);
            const stepStatus = safeGet<string>(step, 'status') ?? 'pending';
            const icon = STEP_ICONS[stepStatus] ?? '⏳';
            return (
              <div key={i} style={st.stepRow}>
                <span style={st.stepIcon}>{icon}</span>
                <span style={{ ...st.stepLabel, color: stepStatus === 'active' || stepStatus === 'in_progress' ? '#fbbf24' : '#64748b' }}>
                  {stepType}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const st: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  schedBlock: {
    background: '#0f172a',
    borderRadius: 7,
    border: '1px solid #1e293b',
    padding: '0.5rem 0.6rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  schedHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  schedIcon: {
    fontSize: '0.9rem',
    flexShrink: 0,
  },
  schedType: {
    color: '#e2e8f0',
    fontWeight: 600,
    fontSize: '0.82rem',
    flex: 1,
  },
  schedProgress: {
    color: '#64748b',
    fontSize: '0.7rem',
    fontVariantNumeric: 'tabular-nums' as const,
    flexShrink: 0,
  },
  schedRemaining: {
    color: '#a78bfa',
    fontSize: '0.72rem',
    flexShrink: 0,
  },
  schedDest: {
    color: '#60a5fa',
    fontSize: '0.75rem',
  },
  barBg: {
    height: 3,
    background: '#1e293b',
    borderRadius: 2,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    background: '#6366f1',
    borderRadius: 2,
    transition: 'width 0.3s',
  },
  planBlock: {
    background: '#0f172a',
    borderRadius: 7,
    border: '1px solid #1e293b',
    padding: '0.5rem 0.6rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  planTitle: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
  },
  planContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  planRow: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
  },
  planLabel: {
    color: '#475569',
    fontSize: '0.72rem',
    flexShrink: 0,
    minWidth: 70,
  },
  planVal: {
    color: '#cbd5e1',
    fontSize: '0.78rem',
    fontWeight: 600,
  },
  steps: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    marginTop: 2,
  },
  stepRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  stepIcon: {
    fontSize: '0.72rem',
    flexShrink: 0,
    minWidth: 16,
  },
  stepLabel: {
    fontSize: '0.73rem',
  },
  planRaw: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    color: '#64748b',
    fontSize: '0.68rem',
    maxHeight: 120,
    overflowY: 'auto',
  },
};
