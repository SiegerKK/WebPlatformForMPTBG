import React, { useEffect, useState } from 'react';
import { matchesApi } from '../../api/client';
import { useAppState } from '../../store';
import type { Match } from '../../types';

const STATUS_COLORS: Record<string, string> = {
  draft: '#94a3b8',
  waiting_for_players: '#f59e0b',
  initializing: '#f59e0b',
  active: '#22c55e',
  paused: '#64748b',
  finished: '#3b82f6',
  archived: '#334155',
  failed: '#ef4444',
};

/** Statuses that are considered "closeable" (not already finished/archived) */
const CLOSEABLE_STATUSES = new Set([
  'draft',
  'waiting_for_players',
  'initializing',
  'active',
  'paused',
]);

export default function MatchList() {
  const { state, dispatch } = useAppState();
  const [gameId, setGameId] = useState('');
  const [creating, setCreating] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closingId, setClosingId] = useState<string | null>(null);

  const isAdmin = state.user?.is_superuser ?? false;

  useEffect(() => {
    loadMatches();
  }, []);

  const loadMatches = async () => {
    dispatch({ type: 'SET_LOADING', payload: true });
    try {
      const res = await matchesApi.list();
      dispatch({ type: 'SET_MATCHES', payload: res.data as Match[] });
    } catch {
      dispatch({ type: 'SET_ERROR', payload: 'Failed to load matches.' });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  };

  const handleCreate = async (e: React.FormEvent, overrideGameId?: string) => {
    e.preventDefault();
    const id = overrideGameId ?? gameId.trim();
    if (!id) return;
    setError(null);
    setCreating(true);
    try {
      const res = await matchesApi.create({ game_id: id });
      dispatch({ type: 'SET_MATCHES', payload: [res.data as Match, ...state.matches] });
      setShowCreate(false);
      setGameId('');
    } catch {
      setError('Failed to create match.');
    } finally {
      setCreating(false);
    }
  };

  const handleQuickCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const res = await matchesApi.create({ game_id: 'tictactoe' });
      const newMatch = res.data as Match;
      dispatch({ type: 'SET_MATCHES', payload: [newMatch, ...state.matches] });
      dispatch({ type: 'SET_CURRENT_MATCH', payload: newMatch });
    } catch {
      setError('Failed to create Tic-Tac-Toe match.');
    } finally {
      setCreating(false);
    }
  };

  const handleClose = async (e: React.MouseEvent, match: Match) => {
    e.stopPropagation(); // don't open the match
    if (!window.confirm(`Close room "${match.game_id}" (${match.id.slice(0, 8)}…)? This will archive the match.`)) return;
    setClosingId(match.id);
    try {
      await matchesApi.delete(match.id);
      // Update the match status in the list to 'archived'
      dispatch({
        type: 'SET_MATCHES',
        payload: state.matches.map((m) =>
          m.id === match.id ? { ...m, status: 'archived' as const } : m,
        ),
      });
      // If this was the currently viewed match, clear it
      if (state.currentMatch?.id === match.id) {
        dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? 'Failed to close the room.');
    } finally {
      setClosingId(null);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h2 style={styles.title}>Matches</h2>
        <div style={styles.headerActions}>
          <button style={styles.refreshBtn} onClick={loadMatches}>↻ Refresh</button>
          <button
            style={styles.tttBtn}
            onClick={handleQuickCreate}
            disabled={creating}
            title="Create a new Tic-Tac-Toe match"
          >
            ✕ New Tic-Tac-Toe
          </button>
          <button style={styles.createBtn} onClick={() => setShowCreate(!showCreate)}>
            {showCreate ? 'Cancel' : '+ Other Game'}
          </button>
        </div>
      </div>

      {showCreate && (
        <form onSubmit={handleCreate} style={styles.createForm}>
          <input
            style={styles.input}
            type="text"
            placeholder="Game ID (e.g. chess, tictactoe)"
            value={gameId}
            onChange={(e) => setGameId(e.target.value)}
            required
          />
          {error && <p style={styles.error}>{error}</p>}
          <button style={styles.createBtn} type="submit" disabled={creating}>
            {creating ? 'Creating…' : 'Create'}
          </button>
        </form>
      )}

      {!showCreate && error && <p style={styles.error}>{error}</p>}

      {state.loading && <p style={styles.loading}>Loading…</p>}

      {state.matches.length === 0 && !state.loading && (
        <p style={styles.empty}>No matches found. Create one to get started.</p>
      )}

      <div style={styles.list}>
        {state.matches.map((match) => {
          const isCreator = match.created_by_user_id === state.user?.id;
          const canClose = (isCreator || isAdmin) && CLOSEABLE_STATUSES.has(match.status);

          return (
            <div
              key={match.id}
              style={{
                ...styles.matchCard,
                ...(state.currentMatch?.id === match.id ? styles.matchCardSelected : {}),
              }}
              onClick={() => dispatch({ type: 'SET_CURRENT_MATCH', payload: match })}
            >
              <div style={styles.matchRow}>
                <span style={styles.gameId}>{match.game_id}</span>
                <div style={styles.rightGroup}>
                  <span
                    style={{
                      ...styles.statusBadge,
                      background: STATUS_COLORS[match.status] ?? '#64748b',
                    }}
                  >
                    {match.status}
                  </span>
                  {canClose && (
                    <button
                      style={styles.closeBtn}
                      onClick={(e) => handleClose(e, match)}
                      disabled={closingId === match.id}
                      title={isAdmin && !isCreator ? 'Close room (admin)' : 'Close room'}
                    >
                      {closingId === match.id ? '…' : '🔒 Close'}
                    </button>
                  )}
                </div>
              </div>
              <div style={styles.matchMeta}>
                <span style={styles.metaText}>ID: {match.id.slice(0, 8)}…</span>
                <span style={styles.metaText}>
                  {new Date(match.created_at).toLocaleString()}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { padding: '1rem' },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '1rem',
    flexWrap: 'wrap',
    gap: 8,
  },
  headerActions: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  title: { color: '#f8fafc', margin: 0, fontSize: '1.2rem' },
  refreshBtn: {
    padding: '0.35rem 0.75rem',
    background: '#334155',
    border: 'none',
    borderRadius: 6,
    color: '#94a3b8',
    cursor: 'pointer',
    fontSize: '0.85rem',
  },
  tttBtn: {
    padding: '0.35rem 0.85rem',
    background: '#7c3aed',
    border: 'none',
    borderRadius: 6,
    color: '#fff',
    cursor: 'pointer',
    fontSize: '0.85rem',
    fontWeight: 600,
  },
  createBtn: {
    padding: '0.35rem 0.75rem',
    background: '#3b82f6',
    border: 'none',
    borderRadius: 6,
    color: '#fff',
    cursor: 'pointer',
    fontSize: '0.85rem',
  },
  createForm: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    marginBottom: '1rem',
    padding: '1rem',
    background: '#1e293b',
    borderRadius: 8,
  },
  input: {
    padding: '0.5rem 0.75rem',
    borderRadius: 6,
    border: '1px solid #475569',
    background: '#0f172a',
    color: '#f8fafc',
    fontSize: '0.95rem',
  },
  error: { color: '#f87171', fontSize: '0.85rem', margin: 0 },
  loading: { color: '#94a3b8', textAlign: 'center' },
  empty: { color: '#64748b', textAlign: 'center', padding: '2rem 0' },
  list: { display: 'flex', flexDirection: 'column', gap: 8 },
  matchCard: {
    background: '#1e293b',
    borderRadius: 8,
    padding: '0.75rem 1rem',
    cursor: 'pointer',
    border: '1px solid #334155',
    transition: 'border-color 0.15s',
  },
  matchCardSelected: { border: '1px solid #3b82f6' },
  matchRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  rightGroup: { display: 'flex', alignItems: 'center', gap: 8 },
  gameId: { color: '#f8fafc', fontWeight: 600, fontSize: '0.95rem' },
  statusBadge: {
    padding: '0.15rem 0.5rem',
    borderRadius: 12,
    color: '#fff',
    fontSize: '0.75rem',
    fontWeight: 600,
    flexShrink: 0,
  },
  closeBtn: {
    padding: '0.18rem 0.55rem',
    background: '#7f1d1d',
    border: '1px solid #ef4444',
    borderRadius: 5,
    color: '#fca5a5',
    cursor: 'pointer',
    fontSize: '0.72rem',
    fontWeight: 600,
    flexShrink: 0,
  },
  matchMeta: { display: 'flex', justifyContent: 'space-between', marginTop: 4 },
  metaText: { color: '#64748b', fontSize: '0.75rem' },
};
