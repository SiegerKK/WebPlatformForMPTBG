import React from 'react';
import { useAppState } from '../../store';

interface LayoutProps {
  children: React.ReactNode;
  onNavSelect: (view: string) => void;
  activeView: string;
}

const BASE_NAV_ITEMS = [
  { id: 'games', label: '🕹️ Games' },
  { id: 'matches', label: '🎮 Matches' },
  { id: 'match', label: '⚔️ Current Match' },
];

const ADMIN_NAV_ITEMS = [
  { id: 'admin', label: '🛡 Admin Panel' },
];

export default function Layout({ children, onNavSelect, activeView }: LayoutProps) {
  const { state, dispatch } = useAppState();

  const handleLogout = () => {
    dispatch({ type: 'LOGOUT' });
  };

  const isAdmin = state.user?.is_superuser ?? false;
  const navItems = isAdmin ? [...BASE_NAV_ITEMS, ...ADMIN_NAV_ITEMS] : BASE_NAV_ITEMS;

  return (
    <div style={styles.root}>
      <header style={styles.header}>
        <span style={styles.appName}>WebPlatformForMPTBG</span>
        <div style={styles.userInfo}>
          {state.user && (
            <>
              <span style={styles.username}>{state.user.username}</span>
              {isAdmin && <span style={styles.adminBadge}>Admin</span>}
            </>
          )}
          <button style={styles.logoutBtn} onClick={handleLogout}>
            Logout
          </button>
        </div>
      </header>

      <div style={styles.body}>
        <nav style={styles.sidebar}>
          {navItems.map((item) => (
            <button
              key={item.id}
              style={{
                ...styles.navItem,
                ...(activeView === item.id ? styles.navItemActive : {}),
              }}
              onClick={() => onNavSelect(item.id)}
            >
              {item.label}
            </button>
          ))}

          {state.currentMatch && (
            <div style={styles.currentMatchInfo}>
              <div style={styles.currentMatchLabel}>Current Match</div>
              <div style={styles.currentMatchId}>
                {state.currentMatch.game_id}
              </div>
              <div style={styles.currentMatchStatus}>
                {state.currentMatch.status}
              </div>
            </div>
          )}
        </nav>

        <main style={styles.main}>{children}</main>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    minHeight: '100vh',
    background: '#0f172a',
    display: 'flex',
    flexDirection: 'column',
    fontFamily: 'system-ui, sans-serif',
  },
  header: {
    background: '#1e293b',
    padding: '0.6rem 1.5rem',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottom: '1px solid #334155',
  },
  appName: {
    color: '#f8fafc',
    fontWeight: 700,
    fontSize: '1rem',
    letterSpacing: '0.03em',
  },
  userInfo: { display: 'flex', alignItems: 'center', gap: 10 },
  username: { color: '#94a3b8', fontSize: '0.875rem' },
  adminBadge: {
    padding: '0.1rem 0.45rem',
    background: '#1e3a5f',
    color: '#60a5fa',
    borderRadius: 6,
    fontSize: '0.7rem',
    fontWeight: 700,
    letterSpacing: '0.04em',
  },
  logoutBtn: {
    padding: '0.3rem 0.7rem',
    background: 'transparent',
    border: '1px solid #475569',
    borderRadius: 6,
    color: '#94a3b8',
    cursor: 'pointer',
    fontSize: '0.8rem',
  },
  body: { display: 'flex', flex: 1 },
  sidebar: {
    width: 200,
    background: '#1e293b',
    borderRight: '1px solid #334155',
    padding: '1rem 0.5rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  navItem: {
    width: '100%',
    padding: '0.5rem 0.75rem',
    textAlign: 'left',
    background: 'transparent',
    border: 'none',
    borderRadius: 6,
    color: '#94a3b8',
    cursor: 'pointer',
    fontSize: '0.875rem',
  },
  navItemActive: {
    background: '#1e3a5f',
    color: '#60a5fa',
  },
  currentMatchInfo: {
    padding: '0.75rem',
    background: '#0f172a',
    borderRadius: 8,
    marginTop: 16,
  },
  currentMatchLabel: { color: '#475569', fontSize: '0.7rem', marginBottom: 2 },
  currentMatchId: { color: '#f8fafc', fontSize: '0.85rem', fontWeight: 600 },
  currentMatchStatus: { color: '#94a3b8', fontSize: '0.75rem' },
  main: { flex: 1, overflowY: 'auto', padding: '1.5rem' },
};
