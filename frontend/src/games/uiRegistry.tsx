/**
 * Game UI component registry.
 *
 * Maps a game_id string to the React component that renders its full in-match
 * UI.  To add a new game UI, import the component and add an entry here —
 * no other platform file needs to change.
 */
import type { ComponentType } from 'react';
import TicTacToeGame from './tictactoe/ui';
import ZoneStalkerGame from './zone_stalkers/ui';
import type { Match, User } from '../types';

export interface GameUIProps {
  match: Match;
  user: User;
  onMatchUpdated: (updated: Match) => void;
  onMatchDeleted: (id: string) => void;
}

export const gameUIRegistry: Record<string, ComponentType<GameUIProps>> = {
  tictactoe: TicTacToeGame,
  zone_stalkers: ZoneStalkerGame,
};
