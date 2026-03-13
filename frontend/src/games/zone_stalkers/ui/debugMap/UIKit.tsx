/**
 * UIKit — small generic primitives used throughout the debug map panels.
 * These are pure presentational components with no game logic.
 */
import React from 'react';

// ─── Badge ────────────────────────────────────────────────────────────────────

export function Badge({
  bg,
  color,
  children,
}: {
  bg: string;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <span
      style={{
        background: bg,
        color,
        borderRadius: 5,
        padding: '0.1rem 0.35rem',
        fontSize: '0.62rem',
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  );
}

// ─── Section ──────────────────────────────────────────────────────────────────

export function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div
        style={{
          color: '#64748b',
          fontSize: '0.67rem',
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

// ─── DetailRow ────────────────────────────────────────────────────────────────

export function DetailRow({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: '#1e293b',
        borderRadius: 5,
        padding: '0.25rem 0.5rem',
        gap: 6,
      }}
    >
      {children}
    </div>
  );
}

// ─── EmptyRow ─────────────────────────────────────────────────────────────────

export function EmptyRow() {
  return <span style={{ color: '#334155', fontSize: '0.72rem' }}>None</span>;
}
