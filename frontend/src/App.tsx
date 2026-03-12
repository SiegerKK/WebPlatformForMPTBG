import React, { useEffect, useRef, useState } from 'react';
import { useAppState } from './store';
import { authApi, matchesApi } from './api/client';
import Login from './components/Login/Login';
import Layout from './components/Layout/Layout';
import MatchList from './components/MatchList/MatchList';
import GameLobby from './components/GameLobby';
import TicTacToeGame from './components/TicTacToeGame';
import ZoneStalkerGame from './components/ZoneStalkerGame';
import AdminPanel from './components/AdminPanel/AdminPanel';
import UserProfile from './components/UserProfile/UserProfile';
import type { User, Match } from './types';

type View = 'games' | 'matches' | 'match' | 'admin' | 'profile';

/** Extract a match UUID from a URL hash like `#/match/uuid`. */
function parseHashMatchId(): string | null {
  const m = window.location.hash.match(/^#\/match\/([0-9a-f-]{36})$/i);
  return m ? m[1] : null;
}

/** Extract a user UUID from a URL hash like `#/profile/uuid`. */
function parseHashProfileId(): string | null {
  const m = window.location.hash.match(/^#\/profile\/([0-9a-f-]{36})$/i);
  return m ? m[1] : null;
}

/** Determine view from current hash without state. */
function viewFromHash(): View {
  if (parseHashMatchId()) return 'match';
  if (parseHashProfileId()) return 'profile';
  if (window.location.hash === '#/admin') return 'admin';
  if (window.location.hash === '#/matches') return 'matches';
  return 'games';
}

export default function App() {
  const { state, dispatch } = useAppState();

  const [view, setView] = useState<View>(() => viewFromHash());
  const [profileUserId, setProfileUserId] = useState<string | null>(
    () => parseHashProfileId(),
  );

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

  // ── Restore match from URL hash after login ───────────────────────────────
  useEffect(() => {
    if (!state.token) return;
    const matchId = parseHashMatchId();
    if (matchId && !state.currentMatch) {
      matchesApi
        .get(matchId)
        .then((res) => dispatch({ type: 'SET_CURRENT_MATCH', payload: res.data as Match }))
        .catch(() => { window.location.hash = ''; });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.token]);

  // ── Keep ref in sync with store ────────────────────────────────────────────
  useEffect(() => {
    currentMatchIdRef.current = state.currentMatch?.id ?? null;
  }, [state.currentMatch?.id]);

  // ── Sync store → URL hash and view ────────────────────────────────────────
  useEffect(() => {
    if (!state.currentMatch) {
      if (view === 'match') {
        setView('matches');
        if (window.location.hash.startsWith('#/match/')) window.location.hash = '';
      }
      return;
    }
    setView('match');
    const newHash = `#/match/${state.currentMatch.id}`;
    if (window.location.hash !== newHash) window.location.hash = newHash;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.currentMatch?.id]);

  // ── Sync URL hash → store (browser back / forward) ────────────────────────
  useEffect(() => {
    const handleHashChange = () => {
      const matchId = parseHashMatchId();
      const profileId = parseHashProfileId();
      const isAdmin = window.location.hash === '#/admin';

      if (matchId) {
        if (matchId !== currentMatchIdRef.current) {
          matchesApi
            .get(matchId)
            .then((res) =>
              dispatch({ type: 'SET_CURRENT_MATCH', payload: res.data as Match }),
            )
            .catch(() => { window.location.hash = ''; });
        }
        setView('match');
      } else if (profileId) {
        setProfileUserId(profileId);
        setView('profile');
      } else if (isAdmin) {
        setView('admin');
      } else if (window.location.hash === '#/matches') {
        setView('matches');
      } else {
        if (currentMatchIdRef.current !== null) {
          dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
        }
        setView('games');
      }
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [dispatch]);

  if (!state.token) return <Login />;

  const isTicTacToe = state.currentMatch?.game_id === 'tictactoe';
  const isZoneStalkers = state.currentMatch?.game_id === 'zone_stalkers';
  const isAdmin = state.user?.is_superuser ?? false;

  const handleMatchDeleted = (deletedId: string) => {
    dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
    dispatch({
      type: 'SET_MATCHES',
      payload: state.matches.filter((m) => m.id !== deletedId),
    });
  };

  const handleNavSelect = (v: string) => {
    if (v === 'games') {
      window.location.hash = '';
      setView('games');
    } else if (v === 'matches') {
      dispatch({ type: 'SET_CURRENT_MATCH', payload: null });
      window.location.hash = '#/matches';
      setView('matches');
    } else if (v === 'admin') {
      window.location.hash = '#/admin';
      setView('admin');
    } else {
      setView(v as View);
    }
  };

  const handleViewProfile = (userId: string) => {
    window.location.hash = `#/profile/${userId}`;
    setProfileUserId(userId);
    setView('profile');
  };

  const handleProfileBack = () => {
    window.history.back();
  };

  return (
    <Layout onNavSelect={handleNavSelect} activeView={view}>
      {view === 'games' && <GameLobby />}

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
          ) : isZoneStalkers && state.user ? (
            <ZoneStalkerGame
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

      {view === 'admin' && isAdmin && state.user && (
        <AdminPanel
          currentUserId={state.user.id}
          onViewProfile={handleViewProfile}
        />
      )}

      {view === 'profile' && profileUserId && state.user && (
        <UserProfile
          userId={profileUserId}
          isSelf={profileUserId === state.user.id}
          isAdmin={isAdmin}
          onBack={handleProfileBack}
        />
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

