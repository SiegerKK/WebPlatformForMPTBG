import React, { useRef, useEffect } from 'react';
import type { GameEvent } from '../../types';

interface EventLogProps {
  events: GameEvent[];
  maxHeight?: number;
}

export default function EventLog({ events, maxHeight = 300 }: EventLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const sortedEvents = [...events].sort((a, b) => a.sequence_no - b.sequence_no);

  return (
    <div style={{ ...styles.container, maxHeight }}>
      {sortedEvents.length === 0 && (
        <p style={styles.empty}>No events yet.</p>
      )}
      {sortedEvents.map((ev) => (
        <div key={ev.id} style={styles.entry}>
          <div style={styles.entryHeader}>
            <span style={styles.seqNum}>#{ev.sequence_no}</span>
            <span style={styles.eventType}>{ev.event_type}</span>
            <span style={styles.timestamp}>
              {new Date(ev.created_at).toLocaleTimeString()}
            </span>
          </div>
          <div style={styles.payload}>
            {Object.entries(ev.payload)
              .slice(0, 4)
              .map(([k, v]) => (
                <span key={k} style={styles.payloadItem}>
                  {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </span>
              ))}
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    overflowY: 'auto',
    background: '#0f172a',
    borderRadius: 8,
    padding: '0.5rem',
    border: '1px solid #1e293b',
  },
  empty: { color: '#475569', textAlign: 'center', fontSize: '0.85rem', margin: '1rem 0' },
  entry: {
    padding: '0.4rem 0.5rem',
    borderBottom: '1px solid #1e293b',
    marginBottom: 2,
  },
  entryHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 2,
  },
  seqNum: {
    color: '#475569',
    fontSize: '0.7rem',
    minWidth: 24,
  },
  eventType: {
    color: '#a78bfa',
    fontWeight: 600,
    fontSize: '0.8rem',
    flex: 1,
  },
  timestamp: {
    color: '#475569',
    fontSize: '0.7rem',
  },
  payload: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
    paddingLeft: 32,
  },
  payloadItem: {
    color: '#94a3b8',
    fontSize: '0.72rem',
    background: '#1e293b',
    borderRadius: 4,
    padding: '0.1rem 0.35rem',
  },
};
