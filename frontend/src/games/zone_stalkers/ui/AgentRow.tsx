/**
 * AgentRow — a reusable clickable card that shows a compact agent summary.
 * Clicking the row opens the full AgentProfileModal overlay.
 * An optional "▶ Взять" button (stops propagation) can trigger a take-control action.
 */
import { useState } from 'react';
import AgentProfileModal, { AgentForProfile } from './AgentProfileModal';

// ─── Props ────────────────────────────────────────────────────────────────────

interface AgentRowProps {
  agent: AgentForProfile;
  /** Pre-resolved location display name. Takes priority over `locations` lookup. */
  locationName?: string;
  /** Mark with ⭐ when this is the current player's character. */
  isCurrentPlayer?: boolean;
  /** If provided, renders a "▶ Взять" button that fires this callback (row click still opens modal). */
  onTakeControl?: () => void;
  /** Location registry used to resolve `agent.location_id` when `locationName` is absent. */
  locations?: Record<string, { name: string }>;
  /** Optional sendCommand so the profile modal can call debug commands (e.g. bot decision preview). */
  sendCommand?: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function AgentRow({
  agent,
  locationName,
  isCurrentPlayer,
  onTakeControl,
  locations,
  sendCommand,
}: AgentRowProps) {
  const [showProfile, setShowProfile] = useState(false);
  const [hovered, setHovered] = useState(false);

  // Resolve location display name
  const resolvedLocName =
    locationName ??
    (locations ? (locations[agent.location_id]?.name ?? agent.location_id) : agent.location_id);

  // HP bar colour
  const hpPct = agent.max_hp > 0 ? agent.hp / agent.max_hp : 0;
  const hpColor =
    hpPct > 0.5 ? '#22c55e' : hpPct > 0.25 ? '#f59e0b' : '#ef4444';

  return (
    <>
      <div
        style={{
          background: hovered ? '#253347' : '#1e293b',
          border: '1px solid #334155',
          borderRadius: 8,
          padding: '0.6rem 0.85rem',
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.45rem 1rem',
          alignItems: 'center',
          cursor: 'pointer',
          transition: 'background 0.15s',
          color: '#e2e8f0',
        }}
        onClick={() => setShowProfile(true)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {/* ── Name ── */}
        <span style={{
          color: agent.is_alive ? '#e2e8f0' : '#475569',
          fontWeight: 700,
          fontSize: '0.9rem',
          minWidth: 120,
          flexShrink: 0,
        }}>
          {isCurrentPlayer && (
            <span style={{ color: '#fbbf24', marginRight: 4 }}>⭐</span>
          )}
          {agent.name}
          {!agent.is_alive && (
            <span style={{ color: '#ef4444', fontSize: '0.7rem', marginLeft: 6 }}>†</span>
          )}
        </span>

        {/* ── Faction badge ── */}
        <span style={{
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: 5,
          padding: '0.1rem 0.4rem',
          color: '#94a3b8',
          fontSize: '0.68rem',
        }}>
          {agent.faction}
        </span>

        {/* ── Controller badge ── */}
        <span style={{
          background: agent.controller.kind === 'human' ? '#1d4ed8' : '#1e293b',
          color: agent.controller.kind === 'human' ? '#bfdbfe' : '#475569',
          border: agent.controller.kind === 'human' ? 'none' : '1px solid #334155',
          borderRadius: 5,
          padding: '0.1rem 0.4rem',
          fontSize: '0.65rem',
          fontWeight: 700,
        }}>
          {agent.controller.kind === 'human' ? '👤' : '🤖'}
        </span>

        {/* ── HP bar ── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 110 }}>
          <div style={{
            flex: 1,
            height: 6,
            background: '#0f172a',
            borderRadius: 3,
            overflow: 'hidden',
            minWidth: 60,
          }}>
            <div style={{
              height: '100%',
              width: `${Math.max(0, Math.min(100, hpPct * 100))}%`,
              background: hpColor,
              borderRadius: 3,
              transition: 'width 0.3s',
            }} />
          </div>
          <span style={{ color: '#94a3b8', fontSize: '0.68rem', whiteSpace: 'nowrap' }}>
            {agent.hp}/{agent.max_hp}
          </span>
        </div>

        {/* ── Location ── */}
        <span style={{ color: '#64748b', fontSize: '0.72rem' }}>
          📍 {resolvedLocName}
        </span>

        {/* ── Money ── */}
        <span style={{ color: '#fbbf24', fontSize: '0.72rem' }}>
          💰 {agent.money} RU
        </span>

        {/* ── Take-control button (stops propagation so it doesn't open the modal) ── */}
        {onTakeControl && (
          <button
            style={{
              background: '#1e3a5f',
              border: '1px solid #3b82f6',
              color: '#93c5fd',
              borderRadius: 6,
              padding: '0.25rem 0.65rem',
              fontSize: '0.72rem',
              fontWeight: 600,
              cursor: 'pointer',
              marginLeft: 'auto',
              flexShrink: 0,
            }}
            onClick={(e) => {
              e.stopPropagation();
              onTakeControl();
            }}
          >
            ▶ Взять
          </button>
        )}
      </div>

      {/* ── Profile modal ── */}
      {showProfile && (
        <AgentProfileModal
          agent={agent}
          locationName={resolvedLocName}
          onClose={() => setShowProfile(false)}
          sendCommand={sendCommand}
        />
      )}
    </>
  );
}

