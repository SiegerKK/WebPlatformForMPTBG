import React, { useEffect, useRef, useState } from 'react';
import { useAppState } from './store';
import { authApi, matchesApi } from './api/client';
import Login from './components/Login/Login';
import Layout from './components/Layout/Layout';
import MatchList from './components/MatchList/MatchList';
import TicTacToeGame from './components/TicTacToeGame';
import type { User, Match } from './types';

type View = 'matches' | 'match';

/** Extract a match UUID from a URL hash like `#/match/uuid`. */
function parseHashMatchId(): string | null {
  const m = window.location.hash.match(/^#\/match\/([0-9a-f-]{36})$/i);
  return m ? m[1] : null;
}

export default function App() {
  const { state, dispatch } = useAppState();

  // Initialise the view from the URL hash so there is no flash on refresh.
  const [view, setView] = useState<View>(() =>
    parseHashMatchId() ? 'match' : 'matches',
  );

  // Keep a ref that is always current so the hashchange handler never closes
  // over a stale currentMatch id.
  const currentMatchIdRef = useRef<string | null>(null);

  // ── Restore user session on mount ─────────────────────────────────────────
  useEffect(() => {
    if (state.token && !state.user) {
      authApi
        .me()
        .then((res) => dispatch({ type: 'SET_USER', payload: res.data as User }))
        .catch(() => dispatch({ type: 'LOGOUT' }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Restore match from URL hash after login / on first load ───────────────
  useEffect(() => {
    if (!state.token) return;
    const matchId = parseHashMatchId();
    if (matchId && !state.currentMatch) {
      matchesApi
        .get(matchId)
        .then((res) => dispatch({ type: 'SET_CURRENT_MATCH', payload: res.data as Match }))
        .catch(() => {
          window.location.hash = '';
        });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.token]);

  // ── Keep ref in sync with store (must run before hash-sync effect) ─────────
  useEffect(() => {
    currentMatchIdRef.current = state.currentMatch?.id ?? null;
  }, [state.currentMatch?.id]);

  // ── Sync store → URL hash and view ────────────────────────────────────────
  useEffect(() => {
    if (!state.currentMatch) {
      setView('matches');
      if (window.location.hash) window.location.hash = '';
      return;
    }
    setView('match');
    const newHash = `#/match/${state.currentMatch.id}`;
    if (window.location.hash !== newHash) {
      window.location.hash = newHash;
    }
  }, [state.currentMatch?.id]);

  // ── Sync URL hash → store (browser back / forward) ────────────────────────
  useEffect(() => {
    const handleHashChange = () => {
      const matchId = parseHashMatchId();
      if (!matchId) {
        if (currentMatchIdRef.current !== null) {
          dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
        }
      } else if (matchId !== currentMatchIdRef.current) {
        matchesApi
          .get(matchId)
          .then((res) =>
            dispatch({ type: 'SET_CURRENT_MATCH', payload: res.data as Match }),
          )
          .catch(() => {
            window.location.hash = '';
          });
      }
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [dispatch]);

  if (!state.token) {
    return <Login />;
  }

  const isTicTacToe = state.currentMatch?.game_id === 'tictactoe';

  const handleMatchDeleted = (deletedId: string) => {
    dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
    dispatch({
      type: 'SET_MATCHES',
      payload: state.matches.filter((m) => m.id !== deletedId),
    });
  };

  return (
    <Layout
      onNavSelect={(v) => {
        if (v === 'matches') dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
        setView(v as View);
      }}
      activeView={view}
    >
      {view === 'matches' && <MatchList />}
      {view === 'match' && (
        <div>
          {!state.currentMatch ? (
            <p style={styles.hint}>Select a match from the Matches list.</p>
          ) : isTicTacToe && state.user ? (
            <TicTacToeGame
              match={state.currentMatch}
              user={state.user}
              onMatchUpdated={(updated: Match) =>
                dispatch({ type: 'SET_CURRENT_MATCH', payload: updated })
              }
              onMatchDeleted={handleMatchDeleted}
            />
          ) : (
            <div style={styles.matchView}>
              <div style={styles.matchHeader}>
                <h2 style={styles.matchTitle}>
                  {state.currentMatch.game_id}
                  <span style={styles.matchStatus}> — {state.currentMatch.status}</span>
                </h2>
                <p style={styles.matchId}>Match ID: {state.currentMatch.id}</p>
              </div>
              <p style={styles.hint}>
                No dedicated UI for game <strong>{state.currentMatch.game_id}</strong> yet.
              </p>
            </div>
          )}
        </div>
      )}
    </Layout>
  );
}

const styles: Record<string, React.CSSProperties> = {
  matchView: { display: 'flex', flexDirection: 'column', gap: '1.5rem' },
  hint: { color: '#64748b', textAlign: 'center', marginTop: '3rem' },
  matchHeader: {},
  matchTitle: { color: '#f8fafc', margin: 0, fontSize: '1.3rem' },
  matchStatus: { color: '#94a3b8', fontWeight: 400 },
  matchId: { color: '#475569', fontSize: '0.8rem', margin: '0.25rem 0 0' },
};
