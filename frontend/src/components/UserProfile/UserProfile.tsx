import React, { useEffect, useState } from 'react';
import { usersApi } from '../../api/client';
import type { UserProfile as UserProfileType } from '../../types';

interface Props {
  userId: string;
  isSelf: boolean;
  isAdmin: boolean;
  onBack: () => void;
}

export default function UserProfile({ userId, isSelf, isAdmin, onBack }: Props) {
  const [profile, setProfile] = useState<UserProfileType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = isAdmin
        ? await usersApi.getProfile(userId)
        : await usersApi.publicProfile(userId);
      setProfile(res.data as UserProfileType);
    } catch {
      setError('Failed to load profile.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const handleToggleActive = async () => {
    if (!profile) return;
    setActionLoading(true);
    try {
      const res = await usersApi.update(userId, { is_active: !profile.is_active });
      setProfile({ ...profile, ...(res.data as UserProfileType) });
    } catch {
      setError('Failed to update user.');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) return <p style={styles.hint}>Loading profile…</p>;
  if (error) return <p style={styles.error}>{error}</p>;
  if (!profile) return null;

  return (
    <div style={styles.container}>
      <button style={styles.backBtn} onClick={onBack}>← Back</button>

      <div style={styles.card}>
        <div style={styles.avatar}>
          {profile.username.slice(0, 1).toUpperCase()}
        </div>

        <div style={styles.info}>
          <h2 style={styles.username}>
            {profile.username}
            {isSelf && <span style={styles.selfBadge}>You</span>}
            {profile.is_superuser && <span style={styles.adminBadge}>🛡 Admin</span>}
          </h2>
          <p style={styles.email}>{profile.email}</p>
          <p style={styles.meta}>
            Joined {new Date(profile.created_at).toLocaleDateString('en-GB', {
              year: 'numeric', month: 'long', day: 'numeric',
            })}
          </p>
        </div>

        <div style={styles.statusGroup}>
          <span
            style={{
              ...styles.statusBadge,
              background: profile.is_active ? '#166534' : '#7f1d1d',
              color: profile.is_active ? '#86efac' : '#fca5a5',
            }}
          >
            {profile.is_active ? 'Active' : 'Inactive'}
          </span>
        </div>
      </div>

      <div style={styles.statsRow}>
        <div style={styles.statCard}>
          <span style={styles.statValue}>{profile.matches_created}</span>
          <span style={styles.statLabel}>Matches Created</span>
        </div>
        <div style={styles.statCard}>
          <span style={styles.statValue}>{profile.matches_played}</span>
          <span style={styles.statLabel}>Matches Joined</span>
        </div>
      </div>

      {isAdmin && !isSelf && (
        <div style={styles.adminActions}>
          <h3 style={styles.sectionTitle}>Admin Actions</h3>
          <div style={styles.actionRow}>
            <button
              style={{
                ...styles.actionBtn,
                background: profile.is_active ? '#7f1d1d' : '#166534',
                color: profile.is_active ? '#fca5a5' : '#86efac',
                borderColor: profile.is_active ? '#ef4444' : '#22c55e',
              }}
              onClick={handleToggleActive}
              disabled={actionLoading}
            >
              {actionLoading ? '…' : profile.is_active ? 'Deactivate Account' : 'Activate Account'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { padding: '1rem', maxWidth: 640 },
  backBtn: {
    background: 'none',
    border: 'none',
    color: '#60a5fa',
    cursor: 'pointer',
    fontSize: '0.9rem',
    padding: '0 0 1rem 0',
    display: 'block',
  },
  card: {
    display: 'flex',
    alignItems: 'center',
    gap: '1.2rem',
    background: '#1e293b',
    borderRadius: 12,
    padding: '1.5rem',
    marginBottom: '1.25rem',
    flexWrap: 'wrap',
  },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: '50%',
    background: '#3b82f6',
    color: '#fff',
    fontSize: '1.8rem',
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  info: { flex: 1, minWidth: 160 },
  username: {
    color: '#f8fafc',
    margin: '0 0 0.2rem',
    fontSize: '1.2rem',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  email: { color: '#94a3b8', margin: '0 0 0.25rem', fontSize: '0.85rem' },
  meta: { color: '#475569', margin: 0, fontSize: '0.78rem' },
  selfBadge: {
    padding: '0.1rem 0.45rem',
    background: '#1e3a5f',
    color: '#60a5fa',
    borderRadius: 8,
    fontSize: '0.72rem',
    fontWeight: 600,
  },
  adminBadge: {
    padding: '0.1rem 0.45rem',
    background: '#1a2a4a',
    color: '#93c5fd',
    borderRadius: 8,
    fontSize: '0.72rem',
    fontWeight: 600,
  },
  statusGroup: { display: 'flex', alignItems: 'center' },
  statusBadge: {
    padding: '0.25rem 0.7rem',
    borderRadius: 10,
    fontSize: '0.8rem',
    fontWeight: 600,
  },
  statsRow: {
    display: 'flex',
    gap: '1rem',
    marginBottom: '1.25rem',
    flexWrap: 'wrap',
  },
  statCard: {
    flex: 1,
    minWidth: 120,
    background: '#1e293b',
    borderRadius: 10,
    padding: '1rem 1.25rem',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
  },
  statValue: { color: '#f8fafc', fontWeight: 700, fontSize: '1.5rem' },
  statLabel: { color: '#64748b', fontSize: '0.78rem', textAlign: 'center' },
  adminActions: {
    background: '#1e293b',
    borderRadius: 10,
    padding: '1rem 1.25rem',
  },
  sectionTitle: {
    color: '#94a3b8',
    fontSize: '0.78rem',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    margin: '0 0 0.75rem 0',
  },
  actionRow: { display: 'flex', gap: 10, flexWrap: 'wrap' },
  actionBtn: {
    padding: '0.45rem 1rem',
    border: '1px solid transparent',
    borderRadius: 7,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.85rem',
  },
  hint: { color: '#64748b' },
  error: { color: '#f87171' },
};
