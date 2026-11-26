"""The board games of 小游戏 seen the same way by nest.core.games: 五子棋 (nest.core.gomoku) and 黑白棋
(nest.core.reversi). Moves are [(x, y), …] in order, black first; a pass (黑白棋 only) is (-1, -1)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from nest.core import gomoku, reversi

Move = tuple[int, int]
PASS = reversi.PASS


class Rules(Protocol):
    name: str
    size: int

    def legal(self, moves: Sequence[Move], move: Move) -> bool: ...

    def with_passes(self, moves: Sequence[Move]) -> list[Move]:
        """The moves, with a pass added for the side to move when it cannot move but the game goes on."""
        ...

    def outcome(self, moves: Sequence[Move]) -> tuple[int | None, list[Move]]:
        """(the winning colour, 0 for a draw, None while it goes on; the five that won, if any)."""
        ...

    def choose(self, moves: Sequence[Move], level: str, chance: Callable[[], float]) -> Move: ...

    def situation(self, moves: Sequence[Move], her_color: int) -> str: ...

    def moment(self, moves: Sequence[Move]) -> str | None:
        """What the last move did worth a word ("four", "three"; "corner", "flip", "pass")."""
        ...

    def grid(self, moves: Sequence[Move]) -> list[list[int]]:
        """The board, row by row: 0 empty, 1 black, 2 white."""
        ...

    def score(self, moves: Sequence[Move]) -> dict[int, int] | None: ...

    def options(self, moves: Sequence[Move]) -> list[Move]:
        """Where the side to move may play (shown for 黑白棋; [] when any empty point will do)."""
        ...


def to_move(moves: Sequence[Move]) -> int:
    """Black first, then in turn (a pass is a move)."""
    return 1 if len(moves) % 2 == 0 else 2


class Gomoku:
    name = "五子棋"
    size = gomoku.SIZE

    def legal(self, moves: Sequence[Move], move: Move) -> bool:
        x, y = move
        return 0 <= x < self.size and 0 <= y < self.size and move not in moves

    def with_passes(self, moves: Sequence[Move]) -> list[Move]:
        return list(moves)

    def outcome(self, moves: Sequence[Move]) -> tuple[int | None, list[Move]]:
        if not moves:
            return None, []
        five = gomoku.winner_line(gomoku.Board.of(moves), moves[-1])
        if five:
            return gomoku.color_of(len(moves) - 1), five
        return (0, []) if len(moves) >= self.size * self.size else (None, [])

    def choose(self, moves: Sequence[Move], level: str, chance: Callable[[], float]) -> Move:
        return gomoku.choose(moves, level, chance)

    def situation(self, moves: Sequence[Move], her_color: int) -> str:
        return gomoku.situation(moves, her_color)

    def moment(self, moves: Sequence[Move]) -> str | None:
        return gomoku.threat(gomoku.Board.of(moves), moves[-1]) if moves else None

    def grid(self, moves: Sequence[Move]) -> list[list[int]]:
        return gomoku.Board.of(moves).grid

    def score(self, moves: Sequence[Move]) -> dict[int, int] | None:
        return None

    def options(self, moves: Sequence[Move]) -> list[Move]:
        return []


class Reversi:
    name = "黑白棋"
    size = reversi.SIZE

    def legal(self, moves: Sequence[Move], move: Move) -> bool:
        return reversi.legal(moves, move)

    def with_passes(self, moves: Sequence[Move]) -> list[Move]:
        return reversi.with_passes(moves)

    def outcome(self, moves: Sequence[Move]) -> tuple[int | None, list[Move]]:
        return reversi.result(moves), []

    def choose(self, moves: Sequence[Move], level: str, chance: Callable[[], float]) -> Move:
        return reversi.choose(moves, level, chance)

    def situation(self, moves: Sequence[Move], her_color: int) -> str:
        return reversi.situation(moves, her_color)

    def moment(self, moves: Sequence[Move]) -> str | None:
        return reversi.moment(moves)

    def grid(self, moves: Sequence[Move]) -> list[list[int]]:
        flat = reversi.board_of(moves)
        return [flat[y * self.size : (y + 1) * self.size] for y in range(self.size)]

    def score(self, moves: Sequence[Move]) -> dict[int, int] | None:
        return reversi.score(reversi.board_of(moves))

    def options(self, moves: Sequence[Move]) -> list[Move]:
        return reversi.legal_moves(reversi.board_of(moves), to_move(moves))


RULES: dict[str, Rules] = {"gomoku": Gomoku(), "reversi": Reversi()}
