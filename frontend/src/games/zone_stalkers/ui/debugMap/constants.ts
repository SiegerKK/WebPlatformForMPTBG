import type { ZoneLocation } from './types';

// ─── Canvas layout constants ──────────────────────────────────────────────────

export const CARD_W = 180;
export const CARD_H = 112;
export const RING_RADIUS = 210;
export const CANVAS_PAD = 100;
/** px upper bound for draggable card positions */
export const MAX_CANVAS_COORD = 16000;

// ─── BFS radial layout ───────────────────────────────────────────────────────

/**
 * BFS radial layout.
 * Returns canvas-space CENTER coordinates {x, y} for each location so that
 * all values are ≥ (CARD_W/2 + CANVAS_PAD, CARD_H/2 + CANVAS_PAD).
 * No external offset variable needed — positions are ready to use directly.
 */
export function computeBfsLayout(
  locations: Record<string, ZoneLocation>,
): Record<string, { x: number; y: number }> {
  const ids = Object.keys(locations);
  if (ids.length === 0) return {};

  const startId =
    Object.values(locations).find((l) => (l.anomaly_activity ?? 0) <= 3)?.id ?? ids[0];

  const visited = new Set<string>();
  const levels: string[][] = [];
  let queue = [startId];

  while (queue.length > 0) {
    const nextQueue: string[] = [];
    const level: string[] = [];
    for (const id of queue) {
      if (!visited.has(id)) {
        visited.add(id);
        level.push(id);
        for (const conn of locations[id].connections) {
          if (!visited.has(conn.to)) nextQueue.push(conn.to);
        }
      }
    }
    if (level.length > 0) levels.push(level);
    queue = nextQueue;
  }

  const disconnected = ids.filter((id) => !visited.has(id));
  if (disconnected.length > 0) levels.push(disconnected);

  // Raw radial positions centred at (0, 0)
  const raw: Record<string, { x: number; y: number }> = {};
  levels.forEach((level, li) => {
    const r = li === 0 ? 0 : RING_RADIUS * li;
    level.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / level.length - Math.PI / 2;
      raw[id] = {
        x: Math.round(r * Math.cos(angle)),
        y: Math.round(r * Math.sin(angle)),
      };
    });
  });

  // Normalise so min values ≥ CARD_W/2 + CANVAS_PAD
  const xs = Object.values(raw).map((p) => p.x);
  const ys = Object.values(raw).map((p) => p.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const shiftX = CARD_W / 2 + CANVAS_PAD - minX;
  const shiftY = CARD_H / 2 + CANVAS_PAD - minY;

  return Object.fromEntries(
    Object.entries(raw).map(([id, p]) => [id, { x: p.x + shiftX, y: p.y + shiftY }]),
  );
}

// ─── Terrain type colors & labels ────────────────────────────────────────────

export const TERRAIN_TYPE_COLOR: Record<string, string> = {
  plain: '#4CAF50',
  hills: '#9ACD32',
  swamp: '#5E8C31',
  field_camp: '#20B2AA',
  slag_heaps: '#6B8E23',
  bridge: '#66BB6A',
  industrial: '#FF9800',
  buildings: '#FFC107',
  military_buildings: '#F44336',
  hamlet: '#FF7043',
  farm: '#FFB74D',
  dungeon: '#7E57C2',
  tunnel: '#3949AB',
  x_lab: '#1E88E5',
  scientific_bunker: '#42A5F5',
};

export const TERRAIN_TYPE_LABELS: Record<string, string> = {
  plain: 'Равнина',
  hills: 'Холмы',
  swamp: 'Болото',
  field_camp: 'Полевой лагерь',
  slag_heaps: 'Терриконы',
  bridge: 'Мост',
  industrial: 'Промзона',
  buildings: 'Здания',
  military_buildings: 'Воен. здания',
  hamlet: 'Хутор',
  farm: 'Ферма',
  dungeon: 'Подземелья',
  tunnel: 'Туннель',
  x_lab: 'Лаборатория X',
  scientific_bunker: 'Науч. бункер',
};

// ─── Region colors & labels ───────────────────────────────────────────────────

/** Fallback labels for the built-in fixed-map regions */
export const REGION_LABELS: Record<string, string> = {
  cordon: 'Кордон',
  garbage: 'Свалка',
  agroprom: 'Агропром',
  dark_valley: 'Тёмная Долина',
  swamps: 'Болота',
};

/** Fallback bg colours for the built-in fixed-map regions */
export const REGION_BG_COLOR: Record<string, string> = {
  cordon: '#1a2a1a',
  garbage: '#2a2010',
  agroprom: '#1a1a2a',
  dark_valley: '#2a1a1a',
  swamps: '#0f2020',
};

/** Fallback border colours for the built-in fixed-map regions */
export const REGION_BORDER_COLOR: Record<string, string> = {
  cordon: '#166534',
  garbage: '#854d0e',
  agroprom: '#1e3a8a',
  dark_valley: '#7f1d1d',
  swamps: '#134e4a',
};

/** 8-slot palette for dynamic regions.
 * Each region stores a `colorIndex` (0–7) which maps to one of these entries.
 * Using a fixed palette keeps the data compact (just an integer per region)
 * while still providing enough visual variety for a typical map. */
export const REGION_COLOR_PALETTE: Array<{ bg: string; border: string }> = [
  { bg: '#1a2a1a', border: '#166534' }, // 0 green
  { bg: '#2a2010', border: '#854d0e' }, // 1 amber
  { bg: '#1a1a2a', border: '#1e3a8a' }, // 2 blue
  { bg: '#2a1a1a', border: '#7f1d1d' }, // 3 red
  { bg: '#0f2020', border: '#134e4a' }, // 4 teal
  { bg: '#1e1a2a', border: '#5b21b6' }, // 5 purple
  { bg: '#1a2020', border: '#0f766e' }, // 6 cyan
  { bg: '#201a1a', border: '#9f1239' }, // 7 rose
];

/** Padding added to each side of a region bounding box */
export const REGION_PAD = 40;
