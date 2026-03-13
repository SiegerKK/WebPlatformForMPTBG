/**
 * DebugMapPage — free-form canvas for inspecting the Zone Stalkers world map.
 *
 * Locations are rendered as draggable cards auto-positioned by a BFS radial
 * layout derived from the connection graph.  Clicking a card opens the full
 * location detail panel on the right.  SVG lines trace the connections.
 */
import React, { useMemo, useState } from 'react';

// ─── Types (mirrored from ZoneStalkerGame/index.tsx) ─────────────────────────

interface LocationConn {
  to: string;
  type: string;
}

interface ZoneLocation {
  id: string;
  name: string;
  type: string;
  danger_level: number;
  connections: LocationConn[];
  anomalies: Array<{ id: string; type: string; name: string; active?: boolean }>;
  artifacts: Array<{ id: string; type: string; name: string; value: number }>;
  items: Array<{ id: string; type: string; name: string }>;
  agents: string[];
}

interface StalkerAgent {
  id: string;
  name: string;
  location_id: string;
  hp: number;
  max_hp: number;
  faction: string;
  is_alive: boolean;
  controller: { kind: string; participant_id?: string | null };
}

interface ZoneMapState {
  context_type: string;
  world_turn: number;
  world_hour: number;
  world_day: number;
  locations: Record<string, ZoneLocation>;
  agents: Record<string, StalkerAgent>;
  mutants: Record<string, { id: string; name: string; location_id: string; hp: number; max_hp: number; is_alive: boolean }>;
  traders: Record<string, { id: string; name: string; location_id: string }>;
  player_agents: Record<string, string>;
  active_events: string[];
  game_over: boolean;
}

interface Props {
  zoneState: ZoneMapState;
  currentLocId: string | null;
}

// ─── Layout ───────────────────────────────────────────────────────────────────

const CARD_W = 180;
const CARD_H = 112;
const RING_RADIUS = 210;
const CANVAS_PADDING = 100;

/**
 * BFS radial layout: place the safe_hub (or first location) at the centre,
 * then arrange BFS-level neighbours in concentric rings.
 */
function computeLayout(
  locations: Record<string, ZoneLocation>,
): Record<string, { x: number; y: number }> {
  const ids = Object.keys(locations);
  if (ids.length === 0) return {};

  // Prefer safe_hub as centre node
  const startId =
    Object.values(locations).find((l) => l.type === 'safe_hub')?.id ?? ids[0];

  const visited = new Set<string>();
  const levels: string[][] = [];
  let queue = [startId];

  while (queue.length > 0) {
    const nextQueue: string[] = [];
    const level: string[] = [];
    for (const id of queue) {
      if (!visited.has(id)) {
        visited.add(id);
        level.push(id);
        for (const conn of locations[id].connections) {
          if (!visited.has(conn.to)) nextQueue.push(conn.to);
        }
      }
    }
    if (level.length > 0) levels.push(level);
    queue = nextQueue;
  }

  // Disconnected nodes form an extra ring
  const disconnected = ids.filter((id) => !visited.has(id));
  if (disconnected.length > 0) levels.push(disconnected);

  // Approximate canvas centre — will be shifted by offset later
  const cx = 430;
  const cy = 300;

  const positions: Record<string, { x: number; y: number }> = {};
  levels.forEach((level, li) => {
    const r = li === 0 ? 0 : RING_RADIUS * li;
    level.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / level.length - Math.PI / 2;
      positions[id] = {
        x: Math.round(cx + r * Math.cos(angle)),
        y: Math.round(cy + r * Math.sin(angle)),
      };
    });
  });

  return positions;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const DANGER_COLORS = ['#22c55e', '#84cc16', '#f59e0b', '#f97316', '#ef4444'];

const LOC_TYPE_COLOR: Record<string, string> = {
  safe_hub: '#22c55e',
  wild_area: '#84cc16',
  anomaly_cluster: '#a855f7',
  ruins: '#94a3b8',
  military_zone: '#3b82f6',
  underground: '#f59e0b',
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function DebugMapPage({ zoneState, currentLocId }: Props) {
  const [selectedLocId, setSelectedLocId] = useState<string | null>(null);

  const positions = useMemo(
    () => computeLayout(zoneState.locations),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(Object.keys(zoneState.locations))],
  );

  // Derive canvas dimensions from layout
  const posValues = Object.values(positions);
  const xs = posValues.map((p) => p.x);
  const ys = posValues.map((p) => p.y);
  const minX = (xs.length ? Math.min(...xs) : 0) - CARD_W / 2 - CANVAS_PADDING;
  const minY = (ys.length ? Math.min(...ys) : 0) - CARD_H / 2 - CANVAS_PADDING;
  const maxX = (xs.length ? Math.max(...xs) : 0) + CARD_W / 2 + CANVAS_PADDING;
  const maxY = (ys.length ? Math.max(...ys) : 0) + CARD_H / 2 + CANVAS_PADDING;
  const canvasW = Math.max(maxX - minX, 400);
  const canvasH = Math.max(maxY - minY, 300);
  const offsetX = -minX;
  const offsetY = -minY;

  const detailLoc = selectedLocId ? zoneState.locations[selectedLocId] : null;

  const toggleSelect = (id: string) =>
    setSelectedLocId((prev) => (prev === id ? null : id));

  return (
    <div style={s.page}>
      {/* ── Canvas ────────────────────────────────────────── */}
      <div style={s.canvasWrap}>
        <div style={s.legend}>
          {Object.entries(LOC_TYPE_COLOR).map(([t, c]) => (
            <span key={t} style={s.legendItem}>
              <span style={{ ...s.legendDot, background: c }} />
              {t.replace(/_/g, ' ')}
            </span>
          ))}
        </div>

        <div style={{ ...s.canvasScroll }}>
          {/* SVG connection lines */}
          <svg
            style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 0 }}
            width={canvasW}
            height={canvasH}
          >
            {Object.values(zoneState.locations).flatMap((loc) =>
              loc.connections.map((conn) => {
                const a = positions[loc.id];
                const b = positions[conn.to];
                if (!a || !b) return null;
                const isDangerous = conn.type === 'dangerous';
                // Deduplicate: only draw A→B, not also B→A
                if (loc.id > conn.to) return null;
                return (
                  <line
                    key={`${loc.id}--${conn.to}`}
                    x1={a.x + offsetX}
                    y1={a.y + offsetY}
                    x2={b.x + offsetX}
                    y2={b.y + offsetY}
                    stroke={isDangerous ? '#7f1d1d' : '#1e3a5f'}
                    strokeWidth={isDangerous ? 2 : 1.5}
                    strokeDasharray={isDangerous ? '6 3' : undefined}
                    strokeOpacity={0.8}
                  />
                );
              }),
            )}
          </svg>

          {/* Location cards */}
          <div style={{ position: 'relative', width: canvasW, height: canvasH }}>
            {Object.entries(zoneState.locations).map(([id, loc]) => {
              const pos = positions[id];
              if (!pos) return null;
              const isSelected = id === selectedLocId;
              const isCurrent = id === currentLocId;
              const aliveAgents = loc.agents.filter(
                (aid) =>
                  zoneState.agents[aid]?.is_alive ||
                  zoneState.mutants[aid]?.is_alive,
              ).length;
              const traderCount = Object.values(zoneState.traders).filter(
                (t) => t.location_id === id,
              ).length;

              return (
                <div
                  key={id}
                  onClick={() => toggleSelect(id)}
                  title={`${loc.name} — click for details`}
                  style={{
                    position: 'absolute',
                    left: pos.x + offsetX - CARD_W / 2,
                    top: pos.y + offsetY - CARD_H / 2,
                    width: CARD_W,
                    background: isCurrent ? '#0d2a1a' : '#0f172a',
                    borderRadius: 10,
                    padding: '0.55rem 0.65rem',
                    border: `1px solid ${isSelected ? '#60a5fa' : isCurrent ? '#22c55e' : '#1e293b'}`,
                    borderLeft: `4px solid ${LOC_TYPE_COLOR[loc.type] ?? '#334155'}`,
                    cursor: 'pointer',
                    boxShadow: isSelected
                      ? '0 0 0 2px #3b82f6'
                      : isCurrent
                      ? '0 0 0 1px #22c55e44'
                      : 'none',
                    transition: 'border-color 0.15s, box-shadow 0.15s',
                    zIndex: isSelected ? 10 : 1,
                    userSelect: 'none',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
                    <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.82rem', lineHeight: 1.2, flex: 1 }}>
                      {loc.name}
                    </span>
                    <span
                      style={{
                        background: DANGER_COLORS[Math.min(loc.danger_level - 1, 4)],
                        color: '#fff',
                        borderRadius: 6,
                        padding: '0.1rem 0.35rem',
                        fontSize: '0.65rem',
                        fontWeight: 700,
                        flexShrink: 0,
                      }}
                    >
                      ⚠ {loc.danger_level}
                    </span>
                  </div>
                  <div style={{ color: '#475569', fontSize: '0.68rem', marginTop: 2 }}>
                    {loc.type.replace(/_/g, ' ')}
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 5 }}>
                    {isCurrent && <Badge bg="#166534" color="#86efac">📍 You</Badge>}
                    {aliveAgents > 0 && <Badge bg="#334155" color="#94a3b8">👥 {aliveAgents}</Badge>}
                    {traderCount > 0 && <Badge bg="#1e293b" color="#94a3b8">🏪 {traderCount}</Badge>}
                    {loc.artifacts.length > 0 && (
                      <Badge bg="#312e81" color="#a5b4fc">💎 {loc.artifacts.length}</Badge>
                    )}
                    {loc.anomalies.length > 0 && (
                      <Badge bg="#4a044e" color="#e879f9">☢ {loc.anomalies.length}</Badge>
                    )}
                    {loc.items.length > 0 && (
                      <Badge bg="#1c1917" color="#78716c">📦 {loc.items.length}</Badge>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Detail panel ──────────────────────────────────── */}
      <div style={s.detailPanel}>
        {detailLoc ? (
          <LocationDetailPanel
            loc={detailLoc}
            zoneState={zoneState}
            onClose={() => setSelectedLocId(null)}
          />
        ) : (
          <EmptyDetailHint totalLocs={Object.keys(zoneState.locations).length} />
        )}
      </div>
    </div>
  );
}

// ─── Location detail panel ────────────────────────────────────────────────────

function LocationDetailPanel({
  loc,
  zoneState,
  onClose,
}: {
  loc: ZoneLocation;
  zoneState: ZoneMapState;
  onClose: () => void;
}) {
  const stalkers = loc.agents
    .map((id) => zoneState.agents[id])
    .filter(Boolean);
  const mutants = loc.agents
    .map((id) => zoneState.mutants[id])
    .filter(Boolean);
  const traders = Object.values(zoneState.traders).filter(
    (t) => t.location_id === loc.id,
  );

  return (
    <div style={s.detail}>
      {/* Header */}
      <div style={s.detailHeader}>
        <div>
          <div style={s.detailName}>{loc.name}</div>
          <div style={s.detailMeta}>
            {loc.type.replace(/_/g, ' ')} · Danger&nbsp;
            <span
              style={{
                color: DANGER_COLORS[Math.min(loc.danger_level - 1, 4)],
                fontWeight: 700,
              }}
            >
              {loc.danger_level}
            </span>
          </div>
        </div>
        <button onClick={onClose} style={s.closeBtn}>✕</button>
      </div>

      {/* Connections */}
      <Section label="🔗 Connections">
        {loc.connections.length === 0 ? (
          <EmptyRow />
        ) : (
          loc.connections.map((c) => {
            const target = zoneState.locations[c.to];
            return (
              <DetailRow key={c.to}>
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>
                  {target?.name ?? c.to}
                </span>
                <span
                  style={{
                    color: c.type === 'dangerous' ? '#ef4444' : '#475569',
                    fontSize: '0.68rem',
                  }}
                >
                  {c.type}
                </span>
              </DetailRow>
            );
          })
        )}
      </Section>

      {/* Stalkers */}
      {stalkers.length > 0 && (
        <Section label="🧍 Stalkers">
          {stalkers.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: a.is_alive ? '#f8fafc' : '#475569', fontSize: '0.8rem' }}>
                {a.name}
                {!a.is_alive && (
                  <span style={{ color: '#ef4444', fontSize: '0.65rem', marginLeft: 4 }}>
                    (dead)
                  </span>
                )}
              </span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>
                {a.hp}/{a.max_hp} HP
              </span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Mutants */}
      {mutants.length > 0 && (
        <Section label="☣️ Mutants">
          {mutants.map((m) => (
            <DetailRow key={m.id}>
              <span style={{ color: m.is_alive ? '#fca5a5' : '#475569', fontSize: '0.8rem' }}>
                {m.name}
              </span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>
                {m.hp}/{m.max_hp} HP
              </span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Traders */}
      {traders.length > 0 && (
        <Section label="🏪 Traders">
          {traders.map((t) => (
            <DetailRow key={t.id}>
              <span style={{ color: '#fbbf24', fontSize: '0.8rem' }}>{t.name}</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Artifacts */}
      {loc.artifacts.length > 0 && (
        <Section label="💎 Artifacts">
          {loc.artifacts.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: '#a5b4fc', fontSize: '0.8rem' }}>{a.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{a.value}&nbsp;RU</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Anomalies */}
      {loc.anomalies.length > 0 && (
        <Section label="⚠️ Anomalies">
          {loc.anomalies.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: '#e879f9', fontSize: '0.8rem' }}>{a.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{a.type}</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Ground items */}
      {loc.items.length > 0 && (
        <Section label="📦 Ground items">
          {loc.items.map((item) => (
            <DetailRow key={item.id}>
              <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>{item.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{item.type}</span>
            </DetailRow>
          ))}
        </Section>
      )}

      <div style={{ color: '#1e293b', fontSize: '0.62rem', marginTop: 6 }}>id: {loc.id}</div>
    </div>
  );
}

// ─── Small sub-components ─────────────────────────────────────────────────────

function Badge({
  bg,
  color,
  children,
}: {
  bg: string;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <span
      style={{
        background: bg,
        color,
        borderRadius: 5,
        padding: '0.1rem 0.35rem',
        fontSize: '0.62rem',
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div
        style={{
          color: '#64748b',
          fontSize: '0.67rem',
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function DetailRow({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: '#1e293b',
        borderRadius: 5,
        padding: '0.25rem 0.5rem',
        gap: 6,
      }}
    >
      {children}
    </div>
  );
}

function EmptyRow() {
  return <span style={{ color: '#334155', fontSize: '0.72rem' }}>None</span>;
}

function EmptyDetailHint({ totalLocs }: { totalLocs: number }) {
  return (
    <div style={s.emptyDetail}>
      <div style={s.emptyDetailTitle}>Location Details</div>
      <p style={s.emptyDetailHint}>Click a location card on the map to see full details.</p>
      <hr style={s.hr} />
      <div style={{ color: '#334155', fontSize: '0.7rem' }}>
        {totalLocs} locations on map
      </div>
    </div>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  page: {
    display: 'flex',
    gap: 16,
    alignItems: 'flex-start',
    minHeight: 500,
  },
  canvasWrap: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    minWidth: 0,
  },
  legend: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '6px 14px',
    padding: '6px 10px',
    background: '#060b14',
    borderRadius: 8,
    border: '1px solid #1e293b',
  },
  legendItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    color: '#475569',
    fontSize: '0.68rem',
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
  },
  canvasScroll: {
    position: 'relative',
    overflow: 'auto',
    background: '#060b14',
    borderRadius: 10,
    border: '1px solid #1e293b',
    minHeight: 400,
  },
  detailPanel: {
    width: 268,
    flexShrink: 0,
  },
  detail: {
    background: '#0f172a',
    borderRadius: 10,
    border: '1px solid #334155',
    padding: '1rem',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.75rem',
    maxHeight: '72vh',
    overflowY: 'auto',
  },
  detailHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 8,
  },
  detailName: {
    color: '#f8fafc',
    fontWeight: 700,
    fontSize: '1rem',
  },
  detailMeta: {
    color: '#475569',
    fontSize: '0.72rem',
    marginTop: 2,
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#64748b',
    cursor: 'pointer',
    fontSize: '1rem',
    padding: '0.15rem',
    lineHeight: 1,
    flexShrink: 0,
  },
  emptyDetail: {
    background: '#0f172a',
    borderRadius: 10,
    border: '1px solid #1e293b',
    padding: '1rem',
    color: '#475569',
    fontSize: '0.82rem',
  },
  emptyDetailTitle: {
    color: '#475569',
    fontWeight: 700,
    fontSize: '0.72rem',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 8,
  },
  emptyDetailHint: {
    margin: 0,
    lineHeight: 1.5,
  },
  hr: {
    borderColor: '#1e293b',
    margin: '10px 0',
  },
};
