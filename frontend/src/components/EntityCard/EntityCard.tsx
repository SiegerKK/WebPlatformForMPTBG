import React from 'react';
import type { Entity } from '../../types';

interface EntityCardProps {
  entity: Entity;
  selected?: boolean;
  onSelect?: (entity: Entity) => void;
}

export default function EntityCard({ entity, selected, onSelect }: EntityCardProps) {
  const position = entity.components['position'] as { x?: number; y?: number } | undefined;
  const stats = entity.components['stats'] as Record<string, number> | undefined;

  return (
    <div
      style={{
        ...styles.card,
        ...(selected ? styles.cardSelected : {}),
      }}
      onClick={() => onSelect?.(entity)}
    >
      <div style={styles.header}>
        <span style={styles.archetype}>{entity.archetype}</span>
        <span style={styles.visibility}>{entity.visibility}</span>
      </div>

      {entity.tags.length > 0 && (
        <div style={styles.tags}>
          {entity.tags.map((tag) => (
            <span key={tag} style={styles.tag}>{tag}</span>
          ))}
        </div>
      )}

      {position && (
        <div style={styles.detail}>
          📍 ({position.x ?? '?'}, {position.y ?? '?'})
        </div>
      )}

      {stats && (
        <div style={styles.stats}>
          {Object.entries(stats).slice(0, 3).map(([k, v]) => (
            <span key={k} style={styles.stat}>{k}: {String(v)}</span>
          ))}
        </div>
      )}

      <div style={styles.id}>ID: {entity.id.slice(0, 8)}…</div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: '#1e293b',
    borderRadius: 8,
    padding: '0.65rem 0.85rem',
    cursor: 'pointer',
    border: '1px solid #334155',
    marginBottom: 6,
  },
  cardSelected: { border: '1px solid #3b82f6' },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  archetype: {
    color: '#f8fafc',
    fontWeight: 700,
    fontSize: '0.9rem',
  },
  visibility: {
    color: '#64748b',
    fontSize: '0.75rem',
  },
  tags: { display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 },
  tag: {
    background: '#334155',
    color: '#94a3b8',
    borderRadius: 4,
    padding: '0.1rem 0.4rem',
    fontSize: '0.7rem',
  },
  detail: { color: '#94a3b8', fontSize: '0.8rem', marginBottom: 2 },
  stats: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 2 },
  stat: { color: '#a3e635', fontSize: '0.75rem' },
  id: { color: '#475569', fontSize: '0.7rem', marginTop: 4 },
};
