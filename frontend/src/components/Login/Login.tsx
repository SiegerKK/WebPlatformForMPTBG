import React, { useState } from 'react';
import { authApi } from '../../api/client';
import { useAppState } from '../../store';
import type { User, Token } from '../../types';

export default function Login() {
  const { dispatch } = useAppState();
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await authApi.login(username, password);
      const token: Token = res.data;
      localStorage.setItem('access_token', token.access_token);
      dispatch({ type: 'SET_TOKEN', payload: token.access_token });
      const meRes = await authApi.me();
      dispatch({ type: 'SET_USER', payload: meRes.data as User });
    } catch {
      setError('Login failed. Check your credentials.');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await authApi.register({ username, email, password });
      const res = await authApi.login(username, password);
      const token: Token = res.data;
      localStorage.setItem('access_token', token.access_token);
      dispatch({ type: 'SET_TOKEN', payload: token.access_token });
      const meRes = await authApi.me();
      dispatch({ type: 'SET_USER', payload: meRes.data as User });
    } catch {
      setError('Registration failed. Username or email may already be taken.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h1 style={styles.title}>WebPlatformForMPTBG</h1>
        <p style={styles.subtitle}>Async Turn-Based Multiplayer Game Platform</p>

        <div style={styles.tabs}>
          <button
            style={{ ...styles.tab, ...(tab === 'login' ? styles.tabActive : {}) }}
            onClick={() => setTab('login')}
          >
            Login
          </button>
          <button
            style={{ ...styles.tab, ...(tab === 'register' ? styles.tabActive : {}) }}
            onClick={() => setTab('register')}
          >
            Register
          </button>
        </div>

        <form onSubmit={tab === 'login' ? handleLogin : handleRegister} style={styles.form}>
          <label style={styles.label}>Username</label>
          <input
            style={styles.input}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
          />

          {tab === 'register' && (
            <>
              <label style={styles.label}>Email</label>
              <input
                style={styles.input}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </>
          )}

          <label style={styles.label}>Password</label>
          <input
            style={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
          />

          {error && <p style={styles.error}>{error}</p>}

          <button style={styles.button} type="submit" disabled={loading}>
            {loading ? 'Please wait…' : tab === 'login' ? 'Login' : 'Register'}
          </button>
        </form>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#0f172a',
  },
  card: {
    background: '#1e293b',
    borderRadius: 12,
    padding: '2rem',
    width: 360,
    boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
  },
  title: {
    color: '#f8fafc',
    margin: 0,
    fontSize: '1.4rem',
    textAlign: 'center',
  },
  subtitle: {
    color: '#94a3b8',
    textAlign: 'center',
    fontSize: '0.85rem',
    margin: '0.4rem 0 1.5rem',
  },
  tabs: {
    display: 'flex',
    gap: 8,
    marginBottom: '1.2rem',
  },
  tab: {
    flex: 1,
    padding: '0.5rem',
    background: '#334155',
    border: 'none',
    borderRadius: 6,
    color: '#94a3b8',
    cursor: 'pointer',
    fontSize: '0.9rem',
  },
  tabActive: {
    background: '#3b82f6',
    color: '#fff',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  label: {
    color: '#cbd5e1',
    fontSize: '0.85rem',
  },
  input: {
    padding: '0.5rem 0.75rem',
    borderRadius: 6,
    border: '1px solid #475569',
    background: '#0f172a',
    color: '#f8fafc',
    fontSize: '0.95rem',
    marginBottom: 8,
  },
  button: {
    marginTop: 8,
    padding: '0.6rem',
    background: '#3b82f6',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: '1rem',
  },
  error: {
    color: '#f87171',
    fontSize: '0.85rem',
    margin: 0,
  },
};
