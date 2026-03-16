/**
 * DebugMapPage — free-form canvas for inspecting and editing the Zone Stalkers world map.
 *
 * Features:
 *  - BFS radial initial layout (restored from persisted positions on reload)
 *  - Drag-and-drop: cards can be freely moved on the canvas (pointer capture)
 *  - Region groups: dragging a region moves all its locations together
 *  - Link mode: click a source card then a target card to create/delete a
 *    bidirectional connection; remove individual connections from the detail panel
 *  - Location & region detail panels with full CRUD
 *  - All edits (positions + connections + regions) are persisted to the backend via
 *    the debug_update_map command so they survive page reloads
 */
import React, { useMemo, useState, useRef, useCallback, useEffect } from 'react';

import type { DebugMapPageProps, LocationConn, ZoneLocation } from './debugMap/types';
import {
  CARD_W, CARD_H, CANVAS_PAD, MAX_CANVAS_COORD, REGION_PAD,
  computeBfsLayout,
  TERRAIN_TYPE_COLOR, TERRAIN_TYPE_LABELS,
  REGION_LABELS, REGION_BG_COLOR, REGION_BORDER_COLOR, REGION_COLOR_PALETTE,
} from './debugMap/constants';
import { s } from './debugMap/styles';
import { Badge, LocationDetailPanel, RegionDetailPanel, EmptyDetailHint } from './debugMap/DetailPanels';
import { LocationModal } from './debugMap/Modals';
import AgentProfileModal from './AgentProfileModal';
import type { AgentForProfile } from './AgentProfileModal';

// ─── Main component ───────────────────────────────────────────────────────────

/** Format a count of game turns (minutes) as "X д Y ч Z мин". */
function formatTurns(turns: number): string {
  if (turns <= 0) return '0 мин';
  const days = Math.floor(turns / (60 * 24));
  const hours = Math.floor((turns % (60 * 24)) / 60);
  const mins = turns % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days} д`);
  if (hours > 0) parts.push(`${hours} ч`);
  if (mins > 0 || parts.length === 0) parts.push(`${mins} мин`);
  return parts.join(' ');
}

// ─── Helper ───────────────────────────────────────────────────────────────────

/** Resolves the bg/border colours for a region, falling back to static maps. */
function getRegionColors(
  region: string,
  localRegions: Record<string, { name: string; colorIndex: number }>,
): { bg: string; border: string } {
  return (
    REGION_COLOR_PALETTE[localRegions[region]?.colorIndex ?? 0] ?? {
      bg: REGION_BG_COLOR[region] ?? '#1a1a2a',
      border: REGION_BORDER_COLOR[region] ?? '#334155',
    }
  );
}

export default function DebugMapPage({ matchId, zoneState, currentLocId, sendCommand, contextId }: DebugMapPageProps) {
  const [selectedLocId, setSelectedLocId] = useState<string | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<string | null>(null);

  // ── Agent profile modal (click stalker in location detail panel) ──────────
  const [profileAgentId, setProfileAgentId] = useState<string | null>(null);
  // ── Trader profile modal (click trader row in location detail panel) ───────
  const [profileTraderId, setProfileTraderId] = useState<string | null>(null);

  // ── Location edit / create modals ─────────────────────────────────────────
  const [editingLocId, setEditingLocId] = useState<string | null>(null);
  const [creatingLoc, setCreatingLoc] = useState(false);

  // ── Viewport persistence (pan + zoom saved per-match in localStorage) ────────
  const _vpKey = `zs_viewport_${matchId}`;
  const _loadVp = (): { x: number; y: number; zoom: number } => {
    try {
      const raw = localStorage.getItem(_vpKey);
      if (raw) {
        const v = JSON.parse(raw) as { x?: number; y?: number; zoom?: number };
        return { x: v.x ?? 0, y: v.y ?? 0, zoom: v.zoom ?? 1.0 };
      }
    } catch { /* ignore */ }
    return { x: 0, y: 0, zoom: 1.0 };
  };
  const _savedVp = _loadVp();
  const _vpTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Canvas pan ────────────────────────────────────────────────────────────
  // panOffset is the translation (in px) of the canvas viewport.
  const [panOffset, setPanOffset] = useState({ x: _savedVp.x, y: _savedVp.y });
  const [isPanning, setIsPanning] = useState(false);

  // ── Zoom ─────────────────────────────────────────────────────────────────
  const [zoom, setZoom] = useState(_savedVp.zoom);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const canvasScrollRef = useRef<HTMLDivElement>(null);

  // ── Fullscreen ────────────────────────────────────────────────────────────
  const [isFullscreen, setIsFullscreen] = useState(false);
  // pageWrapRef is on the outer page div so that the detail panel and modals
  // are included inside the fullscreen element.
  const pageWrapRef = useRef<HTMLDivElement>(null);

  // ── Import file input ref ─────────────────────────────────────────────────
  const importInputRef = useRef<HTMLInputElement>(null);
  const panRef = useRef<{
    startPtrX: number;
    startPtrY: number;
    startPanX: number;
    startPanY: number;
  } | null>(null);
  const panOffsetRef = useRef(panOffset);
  panOffsetRef.current = panOffset;

  // ── Persist pan + zoom to localStorage (debounced 400 ms) ─────────────────
  useEffect(() => {
    if (_vpTimerRef.current) clearTimeout(_vpTimerRef.current);
    _vpTimerRef.current = setTimeout(() => {
      try {
        localStorage.setItem(_vpKey, JSON.stringify({ x: panOffset.x, y: panOffset.y, zoom }));
      } catch { /* storage full or unavailable — ignore */ }
    }, 400);
    return () => { if (_vpTimerRef.current) clearTimeout(_vpTimerRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panOffset.x, panOffset.y, zoom]);

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

  // ── Region drag ───────────────────────────────────────────────────────────
  const regionDragRef = useRef<{
    region: string;
    startPtrX: number;
    startPtrY: number;
    startPositions: Record<string, { x: number; y: number }>;
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

  // ── Local (editable) regions ──────────────────────────────────────────────
  // Seeded from zoneState.debug_layout?.regions, then the predefined static regions
  const [localRegions, setLocalRegions] = useState<Record<string, { name: string; colorIndex: number }>>(() => {
    const persisted = zoneState.debug_layout?.regions ?? {};
    const fromLocs: Record<string, { name: string; colorIndex: number }> = {};
    const STATIC_COLOR_INDEX: Record<string, number> = { cordon: 0, garbage: 1, agroprom: 2, dark_valley: 3, swamps: 4 };
    const STATIC_NAMES: Record<string, string> = { cordon: 'Кордон', garbage: 'Свалка', agroprom: 'Агропром', dark_valley: 'Тёмная Долина', swamps: 'Болота' };
    for (const loc of Object.values(zoneState.locations)) {
      const r = loc.region;
      if (r && !persisted[r] && !fromLocs[r]) {
        fromLocs[r] = { name: STATIC_NAMES[r] ?? r, colorIndex: STATIC_COLOR_INDEX[r] ?? 0 };
      }
    }
    return { ...fromLocs, ...persisted };
  });

  // ── Sync new locations into dragOverrides + localConns after CRUD ops ──────
  // After debug_create_location, zoneState gets a new entry; we need to
  // incorporate its persisted position and open an empty conns slot.
  // On non-initial changes we also freeze the entire effective layout so that
  // the BFS graph topology change (new node) does not reshuffle cards.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const locKeysStr = JSON.stringify(Object.keys(zoneState.locations).sort());
  const isFirstLocKeysEffect = useRef(true);
  useEffect(() => {
    const isFirst = isFirstLocKeysEffect.current;
    isFirstLocKeysEffect.current = false;

    const persistedPos = zoneState.debug_layout?.positions ?? {};
    const prevOverrides = dragOverridesRef.current;

    // Find positions that the backend has saved but are not yet in local overrides
    const incoming: Record<string, { x: number; y: number }> = {};
    for (const [id, pos] of Object.entries(persistedPos)) {
      if (!(id in prevOverrides)) incoming[id] = pos as { x: number; y: number };
    }

    if (Object.keys(incoming).length > 0) {
      setDragOverrides((prev) => ({ ...prev, ...incoming }));

      // On non-initial changes (i.e. a location was just added) freeze the
      // current effective layout by persisting all positions.  This prevents
      // BFS from reshuffling cards that were already laid out.
      if (!isFirst) {
        const fullPositions = { ...effectivePosRef.current, ...incoming };
        persistMap(fullPositions, localConnsRef.current);
      }
    }

    setLocalConns((prev) => {
      const newConns: Record<string, LocationConn[]> = {};
      for (const [id, loc] of Object.entries(zoneState.locations)) {
        if (!(id in prev)) newConns[id] = [...(loc as ZoneLocation).connections];
      }
      return Object.keys(newConns).length ? { ...prev, ...newConns } : prev;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locKeysStr]);

  // ── Persistence ──────────────────────────────────────────────────────────
  // Saving flag and error shown in toolbar
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // ── Advance turn ─────────────────────────────────────────────────────────
  const [ticking, setTicking] = useState(false);
  const handleTick = async () => {
    setTicking(true);
    try { await sendCommand('debug_advance_turns', { max_n: 1, stop_on_decision: false }); }
    finally { setTicking(false); }
  };

  // ── Auto-run (течение времени) — server-side, synced across all tabs ────────
  // The running state lives in zoneState.auto_tick_enabled and
  // zoneState.auto_tick_speed (core flags set by the platform meta-command
  // "set_auto_tick").  Any tab / any user receives the change via the
  // "auto_tick_changed" WebSocket message, so all views stay in sync without
  // an extra HTTP round-trip.
  const autoRunning = zoneState.auto_tick_enabled ?? false;
  // When running but no speed is stored (legacy state from old clients), the
  // backend defaults to no throttle which matches "x100" behaviour.
  const activeSpeed = autoRunning ? (zoneState.auto_tick_speed ?? 'x100') : null;

  const handleSetSpeed = useCallback(async (speed: 'realtime' | 'x10' | 'x100' | 'x600') => {
    if (activeSpeed === speed) {
      // Already at this speed — toggle it off
      await sendCommand('set_auto_tick', { enabled: false });
    } else {
      await sendCommand('set_auto_tick', { enabled: true, speed });
    }
  }, [sendCommand, activeSpeed]);

  // Build the serializable connections map (all locations) and call backend
  const persistMap = useCallback(
    async (
      positions: Record<string, { x: number; y: number }>,
      conns: Record<string, LocationConn[]>,
      regions?: Record<string, { name: string; colorIndex: number }>,
    ) => {
      setSaving(true);
      setSaveError(null);
      try {
        // Serialize connections: plain objects, no class instances
        const serialisedConns: Record<string, Array<{ to: string; type: string; travel_time: number; closed: boolean }>> = {};
        for (const [id, cs] of Object.entries(conns)) {
          serialisedConns[id] = cs.map((c) => ({ to: c.to, type: c.type, travel_time: c.travel_time ?? 15, closed: c.closed ?? false }));
        }
        await sendCommand('debug_update_map', {
          positions,
          connections: serialisedConns,
          regions: regions ?? localRegionsRef.current,
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

  // Region bounding boxes (for background rects)
  const regionBBoxes = useMemo(() => {
    const boxes: Record<string, { minX: number; minY: number; maxX: number; maxY: number }> = {};
    for (const [id, loc] of Object.entries(zoneState.locations)) {
      const region = loc.region;
      if (!region) continue;
      // Only render regions that exist in localRegions
      if (!localRegions[region]) continue;
      const pos = effectivePos[id];
      if (!pos) continue;
      const half_w = CARD_W / 2 + REGION_PAD;
      const half_h = CARD_H / 2 + REGION_PAD;
      if (!boxes[region]) {
        boxes[region] = { minX: pos.x - half_w, minY: pos.y - half_h, maxX: pos.x + half_w, maxY: pos.y + half_h };
      } else {
        boxes[region].minX = Math.min(boxes[region].minX, pos.x - half_w);
        boxes[region].minY = Math.min(boxes[region].minY, pos.y - half_h);
        boxes[region].maxX = Math.max(boxes[region].maxX, pos.x + half_w);
        boxes[region].maxY = Math.max(boxes[region].maxY, pos.y + half_h);
      }
    }
    return boxes;
  }, [effectivePos, zoneState.locations, localRegions]);

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

  // effectivePosRef mirrors effectivePos = { ...layoutPositions, ...dragOverrides }.
  // Using this in persistMap saves ALL card positions (including BFS-computed ones
  // for cards never manually dragged), so that topology changes (new location added)
  // do not reshuffle cards that the user has already positioned.
  const effectivePosRef = useRef(effectivePos);
  effectivePosRef.current = effectivePos;

  const localConnsRef = useRef(localConns);
  localConnsRef.current = localConns;

  const localRegionsRef = useRef(localRegions);
  localRegionsRef.current = localRegions;

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
            [aId]: [...aC, { to: bId, type: 'normal', travel_time: 15 }],
            [bId]: [...bC, { to: aId, type: 'normal', travel_time: 15 }],
          };
      setLocalConns(newConns);
      persistMap(effectivePosRef.current, newConns);
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
    persistMap(effectivePosRef.current, newConns);
  }, [persistMap]);

  const updateConnectionWeight = useCallback((fromId: string, toId: string, travelTime: number) => {
    const prev = localConnsRef.current;
    const applyWeight = (list: LocationConn[]) =>
      list.map((c) => c.to === toId ? { ...c, travel_time: travelTime } : c);
    const newConns = {
      ...prev,
      [fromId]: applyWeight(prev[fromId] ?? []),
      [toId]: (prev[toId] ?? []).map((c) => c.to === fromId ? { ...c, travel_time: travelTime } : c),
    };
    setLocalConns(newConns);
    persistMap(effectivePosRef.current, newConns);
  }, [persistMap]);

  const toggleConnectionClosed = useCallback((fromId: string, toId: string) => {
    const prev = localConnsRef.current;
    const fromConn = (prev[fromId] ?? []).find((c) => c.to === toId);
    const newClosed = !(fromConn?.closed ?? false);
    const applyToggle = (list: LocationConn[], target: string) =>
      list.map((c) => c.to === target ? { ...c, closed: newClosed } : c);
    const newConns = {
      ...prev,
      [fromId]: applyToggle(prev[fromId] ?? [], toId),
      [toId]: applyToggle(prev[toId] ?? [], fromId),
    };
    setLocalConns(newConns);
    persistMap(effectivePosRef.current, newConns);
  }, [persistMap]);

  // ── Pointer handlers on each card ────────────────────────────────────────
  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, id: string) => {
      e.preventDefault();
      e.stopPropagation();
      // Always capture the pointer so handlePointerUp fires on this card.
      // In link mode we don't start a drag, but we still need capture so the
      // click-to-link logic in handlePointerUp fires (not the canvas pan handler).
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      if (linkMode) return;
      const pos = effectivePos[id] ?? { x: 0, y: 0 };
      dragRef.current = {
        id,
        startPtrX: e.clientX,
        startPtrY: e.clientY,
        startCardX: pos.x,
        startCardY: pos.y,
        hasMoved: false,
      };
    },
    [linkMode, effectivePos],
  );

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = (e.clientX - d.startPtrX) / zoomRef.current;
    const dy = (e.clientY - d.startPtrY) / zoomRef.current;
    if (!d.hasMoved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
    d.hasMoved = true;
    setDragOverrides((prev) => ({
      ...prev,
      [d.id]: {
        x: Math.max(-MAX_CANVAS_COORD, Math.min(MAX_CANVAS_COORD, d.startCardX + dx)),
        y: Math.max(-MAX_CANVAS_COORD, Math.min(MAX_CANVAS_COORD, d.startCardY + dy)),
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
        const finalX = Math.max(-MAX_CANVAS_COORD, Math.min(MAX_CANVAS_COORD, d.startCardX + (e.clientX - d.startPtrX) / zoomRef.current));
        const finalY = Math.max(-MAX_CANVAS_COORD, Math.min(MAX_CANVAS_COORD, d.startCardY + (e.clientY - d.startPtrY) / zoomRef.current));
        const finalPositions = { ...effectivePosRef.current, [d.id]: { x: finalX, y: finalY } };
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
        setSelectedRegionId(null);
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

  // ── Region drag handlers ──────────────────────────────────────────────────
  const handleRegionPointerDown = useCallback(
    (e: React.PointerEvent<SVGRectElement>, region: string) => {
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      const startPositions: Record<string, { x: number; y: number }> = {};
      for (const [id, loc] of Object.entries(zoneState.locations)) {
        if (loc.region === region) {
          startPositions[id] = effectivePos[id] ?? { x: 0, y: 0 };
        }
      }
      regionDragRef.current = {
        region,
        startPtrX: e.clientX,
        startPtrY: e.clientY,
        startPositions,
        hasMoved: false,
      };
    },
    [zoneState.locations, effectivePos],
  );

  const handleRegionPointerMove = useCallback(
    (e: React.PointerEvent<SVGRectElement>) => {
      const d = regionDragRef.current;
      if (!d) return;
      const dx = (e.clientX - d.startPtrX) / zoomRef.current;
      const dy = (e.clientY - d.startPtrY) / zoomRef.current;
      if (!d.hasMoved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
      d.hasMoved = true;
      setDragOverrides((prev) => {
        const next = { ...prev };
        for (const [id, startPos] of Object.entries(d.startPositions)) {
          next[id] = {
            x: startPos.x + dx,
            y: startPos.y + dy,
          };
        }
        return next;
      });
    },
    [],
  );

  const handleRegionPointerUp = useCallback(
    (e: React.PointerEvent<SVGRectElement>, _region: string) => {
      const d = regionDragRef.current;
      regionDragRef.current = null;
      if (!d) return;
      if (!d.hasMoved) {
        // It was a click — select the region, clear location selection
        setSelectedRegionId(d.region);
        setSelectedLocId(null);
        return;
      }
      const dx = (e.clientX - d.startPtrX) / zoomRef.current;
      const dy = (e.clientY - d.startPtrY) / zoomRef.current;
      const finalPositions = { ...effectivePosRef.current };
      for (const [id, startPos] of Object.entries(d.startPositions)) {
        finalPositions[id] = { x: startPos.x + dx, y: startPos.y + dy };
      }
      setDragOverrides(finalPositions);
      persistMap(finalPositions, localConnsRef.current);
    },
    [persistMap],
  );

  // ── Wheel zoom (imperative listener so we can pass { passive: false }) ────
  useEffect(() => {
    const el = canvasScrollRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.1 : -0.1;
      setZoom((prev) => {
        const nextZoom = Math.min(3, Math.max(0.25, +(prev + delta).toFixed(2)));
        const rect = el.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        setPanOffset((pan) => ({
          x: mouseX - (mouseX - pan.x) * (nextZoom / prev),
          y: mouseY - (mouseY - pan.y) * (nextZoom / prev),
        }));
        return nextZoom;
      });
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  // ── Fullscreen tracking + handler ─────────────────────────────────────────
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  const handleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      pageWrapRef.current?.requestFullscreen().catch(() => {/* ignore */});
    } else {
      document.exitFullscreen().catch(() => {/* ignore */});
    }
  }, []);

  // ── Export full map ───────────────────────────────────────────────────────
  const handleExport = useCallback(() => {
    const exportData = {
      version: 3,
      // Canvas layout
      positions: effectivePosRef.current,
      regions: localRegionsRef.current,
      // Full location data (IDs, connections, terrain, anomalies, region)
      locations: Object.fromEntries(
        Object.entries(zoneState.locations).map(([id, loc]) => [
          id,
          {
            name: loc.name,
            terrain_type: loc.terrain_type,
            anomaly_activity: loc.anomaly_activity,
            dominant_anomaly_type: loc.dominant_anomaly_type ?? null,
            region: loc.region ?? null,
            // Use the live canvas connections (may differ from zoneState if unsaved)
            connections: (localConnsRef.current[id] ?? loc.connections ?? []).map((c) => ({
              to: c.to,
              travel_time: c.travel_time,
              type: c.type ?? 'normal',
              closed: c.closed ?? false,
            })),
            artifacts: loc.artifacts ?? [],
          },
        ]),
      ),
      // World time
      world_turn: zoneState.world_turn,
      world_day: zoneState.world_day,
      world_hour: zoneState.world_hour,
      world_minute: zoneState.world_minute ?? 0,
      // Emission state
      emission_active: zoneState.emission_active,
      emission_scheduled_turn: zoneState.emission_scheduled_turn ?? null,
      emission_ends_turn: zoneState.emission_ends_turn ?? null,
    };
    const json = JSON.stringify(exportData, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `zone_map_day${zoneState.world_day}_turn${zoneState.world_turn}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [zoneState]);

  // ── Import full map ────────────────────────────────────────────────────────
  const handleImportFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      file.text().then(async (text) => {
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(text) as Record<string, unknown>;
        } catch {
          setSaveError('Import failed: invalid JSON');
          e.target.value = '';
          return;
        }

        // Full-map import (v3)
        if (
          parsed.version === 3 &&
          parsed.locations &&
          typeof parsed.locations === 'object' &&
          !Array.isArray(parsed.locations)
        ) {
          const locCount = Object.keys(parsed.locations as object).length;
          const confirmed = window.confirm(
            `⚠️ Полный импорт карты!\n\n` +
            `Это заменит ВСЕ локации (${locCount}), переходы, регионы, ` +
            `время и состояние выброса.\n\n` +
            `Персонажи и предметы в совпадающих локациях будут сохранены, ` +
            `остальные могут стать недоступны.\n\n` +
            `Продолжить?`
          );
          if (!confirmed) { e.target.value = ''; return; }

          // Send the whole thing to the backend in a single atomic command
          await sendCommand('debug_import_full_map', {
            locations: parsed.locations,
            positions: parsed.positions ?? {},
            regions: parsed.regions ?? {},
            world_turn: parsed.world_turn,
            world_day: parsed.world_day,
            world_hour: parsed.world_hour,
            world_minute: parsed.world_minute,
            emission_active: parsed.emission_active,
            emission_scheduled_turn: parsed.emission_scheduled_turn,
            emission_ends_turn: parsed.emission_ends_turn,
          });

          // Sync local canvas state
          const newPositions = (parsed.positions ?? {}) as Record<string, { x: number; y: number }>;
          const newConns = {} as Record<string, LocationConn[]>;
          for (const [id, locData] of Object.entries(parsed.locations as Record<string, Record<string, unknown>>)) {
            if (Array.isArray(locData.connections)) {
              newConns[id] = locData.connections as LocationConn[];
            }
          }
          const newRegions = (typeof parsed.regions === 'object' && !Array.isArray(parsed.regions) && parsed.regions !== null)
            ? parsed.regions as Record<string, { name: string; colorIndex: number }>
            : {};
          setDragOverrides(newPositions);
          setLocalConns(newConns);
          setLocalRegions(newRegions);
          e.target.value = '';
          return;
        }

        // Legacy v1/v2 import — positions + connections + per-location metadata only
        if (!parsed.positions || typeof parsed.positions !== 'object' || Array.isArray(parsed.positions)) {
          setSaveError('Import failed: JSON must have a "positions" object');
          e.target.value = '';
          return;
        }
        const newPositions = parsed.positions as Record<string, { x: number; y: number }>;

        // Detect if the legacy file has location IDs that don't exist in the current map.
        // In that case the legacy "update only existing" path cannot create those cards,
        // so we treat it as a full-map replacement (same as v3).
        const legacyLocsData = (
          parsed.locations &&
          typeof parsed.locations === 'object' &&
          !Array.isArray(parsed.locations)
        ) ? parsed.locations as Record<string, {
              name?: string;
              terrain_type?: string;
              anomaly_activity?: number;
              dominant_anomaly_type?: string | null;
              region?: string | null;
            }>
          : null;

        const legacyConnsData = (
          parsed.connections &&
          typeof parsed.connections === 'object' &&
          !Array.isArray(parsed.connections)
        ) ? parsed.connections as Record<string, LocationConn[]> : null;

        if (legacyLocsData) {
          const unknownIds = Object.keys(legacyLocsData).filter((id) => !(id in zoneState.locations));
          if (unknownIds.length > 0) {
            // Some location IDs in the file don't exist on this map.
            // Build embedded-connection location objects and run a full import.
            const locCount = Object.keys(legacyLocsData).length;
            const confirmed = window.confirm(
              `⚠️ Полный импорт карты!\n\n` +
              `Файл содержит ${unknownIds.length} из ${locCount} локаций, которых нет на текущей карте.\n` +
              `Это заменит ВСЕ текущие локации (${locCount} в файле).\n\n` +
              `Продолжить?`
            );
            if (!confirmed) { e.target.value = ''; return; }

            // Build locations with embedded connections (combine v2 metadata + top-level connections)
            const mergedLocations: Record<string, Record<string, unknown>> = {};
            for (const [id, locData] of Object.entries(legacyLocsData)) {
              mergedLocations[id] = {
                ...locData,
                connections: legacyConnsData?.[id] ?? [],
                artifacts: [],
              };
            }
            const newRegionsForFullImport = (
              typeof parsed.regions === 'object' && !Array.isArray(parsed.regions) && parsed.regions !== null
            ) ? parsed.regions as Record<string, { name: string; colorIndex: number }> : {};

            await sendCommand('debug_import_full_map', {
              locations: mergedLocations,
              positions: newPositions,
              regions: newRegionsForFullImport,
            });

            setDragOverrides(newPositions);
            const newConnsForFull = {} as Record<string, LocationConn[]>;
            for (const [id, locData] of Object.entries(mergedLocations)) {
              if (Array.isArray(locData.connections)) {
                newConnsForFull[id] = locData.connections as LocationConn[];
              }
            }
            setLocalConns(newConnsForFull);
            setLocalRegions(newRegionsForFullImport);
            e.target.value = '';
            return;
          }
        }

        setDragOverrides(newPositions);

        let newConns = localConnsRef.current;
        if (legacyConnsData) {
          newConns = legacyConnsData;
          setLocalConns(newConns);
        }

        let newRegions: Record<string, { name: string; colorIndex: number }> | undefined;
        if (parsed.regions && typeof parsed.regions === 'object' && !Array.isArray(parsed.regions)) {
          newRegions = parsed.regions as Record<string, { name: string; colorIndex: number }>;
          setLocalRegions(newRegions);
        }

        await persistMap(newPositions, newConns, newRegions);

        if (legacyLocsData) {
          for (const [locId, locData] of Object.entries(legacyLocsData)) {
            if (locId in zoneState.locations) {
              await sendCommand('debug_update_location', {
                loc_id: locId,
                name: locData.name ?? zoneState.locations[locId].name,
                terrain_type: locData.terrain_type,
                anomaly_activity: locData.anomaly_activity,
                dominant_anomaly_type: locData.dominant_anomaly_type,
                region: locData.region,
              });
            }
          }
        }

        // Reset so the same file can be re-imported
        e.target.value = '';
      });
    },
    [persistMap, sendCommand, zoneState.locations],
  );


  const handleSaveEdit = useCallback(
    async (data: { name: string; terrainType: string; anomalyActivity: number; dominantAnomalyType: string; region: string; exitZone: boolean }) => {
      if (!editingLocId) return;
      await sendCommand('debug_update_location', {
        loc_id: editingLocId,
        name: data.name,
        terrain_type: data.terrainType,
        anomaly_activity: data.anomalyActivity,
        dominant_anomaly_type: data.dominantAnomalyType || null,
        region: data.region || null,
        exit_zone: data.exitZone,
      });
    },
    [editingLocId, sendCommand],
  );

  const handleSaveCreate = useCallback(
    async (data: { name: string; terrainType: string; anomalyActivity: number; dominantAnomalyType: string; region: string; exitZone: boolean }) => {
      // Place the new card at the center of the currently visible viewport area.
      // The canvas transform is: translate(panOffset.x, panOffset.y) scale(zoom)
      // So the visible center in canvas-space is:
      //   canvasCX = (-panOffset.x + viewportW/2) / zoom
      //   canvasCY = (-panOffset.y + viewportH/2) / zoom
      const vpEl = canvasScrollRef.current;
      const vpW = vpEl ? vpEl.clientWidth : 800;
      const vpH = vpEl ? vpEl.clientHeight : 600;
      const pos = {
        x: Math.max(CARD_W / 2 + CANVAS_PAD, (-panOffsetRef.current.x + vpW / 2) / zoomRef.current),
        y: Math.max(CARD_H / 2 + CANVAS_PAD, (-panOffsetRef.current.y + vpH / 2) / zoomRef.current),
      };
      await sendCommand('debug_create_location', {
        name: data.name,
        terrain_type: data.terrainType,
        anomaly_activity: data.anomalyActivity,
        dominant_anomaly_type: data.dominantAnomalyType || null,
        region: data.region || null,
        exit_zone: data.exitZone,
        position: pos,
      });
    },
    [sendCommand],
  );

  const handleDeleteLoc = useCallback(async (locId: string) => {
    if (!window.confirm(`Удалить локацию "${zoneState.locations[locId]?.name ?? locId}"? Все связи с ней будут разорваны.`)) return;
    // Clean up local state immediately so UI is responsive before the server round-trip
    setSelectedLocId(null);
    setDragOverrides((prev) => {
      const next = { ...prev };
      delete next[locId];
      return next;
    });
    const nextConns = { ...localConnsRef.current };
    delete nextConns[locId];
    for (const id of Object.keys(nextConns)) {
      nextConns[id] = nextConns[id].filter((c) => c.to !== locId);
    }
    setLocalConns(nextConns);
    await sendCommand('debug_delete_location', { loc_id: locId });
  }, [sendCommand, zoneState.locations]);

  const detailLoc = selectedLocId ? zoneState.locations[selectedLocId] : null;
  const detailConns = selectedLocId ? (localConns[selectedLocId] ?? []) : [];

  // ── Region CRUD ───────────────────────────────────────────────────────────
  const handleSaveRegion = useCallback((regionId: string, name: string, colorIndex: number) => {
    const next = { ...localRegionsRef.current, [regionId]: { name, colorIndex } };
    setLocalRegions(next);
    persistMap(effectivePosRef.current, localConnsRef.current, next);
  }, [persistMap]);

  const handleDeleteRegion = useCallback((regionId: string) => {
    const nextRegions = { ...localRegionsRef.current };
    delete nextRegions[regionId];
    setLocalRegions(nextRegions);
    setSelectedRegionId(null);
    const locsInRegion = Object.values(zoneState.locations).filter((l) => l.region === regionId);
    const updatePromises = locsInRegion.map((l) =>
      sendCommand('debug_update_location', {
        loc_id: l.id,
        name: l.name,
        terrain_type: l.terrain_type,
        anomaly_activity: l.anomaly_activity,
        dominant_anomaly_type: l.dominant_anomaly_type || null,
        region: null,
      }),
    );
    Promise.all(updatePromises).then(() => {
      persistMap(effectivePosRef.current, localConnsRef.current, nextRegions);
    });
  }, [zoneState.locations, sendCommand, persistMap]);

  const handleCreateRegion = useCallback(() => {
    const existing = localRegionsRef.current;
    let n = Object.keys(existing).length;
    let newId = `region_${n}`;
    while (newId in existing) { n++; newId = `region_${n}`; }
    const colorIndex = n % REGION_COLOR_PALETTE.length;
    const next = { ...existing, [newId]: { name: `Регион ${n + 1}`, colorIndex } };
    setLocalRegions(next);
    setSelectedRegionId(newId);
    persistMap(effectivePosRef.current, localConnsRef.current, next);
  }, [persistMap]);

  return (
    <div
      ref={pageWrapRef}
      style={{
        ...s.page,
        ...(isFullscreen ? { background: '#060b14', height: '100vh', padding: 8, boxSizing: 'border-box', alignItems: 'stretch' } : {}),
      }}
    >
      {/* ── Canvas ────────────────────────────────────────── */}
      <div style={s.canvasWrap}>
        {/* Toolbar */}
        <div style={s.toolbar}>
          {/* ── Row 1: legend + world clock + save indicator ── */}
          <div style={s.toolbarRow1}>
            <div style={s.legend}>
              {Object.entries(TERRAIN_TYPE_COLOR).map(([t, c]) => (
                <span key={t} style={s.legendItem}>
                  <span style={{ ...s.legendDot, background: c }} />
                  {TERRAIN_TYPE_LABELS[t] ?? t.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* World clock + emission */}
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                background: '#0f172a', border: '1px solid #1e3a5f',
                borderRadius: 6, padding: '0.2rem 0.5rem', gap: 1,
              }}>
                <span style={{ color: '#60a5fa', fontSize: '0.72rem', whiteSpace: 'nowrap', fontWeight: 700 }}>
                  📅 День {zoneState.world_day} · {String(zoneState.world_hour).padStart(2, '0')}:{String(zoneState.world_minute ?? 0).padStart(2, '0')}
                </span>
                <span style={{ color: '#475569', fontSize: '0.62rem', whiteSpace: 'nowrap' }}>
                  Ход {zoneState.world_turn}
                </span>
                {zoneState.emission_active ? (
                  <span style={{ color: '#ef4444', fontSize: '0.65rem', whiteSpace: 'nowrap', fontWeight: 700 }}>
                    ⚡ ВЫБРОС! (ещё {formatTurns((zoneState.emission_ends_turn ?? 0) - zoneState.world_turn)})
                  </span>
                ) : (
                  <span style={{ color: '#f59e0b', fontSize: '0.65rem', whiteSpace: 'nowrap' }}>
                    ⚡ через {formatTurns(Math.max(0, (zoneState.emission_scheduled_turn ?? 0) - zoneState.world_turn))}
                  </span>
                )}
              </div>
              <span style={{ color: '#64748b', fontSize: '0.68rem', visibility: saving ? 'visible' : 'hidden' }}>💾 Saving…</span>
              <span style={{ color: '#ef4444', fontSize: '0.68rem', visibility: saveError ? 'visible' : 'hidden' }} title={saveError ?? ''}>
                ⚠ Save failed
              </span>
            </div>
          </div>
          {/* ── Row 2: action buttons in groups ── */}
          <div style={s.toolbarRow2}>
            {/* Group: Time controls */}
            <div style={s.toolbarGroup}>
              <button
                style={s.toolBtn}
                onClick={handleTick}
                disabled={ticking || autoRunning}
                title="Пропустить 1 ход (1 минута)"
              >
                {ticking ? '…' : '⏭ Ход'}
              </button>
              <button
                style={{ ...s.toolBtn, color: activeSpeed === 'realtime' ? '#fca5a5' : '#86efac', borderColor: activeSpeed === 'realtime' ? '#ef4444' : '#22c55e' }}
                onClick={() => handleSetSpeed('realtime')}
                disabled={ticking}
                title={activeSpeed === 'realtime' ? 'Остановить (синхр. со всеми)' : 'Реальное время: 1 ход/мин — 1 игровая минута = 1 реальная минута (синхр. со всеми)'}
              >
                {activeSpeed === 'realtime' ? '⏸ Реал.' : '▶ Реал.'}
              </button>
              <button
                style={{ ...s.toolBtn, color: activeSpeed === 'x10' ? '#fca5a5' : '#fde68a', borderColor: activeSpeed === 'x10' ? '#ef4444' : '#f59e0b' }}
                onClick={() => handleSetSpeed('x10')}
                disabled={ticking}
                title={activeSpeed === 'x10' ? 'Остановить (синхр. со всеми)' : '×10: 1 ход каждые 6 секунд (синхр. со всеми)'}
              >
                {activeSpeed === 'x10' ? '⏸ ×10' : '⏩ ×10'}
              </button>
              <button
                style={{ ...s.toolBtn, color: activeSpeed === 'x100' ? '#fca5a5' : '#c4b5fd', borderColor: activeSpeed === 'x100' ? '#ef4444' : '#8b5cf6' }}
                onClick={() => handleSetSpeed('x100')}
                disabled={ticking}
                title={activeSpeed === 'x100' ? 'Остановить (синхр. со всеми)' : '×100: 1 ход каждые 0.6 секунды (синхр. со всеми)'}
              >
                {activeSpeed === 'x100' ? '⏸ ×100' : '⏩⏩ ×100'}
              </button>
              <button
                style={{ ...s.toolBtn, color: activeSpeed === 'x600' ? '#fca5a5' : '#f9a8d4', borderColor: activeSpeed === 'x600' ? '#ef4444' : '#ec4899' }}
                onClick={() => handleSetSpeed('x600')}
                disabled={ticking}
                title={activeSpeed === 'x600' ? 'Остановить (синхр. со всеми)' : '×600: 1 ход каждые 0.1 секунды (синхр. со всеми)'}
              >
                {activeSpeed === 'x600' ? '⏸ ×600' : '⚡⚡ ×600'}
              </button>
              <button
                style={{ ...s.toolBtn, color: '#fca5a5', borderColor: '#ef4444' }}
                onClick={async () => {
                  try { await sendCommand('debug_trigger_emission', {}); }
                  catch { /* ignore */ }
                }}
                disabled={zoneState.emission_active}
                title={zoneState.emission_active ? 'Выброс уже активен' : 'Запланировать выброс через 10–15 минут (debug)'}
              >
                ⚡ Выброс
              </button>
            </div>
            <div style={s.toolbarSep} />
            {/* Group: Edit tools */}
            <div style={s.toolbarGroup}>
              <button style={s.toolBtn} onClick={() => setCreatingLoc(true)} title="Создать новую локацию">
                ➕ Лок.
              </button>
              <button style={s.toolBtn} onClick={handleCreateRegion} title="Создать новый регион">
                ➕ Рег.
              </button>
              <button
                style={{ ...s.toolBtn, ...(linkMode ? s.toolBtnActive : {}) }}
                onClick={handleToggleLinkMode}
                title="Режим связей: кликните локацию-источник, потом цель"
              >
                🔗 {linkMode ? 'ON' : 'Связи'}
              </button>
            </div>
            <div style={s.toolbarSep} />
            {/* Group: View tools */}
            <div style={s.toolbarGroup}>
              {Object.keys(dragOverrides).length > 0 && (
                <button
                  style={s.toolBtn}
                  onClick={() => { setDragOverrides({}); persistMap({}, localConnsRef.current); }}
                  title="Сбросить позиции карточек к авто-раскладке"
                >
                  ↺ Сброс
                </button>
              )}
              {(panOffset.x !== 0 || panOffset.y !== 0) && (
                <button style={s.toolBtn} onClick={() => setPanOffset({ x: 0, y: 0 })} title="Вернуть центр холста">
                  ⊙ Центр
                </button>
              )}
              {/* Zoom inline group */}
              <div style={s.zoomGroup}>
                <button
                  style={{ ...s.toolBtn, ...s.zoomBtn }}
                  onClick={() => setZoom((prev) => Math.max(0.25, +(prev - 0.1).toFixed(2)))}
                  disabled={zoom <= 0.25}
                  title="Zoom out"
                >–</button>
                <button
                  style={{ ...s.toolBtn, ...s.zoomBtn, minWidth: 38, borderLeft: 'none', borderRight: 'none', borderRadius: 0 }}
                  onClick={() => setZoom(1.0)}
                  title="Сброс масштаба"
                >{Math.round(zoom * 100)}%</button>
                <button
                  style={{ ...s.toolBtn, ...s.zoomBtn, borderLeft: 'none', borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
                  onClick={() => setZoom((prev) => Math.min(3, +(prev + 0.1).toFixed(2)))}
                  disabled={zoom >= 3}
                  title="Zoom in"
                >+</button>
              </div>
              <button
                style={s.toolBtn}
                onClick={handleFullscreen}
                title={isFullscreen ? 'Выйти из полноэкранного режима' : 'Полноэкранный режим'}
                aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
              >
                {isFullscreen ? '⊡' : '⛶'}
              </button>
            </div>
            <div style={s.toolbarSep} />
            {/* Group: Data */}
            <div style={s.toolbarGroup}>
              <button style={s.toolBtn} onClick={handleExport} title="Экспортировать карту в JSON">
                📤
              </button>
              <button style={s.toolBtn} onClick={() => importInputRef.current?.click()} title="Импортировать карту из JSON">
                📥
              </button>
            </div>
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
          ref={canvasScrollRef}
          style={{
            ...s.canvasScroll,
            cursor: isPanning ? 'grabbing' : linkMode ? 'crosshair' : 'grab',
            ...(isFullscreen ? { flex: 1, height: 'auto', borderRadius: 0 } : {}),
          }}
          onPointerDown={handleCanvasPointerDown}
          onPointerMove={handleCanvasPointerMove}
          onPointerUp={handleCanvasPanEnd}
          onPointerCancel={handleCanvasPanEnd}
        >
          {/* Panning layer — translated by panOffset and scaled by zoom */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${zoom})`,
              transformOrigin: '0 0',
              willChange: 'transform',
            }}
          >
            {/* SVG connection lines */}
            <svg
              style={{ position: 'absolute', top: 0, left: 0, zIndex: 0 }}
              width={canvasW}
              height={canvasH}
              overflow="visible"
            >
              {/* Region background rects */}
              {Object.entries(regionBBoxes).map(([region, box]) => {
                const { bg, border } = getRegionColors(region, localRegions);
                const label = localRegions[region]?.name ?? REGION_LABELS[region] ?? region;
                const isRegionSelected = region === selectedRegionId;
                return (
                  <g key={region}>
                    <rect
                      x={box.minX}
                      y={box.minY}
                      width={box.maxX - box.minX}
                      height={box.maxY - box.minY}
                      fill={bg}
                      stroke={isRegionSelected ? '#f8fafc' : border}
                      strokeWidth={isRegionSelected ? 2 : 1.5}
                      strokeDasharray="6 3"
                      rx={14}
                      opacity={0.85}
                      style={{ cursor: 'grab' }}
                      onPointerDown={(e) => handleRegionPointerDown(e, region)}
                      onPointerMove={handleRegionPointerMove}
                      onPointerUp={(e) => handleRegionPointerUp(e, region)}
                    />
                    <text
                      x={box.minX + 12}
                      y={box.minY + 22}
                      fill={border}
                      fontSize={13}
                      fontWeight={700}
                      fontFamily="system-ui, sans-serif"
                      style={{ pointerEvents: 'none', userSelect: 'none' }}
                    >
                      {label}
                    </text>
                  </g>
                );
              })}
              {/* Draw connections from localConns */}
              <g pointerEvents="none">
              {Object.entries(localConns).flatMap(([locId, conns]) =>
                conns.map((conn) => {
                  // Deduplicate: only draw A→B when A < B
                  if (locId > conn.to) return null;
                  const a = effectivePos[locId];
                  const b = effectivePos[conn.to];
                  if (!a || !b) return null;
                  const isDangerous = conn.type === 'dangerous';
                  const isClosed = !!conn.closed;
                  const mx = (a.x + b.x) / 2;
                  const my = (a.y + b.y) / 2;
                  const travelTime = conn.travel_time ?? 15;
                  // Highlight if this edge touches the selected location
                  const isLocHighlighted = !!selectedLocId && (locId === selectedLocId || conn.to === selectedLocId);
                  // Highlight if either endpoint belongs to the selected region
                  const isRegionHighlighted = !!selectedRegionId && (
                    zoneState.locations[locId]?.region === selectedRegionId ||
                    zoneState.locations[conn.to]?.region === selectedRegionId
                  );
                  const isHighlighted = isLocHighlighted || isRegionHighlighted;
                  const hasSelection = !!(selectedLocId || selectedRegionId);
                  // Dim non-highlighted edges when something is selected
                  const baseOpacity = hasSelection ? (isHighlighted ? 1.0 : 0.18) : 0.8;
                  // Closed edges: bright-red when highlighted, dark-red otherwise
                  const strokeColor = isClosed
                    ? (isHighlighted ? '#ef4444' : '#7f1d1d')
                    : isHighlighted
                      ? (isLocHighlighted ? '#fbbf24' : '#c084fc')
                      : (isDangerous ? '#7f1d1d' : '#1e3a5f');
                  const strokeW = isHighlighted ? 2.5 : (isDangerous || isClosed ? 2 : 1.5);
                  const dashArray = isClosed ? '4 4' : (isDangerous && !isHighlighted ? '6 3' : undefined);
                  return (
                    <g key={`${locId}--${conn.to}`}>
                      <line
                        x1={a.x}
                        y1={a.y}
                        x2={b.x}
                        y2={b.y}
                        stroke={strokeColor}
                        strokeWidth={strokeW}
                        strokeDasharray={dashArray}
                        strokeOpacity={baseOpacity}
                      />
                      {/* Travel-time label */}
                      <rect
                        x={mx - 12}
                        y={my - 8}
                        width={24}
                        height={14}
                        rx={4}
                        fill="#0f172a"
                        fillOpacity={baseOpacity * 0.85}
                      />
                      <text
                        x={mx}
                        y={my + 3}
                        textAnchor="middle"
                        fontSize={9}
                        fill={isClosed ? (isHighlighted ? '#fca5a5' : '#7f1d1d') : (isHighlighted ? '#fde68a' : (isDangerous ? '#fca5a5' : '#94a3b8'))}
                        fontFamily="monospace"
                        opacity={baseOpacity}
                      >
                        {travelTime}м
                      </text>
                    </g>
                  );
                }),
              )}
              </g>
          </svg>

          {/* Location cards */}
          {/* pointerEvents:'none' lets clicks on empty space pass through to the SVG region rects below */}
          <div style={{ position: 'relative', width: canvasW, height: canvasH, pointerEvents: 'none' }}>
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
                    pointerEvents: 'auto', // re-enable on cards — parent container has pointerEvents:'none'
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
                    {loc.region && (
                      <span style={{ color: '#334155', marginLeft: 5 }}>
                        · {localRegions[loc.region]?.name ?? REGION_LABELS[loc.region] ?? loc.region}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 5 }}>
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

      {/* ── Hidden file input for Import ─────────────────────── */}
      <input
        ref={importInputRef}
        type="file"
        accept=".json"
        style={{ display: 'none' }}
        onChange={handleImportFile}
      />

      {/* ── Detail panel ──────────────────────────────────── */}
      <div style={s.detailPanel}>
        {detailLoc ? (
          <LocationDetailPanel
            loc={detailLoc}
            conns={detailConns}
            regionName={detailLoc.region ? (localRegions[detailLoc.region]?.name ?? REGION_LABELS[detailLoc.region] ?? detailLoc.region) : undefined}
            zoneState={zoneState}
            onClose={() => setSelectedLocId(null)}
            onEdit={() => setEditingLocId(selectedLocId!)}
            onSpawnStalker={async (name, faction, globalGoal) => {
              await sendCommand('debug_spawn_stalker', { loc_id: selectedLocId!, name, faction, global_goal: globalGoal });
            }}
            onSpawnTrader={async (name) => {
              await sendCommand('debug_spawn_trader', { loc_id: selectedLocId!, name });
            }}
            onSpawnMutant={async (mutantType) => {
              await sendCommand('debug_spawn_mutant', { loc_id: selectedLocId!, mutant_type: mutantType });
            }}
            onSpawnArtifact={async (artifactType) => {
              await sendCommand('debug_spawn_artifact', { loc_id: selectedLocId!, artifact_type: artifactType });
            }}
            onSpawnItem={async (itemType) => {
              await sendCommand('debug_spawn_item_on_location', { loc_id: selectedLocId!, item_type: itemType });
            }}
            onDeleteConnection={(toId) => deleteConnection(selectedLocId!, toId)}
            onUpdateConnectionWeight={(toId, travelTime) => updateConnectionWeight(selectedLocId!, toId, travelTime)}
            onToggleConnectionClosed={(toId) => toggleConnectionClosed(selectedLocId!, toId)}
            onAgentClick={(agentId) => setProfileAgentId(agentId)}
            onTraderClick={(traderId) => setProfileTraderId(traderId)}
            onDeleteLoc={() => handleDeleteLoc(selectedLocId!)}
          />
        ) : selectedRegionId && localRegions[selectedRegionId] ? (
          <RegionDetailPanel
            regionId={selectedRegionId}
            region={localRegions[selectedRegionId]}
            locations={Object.values(zoneState.locations)
              .filter((l) => l.region === selectedRegionId)
              .map((l) => ({ id: l.id, name: l.name }))}
            onClose={() => setSelectedRegionId(null)}
            onSave={(name, colorIndex) => handleSaveRegion(selectedRegionId, name, colorIndex)}
            onDelete={() => handleDeleteRegion(selectedRegionId)}
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
          initialRegion={zoneState.locations[editingLocId].region ?? ''}
          initialExitZone={zoneState.locations[editingLocId].exit_zone ?? false}
          regions={localRegions}
          locId={editingLocId}
          onClose={() => setEditingLocId(null)}
          onSave={handleSaveEdit}
        />
      )}

      {/* ── Location create modal ─────────────────────────────── */}
      {creatingLoc && (
        <LocationModal
          mode="create"
          regions={localRegions}
          onClose={() => setCreatingLoc(false)}
          onSave={handleSaveCreate}
        />
      )}

      {/* ── Agent profile modal (from clicking a stalker in the location panel) ── */}
      {profileAgentId && zoneState.agents[profileAgentId] && (
        <AgentProfileModal
          agent={zoneState.agents[profileAgentId] as unknown as AgentForProfile}
          locationName={
            zoneState.locations[zoneState.agents[profileAgentId].location_id]?.name ??
            zoneState.agents[profileAgentId].location_id
          }
          locations={zoneState.locations}
          onClose={() => setProfileAgentId(null)}
          sendCommand={sendCommand}
          contextId={contextId}
        />
      )}

      {/* ── Trader profile modal (from clicking a trader row in the location panel) ── */}
      {profileTraderId && zoneState.traders[profileTraderId] && (() => {
        const t = zoneState.traders[profileTraderId];
        const traderAsAgent: AgentForProfile = {
          id: t.id,
          name: t.name,
          location_id: t.location_id,
          hp: 100,
          max_hp: 100,
          radiation: 0,
          hunger: 0,
          thirst: 0,
          sleepiness: 0,
          money: t.money ?? 0,
          faction: 'trader',
          inventory: t.inventory ?? [],
          equipment: {},
          is_alive: true,
          action_used: false,
          scheduled_action: null,
          controller: { kind: 'npc' },
          memory: [],
        };
        return (
          <AgentProfileModal
            agent={traderAsAgent}
            locationName={zoneState.locations[t.location_id]?.name ?? t.location_id}
            locations={zoneState.locations}
            onClose={() => setProfileTraderId(null)}
            sendCommand={sendCommand}
            contextId={contextId}
          />
        );
      })()}
    </div>
  );
}

