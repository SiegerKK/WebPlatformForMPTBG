import React, { useEffect, useState } from 'react';
import { usersApi } from '../../api/client';
import type { User } from '../../types';

interface Props {
  currentUserId: string;
  onViewProfile: (userId: string) => void;
}

export default function AdminPanel({ currentUserId, onViewProfile }: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await usersApi.list();
      setUsers(res.data as User[]);
    } catch {
      setError('Failed to load users.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleToggleActive = async (user: User) => {
    setActionLoading(user.id);
    try {
      const res = await usersApi.update(user.id, { is_active: !user.is_active });
      setUsers((prev) => prev.map((u) => (u.id === user.id ? (res.data as User) : u)));
    } catch {
      setError('Failed to update user.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleToggleSuperuser = async (user: User) => {
    setActionLoading(user.id + '_su');
    try {
      const res = await usersApi.update(user.id, { is_superuser: !user.is_superuser });
      setUsers((prev) => prev.map((u) => (u.id === user.id ? (res.data as User) : u)));
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to update user.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (user: User) => {
    if (!window.confirm(`Delete user "${user.username}"? This cannot be undone.`)) return;
    setActionLoading(user.id + '_del');
    try {
      await usersApi.delete(user.id);
      setUsers((prev) => prev.filter((u) => u.id !== user.id));
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to delete user.');
    } finally {
      setActionLoading(null);
    }
  };

  const filtered = users.filter(
    (u) =>
      u.username.toLowerCase().includes(search.toLowerCase()) ||
      u.email.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h2 style={styles.title}>👥 User Management</h2>
        <button style={styles.refreshBtn} onClick={loadUsers}>↻ Refresh</button>
      </div>

      <input
        style={styles.search}
        type="text"
        placeholder="Search by username or email…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {error && <p style={styles.error}>{error}</p>}
      {loading && <p style={styles.hint}>Loading…</p>}

      {!loading && (
        <div style={styles.tableWrapper}>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>Username</th>
                <th style={styles.th}>Email</th>
                <th style={styles.th}>Joined</th>
                <th style={styles.th}>Status</th>
                <th style={styles.th}>Role</th>
                <th style={styles.th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((user) => {
                const isMe = user.id === currentUserId;
                return (
                  <tr
                    key={user.id}
                    style={{ ...styles.tr, ...(isMe ? styles.trMe : {}) }}
                  >
                    <td style={styles.td}>
                      <button
                        style={styles.usernameLink}
                        onClick={() => onViewProfile(user.id)}
                        title="View profile"
                      >
                        {user.username}
                        {isMe && <span style={styles.meTag}> (you)</span>}
                      </button>
                    </td>
                    <td style={styles.td}>{user.email}</td>
                    <td style={styles.td}>
                      {new Date(user.created_at).toLocaleDateString()}
                    </td>
                    <td style={styles.td}>
                      <span
                        style={{
                          ...styles.badge,
                          background: user.is_active ? '#166534' : '#7f1d1d',
                          color: user.is_active ? '#86efac' : '#fca5a5',
                        }}
                      >
                        {user.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td style={styles.td}>
                      <span
                        style={{
                          ...styles.badge,
                          background: user.is_superuser ? '#1e3a5f' : '#1e293b',
                          color: user.is_superuser ? '#93c5fd' : '#64748b',
                        }}
                      >
                        {user.is_superuser ? '🛡 Admin' : 'User'}
                      </span>
                    </td>
                    <td style={styles.tdActions}>
                      <button
                        style={styles.actionBtn}
                        onClick={() => onViewProfile(user.id)}
                        title="View profile"
                      >
                        Profile
                      </button>
                      <button
                        style={{
                          ...styles.actionBtn,
                          background: user.is_active ? '#334155' : '#166534',
                          color: user.is_active ? '#f87171' : '#86efac',
                        }}
                        onClick={() => handleToggleActive(user)}
                        disabled={actionLoading === user.id || isMe}
                        title={isMe ? 'Cannot deactivate yourself' : ''}
                      >
                        {actionLoading === user.id
                          ? '…'
                          : user.is_active
                          ? 'Deactivate'
                          : 'Activate'}
                      </button>
                      <button
                        style={{
                          ...styles.actionBtn,
                          background: user.is_superuser ? '#7c2d12' : '#1e3a5f',
                          color: user.is_superuser ? '#fdba74' : '#93c5fd',
                        }}
                        onClick={() => handleToggleSuperuser(user)}
                        disabled={actionLoading === user.id + '_su' || isMe}
                        title={isMe ? 'Cannot change your own admin role' : ''}
                      >
                        {actionLoading === user.id + '_su'
                          ? '…'
                          : user.is_superuser
                          ? 'Revoke Admin'
                          : 'Make Admin'}
                      </button>
                      {!isMe && (
                        <button
                          style={{ ...styles.actionBtn, ...styles.deleteBtn }}
                          onClick={() => handleDelete(user)}
                          disabled={actionLoading === user.id + '_del'}
                        >
                          {actionLoading === user.id + '_del' ? '…' : 'Delete'}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <p style={styles.hint}>No users match your search.</p>
          )}
        </div>
      )}
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
  },
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
  search: {
    width: '100%',
    padding: '0.5rem 0.75rem',
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: 6,
    color: '#f8fafc',
    fontSize: '0.9rem',
    marginBottom: '1rem',
    boxSizing: 'border-box',
  },
  error: { color: '#f87171', fontSize: '0.85rem' },
  hint: { color: '#64748b', textAlign: 'center', padding: '1rem 0' },
  tableWrapper: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: {
    textAlign: 'left',
    color: '#94a3b8',
    fontSize: '0.75rem',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    padding: '0.5rem 0.75rem',
    borderBottom: '1px solid #334155',
  },
  tr: { borderBottom: '1px solid #1e293b' },
  trMe: { background: 'rgba(59,130,246,0.06)' },
  td: {
    color: '#cbd5e1',
    fontSize: '0.85rem',
    padding: '0.5rem 0.75rem',
    verticalAlign: 'middle',
  },
  tdActions: {
    padding: '0.4rem 0.75rem',
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap',
    alignItems: 'center',
  },
  badge: {
    padding: '0.15rem 0.5rem',
    borderRadius: 10,
    fontSize: '0.75rem',
    fontWeight: 600,
  },
  usernameLink: {
    background: 'none',
    border: 'none',
    color: '#60a5fa',
    cursor: 'pointer',
    padding: 0,
    fontSize: '0.85rem',
    fontWeight: 600,
    textDecoration: 'underline',
    textUnderlineOffset: 2,
  },
  meTag: { color: '#64748b', fontWeight: 400, textDecoration: 'none' },
  actionBtn: {
    padding: '0.25rem 0.6rem',
    background: '#334155',
    border: 'none',
    borderRadius: 5,
    color: '#cbd5e1',
    cursor: 'pointer',
    fontSize: '0.78rem',
    fontWeight: 500,
  },
  deleteBtn: { background: '#7f1d1d', color: '#fca5a5' },
};
