import React from 'react';

interface Tile {
  x: number;
  y: number;
  type: string;
  entity?: string;
}

interface TileGridProps {
  width: number;
  height: number;
  tiles: Tile[];
  onTileClick?: (x: number, y: number) => void;
}

const TILE_COLORS: Record<string, string> = {
  empty: '#1e293b',
  wall: '#475569',
  floor: '#334155',
  water: '#1d4ed8',
  grass: '#15803d',
  sand: '#a16207',
  lava: '#b91c1c',
};

export default function TileGrid({ width, height, tiles, onTileClick }: TileGridProps) {
  const tileMap = new Map<string, Tile>();
  for (const tile of tiles) {
    tileMap.set(`${tile.x},${tile.y}`, tile);
  }

  return (
    <div style={styles.wrapper}>
      <table style={styles.table}>
        <tbody>
          {Array.from({ length: height }, (_, row) => (
            <tr key={row}>
              {Array.from({ length: width }, (_, col) => {
                const tile = tileMap.get(`${col},${row}`);
                const bg = TILE_COLORS[tile?.type ?? 'empty'] ?? '#1e293b';
                return (
                  <td
                    key={col}
                    style={{ ...styles.cell, background: bg }}
                    onClick={() => onTileClick?.(col, row)}
                    title={tile ? `(${col},${row}) ${tile.type}${tile.entity ? ` [${tile.entity}]` : ''}` : `(${col},${row})`}
                  >
                    {tile?.entity && <span style={styles.entity}>●</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    overflow: 'auto',
    maxWidth: '100%',
  },
  table: {
    borderCollapse: 'collapse',
    tableLayout: 'fixed',
  },
  cell: {
    width: 28,
    height: 28,
    border: '1px solid #0f172a',
    cursor: 'pointer',
    textAlign: 'center',
    verticalAlign: 'middle',
    userSelect: 'none',
  },
  entity: {
    color: '#fbbf24',
    fontSize: 12,
    lineHeight: 1,
  },
};
