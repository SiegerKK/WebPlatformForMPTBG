/**
 * DebugMapPage — free-form canvas for inspecting and editing the Zone Stalkers world map.
 *
 * Features:
 *  - BFS radial initial layout (restored from persisted positions on reload)
 *  - Drag-and-drop: cards can be freely moved on the canvas (pointer capture)
 *  - Link mode: click a source card then a target card to create/delete a
 *    bidirectional connection; remove individual connections from the detail panel
 *  - Location detail panel with connection management
 *  - All edits (positions + connections) are persisted to the backend via
 *    the debug_update_map command so they survive page reloads
 */
import React, { useMemo, useState, useRef, useCallback, useEffect } from 'react';

// ─── Types (mirrored from ZoneStalkerGame/index.tsx) ─────────────────────────

interface LocationConn {
  to: string;
  type: string;
}

interface ZoneLocation {
  id: string;
  name: string;
  terrain_type?: string;
  anomaly_activity?: number;
  dominant_anomaly_type?: string | null;
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
  /** Persisted debug canvas state written by the debug_update_map command */
  debug_layout?: { positions: Record<string, { x: number; y: number }> };
}

interface Props {
  zoneState: ZoneMapState;
  currentLocId: string | null;
  /** Send a command to the backend (uses the zone_map context id automatically) */
  sendCommand: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
}

// ─── Layout ───────────────────────────────────────────────────────────────────

const CARD_W = 180;
const CARD_H = 112;
const RING_RADIUS = 210;
const CANVAS_PAD = 100;
const MAX_CANVAS_COORD = 4000; // px upper bound for draggable card positions

/**
 * BFS radial layout.
 * Returns canvas-space CENTER coordinates {x, y} for each location so that
 * all values are ≥ (CARD_W/2 + CANVAS_PAD, CARD_H/2 + CANVAS_PAD).
 * No external offset variable needed — positions are ready to use directly.
 */
function computeBfsLayout(
  locations: Record<string, ZoneLocation>,
): Record<string, { x: number; y: number }> {
  const ids = Object.keys(locations);
  if (ids.length === 0) return {};

  const startId =
    Object.values(locations).find((l) => (l.anomaly_activity ?? 0) <= 3)?.id ?? ids[0];

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

  const disconnected = ids.filter((id) => !visited.has(id));
  if (disconnected.length > 0) levels.push(disconnected);

  // Raw radial positions centred at (0, 0)
  const raw: Record<string, { x: number; y: number }> = {};
  levels.forEach((level, li) => {
    const r = li === 0 ? 0 : RING_RADIUS * li;
    level.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / level.length - Math.PI / 2;
      raw[id] = {
        x: Math.round(r * Math.cos(angle)),
        y: Math.round(r * Math.sin(angle)),
      };
    });
  });

  // Normalise so min values ≥ CARD_W/2 + CANVAS_PAD
  const xs = Object.values(raw).map((p) => p.x);
  const ys = Object.values(raw).map((p) => p.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const shiftX = CARD_W / 2 + CANVAS_PAD - minX;
  const shiftY = CARD_H / 2 + CANVAS_PAD - minY;

  return Object.fromEntries(
    Object.entries(raw).map(([id, p]) => [id, { x: p.x + shiftX, y: p.y + shiftY }]),
  );
}

// ─── Constants ────────────────────────────────────────────────────────────────

const TERRAIN_TYPE_COLOR: Record<string, string> = {
  plain: '#84cc16',
  hills: '#3b82f6',
  slag_heaps: '#94a3b8',
  industrial: '#f59e0b',
  urban: '#a855f7',
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function DebugMapPage({ zoneState, currentLocId, sendCommand }: Props) {
  const [selectedLocId, setSelectedLocId] = useState<string | null>(null);

  // ── Location edit / create modals ─────────────────────────────────────────
  const [editingLocId, setEditingLocId] = useState<string | null>(null);
  const [creatingLoc, setCreatingLoc] = useState(false);

  // ── Canvas pan ────────────────────────────────────────────────────────────
  // panOffset is the translation (in px) of the canvas viewport.
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef<{
    startPtrX: number;
    startPtrY: number;
    startPanX: number;
    startPanY: number;
  } | null>(null);
  const panOffsetRef = useRef(panOffset);
  panOffsetRef.current = panOffset;

  // ── Drag ─────────────────────────────────────────────────────────────────
  // dragOverrides: user-defined card CENTER positions in canvas-space pixels.
  // Seeded from persisted debug_layout.positions so positions survive reload.
  const [dragOverrides, setDragOverrides] = useState<Record<string, { x: number; y: number }>>(
    () => zoneState.debug_layout?.positions ?? {},
  );
  const dragRef = useRef<{
    id: string;
    startPtrX: number;
    startPtrY: number;
    startCardX: number;
    startCardY: number;
    hasMoved: boolean;
  } | null>(null);

  // ── Link mode ────────────────────────────────────────────────────────────
  const [linkMode, setLinkMode] = useState(false);
  const [linkSource, setLinkSource] = useState<string | null>(null);

  // ── Local (editable) connections ─────────────────────────────────────────
  // Seeded from zoneState which already contains any previously-persisted edits.
  const [localConns, setLocalConns] = useState<Record<string, LocationConn[]>>(() =>
    Object.fromEntries(
      Object.entries(zoneState.locations).map(([id, loc]) => [id, [...loc.connections]]),
    ),
  );

  // ── Sync new locations into dragOverrides + localConns after CRUD ops ──────
  // After debug_create_location, zoneState gets a new entry; we need to
  // incorporate its persisted position and open an empty conns slot.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const locKeysStr = JSON.stringify(Object.keys(zoneState.locations).sort());
  useEffect(() => {
    const persistedPos = zoneState.debug_layout?.positions ?? {};
    setDragOverrides((prev) => {
      const incoming: Record<string, { x: number; y: number }> = {};
      for (const [id, pos] of Object.entries(persistedPos)) {
        if (!(id in prev)) incoming[id] = pos as { x: number; y: number };
      }
      return Object.keys(incoming).length ? { ...prev, ...incoming } : prev;
    });
    setLocalConns((prev) => {
      const incoming: Record<string, LocationConn[]> = {};
      for (const [id, loc] of Object.entries(zoneState.locations)) {
        if (!(id in prev)) incoming[id] = [...(loc as ZoneLocation).connections];
      }
      return Object.keys(incoming).length ? { ...prev, ...incoming } : prev;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locKeysStr]);

  // ── Persistence ──────────────────────────────────────────────────────────
  // Saving flag and error shown in toolbar
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Build the serializable connections map (all locations) and call backend
  const persistMap = useCallback(
    async (
      positions: Record<string, { x: number; y: number }>,
      conns: Record<string, LocationConn[]>,
    ) => {
      setSaving(true);
      setSaveError(null);
      try {
        // Serialize connections: plain objects, no class instances
        const serialisedConns: Record<string, Array<{ to: string; type: string }>> = {};
        for (const [id, cs] of Object.entries(conns)) {
          serialisedConns[id] = cs.map((c) => ({ to: c.to, type: c.type }));
        }
        await sendCommand('debug_update_map', {
          positions,
          connections: serialisedConns,
        });
      } catch (err: unknown) {
        const msg = (err as { message?: string })?.message ?? 'Save failed';
        setSaveError(msg);
      } finally {
        setSaving(false);
      }
    },
    [sendCommand],
  );

  // ── Layout ───────────────────────────────────────────────────────────────
  const layoutPositions = useMemo(
    () => computeBfsLayout(zoneState.locations),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(Object.keys(zoneState.locations))],
  );

  // Merge layout + user overrides
  const effectivePos = useMemo(
    () => ({ ...layoutPositions, ...dragOverrides }),
    [layoutPositions, dragOverrides],
  );

  // Canvas dimensions auto-expand when cards are dragged outward
  const canvasW = useMemo(() => {
    const vals = Object.values(effectivePos);
    if (!vals.length) return 400;
    return Math.max(400, ...vals.map((p) => p.x + CARD_W / 2 + CANVAS_PAD));
  }, [effectivePos]);

  const canvasH = useMemo(() => {
    const vals = Object.values(effectivePos);
    if (!vals.length) return 300;
    return Math.max(300, ...vals.map((p) => p.y + CARD_H / 2 + CANVAS_PAD));
  }, [effectivePos]);

  // ── Refs that always hold the latest state (for use inside callbacks) ─────
  const dragOverridesRef = useRef(dragOverrides);
  dragOverridesRef.current = dragOverrides;

  const localConnsRef = useRef(localConns);
  localConnsRef.current = localConns;

  // ── Link/click logic ─────────────────────────────────────────────────────
  const toggleLink = useCallback(
    (aId: string, bId: string) => {
      const prev = localConnsRef.current;
      const aC = prev[aId] ?? [];
      const bC = prev[bId] ?? [];
      const exists = aC.some((c) => c.to === bId);
      const newConns = exists
        ? {
            ...prev,
            [aId]: aC.filter((c) => c.to !== bId),
            [bId]: bC.filter((c) => c.to !== aId),
          }
        : {
            ...prev,
            [aId]: [...aC, { to: bId, type: 'normal' }],
            [bId]: [...bC, { to: aId, type: 'normal' }],
          };
      setLocalConns(newConns);
      persistMap(dragOverridesRef.current, newConns);
    },
    [persistMap],
  );

  const deleteConnection = useCallback((fromId: string, toId: string) => {
    const prev = localConnsRef.current;
    const newConns = {
      ...prev,
      [fromId]: (prev[fromId] ?? []).filter((c) => c.to !== toId),
      [toId]: (prev[toId] ?? []).filter((c) => c.to !== fromId),
    };
    setLocalConns(newConns);
    persistMap(dragOverridesRef.current, newConns);
  }, [persistMap]);

  // ── Pointer handlers on each card ────────────────────────────────────────
  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, id: string) => {
      if (linkMode) return; // clicks handled in onPointerUp
      e.preventDefault();
      e.stopPropagation();
      const pos = effectivePos[id] ?? { x: 0, y: 0 };
      dragRef.current = {
        id,
        startPtrX: e.clientX,
        startPtrY: e.clientY,
        startCardX: pos.x,
        startCardY: pos.y,
        hasMoved: false,
      };
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    },
    [linkMode, effectivePos],
  );

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startPtrX;
    const dy = e.clientY - d.startPtrY;
    if (!d.hasMoved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
    d.hasMoved = true;
    setDragOverrides((prev) => ({
      ...prev,
      [d.id]: {
        // Keep card within [half-card-width, MAX_CANVAS_COORD] so it stays accessible
        x: Math.max(CARD_W / 2, Math.min(MAX_CANVAS_COORD, d.startCardX + dx)),
        y: Math.max(CARD_H / 2, Math.min(MAX_CANVAS_COORD, d.startCardY + dy)),
      },
    }));
  }, []);

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, id: string) => {
      const d = dragRef.current;
      dragRef.current = null;
      if (d?.hasMoved) {
        // Compute the exact final position from the pointer event to avoid
        // reading a potentially-stale dragOverridesRef (which is only updated
        // on re-render, and the last pointermove's render may not have
        // completed before pointerup fires).
        const finalX = Math.max(CARD_W / 2, Math.min(MAX_CANVAS_COORD, d.startCardX + (e.clientX - d.startPtrX)));
        const finalY = Math.max(CARD_H / 2, Math.min(MAX_CANVAS_COORD, d.startCardY + (e.clientY - d.startPtrY)));
        const finalPositions = { ...dragOverridesRef.current, [d.id]: { x: finalX, y: finalY } };
        // Update state immediately with the exact final position
        setDragOverrides(finalPositions);
        persistMap(finalPositions, localConnsRef.current);
        return;
      }

      // It was a click
      if (linkMode) {
        if (!linkSource) {
          setLinkSource(id);
        } else if (linkSource === id) {
          setLinkSource(null);
        } else {
          toggleLink(linkSource, id);
          setLinkSource(null);
        }
      } else {
        setSelectedLocId((prev) => (prev === id ? null : id));
      }
    },
    [linkMode, linkSource, toggleLink, persistMap],
  );

  // When exiting link mode, clear source
  const handleToggleLinkMode = useCallback(() => {
    setLinkMode((prev) => {
      if (prev) setLinkSource(null);
      return !prev;
    });
  }, []);

  // ── Canvas pan handlers ───────────────────────────────────────────────────
  // These fire on the viewport div. Cards stop propagation in their
  // onPointerDown, so pan only starts when the canvas background is pressed.
  const handleCanvasPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // If a card drag is already active, don't start pan
      if (dragRef.current) return;
      e.preventDefault();
      panRef.current = {
        startPtrX: e.clientX,
        startPtrY: e.clientY,
        startPanX: panOffsetRef.current.x,
        startPanY: panOffsetRef.current.y,
      };
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      setIsPanning(true);
    },
    [],
  );

  const handleCanvasPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const d = panRef.current;
      if (!d) return;
      setPanOffset({
        x: d.startPanX + e.clientX - d.startPtrX,
        y: d.startPanY + e.clientY - d.startPtrY,
      });
    },
    [],
  );

  const handleCanvasPanEnd = useCallback(() => {
    panRef.current = null;
    setIsPanning(false);
  }, []);

  // ── Location CRUD ─────────────────────────────────────────────────────────
  const handleSaveEdit = useCallback(
    async (data: { name: string; terrainType: string; anomalyActivity: number; dominantAnomalyType: string }) => {
      if (!editingLocId) return;
      await sendCommand('debug_update_location', {
        loc_id: editingLocId,
        name: data.name,
        terrain_type: data.terrainType,
        anomaly_activity: data.anomalyActivity,
        dominant_anomaly_type: data.dominantAnomalyType || null,
      });
    },
    [editingLocId, sendCommand],
  );

  const handleSaveCreate = useCallback(
    async (data: { name: string; terrainType: string; anomalyActivity: number; dominantAnomalyType: string }) => {
      // Place the new card just below the current canvas bottom-center so it's visible
      const pos = {
        x: Math.max(CARD_W / 2 + CANVAS_PAD, canvasW / 2),
        y: canvasH + CARD_H / 2 + CANVAS_PAD,
      };
      await sendCommand('debug_create_location', {
        name: data.name,
        terrain_type: data.terrainType,
        anomaly_activity: data.anomalyActivity,
        dominant_anomaly_type: data.dominantAnomalyType || null,
        position: pos,
      });
    },
    [sendCommand, canvasW, canvasH],
  );

  const detailLoc = selectedLocId ? zoneState.locations[selectedLocId] : null;
  const detailConns = selectedLocId ? (localConns[selectedLocId] ?? []) : [];

  return (
    <div style={s.page}>
      {/* ── Canvas ────────────────────────────────────────── */}
      <div style={s.canvasWrap}>
        {/* Toolbar */}
        <div style={s.toolbar}>
          <div style={s.legend}>
            {Object.entries(TERRAIN_TYPE_COLOR).map(([t, c]) => (
              <span key={t} style={s.legendItem}>
                <span style={{ ...s.legendDot, background: c }} />
                {TERRAIN_TYPE_LABELS[t] ?? t.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
          <div style={s.toolbarRight}>
            {saving && (
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>💾 Saving…</span>
            )}
            {saveError && (
              <span style={{ color: '#ef4444', fontSize: '0.68rem' }} title={saveError}>
                ⚠ Save failed
              </span>
            )}
            <button
              style={s.toolBtn}
              onClick={() => setCreatingLoc(true)}
              title="Create a new location"
            >
              ➕ Location
            </button>
            <button
              style={{
                ...s.toolBtn,
                ...(linkMode ? s.toolBtnActive : {}),
              }}
              onClick={handleToggleLinkMode}
              title="Toggle link-editing mode. In link mode: click a location to select it as source, then click another to create/remove a connection."
            >
              🔗 {linkMode ? 'Link mode ON' : 'Link mode'}
            </button>
            {(Object.keys(dragOverrides).length > 0) && (
              <button
                style={s.toolBtn}
                onClick={() => {
                  setDragOverrides({});
                  persistMap({}, localConnsRef.current);
                }}
                title="Reset all card positions to the auto-layout"
              >
                ↺ Reset layout
              </button>
            )}
            {(panOffset.x !== 0 || panOffset.y !== 0) && (
              <button
                style={s.toolBtn}
                onClick={() => setPanOffset({ x: 0, y: 0 })}
                title="Re-centre the canvas view"
              >
                ⊙ Re-centre
              </button>
            )}
          </div>
        </div>

        {linkMode && (
          <div style={s.linkHint}>
            {linkSource
              ? `📍 Source: ${zoneState.locations[linkSource]?.name ?? linkSource} — click another location to create/remove a connection, or click it again to deselect`
              : '🔗 Click a location card to select it as the connection source'}
          </div>
        )}

        <div
          style={{
            ...s.canvasScroll,
            cursor: isPanning ? 'grabbing' : linkMode ? 'crosshair' : 'grab',
          }}
          onPointerDown={handleCanvasPointerDown}
          onPointerMove={handleCanvasPointerMove}
          onPointerUp={handleCanvasPanEnd}
          onPointerCancel={handleCanvasPanEnd}
        >
          {/* Panning layer — translated by panOffset */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              transform: `translate(${panOffset.x}px, ${panOffset.y}px)`,
              willChange: 'transform',
            }}
          >
            {/* SVG connection lines */}
            <svg
              style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 0 }}
              width={canvasW}
              height={canvasH}
            >
              {/* Draw connections from localConns */}
              {Object.entries(localConns).flatMap(([locId, conns]) =>
                conns.map((conn) => {
                  // Deduplicate: only draw A→B when A < B
                  if (locId > conn.to) return null;
                  const a = effectivePos[locId];
                  const b = effectivePos[conn.to];
                  if (!a || !b) return null;
                  const isDangerous = conn.type === 'dangerous';
                  return (
                    <line
                      key={`${locId}--${conn.to}`}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
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
              const pos = effectivePos[id];
              if (!pos) return null;

              const isSelected = id === selectedLocId;
              const isCurrent = id === currentLocId;
              const isLinkSrc = id === linkSource;
              const isLinkTarget =
                linkSource && linkSource !== id
                  ? (localConns[linkSource] ?? []).some((c) => c.to === id)
                  : false;

              const aliveAgents = loc.agents.filter(
                (aid) => zoneState.agents[aid]?.is_alive,
              ).length;
              const aliveMutantsOnCard = loc.agents.filter(
                (aid) => zoneState.mutants[aid]?.is_alive,
              ).length;
              const traderCount = Object.values(zoneState.traders).filter(
                (t) => t.location_id === id,
              ).length;

              // ── Border: avoid border shorthand + borderLeft conflict ────────
              // Using explicit individual side borders fixes the "stripe
              // disappears on deselect" bug caused by React only updating the
              // changed shorthand property, which resets border-left-width.
              const sideColor = isLinkSrc
                ? '#f59e0b'
                : isSelected
                ? '#60a5fa'
                : isCurrent
                ? '#22c55e'
                : '#1e293b';
              const stripColor = TERRAIN_TYPE_COLOR[loc.terrain_type ?? ''] ?? '#334155';

              return (
                <div
                  key={id}
                  title={`${loc.name} — ${linkMode ? 'click to link' : 'click for details, drag to move'}`}
                  style={{
                    position: 'absolute',
                    left: pos.x - CARD_W / 2,
                    top: pos.y - CARD_H / 2,
                    width: CARD_W,
                    background: isCurrent ? '#0d2a1a' : '#0f172a',
                    borderRadius: 10,
                    padding: '0.55rem 0.65rem',
                    // Individual side borders — avoids the CSS shorthand reset bug
                    borderTop: `1px solid ${sideColor}`,
                    borderRight: `1px solid ${sideColor}`,
                    borderBottom: `1px solid ${sideColor}`,
                    borderLeft: `4px solid ${stripColor}`,
                    cursor: linkMode ? 'crosshair' : 'grab',
                    boxShadow: isLinkSrc
                      ? '0 0 0 2px #f59e0b'
                      : isLinkTarget
                      ? '0 0 0 2px #a855f7'
                      : isSelected
                      ? '0 0 0 2px #3b82f6'
                      : isCurrent
                      ? '0 0 0 1px #22c55e44'
                      : 'none',
                    transition: 'box-shadow 0.15s',
                    zIndex: isSelected || isLinkSrc ? 10 : 1,
                    userSelect: 'none',
                    touchAction: 'none',
                  }}
                  onPointerDown={(e) => handlePointerDown(e, id)}
                  onPointerMove={handlePointerMove}
                  onPointerUp={(e) => handlePointerUp(e, id)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
                    <span style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.82rem', lineHeight: 1.2, flex: 1 }}>
                      {loc.name}
                    </span>
                    {/* Edit button — stops drag + link capture so it fires as a click */}
                    <button
                      style={s.editBtn}
                      title="Edit location"
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); setEditingLocId(id); }}
                    >
                      ✏
                    </button>
                  </div>
                  <div style={{ color: '#475569', fontSize: '0.68rem', marginTop: 2 }}>
                    {TERRAIN_TYPE_LABELS[loc.terrain_type ?? ''] ?? (loc.terrain_type ?? '—')}
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 5 }}>
                    {isCurrent && <Badge bg="#166534" color="#86efac">📍 You</Badge>}
                    {aliveAgents > 0 && <Badge bg="#334155" color="#94a3b8">👥 {aliveAgents}</Badge>}
                    {aliveMutantsOnCard > 0 && <Badge bg="#7f1d1d" color="#fca5a5">☣️ {aliveMutantsOnCard}</Badge>}
                    {traderCount > 0 && <Badge bg="#1e293b" color="#94a3b8">🏪 {traderCount}</Badge>}
                    {loc.artifacts.length > 0 && (
                      <Badge bg="#312e81" color="#a5b4fc">💎 {loc.artifacts.length}</Badge>
                    )}
                    {(loc.anomaly_activity ?? 0) > 0 && (
                      <Badge bg="#4a044e" color="#e879f9">☢ {loc.anomaly_activity}</Badge>
                    )}
                    {loc.items.length > 0 && (
                      <Badge bg="#1c1917" color="#78716c">📦 {loc.items.length}</Badge>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          </div>{/* end panning layer */}
        </div>
      </div>

      {/* ── Detail panel ──────────────────────────────────── */}
      <div style={s.detailPanel}>
        {detailLoc ? (
          <LocationDetailPanel
            loc={detailLoc}
            conns={detailConns}
            zoneState={zoneState}
            onClose={() => setSelectedLocId(null)}
            onEdit={() => setEditingLocId(selectedLocId!)}
            onSpawnStalker={async (name) => {
              await sendCommand('debug_spawn_stalker', { loc_id: selectedLocId!, name });
            }}
            onSpawnMutant={async (mutantType) => {
              await sendCommand('debug_spawn_mutant', { loc_id: selectedLocId!, mutant_type: mutantType });
            }}
            onDeleteConnection={(toId) => deleteConnection(selectedLocId!, toId)}
          />
        ) : (
          <EmptyDetailHint totalLocs={Object.keys(zoneState.locations).length} />
        )}
      </div>

      {/* ── Location edit modal ───────────────────────────────── */}
      {editingLocId && zoneState.locations[editingLocId] && (
        <LocationModal
          mode="edit"
          initialName={zoneState.locations[editingLocId].name}
          initialTerrainType={zoneState.locations[editingLocId].terrain_type ?? 'plain'}
          initialAnomalyActivity={zoneState.locations[editingLocId].anomaly_activity ?? 0}
          initialDominantAnomalyType={zoneState.locations[editingLocId].dominant_anomaly_type ?? ''}
          locId={editingLocId}
          onClose={() => setEditingLocId(null)}
          onSave={handleSaveEdit}
        />
      )}

      {/* ── Location create modal ─────────────────────────────── */}
      {creatingLoc && (
        <LocationModal
          mode="create"
          onClose={() => setCreatingLoc(false)}
          onSave={handleSaveCreate}
        />
      )}
    </div>
  );
}

// ─── Location detail panel ────────────────────────────────────────────────────

function LocationDetailPanel({
  loc,
  conns,
  zoneState,
  onClose,
  onEdit,
  onSpawnStalker,
  onSpawnMutant,
  onDeleteConnection,
}: {
  loc: ZoneLocation;
  conns: LocationConn[];
  zoneState: ZoneMapState;
  onClose: () => void;
  onEdit: () => void;
  onSpawnStalker: (name: string) => Promise<void>;
  onSpawnMutant: (mutantType: string) => Promise<void>;
  onDeleteConnection: (toId: string) => void;
}) {
  const [showSpawnModal, setShowSpawnModal] = useState<'stalker' | 'mutant' | null>(null);
  const stalkers = loc.agents
    .map((id) => zoneState.agents[id])
    .filter(Boolean);
  const mutants = loc.agents
    .map((id) => zoneState.mutants[id])
    .filter(Boolean);
  const aliveMutants = mutants.filter((m) => m.is_alive);
  const deadMutants = mutants.filter((m) => !m.is_alive);
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
            {TERRAIN_TYPE_LABELS[loc.terrain_type ?? ''] ?? (loc.terrain_type ?? '—')}
            {(loc.anomaly_activity ?? 0) > 0 && (
              <span style={{ color: '#a855f7', marginLeft: 6 }}>· ☢ {loc.anomaly_activity}</span>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
          <button
            onClick={onEdit}
            style={s.editDetailBtn}
            title="Редактировать локацию"
          >
            ✏ Редактировать
          </button>
          <button onClick={onClose} style={s.closeBtn}>✕</button>
        </div>
      </div>

      {/* New location properties */}
      <Section label="🌍 Характеристики">
        {loc.terrain_type && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Местность</span>
            <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>
              {TERRAIN_TYPE_LABELS[loc.terrain_type] ?? loc.terrain_type}
            </span>
          </DetailRow>
        )}
        <DetailRow>
          <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Аном. активность</span>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ flex: 1, height: 5, background: '#0f172a', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${((loc.anomaly_activity ?? 0) / 10) * 100}%`, background: '#a855f7', borderRadius: 3 }} />
            </div>
            <span style={{ color: '#a855f7', fontSize: '0.7rem', width: 24, textAlign: 'right', flexShrink: 0 }}>{loc.anomaly_activity ?? 0}</span>
          </div>
        </DetailRow>
        {loc.dominant_anomaly_type && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Тип аномалий</span>
            <span style={{ color: '#e879f9', fontSize: '0.8rem' }}>{loc.dominant_anomaly_type}</span>
          </DetailRow>
        )}
        <DetailRow>
          <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Артефактов</span>
          <span style={{ color: '#a5b4fc', fontSize: '0.8rem' }}>{loc.artifacts.length}</span>
        </DetailRow>
      </Section>

      {/* Connections */}
      <Section label="🔗 Connections">
        {conns.length === 0 ? (
          <EmptyRow />
        ) : (
          conns.map((c) => {
            const target = zoneState.locations[c.to];
            return (
              <DetailRow key={c.to}>
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem', flex: 1 }}>
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
                <button
                  style={s.connDelBtn}
                  onClick={() => onDeleteConnection(c.to)}
                  title="Delete this connection"
                >
                  ✕
                </button>
              </DetailRow>
            );
          })
        )}
      </Section>

      {/* Stalkers — always show with count, full list */}
      <Section label={`🧍 Сталкеры (${stalkers.length})`}>
        {stalkers.length === 0 ? (
          <EmptyRow />
        ) : (
          stalkers.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: a.is_alive ? '#f8fafc' : '#475569', fontSize: '0.8rem', flex: 1 }}>
                {a.name}
                {!a.is_alive && (
                  <span style={{ color: '#ef4444', fontSize: '0.65rem', marginLeft: 4 }}>
                    (мёртв)
                  </span>
                )}
              </span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>
                {a.hp}/{a.max_hp} HP
              </span>
              <span style={{
                background: a.controller.kind === 'human' ? '#1d4ed8' : '#1e293b',
                color: a.controller.kind === 'human' ? '#bfdbfe' : '#475569',
                borderRadius: 4,
                padding: '0 0.3rem',
                fontSize: '0.62rem',
                flexShrink: 0,
              }}>
                {a.controller.kind === 'human' ? '👤' : '🤖'}
              </span>
            </DetailRow>
          ))
        )}
      </Section>

      {/* Mutants — show count stats + list */}
      <Section label={`☣️ Мутанты (${mutants.length})`}>
        {mutants.length === 0 ? (
          <EmptyRow />
        ) : (
          <>
            <div style={{ display: 'flex', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
              <span style={{ color: '#fca5a5', fontSize: '0.72rem' }}>Живых: {aliveMutants.length}</span>
              {deadMutants.length > 0 && (
                <span style={{ color: '#475569', fontSize: '0.72rem' }}>Мёртвых: {deadMutants.length}</span>
              )}
            </div>
            {mutants.map((m) => (
              <DetailRow key={m.id}>
                <span style={{ color: m.is_alive ? '#fca5a5' : '#475569', fontSize: '0.8rem', flex: 1 }}>
                  {m.name}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem' }}>
                  {m.hp}/{m.max_hp} HP
                </span>
              </DetailRow>
            ))}
          </>
        )}
      </Section>

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

      {/* Spawn controls */}
      <Section label="⚡ Spawn">
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            style={s.spawnBtn}
            onClick={() => setShowSpawnModal('stalker')}
          >
            👤 Spawn Stalker
          </button>
          <button
            style={s.spawnBtn}
            onClick={() => setShowSpawnModal('mutant')}
          >
            ☣️ Spawn Mutant
          </button>
        </div>
      </Section>

      <div style={{ color: '#1e293b', fontSize: '0.62rem', marginTop: 6 }}>id: {loc.id}</div>

      {showSpawnModal === 'stalker' && (
        <SpawnStalkerModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (name) => { await onSpawnStalker(name); setShowSpawnModal(null); }}
        />
      )}
      {showSpawnModal === 'mutant' && (
        <SpawnMutantModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (mutantType) => { await onSpawnMutant(mutantType); setShowSpawnModal(null); }}
        />
      )}
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
      <p style={s.emptyDetailHint}>Кликните на карточку локации на карте, чтобы увидеть детали и кнопку редактирования.</p>
      <hr style={s.hr} />
      <div style={{ color: '#334155', fontSize: '0.7rem' }}>
        {totalLocs} locations on map
      </div>
    </div>
  );
}

// ─── Location modal (edit & create) ──────────────────────────────────────────

const TERRAIN_TYPES = [
  'plain', 'hills', 'slag_heaps', 'industrial', 'urban',
] as const;

const TERRAIN_TYPE_LABELS: Record<string, string> = {
  plain: 'Равнина',
  hills: 'Холмы',
  slag_heaps: 'Террикони',
  industrial: 'Промышленная застройка',
  urban: 'Городская застройка',
};

const DOMINANT_ANOMALY_OPTIONS = [
  '', 'chemical', 'electric', 'gravitational', 'thermal', 'radioactive',
] as const;

function LocationModal({
  mode,
  initialName = '',
  initialTerrainType = 'plain',
  initialAnomalyActivity = 0,
  initialDominantAnomalyType = '',
  locId,
  onClose,
  onSave,
}: {
  mode: 'edit' | 'create';
  initialName?: string;
  initialTerrainType?: string;
  initialAnomalyActivity?: number;
  initialDominantAnomalyType?: string;
  locId?: string;
  onClose: () => void;
  onSave: (data: { name: string; terrainType: string; anomalyActivity: number; dominantAnomalyType: string }) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [terrainType, setTerrainType] = useState(initialTerrainType);
  const [anomalyActivity, setAnomalyActivity] = useState(initialAnomalyActivity);
  const [dominantAnomalyType, setDominantAnomalyType] = useState(initialDominantAnomalyType);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setErr('Name cannot be empty'); return; }
    setSaving(true); setErr(null);
    try {
      await onSave({ name: trimmed, terrainType, anomalyActivity, dominantAnomalyType });
      onClose();
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={s.modalOverlay}
      onMouseDown={onClose}
    >
      <div
        style={s.modal}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>
          {mode === 'edit' ? '✏ Edit Location' : '➕ New Location'}
        </h3>
        {locId && (
          <div style={{ color: '#475569', fontSize: '0.65rem', marginBottom: 10 }}>ID: {locId}</div>
        )}

        <label style={s.modalLabel}>Name</label>
        <input
          style={s.modalInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Location name"
          autoFocus
        />

        <label style={s.modalLabel}>Terrain type</label>
        <select
          style={s.modalInput}
          value={terrainType}
          onChange={(e) => setTerrainType(e.target.value)}
        >
          {TERRAIN_TYPES.map((t) => (
            <option key={t} value={t}>{TERRAIN_TYPE_LABELS[t] ?? t}</option>
          ))}
        </select>

        <label style={s.modalLabel}>Anomaly activity: {anomalyActivity}</label>
        <input
          type="range"
          min={0}
          max={10}
          step={1}
          value={anomalyActivity}
          onChange={(e) => setAnomalyActivity(Number(e.target.value))}
          style={{ width: '100%', accentColor: '#a855f7', marginBottom: 10 }}
        />

        <label style={s.modalLabel}>Dominant anomaly type</label>
        <select
          style={s.modalInput}
          value={dominantAnomalyType}
          onChange={(e) => setDominantAnomalyType(e.target.value)}
        >
          {DOMINANT_ANOMALY_OPTIONS.map((t) => (
            <option key={t} value={t}>{t === '' ? '— none —' : t}</option>
          ))}
        </select>

        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Spawn Modals ─────────────────────────────────────────────────────────────

const MUTANT_TYPE_OPTIONS = [
  'blind_dog', 'flesh', 'zombie', 'bloodsucker', 'psi_controller',
] as const;

const MUTANT_TYPE_LABELS: Record<string, string> = {
  blind_dog: 'Blind Dog',
  flesh: 'Flesh',
  zombie: 'Zombie',
  bloodsucker: 'Bloodsucker',
  psi_controller: 'Psi-Controller',
};

function SpawnStalkerModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true); setErr(null);
    try {
      await onSave(name.trim());
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Spawn failed');
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>👤 Spawn Stalker</h3>
        <label style={s.modalLabel}>Имя (необязательно)</label>
        <input
          style={s.modalInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Оставьте пустым для случайного"
          autoFocus
        />
        <div style={{ color: '#64748b', fontSize: '0.68rem', marginTop: 4, marginBottom: 8 }}>
          Фракция, снаряжение и навыки генерируются автоматически.
        </div>
        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Spawning…' : 'Spawn'}
          </button>
        </div>
      </div>
    </div>
  );
}

function SpawnMutantModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (mutantType: string) => Promise<void>;
}) {
  const [mutantType, setMutantType] = useState<string>(MUTANT_TYPE_OPTIONS[0]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true); setErr(null);
    try {
      await onSave(mutantType);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Spawn failed');
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>☣️ Spawn Mutant</h3>
        <label style={s.modalLabel}>Тип мутанта</label>
        <select
          style={s.modalInput}
          value={mutantType}
          onChange={(e) => setMutantType(e.target.value)}
          autoFocus
        >
          {MUTANT_TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>{MUTANT_TYPE_LABELS[t] ?? t}</option>
          ))}
        </select>
        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Spawning…' : 'Spawn'}
          </button>
        </div>
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
  toolbar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
    padding: '6px 10px',
    background: '#060b14',
    borderRadius: 8,
    border: '1px solid #1e293b',
  },
  legend: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '6px 14px',
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
  toolbarRight: {
    display: 'flex',
    gap: 6,
    alignItems: 'center',
    flexShrink: 0,
  },
  toolBtn: {
    padding: '0.25rem 0.65rem',
    background: '#0f172a',
    color: '#94a3b8',
    border: '1px solid #334155',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.72rem',
    fontWeight: 600,
  },
  toolBtnActive: {
    background: '#1c2a1a',
    color: '#86efac',
    borderColor: '#22c55e',
  },
  linkHint: {
    background: '#0d1a0d',
    border: '1px solid #166534',
    borderRadius: 7,
    padding: '0.35rem 0.7rem',
    color: '#86efac',
    fontSize: '0.75rem',
  },
  canvasScroll: {
    position: 'relative',
    overflow: 'hidden',
    background: '#060b14',
    borderRadius: 10,
    border: '1px solid #1e293b',
    height: 'min(620px, 72vh)',
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
  connDelBtn: {
    background: 'transparent',
    border: 'none',
    color: '#475569',
    cursor: 'pointer',
    fontSize: '0.7rem',
    padding: '0 0.2rem',
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
  editBtn: {
    background: '#1e3a5f',
    border: '1px solid #3b82f6',
    color: '#93c5fd',
    cursor: 'pointer',
    fontSize: '0.68rem',
    padding: '0.15rem 0.4rem',
    borderRadius: 4,
    lineHeight: 1.4,
    flexShrink: 0,
  },
  editDetailBtn: {
    background: '#1e3a5f',
    border: '1px solid #3b82f6',
    color: '#93c5fd',
    cursor: 'pointer',
    fontSize: '0.72rem',
    padding: '0.25rem 0.6rem',
    borderRadius: 5,
    lineHeight: 1.4,
    flexShrink: 0,
    fontWeight: 600,
  },
  modalOverlay: {
    position: 'fixed' as const,
    inset: 0,
    background: 'rgba(0,0,0,0.65)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
  },
  modal: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 12,
    padding: '1.25rem 1.5rem',
    width: 340,
    maxWidth: '95vw',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 4,
  },
  modalLabel: {
    color: '#64748b',
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
    marginTop: 8,
    marginBottom: 3,
    display: 'block',
  },
  modalInput: {
    width: '100%',
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: 6,
    color: '#f1f5f9',
    fontSize: '0.82rem',
    padding: '0.4rem 0.55rem',
    boxSizing: 'border-box' as const,
  },
  modalSaveBtn: {
    padding: '0.4rem 1rem',
    background: '#1d4ed8',
    color: '#fff',
    border: 'none',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.8rem',
    fontWeight: 600,
  },
  modalCancelBtn: {
    padding: '0.4rem 0.8rem',
    background: '#1e293b',
    color: '#94a3b8',
    border: '1px solid #334155',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.8rem',
  },
  spawnBtn: {
    background: '#1e3a5f',
    border: '1px solid #3b82f6',
    color: '#93c5fd',
    cursor: 'pointer',
    fontSize: '0.72rem',
    padding: '0.3rem 0.6rem',
    borderRadius: 5,
    fontWeight: 600,
    flex: 1,
  },
};
