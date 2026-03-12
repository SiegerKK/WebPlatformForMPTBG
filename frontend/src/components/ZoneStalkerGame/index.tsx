import React, { useCallback, useEffect, useRef, useState } from 'react';
import { commandsApi, contextsApi, eventsApi, matchesApi } from '../../api/client';
import type { GameContext, GameEvent, Match, MatchParticipant, User } from '../../types';

interface Props {
  match: Match;
  user: User;
  onMatchUpdated: (match: Match) => void;
  onMatchDeleted: (matchId: string) => void;
}

// ─── State types pulled from state_blob ─────────────────────────────────────

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
  anomalies: Array<{ id: string; type: string; name: string }>;
  artifacts: Array<{ id: string; type: string; name: string; value: number }>;
  items: Array<{ id: string; type: string; name: string }>;
  agents: string[];
}

interface AgentInventoryItem {
  id: string;
  type: string;
  name: string;
  weight?: number;
  value?: number;
}

interface ScheduledAction {
  type: 'travel' | 'explore' | 'sleep' | 'event';
  turns_remaining: number;
  turns_total: number;
  target_id: string;
  route?: string[];
  started_turn: number;
}

interface MemoryEntry {
  world_turn: number;
  world_day: number;
  world_hour: number;
  type: string;
  title: string;
  summary: string;
  effects: Record<string, number>;
}

interface StalkerAgent {
  id: string;
  name: string;
  location_id: string;
  hp: number;
  max_hp: number;
  radiation: number;
  stamina: number;
  money: number;
  faction: string;
  inventory: AgentInventoryItem[];
  equipment: Record<string, AgentInventoryItem | null>;
  is_alive: boolean;
  action_used: boolean;
  scheduled_action: ScheduledAction | null;
  memory: MemoryEntry[];
  controller: { kind: string };
}

interface ZoneMapState {
  context_type: string;
  world_turn: number;
  world_hour: number;
  world_day: number;
  max_turns: number;
  locations: Record<string, ZoneLocation>;
  agents: Record<string, StalkerAgent>;
  mutants: Record<string, { id: string; name: string; location_id: string; hp: number; max_hp: number; is_alive: boolean }>;
  traders: Record<string, { id: string; name: string; location_id: string }>;
  player_agents: Record<string, string>;
  active_events: string[];
  game_over: boolean;
}

interface ZoneEventState {
  context_type: string;
  event_id: string;
  title: string;
  description: string;
  phase: 'waiting' | 'active' | 'ended';
  current_turn: number;
  max_turns: number;
  participants: Record<string, { player_id: string; status: string; choice: number | null }>;
  current_narration: string;
  current_options: string[];
  narration_history: Array<{
    turn: number;
    narration: string;
    options: string[];
    choices: Record<string, string>;
  }>;
  outcome: string | null;
}

// ─── Location type colour ────────────────────────────────────────────────────

const LOC_TYPE_COLOR: Record<string, string> = {
  safe_hub: '#166534',
  wild_area: '#854d0e',
  anomaly_cluster: '#7c3aed',
  underground: '#374151',
  ruins: '#5b4a30',
  military_zone: '#1e3a5f',
};

const DANGER_COLORS = ['#22c55e', '#84cc16', '#f59e0b', '#f97316', '#ef4444'];

const HOUR_LABEL = (h: number) => `${String(h).padStart(2, '0')}:00`;

const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore: '🔍',
  sleep: '😴',
  event: '📖',
};

export default function ZoneStalkerGame({ match, user, onMatchUpdated, onMatchDeleted }: Props) {
  const [context, setContext] = useState<GameContext | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [participants, setParticipants] = useState<MatchParticipant[]>([]);
  const [activeEventCtx, setActiveEventCtx] = useState<GameContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedLocId, setSelectedLocId] = useState<string | null>(null);
  const [travelTarget, setTravelTarget] = useState<string | null>(null);
  const [sleepHours, setSleepHours] = useState(6);
  const [showMemory, setShowMemory] = useState(false);
  const [showTravelPanel, setShowTravelPanel] = useState(false);
  const [activeTab, setActiveTab] = useState<'map' | 'event' | 'memory'>('map');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lobbyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const zoneState: ZoneMapState | null = context
    ? (context.state_blob as unknown as ZoneMapState)
    : null;

  const myAgentId = zoneState?.player_agents?.[user.id] ?? null;
  const myAgent: StalkerAgent | null = myAgentId ? (zoneState?.agents?.[myAgentId] ?? null) : null;
  const currentLocId = myAgent?.location_id ?? null;
  const currentLoc = currentLocId ? zoneState?.locations?.[currentLocId] : null;

  const isCreator = match.created_by_user_id === user.id;
  const isWaiting = match.status === 'waiting_for_players' || match.status === 'draft';
  const isActive = match.status === 'active';

  const canAct = myAgent?.is_alive &&
    !myAgent?.action_used &&
    !myAgent?.scheduled_action &&
    !zoneState?.game_over;

  const eventState: ZoneEventState | null =
    activeEventCtx ? (activeEventCtx.state_blob as unknown as ZoneEventState) : null;
  const myEventParticipation = eventState?.participants?.[user.id];
  const canChooseOption =
    eventState?.phase === 'active' &&
    myEventParticipation?.status === 'active' &&
    myEventParticipation?.choice == null;

  // ─── load participants ───────────────────────────────────────────────────
  const loadParticipants = useCallback(async () => {
    try {
      const res = await matchesApi.participants(match.id);
      setParticipants(res.data as MatchParticipant[]);
    } catch { /* non-fatal */ }
  }, [match.id]);

  // ─── refresh context + events ────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const ctxRes = await contextsApi.getTree(match.id);
      const ctxList = ctxRes.data as GameContext[];
      const zoneCtx = ctxList.find((c) => c.context_type === 'zone_map') ?? null;
      setContext(zoneCtx);

      // Find active zone_event context
      const zoneState = zoneCtx?.state_blob as unknown as ZoneMapState | null;
      if (zoneState?.active_events?.length) {
        const activeEvtId = zoneState.active_events[0];
        const evtCtx = ctxList.find((c) => c.id === activeEvtId) ?? null;
        setActiveEventCtx(evtCtx);
        if (evtCtx) setActiveTab('event');
      } else {
        setActiveEventCtx(null);
      }

      const evRes = await eventsApi.listForMatch(match.id);
      setEvents(evRes.data as GameEvent[]);
    } catch { /* ignore */ }
  }, [match.id]);

  // ─── ensure zone_map context exists ─────────────────────────────────────
  const ensureContext = useCallback(async () => {
    const ctxRes = await contextsApi.getTree(match.id);
    const existing = (ctxRes.data as GameContext[]).find((c) => c.context_type === 'zone_map');
    if (existing) { setContext(existing); return existing; }
    const newCtx = await contextsApi.create({ match_id: match.id, context_type: 'zone_map' });
    setContext(newCtx.data as GameContext);
    return newCtx.data as GameContext;
  }, [match.id]);

  // ─── initial load ────────────────────────────────────────────────────────
  useEffect(() => {
    loadParticipants();
    if (isActive) ensureContext().then(() => refresh());
    else refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.status]);

  // ─── lobby polling ───────────────────────────────────────────────────────
  useEffect(() => {
    if (lobbyPollRef.current) clearInterval(lobbyPollRef.current);
    if (!isWaiting) return;
    lobbyPollRef.current = setInterval(async () => {
      await loadParticipants();
      try {
        const mRes = await matchesApi.get(match.id);
        const updated = mRes.data as Match;
        if (updated.status === 'archived') { onMatchDeleted(match.id); return; }
        if (updated.status !== match.status) onMatchUpdated(updated);
      } catch (e: unknown) {
        if ((e as { response?: { status?: number } })?.response?.status === 404)
          onMatchDeleted(match.id);
      }
    }, 2500);
    return () => { if (lobbyPollRef.current) clearInterval(lobbyPollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWaiting, match.id, match.status]);

  // ─── active polling ──────────────────────────────────────────────────────
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (isActive && !zoneState?.game_over) {
      pollRef.current = setInterval(async () => {
        try {
          const mRes = await matchesApi.get(match.id);
          const updated = mRes.data as Match;
          if (updated.status === 'archived') { onMatchDeleted(match.id); return; }
          if (updated.status !== match.status) onMatchUpdated(updated);
        } catch (e: unknown) {
          if ((e as { response?: { status?: number } })?.response?.status === 404)
            onMatchDeleted(match.id);
        }
        await refresh();
      }, 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, zoneState?.game_over, match.id, refresh]);

  // ─── commands ────────────────────────────────────────────────────────────
  const sendCommand = async (commandType: string, payload: Record<string, unknown>, contextId?: string) => {
    const ctxId = contextId ?? context?.id;
    if (!ctxId) return;
    setActionLoading(true);
    setError(null);
    try {
      const res = await commandsApi.submit({
        match_id: match.id,
        context_id: ctxId,
        command_type: commandType,
        payload,
      });
      if (res.data.status === 'rejected') {
        setError(res.data.error ?? 'Action rejected');
      }
      await refresh();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Command failed.');
    } finally {
      setActionLoading(false);
    }
  };

  const handleMove = async (targetLocId: string) => {
    await sendCommand('move_agent', { target_location_id: targetLocId });
    setSelectedLocId(null);
  };

  const handleTravel = async (targetLocId: string) => {
    await sendCommand('travel', { target_location_id: targetLocId });
    setTravelTarget(null);
    setShowTravelPanel(false);
  };

  const handleExplore = async () => sendCommand('explore_location', {});

  const handleSleep = async () => sendCommand('sleep', { hours: sleepHours });

  const handlePickUpArtifact = async (artifactId: string) =>
    sendCommand('pick_up_artifact', { artifact_id: artifactId });

  const handlePickUpItem = async (itemId: string) =>
    sendCommand('pick_up_item', { item_id: itemId });

  const handleEndTurn = async () => sendCommand('end_turn', {});

  const handleChooseOption = async (optionIndex: number) => {
    if (!activeEventCtx) return;
    await sendCommand('choose_option', { option_index: optionIndex }, activeEventCtx.id);
  };

  const handleLeaveEvent = async () => {
    if (!activeEventCtx) return;
    await sendCommand('leave_event', {}, activeEventCtx.id);
  };

  const handleManualTick = async () => {
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.tick(match.id);
      await refresh();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Tick failed.');
    } finally {
      setActionLoading(false);
    }
  };

  const handleJoin = async () => {
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.join(match.id);
      await loadParticipants();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg && !msg.toLowerCase().includes('already')) setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleStart = async () => {
    setActionLoading(true);
    setError(null);
    try {
      const res = await matchesApi.start(match.id);
      onMatchUpdated(res.data as Match);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to start match.');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteMatch = async () => {
    if (!window.confirm('Close this room?')) return;
    setActionLoading(true);
    try {
      await matchesApi.delete(match.id);
      onMatchDeleted(match.id);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to close room.');
      setActionLoading(false);
    }
  };

  const alreadyJoined = participants.some((p) => p.user_id === user.id);

  // ─── render: lobby ───────────────────────────────────────────────────────
  const renderLobby = () => {
    const canStart = isCreator && participants.length >= 1;
    return (
      <div style={styles.lobby}>
        <span style={styles.lobbyEmoji}>☢️</span>
        <h3 style={styles.lobbyTitle}>Zone Stalkers — Waiting Room</h3>
        <p style={styles.lobbyHint}>
          {participants.length} player{participants.length !== 1 ? 's' : ''} in the Zone
        </p>
        <div style={styles.participantList}>
          {participants.map((p) => (
            <div key={p.id} style={styles.participantBadge}>
              👤 {p.display_name ?? p.user_id?.slice(0, 8) ?? 'Unknown'}
            </div>
          ))}
        </div>
        <p style={styles.lobbyNote}>
          Zone Stalkers can be played solo or with multiple players. Each player gets their own stalker agent.
          The game runs in <strong>real time</strong> — 1 game-hour per tick.
        </p>
        <div style={styles.lobbyActions}>
          {!alreadyJoined && (
            <button style={styles.btnPrimary} onClick={handleJoin} disabled={actionLoading}>
              {actionLoading ? '…' : 'Join the Zone'}
            </button>
          )}
          {isCreator && (
            <button
              style={{ ...styles.btnPrimary, ...(canStart ? {} : styles.btnDisabled) }}
              onClick={handleStart}
              disabled={actionLoading || !canStart}
            >
              {actionLoading ? '…' : 'Enter the Zone'}
            </button>
          )}
          {isCreator && (
            <button style={styles.btnDanger} onClick={handleDeleteMatch} disabled={actionLoading}>
              {actionLoading ? '…' : 'Close Room'}
            </button>
          )}
        </div>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  // ─── render: scheduled action status ────────────────────────────────────
  const renderScheduledAction = () => {
    if (!myAgent?.scheduled_action) return null;
    const sa = myAgent.scheduled_action;
    const icon = SCHED_ICONS[sa.type] ?? '⏳';
    const label =
      sa.type === 'travel'
        ? `Travelling to ${zoneState?.locations?.[sa.target_id]?.name ?? sa.target_id}`
        : sa.type === 'explore'
        ? `Exploring ${zoneState?.locations?.[sa.target_id]?.name ?? 'location'}`
        : sa.type === 'sleep'
        ? `Sleeping (${sa.turns_remaining}h remaining)`
        : sa.type === 'event'
        ? 'Participating in event'
        : `${sa.type} in progress`;
    const pct = ((sa.turns_total - sa.turns_remaining) / sa.turns_total) * 100;
    return (
      <div style={styles.schedCard}>
        <div style={styles.schedRow}>
          <span style={styles.schedIcon}>{icon}</span>
          <span style={styles.schedLabel}>{label}</span>
          <span style={styles.schedTurns}>{sa.turns_remaining}h left</span>
        </div>
        <div style={styles.progressBg}>
          <div style={{ ...styles.progressFill, width: `${pct}%` }} />
        </div>
        <p style={styles.schedHint}>Use "⏭ End Turn" to advance time — or wait for the auto-ticker.</p>
      </div>
    );
  };

  // ─── render: action panel ────────────────────────────────────────────────
  const renderActionPanel = () => {
    if (!myAgent) return null;
    const loc = currentLoc;
    const isSafe = loc?.type === 'safe_hub' || loc?.type === 'ruins';
    const scheduled = !!myAgent.scheduled_action;

    return (
      <div style={styles.actionPanel}>
        <div style={styles.panelTitle}>⚡ Actions</div>
        {renderScheduledAction()}
        {!scheduled && canAct && (
          <>
            {/* Explore */}
            <button
              style={styles.actionBtn}
              onClick={handleExplore}
              disabled={actionLoading}
              title="Spend 1 hour searching this location"
            >
              🔍 Explore (1h)
            </button>

            {/* Travel */}
            <button
              style={styles.actionBtn}
              onClick={() => setShowTravelPanel(!showTravelPanel)}
              disabled={actionLoading}
              title="Plan a multi-hour journey"
            >
              🚶 Travel…
            </button>

            {/* Sleep */}
            {isSafe && (
              <div style={styles.sleepRow}>
                <button
                  style={styles.actionBtn}
                  onClick={handleSleep}
                  disabled={actionLoading}
                  title="Rest to recover HP and reduce radiation"
                >
                  😴 Sleep
                </button>
                <select
                  style={styles.sleepSelect}
                  value={sleepHours}
                  onChange={(e) => setSleepHours(Number(e.target.value))}
                >
                  {[2, 4, 6, 8, 10].map((h) => (
                    <option key={h} value={h}>{h}h</option>
                  ))}
                </select>
              </div>
            )}

            {/* Active events */}
            {zoneState?.active_events?.map((evtId) => (
              <button
                key={evtId}
                style={{ ...styles.actionBtn, background: '#312e81', borderColor: '#4f46e5' }}
                onClick={() => sendCommand('join_event', { event_context_id: evtId })}
                disabled={actionLoading}
              >
                📖 Join Event
              </button>
            ))}
          </>
        )}

        {/* Travel panel */}
        {showTravelPanel && canAct && (
          <div style={styles.travelPanel}>
            <div style={styles.panelTitle}>Select destination:</div>
            {Object.values(zoneState?.locations ?? {})
              .filter((l) => l.id !== currentLocId)
              .map((loc) => (
                <button
                  key={loc.id}
                  style={{
                    ...styles.travelLocBtn,
                    ...(travelTarget === loc.id ? styles.travelLocBtnSelected : {}),
                  }}
                  onClick={() => {
                    if (travelTarget === loc.id) {
                      handleTravel(loc.id);
                    } else {
                      setTravelTarget(loc.id);
                    }
                  }}
                >
                  {loc.name}
                  <span style={styles.travelDanger}> ⚠ {loc.danger_level}</span>
                  {travelTarget === loc.id && <span style={{ color: '#60a5fa' }}> → Go!</span>}
                </button>
              ))}
            <button style={styles.cancelBtn} onClick={() => { setShowTravelPanel(false); setTravelTarget(null); }}>
              Cancel
            </button>
          </div>
        )}

        {/* End turn always available */}
        <button
          style={styles.endTurnBtn}
          onClick={handleEndTurn}
          disabled={actionLoading}
          title="Advance time by 1 hour"
        >
          {actionLoading ? '…' : '⏭ End Turn (1h)'}
        </button>

        {/* Manual tick (dev helper) */}
        <button
          style={styles.tickBtn}
          onClick={handleManualTick}
          disabled={actionLoading}
          title="Force a world tick (admin/dev)"
        >
          {actionLoading ? '…' : '⚙ Tick World'}
        </button>

        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  // ─── render: zone map ────────────────────────────────────────────────────
  const renderZoneMap = () => {
    if (!zoneState) return <p style={styles.loadingText}>Generating the Zone…</p>;
    const locations = Object.values(zoneState.locations);

    return (
      <div style={styles.mapContainer}>
        {/* ── Agent status panel ── */}
        <div style={styles.agentPanel}>
          <div style={styles.panelTitle}>☢️ Your Stalker</div>
          {myAgent ? (
            <>
              <div style={styles.agentName}>{myAgent.name}</div>
              <div style={styles.agentFaction}>Faction: {myAgent.faction}</div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>❤️ HP</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${(myAgent.hp / myAgent.max_hp) * 100}%`, background: myAgent.hp > 50 ? '#22c55e' : myAgent.hp > 25 ? '#f59e0b' : '#ef4444' }} />
                </div>
                <span style={styles.statVal}>{myAgent.hp}/{myAgent.max_hp}</span>
              </div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>☢ Rad</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${Math.min(myAgent.radiation, 100)}%`, background: '#a855f7' }} />
                </div>
                <span style={styles.statVal}>{myAgent.radiation}</span>
              </div>
              <div style={styles.moneyRow}>💰 {myAgent.money} RU</div>
              <div style={styles.locationLabel}>
                📍 {zoneState.locations[myAgent.location_id]?.name ?? myAgent.location_id}
              </div>
              <div style={styles.turnInfo}>
                Day {zoneState.world_day} · {HOUR_LABEL(zoneState.world_hour)} · Turn {zoneState.world_turn}/{zoneState.max_turns}
                {myAgent.action_used && !myAgent.scheduled_action && (
                  <span style={styles.actionUsedBadge}>Acted</span>
                )}
                {myAgent.scheduled_action && (
                  <span style={{ ...styles.actionUsedBadge, background: '#1e3a5f', color: '#60a5fa' }}>
                    {SCHED_ICONS[myAgent.scheduled_action.type]} {myAgent.scheduled_action.type}
                  </span>
                )}
              </div>

              {/* Inventory */}
              <div style={styles.inventoryTitle}>🎒 Inventory ({myAgent.inventory.length})</div>
              <div style={styles.inventoryList}>
                {myAgent.inventory.length === 0
                  ? <span style={styles.emptyText}>Empty</span>
                  : myAgent.inventory.map((item) => (
                    <div key={item.id} style={styles.inventoryItem}>
                      <span style={styles.itemName}>{item.name}</span>
                      {item.value != null && <span style={styles.itemVal}>{item.value} RU</span>}
                    </div>
                  ))}
              </div>
            </>
          ) : (
            <p style={styles.emptyText}>No agent assigned yet.</p>
          )}
        </div>

        {/* ── Center: tabs (map / event / memory) ── */}
        <div style={styles.centerPanel}>
          <div style={styles.tabBar}>
            {(['map', 'event', 'memory'] as const).map((tab) => (
              <button
                key={tab}
                style={{ ...styles.tabBtn, ...(activeTab === tab ? styles.tabBtnActive : {}) }}
                onClick={() => setActiveTab(tab)}
              >
                {tab === 'map' ? '🗺 Map' : tab === 'event' ? `📖 Event${eventState?.phase === 'active' ? ' ●' : ''}` : '🧠 Memory'}
              </button>
            ))}
          </div>

          {activeTab === 'map' && (
            <>
              <p style={styles.mapHint}>
                {canAct
                  ? 'Click a connected location once to select, twice to move instantly.'
                  : myAgent?.scheduled_action
                  ? `${SCHED_ICONS[myAgent.scheduled_action.type]} ${myAgent.scheduled_action.type} in progress — ${myAgent.scheduled_action.turns_remaining}h remaining.`
                  : 'Waiting for turn.'}
              </p>
              <div style={styles.locationGrid}>
                {locations.map((loc) => {
                  const isCurrentLoc = loc.id === currentLocId;
                  const isConnected = currentLoc?.connections.some((c) => c.to === loc.id) ?? false;
                  const isSelected = loc.id === selectedLocId;
                  const agentHere = zoneState
                    ? loc.agents.filter((aid) => zoneState.agents[aid]?.is_alive).length +
                      loc.agents.filter((aid) => zoneState.mutants[aid]?.is_alive).length
                    : 0;
                  const tradersHere = Object.values(zoneState?.traders ?? {}).filter((t) => t.location_id === loc.id).length;

                  return (
                    <div
                      key={loc.id}
                      style={{
                        ...styles.locationCard,
                        ...(isCurrentLoc ? styles.locationCurrent : {}),
                        ...(isConnected && canAct ? styles.locationReachable : {}),
                        ...(isSelected ? styles.locationSelected : {}),
                        cursor: isConnected && canAct ? 'pointer' : 'default',
                        borderLeftColor: LOC_TYPE_COLOR[loc.type] ?? '#475569',
                      }}
                      onClick={() => {
                        if (isConnected && canAct) {
                          if (isSelected) handleMove(loc.id);
                          else setSelectedLocId(loc.id);
                        }
                      }}
                    >
                      <div style={styles.locRow}>
                        <span style={styles.locName}>{loc.name}</span>
                        <span style={{ ...styles.dangerBadge, background: DANGER_COLORS[Math.min(loc.danger_level - 1, 4)] }}>
                          ⚠ {loc.danger_level}
                        </span>
                      </div>
                      <div style={styles.locType}>{loc.type.replace('_', ' ')}</div>
                      <div style={styles.locIcons}>
                        {isCurrentLoc && <span style={styles.locBadgeSelf}>📍 You</span>}
                        {agentHere > 0 && <span style={styles.locBadge}>👥 {agentHere}</span>}
                        {tradersHere > 0 && <span style={styles.locBadge}>🏪 Trader</span>}
                        {loc.artifacts.length > 0 && <span style={styles.locBadgeArt}>💎 {loc.artifacts.length}</span>}
                        {loc.anomalies.length > 0 && <span style={styles.locBadgeAnom}>☢ {loc.anomalies.length}</span>}
                      </div>
                      {isSelected && isConnected && <div style={styles.moveHint}>Click again to move →</div>}
                    </div>
                  );
                })}
              </div>

              {/* Current location details */}
              {currentLoc && (
                <div style={styles.locDetail}>
                  <div style={styles.locDetailTitle}>📍 {currentLoc.name}</div>
                  {currentLoc.artifacts.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>💎 Artifacts</div>
                      {currentLoc.artifacts.map((art) => (
                        <div key={art.id} style={styles.locDetailItem}>
                          <span>{art.name}</span>
                          <span style={styles.itemVal}>{art.value} RU</span>
                          {canAct && (
                            <button style={styles.pickUpBtn} onClick={() => handlePickUpArtifact(art.id)} disabled={actionLoading}>
                              Pick up
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {currentLoc.items.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>📦 Items</div>
                      {currentLoc.items.map((item) => (
                        <div key={item.id} style={styles.locDetailItem}>
                          <span>{item.name}</span>
                          {canAct && (
                            <button style={styles.pickUpBtn} onClick={() => handlePickUpItem(item.id)} disabled={actionLoading}>
                              Pick up
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {currentLoc.anomalies.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>☢ Anomalies</div>
                      {currentLoc.anomalies.map((a) => (
                        <div key={a.id} style={{ ...styles.locDetailItem, color: '#a855f7' }}>{a.name}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {activeTab === 'event' && (
            <div style={styles.eventPanel}>
              {!eventState ? (
                <p style={styles.emptyText}>No active events. Explore the Zone to find them.</p>
              ) : (
                <>
                  <div style={styles.eventTitle}>📖 {eventState.title}</div>
                  <div style={{ ...styles.eventPhase, color: eventState.phase === 'ended' ? '#64748b' : '#22c55e' }}>
                    {eventState.phase === 'waiting' ? 'Waiting to start…'
                      : eventState.phase === 'active' ? `Round ${eventState.current_turn} / ${eventState.max_turns}`
                      : 'Concluded'}
                  </div>

                  {/* Narration history */}
                  <div style={styles.narrationHistory}>
                    {eventState.narration_history.map((h) => (
                      <div key={h.turn} style={styles.narrationEntry}>
                        <div style={styles.narrationTurn}>Round {h.turn}</div>
                        <div style={styles.narrationText}>{h.narration}</div>
                        {Object.keys(h.choices).length > 0 && (
                          <div style={styles.choicesSummary}>
                            {Object.entries(h.choices).map(([pid, choice]) => (
                              <span key={pid} style={styles.choiceBadge}>
                                {pid.slice(0, 4)}: {choice}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Current options */}
                  {eventState.phase === 'active' && eventState.current_options.length > 0 && (
                    <div style={styles.optionsPanel}>
                      <div style={styles.optionsLabel}>
                        {canChooseOption ? 'Choose your action:' : myEventParticipation?.choice != null ? '✓ You chose — waiting for others…' : 'Observing…'}
                      </div>
                      {eventState.current_options.map((opt, i) => (
                        <button
                          key={i}
                          style={{
                            ...styles.optionBtn,
                            ...(myEventParticipation?.choice === i ? styles.optionBtnChosen : {}),
                            ...((!canChooseOption) ? styles.btnDisabled : {}),
                          }}
                          onClick={() => canChooseOption && handleChooseOption(i)}
                          disabled={actionLoading || !canChooseOption}
                        >
                          {i + 1}. {opt}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Outcome */}
                  {eventState.phase === 'ended' && eventState.outcome && (
                    <div style={styles.outcomeBox}>
                      <div style={styles.outcomeLabel}>📜 Outcome</div>
                      <p style={styles.outcomeText}>{eventState.outcome}</p>
                    </div>
                  )}

                  {/* Leave event */}
                  {eventState.phase !== 'ended' && myEventParticipation?.status === 'active' && (
                    <button style={styles.leaveEventBtn} onClick={handleLeaveEvent} disabled={actionLoading}>
                      Leave Event
                    </button>
                  )}
                </>
              )}
            </div>
          )}

          {activeTab === 'memory' && (
            <div style={styles.memoryPanel}>
              <div style={styles.panelTitle}>🧠 Stalker Memory</div>
              {(!myAgent || myAgent.memory.length === 0) ? (
                <p style={styles.emptyText}>No memories yet. Go explore the Zone!</p>
              ) : (
                <div style={styles.memoryList}>
                  {[...myAgent.memory].reverse().map((m, i) => (
                    <div key={i} style={styles.memoryEntry}>
                      <div style={styles.memoryHeader}>
                        <span style={styles.memoryType}>{SCHED_ICONS[m.type] ?? '📝'} {m.type}</span>
                        <span style={styles.memoryWhen}>Day {m.world_day} · {HOUR_LABEL(m.world_hour)}</span>
                      </div>
                      <div style={styles.memoryTitle}>{m.title}</div>
                      <div style={styles.memorySummary}>{m.summary}</div>
                      {Object.keys(m.effects).filter(k => m.effects[k] !== 0).length > 0 && (
                        <div style={styles.memoryEffects}>
                          {Object.entries(m.effects).filter(([, v]) => v !== 0).map(([k, v]) => (
                            <span key={k} style={{ ...styles.effectChip, color: v > 0 ? '#86efac' : '#fca5a5' }}>
                              {k}: {v > 0 ? '+' : ''}{v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Right: action panel + event log ── */}
        <div style={styles.rightPanel}>
          {renderActionPanel()}

          <div style={styles.eventsPanel}>
            <div style={styles.panelTitle}>📜 Event Log</div>
            {renderEvents()}
          </div>
        </div>
      </div>
    );
  };

  // ─── render: events ──────────────────────────────────────────────────────
  const renderEvents = () => {
    const contextEvents = context
      ? [...events]
          .filter((e) => e.context_id === context.id)
          .sort((a, b) => b.sequence_no - a.sequence_no)
          .slice(0, 20)
      : [];
    if (contextEvents.length === 0) return <p style={styles.emptyText}>No events yet.</p>;
    return (
      <div style={styles.eventList}>
        {contextEvents.map((ev) => (
          <div key={ev.id} style={styles.eventEntry}>
            <span style={styles.evType}>{ev.event_type}</span>
            <span style={styles.evPayload}>
              {Object.entries(ev.payload)
                .slice(0, 3)
                .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                .join(' · ')}
            </span>
          </div>
        ))}
      </div>
    );
  };

  // ─── main render ─────────────────────────────────────────────────────────
  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <h2 style={styles.title}>
          ☢️ Zone Stalkers
          <span style={styles.matchIdBadge}>{match.id.slice(0, 8)}…</span>
        </h2>
        <div style={styles.headerRight}>
          <span style={{ ...styles.statusPill, background: isActive ? '#166534' : '#334155' }}>
            {match.status}
          </span>
          {isCreator && (
            <button style={styles.btnDangerSmall} onClick={handleDeleteMatch} disabled={actionLoading}>
              ✕ Close Room
            </button>
          )}
        </div>
      </div>
      {isWaiting && renderLobby()}
      {isActive && renderZoneMap()}
      {!isWaiting && !isActive && <p style={styles.loadingText}>Match status: {match.status}</p>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', gap: '1.25rem' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  headerRight: { display: 'flex', alignItems: 'center', gap: 10 },
  title: { color: '#f8fafc', margin: 0, fontSize: '1.25rem', display: 'flex', alignItems: 'center', gap: 10 },
  matchIdBadge: { color: '#475569', fontSize: '0.75rem', fontWeight: 400 },
  statusPill: { padding: '0.2rem 0.6rem', borderRadius: 12, color: '#fff', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase' as const },

  // lobby
  lobby: { background: '#1e293b', borderRadius: 12, padding: '2rem', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', maxWidth: 480 },
  lobbyEmoji: { fontSize: '3rem' },
  lobbyTitle: { color: '#f8fafc', margin: 0, fontSize: '1.1rem' },
  lobbyHint: { color: '#94a3b8', margin: 0 },
  lobbyNote: { color: '#64748b', fontSize: '0.82rem', textAlign: 'center' as const, margin: 0 },
  participantList: { display: 'flex', gap: 8, flexWrap: 'wrap' as const },
  participantBadge: { background: '#334155', color: '#cbd5e1', borderRadius: 8, padding: '0.3rem 0.7rem', fontSize: '0.85rem' },
  lobbyActions: { display: 'flex', gap: 10, flexWrap: 'wrap' as const, justifyContent: 'center' as const },

  btnPrimary: { padding: '0.5rem 1.2rem', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: '0.95rem' },
  btnDisabled: { background: '#334155', color: '#64748b', cursor: 'not-allowed' as const },
  btnDanger: { padding: '0.5rem 1.2rem', background: '#7f1d1d', color: '#fca5a5', border: '1px solid #ef4444', borderRadius: 8, cursor: 'pointer', fontWeight: 600 },
  btnDangerSmall: { padding: '0.25rem 0.7rem', background: '#7f1d1d', color: '#fca5a5', border: '1px solid #ef4444', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: '0.78rem' },
  cancelBtn: { padding: '0.25rem 0.6rem', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem', marginTop: 4 },

  // map layout
  mapContainer: { display: 'flex', gap: '1rem', alignItems: 'flex-start', flexWrap: 'wrap' as const },

  agentPanel: {
    width: 200, flexShrink: 0,
    background: '#1e293b', borderRadius: 10, padding: '0.9rem',
    display: 'flex', flexDirection: 'column', gap: '0.4rem',
    border: '1px solid #334155',
  },
  panelTitle: { color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase' as const, letterSpacing: '0.06em', marginBottom: 2, fontWeight: 700 },
  agentName: { color: '#f8fafc', fontWeight: 700, fontSize: '1rem' },
  agentFaction: { color: '#64748b', fontSize: '0.78rem' },
  statRow: { display: 'flex', alignItems: 'center', gap: 6 },
  statLabel: { color: '#94a3b8', fontSize: '0.72rem', width: 32, flexShrink: 0 },
  barBg: { flex: 1, height: 6, background: '#0f172a', borderRadius: 3, overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 3, transition: 'width 0.3s' },
  statVal: { color: '#94a3b8', fontSize: '0.7rem', width: 36, textAlign: 'right' as const },
  moneyRow: { color: '#fbbf24', fontSize: '0.85rem', fontWeight: 600, marginTop: 4 },
  locationLabel: { color: '#60a5fa', fontSize: '0.8rem' },
  turnInfo: { color: '#475569', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' as const },
  actionUsedBadge: { background: '#334155', color: '#94a3b8', borderRadius: 6, padding: '0 5px', fontSize: '0.66rem' },
  inventoryTitle: { color: '#94a3b8', fontSize: '0.75rem', marginTop: 6 },
  inventoryList: { display: 'flex', flexDirection: 'column', gap: 3, maxHeight: 120, overflowY: 'auto' as const },
  inventoryItem: { display: 'flex', justifyContent: 'space-between', background: '#0f172a', borderRadius: 5, padding: '0.2rem 0.4rem' },
  itemName: { color: '#cbd5e1', fontSize: '0.72rem' },
  itemVal: { color: '#64748b', fontSize: '0.7rem' },

  // center panel
  centerPanel: { flex: 1, minWidth: 280, display: 'flex', flexDirection: 'column', gap: '0.75rem' },
  tabBar: { display: 'flex', gap: 6 },
  tabBtn: { padding: '0.35rem 0.8rem', background: '#1e293b', color: '#64748b', border: '1px solid #334155', borderRadius: 8, cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 },
  tabBtnActive: { background: '#0f172a', color: '#f8fafc', borderColor: '#475569' },

  mapHint: { color: '#64748b', fontSize: '0.8rem', margin: 0 },
  locationGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8 },
  locationCard: {
    background: '#1e293b', borderRadius: 8, padding: '0.65rem 0.7rem',
    border: '1px solid #334155', borderLeft: '4px solid #334155',
    transition: 'border-color 0.15s, background 0.1s',
  },
  locationCurrent: { background: '#1e3a2a', border: '1px solid #22c55e', borderLeft: '4px solid #22c55e' },
  locationReachable: { border: '1px solid #3b82f6' },
  locationSelected: { background: '#1e3a5f' },
  locRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 },
  locName: { color: '#f8fafc', fontWeight: 600, fontSize: '0.85rem' },
  dangerBadge: { padding: '0.1rem 0.4rem', borderRadius: 8, color: '#fff', fontSize: '0.68rem', fontWeight: 700, flexShrink: 0 },
  locType: { color: '#64748b', fontSize: '0.72rem', marginTop: 2 },
  locIcons: { display: 'flex', gap: 4, flexWrap: 'wrap' as const, marginTop: 4 },
  locBadge: { background: '#334155', color: '#94a3b8', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  locBadgeSelf: { background: '#166534', color: '#86efac', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem', fontWeight: 700 },
  locBadgeArt: { background: '#312e81', color: '#a5b4fc', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  locBadgeAnom: { background: '#4a044e', color: '#e879f9', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  moveHint: { color: '#60a5fa', fontSize: '0.7rem', marginTop: 4, fontStyle: 'italic' },

  locDetail: { background: '#1e293b', borderRadius: 8, padding: '0.75rem', border: '1px solid #334155' },
  locDetailTitle: { color: '#f8fafc', fontWeight: 700, fontSize: '0.9rem', marginBottom: 8 },
  locDetailSection: { marginBottom: 8 },
  locDetailLabel: { color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase' as const, marginBottom: 4, fontWeight: 700 },
  locDetailItem: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem', color: '#cbd5e1', marginBottom: 3 },
  pickUpBtn: { marginLeft: 'auto', padding: '0.15rem 0.55rem', background: '#1e3a5f', color: '#60a5fa', border: '1px solid #3b82f6', borderRadius: 5, cursor: 'pointer', fontSize: '0.72rem' },

  // event panel
  eventPanel: { display: 'flex', flexDirection: 'column', gap: '0.75rem' },
  eventTitle: { color: '#f8fafc', fontWeight: 700, fontSize: '1.05rem' },
  eventPhase: { fontSize: '0.8rem', fontWeight: 600 },
  narrationHistory: { display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 280, overflowY: 'auto' as const },
  narrationEntry: { background: '#0f172a', borderRadius: 8, padding: '0.75rem', border: '1px solid #334155' },
  narrationTurn: { color: '#475569', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase' as const, marginBottom: 4 },
  narrationText: { color: '#cbd5e1', fontSize: '0.88rem', lineHeight: 1.6 },
  choicesSummary: { display: 'flex', gap: 6, flexWrap: 'wrap' as const, marginTop: 8 },
  choiceBadge: { background: '#1e3a5f', color: '#60a5fa', borderRadius: 8, padding: '0.15rem 0.5rem', fontSize: '0.7rem' },
  optionsPanel: { display: 'flex', flexDirection: 'column', gap: 8 },
  optionsLabel: { color: '#94a3b8', fontSize: '0.8rem', fontWeight: 600 },
  optionBtn: { padding: '0.6rem 1rem', background: '#1e293b', color: '#f8fafc', border: '1px solid #334155', borderRadius: 8, cursor: 'pointer', fontSize: '0.9rem', textAlign: 'left' as const },
  optionBtnChosen: { background: '#166534', borderColor: '#22c55e', color: '#86efac' },
  outcomeBox: { background: '#1e293b', borderRadius: 8, padding: '0.75rem', border: '1px solid #475569' },
  outcomeLabel: { color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700, marginBottom: 6 },
  outcomeText: { color: '#cbd5e1', fontSize: '0.9rem', margin: 0, lineHeight: 1.6 },
  leaveEventBtn: { padding: '0.3rem 0.8rem', background: 'transparent', color: '#64748b', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem', alignSelf: 'flex-start' as const },

  // memory panel
  memoryPanel: { display: 'flex', flexDirection: 'column', gap: '0.6rem' },
  memoryList: { display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 480, overflowY: 'auto' as const },
  memoryEntry: { background: '#1e293b', borderRadius: 8, padding: '0.65rem', border: '1px solid #334155' },
  memoryHeader: { display: 'flex', justifyContent: 'space-between', marginBottom: 4 },
  memoryType: { color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700 },
  memoryWhen: { color: '#475569', fontSize: '0.7rem' },
  memoryTitle: { color: '#f8fafc', fontWeight: 600, fontSize: '0.85rem', marginBottom: 4 },
  memorySummary: { color: '#94a3b8', fontSize: '0.8rem', lineHeight: 1.5 },
  memoryEffects: { display: 'flex', gap: 6, flexWrap: 'wrap' as const, marginTop: 6 },
  effectChip: { background: '#0f172a', borderRadius: 6, padding: '0.1rem 0.45rem', fontSize: '0.7rem' },

  // right panel
  rightPanel: { width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '1rem' },

  // action panel
  actionPanel: { background: '#1e293b', borderRadius: 10, padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', border: '1px solid #334155' },
  actionBtn: { padding: '0.4rem 0.8rem', background: '#0f172a', color: '#cbd5e1', border: '1px solid #334155', borderRadius: 7, cursor: 'pointer', fontSize: '0.82rem', textAlign: 'left' as const },
  endTurnBtn: { padding: '0.45rem 1rem', background: '#334155', color: '#f8fafc', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: '0.875rem', marginTop: 4 },
  tickBtn: { padding: '0.3rem 0.8rem', background: 'transparent', color: '#475569', border: '1px dashed #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.75rem' },

  schedCard: { background: '#0f172a', borderRadius: 8, padding: '0.6rem', border: '1px solid #1e3a5f', display: 'flex', flexDirection: 'column', gap: 6 },
  schedRow: { display: 'flex', alignItems: 'center', gap: 6 },
  schedIcon: { fontSize: '1.2rem', flexShrink: 0 },
  schedLabel: { color: '#cbd5e1', fontSize: '0.78rem', flex: 1, lineHeight: 1.3 },
  schedTurns: { color: '#60a5fa', fontSize: '0.72rem', fontWeight: 700, flexShrink: 0 },
  progressBg: { height: 4, background: '#334155', borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', background: '#3b82f6', borderRadius: 2, transition: 'width 0.3s' },
  schedHint: { color: '#475569', fontSize: '0.68rem', margin: 0 },

  sleepRow: { display: 'flex', gap: 6, alignItems: 'center' },
  sleepSelect: { padding: '0.35rem 0.4rem', background: '#0f172a', color: '#cbd5e1', border: '1px solid #334155', borderRadius: 6, fontSize: '0.8rem' },

  travelPanel: { background: '#0f172a', borderRadius: 8, padding: '0.6rem', display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflowY: 'auto' as const },
  travelLocBtn: { padding: '0.3rem 0.6rem', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.78rem', textAlign: 'left' as const },
  travelLocBtnSelected: { background: '#1e3a5f', color: '#f8fafc', borderColor: '#3b82f6' },
  travelDanger: { color: '#f59e0b', fontSize: '0.7rem' },

  eventsPanel: { background: '#1e293b', borderRadius: 10, padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', border: '1px solid #334155', flex: 1 },
  eventList: { display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 280, overflowY: 'auto' as const },
  eventEntry: { background: '#0f172a', borderRadius: 6, padding: '0.3rem 0.5rem', display: 'flex', flexDirection: 'column', gap: 2 },
  evType: { color: '#a78bfa', fontWeight: 600, fontSize: '0.73rem' },
  evPayload: { color: '#475569', fontSize: '0.67rem' },

  emptyText: { color: '#475569', fontSize: '0.8rem' },
  loadingText: { color: '#94a3b8' },
  error: { color: '#f87171', fontSize: '0.85rem', margin: 0 },
};

