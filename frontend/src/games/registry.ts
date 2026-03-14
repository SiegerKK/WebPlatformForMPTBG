/**
 * Game catalog registry.
 *
 * Each entry describes a game available on the platform.  To add a new game,
 * append an entry here — no other platform file needs to change.
 */

export interface GameCard {
  id: string;
  name: string;
  emoji: string;
  description: string;
  minPlayers: number;
  maxPlayers: number;
  tags: string[];
}

export const GAME_CATALOG: GameCard[] = [
  {
    id: 'tictactoe',
    name: 'Tic-Tac-Toe',
    emoji: '✕',
    description:
      'Classic two-player game. Place X or O on a 3×3 grid — first to get three in a row wins.',
    minPlayers: 2,
    maxPlayers: 2,
    tags: ['classic', '2 players', 'quick'],
  },
  {
    id: 'zone_stalkers',
    name: 'Zone Stalkers',
    emoji: '☢️',
    description:
      'Async sandbox RPG in a post-apocalyptic Zone. Explore locations, collect artifacts, fight mutants, trade with merchants. Human players and AI bots coexist as equal agents.',
    minPlayers: 1,
    maxPlayers: 8,
    tags: ['RPG', 'sandbox', 'async', 'multiplayer', 'S.T.A.L.K.E.R. inspired'],
  },
];
