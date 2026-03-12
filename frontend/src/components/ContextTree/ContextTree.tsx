import React from 'react';
import type { GameContext } from '../../types';

interface ContextTreeProps {
  contexts: GameContext[];
  selectedId?: string;
  onSelect?: (context: GameContext) => void;
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  active: '#22c55e',
  resolved: '#3b82f6',
  archived: '#64748b',
};

function buildTree(contexts: GameContext[]): Map<string | undefined, GameContext[]> {
  const map = new Map<string | undefined, GameContext[]>();
  for (const ctx of contexts) {
    const key = ctx.parent_id;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(ctx);
  }
  return map;
}

function ContextNode({
  context,
  tree,
  depth,
  selectedId,
  onSelect,
}: {
  context: GameContext;
  tree: Map<string | undefined, GameContext[]>;
  depth: number;
  selectedId?: string;
  onSelect?: (context: GameContext) => void;
}) {
  const children = tree.get(context.id) ?? [];

  return (
    <div>
      <div
        style={{
          ...styles.node,
          paddingLeft: 12 + depth * 20,
          ...(selectedId === context.id ? styles.nodeSelected : {}),
        }}
        onClick={() => onSelect?.(context)}
      >
        <span
          style={{
            ...styles.dot,
            background: STATUS_COLORS[context.status] ?? '#64748b',
          }}
        />
        <span style={styles.contextType}>{context.context_type}</span>
        <span style={styles.status}>{context.status}</span>
        <span style={styles.id}>{context.id.slice(0, 8)}…</span>
      </div>
      {children.map((child) => (
        <ContextNode
          key={child.id}
          context={child}
          tree={tree}
          depth={depth + 1}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

export default function ContextTree({ contexts, selectedId, onSelect }: ContextTreeProps) {
  const tree = buildTree(contexts);
  const roots = tree.get(undefined) ?? [];

  if (contexts.length === 0) {
    return <p style={styles.empty}>No contexts.</p>;
  }

  return (
    <div style={styles.container}>
      {roots.map((ctx) => (
        <ContextNode
          key={ctx.id}
          context={ctx}
          tree={tree}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: '#0f172a',
    borderRadius: 8,
    border: '1px solid #1e293b',
    overflow: 'hidden',
  },
  empty: { color: '#475569', textAlign: 'center', fontSize: '0.85rem', margin: '0.75rem 0' },
  node: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '0.4rem 0.75rem',
    cursor: 'pointer',
    borderBottom: '1px solid #1e293b',
  },
  nodeSelected: { background: '#1e3a5f' },
  dot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
  },
  contextType: {
    color: '#f8fafc',
    fontSize: '0.82rem',
    fontWeight: 600,
    flex: 1,
  },
  status: {
    color: '#94a3b8',
    fontSize: '0.72rem',
  },
  id: {
    color: '#475569',
    fontSize: '0.7rem',
  },
};
