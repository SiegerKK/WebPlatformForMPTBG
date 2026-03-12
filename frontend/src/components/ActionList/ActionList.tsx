import React from 'react';

interface Action {
  type: string;
  label: string;
  payload?: Record<string, unknown>;
}

interface ActionListProps {
  actions: Action[];
  onAction: (type: string, payload?: Record<string, unknown>) => void;
  disabled?: boolean;
}

export default function ActionList({ actions, onAction, disabled }: ActionListProps) {
  if (actions.length === 0) {
    return <p style={styles.empty}>No actions available.</p>;
  }

  return (
    <div style={styles.container}>
      {actions.map((action) => (
        <button
          key={action.type}
          style={{
            ...styles.button,
            ...(disabled ? styles.buttonDisabled : {}),
          }}
          onClick={() => !disabled && onAction(action.type, action.payload)}
          disabled={disabled}
          title={action.payload ? JSON.stringify(action.payload) : undefined}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  empty: {
    color: '#475569',
    fontSize: '0.85rem',
    textAlign: 'center',
    margin: '0.5rem 0',
  },
  button: {
    padding: '0.5rem 0.75rem',
    background: '#1e40af',
    border: '1px solid #3b82f6',
    borderRadius: 6,
    color: '#bfdbfe',
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: '0.875rem',
    transition: 'background 0.15s',
  },
  buttonDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
};
