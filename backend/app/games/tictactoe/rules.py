from typing import List, Tuple
from sdk.rule_set import RuleSet, RuleCheckResult

WINNING_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # columns
    (0, 4, 8), (2, 4, 6),             # diagonals
]


def _check_winner(board: List) -> str | None:
    for a, b, c in WINNING_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _init_state() -> dict:
    return {
        "board": [None] * 9,
        "player_marks": {},
        "current_player_id": None,
        "winner": None,
        "winner_mark": None,
        "game_over": False,
        "turn_count": 0,
    }


class TicTacToeRuleSet(RuleSet):
    """RuleSet for the Tic-Tac-Toe game (Крестики-нолики)."""

    def validate_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> RuleCheckResult:
        if command_type == "end_turn":
            return RuleCheckResult(valid=True)

        if command_type != "place_mark":
            return RuleCheckResult(valid=False, error=f"Unknown command: {command_type}")

        state = context_state if context_state else _init_state()

        if state.get("game_over"):
            return RuleCheckResult(valid=False, error="Game is already over")

        player_marks: dict = state.get("player_marks", {})
        # Allow up to 2 players to register
        if player_id not in player_marks and len(player_marks) >= 2:
            return RuleCheckResult(valid=False, error="No slot available for a third player")

        # Once both players are registered, enforce turn order
        if len(player_marks) == 2 and state.get("current_player_id") != player_id:
            return RuleCheckResult(valid=False, error="It is not your turn")

        cell = payload.get("cell")
        if cell is None or not isinstance(cell, int) or cell < 0 or cell > 8:
            return RuleCheckResult(valid=False, error="Payload must contain 'cell' (0-8)")

        board = state.get("board", [None] * 9)
        if board[cell] is not None:
            return RuleCheckResult(valid=False, error=f"Cell {cell} is already occupied")

        return RuleCheckResult(valid=True)

    def resolve_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> Tuple[dict, List[dict]]:
        if command_type == "end_turn":
            return context_state, [{"event_type": "turn_submitted", "payload": {"participant_id": player_id}}]

        # place_mark
        state = dict(context_state) if context_state else _init_state()
        if "board" not in state:
            state = _init_state()

        board = list(state["board"])
        player_marks: dict = dict(state.get("player_marks", {}))

        # Assign mark on first placement
        if player_id not in player_marks:
            mark = "X" if not player_marks else "O"
            player_marks[player_id] = mark

        mark = player_marks[player_id]
        cell = payload["cell"]
        board[cell] = mark

        turn_count = state.get("turn_count", 0) + 1

        winner_mark = _check_winner(board)
        game_over = winner_mark is not None or turn_count >= 9
        winner = player_id if winner_mark else None

        # Determine next player
        if len(player_marks) == 2:
            other_player = next(pid for pid in player_marks if pid != player_id)
            next_player = other_player if not game_over else None
        else:
            next_player = None  # waiting for second player

        new_state = {
            "board": board,
            "player_marks": player_marks,
            "current_player_id": next_player,
            "winner": winner,
            "winner_mark": winner_mark,
            "game_over": game_over,
            "turn_count": turn_count,
        }

        events = [
            {
                "event_type": "mark_placed",
                "payload": {"player_id": player_id, "mark": mark, "cell": cell},
            }
        ]

        if winner_mark:
            events.append(
                {
                    "event_type": "game_won",
                    "payload": {"winner_id": player_id, "winner_mark": winner_mark},
                }
            )
        elif game_over:
            events.append({"event_type": "game_drawn", "payload": {}})

        return new_state, events
