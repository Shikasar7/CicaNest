"""五子棋's rules and the little engine a companion plays with."""

from __future__ import annotations

import random

from nest.core.gomoku import (
    BLACK,
    WHITE,
    Board,
    choose,
    label,
    situation,
    threat,
    to_move,
    win_by_fours,
    winner_line,
)

Move = tuple[int, int]


def _play(black: list[Move], white: list[Move]) -> list[Move]:
    """Moves in order from two lists (black has as many as white, or one more)."""
    out: list[Move] = []
    for n, move in enumerate(black):
        out.append(move)
        if n < len(white):
            out.append(white[n])
    return out


def test_five_or_more_in_a_row_wins_in_every_direction() -> None:
    for line in (
        [(3 + i, 7) for i in range(5)],
        [(7, 2 + i) for i in range(5)],
        [(2 + i, 2 + i) for i in range(5)],
        [(2 + i, 10 - i) for i in range(5)],
    ):
        moves = _play(line, [(0, n) for n in range(4)])
        assert winner_line(Board.of(moves), line[-1]) == sorted(line)
    # Four is not five; the sixth stone of a long row still wins (no forbidden moves).
    four = _play([(3 + i, 7) for i in range(4)], [(0, n) for n in range(4)])
    assert winner_line(Board.of(four), (6, 7)) is None
    six = _play([(1, 1), (2, 1), (3, 1), (5, 1), (6, 1), (4, 1)], [(0, n) for n in range(6)][:5])
    assert winner_line(Board.of(six), (4, 1)) == [(x, 1) for x in range(1, 7)]


def test_who_moves_and_how_a_point_is_called() -> None:
    assert to_move([]) == BLACK and to_move([(7, 7)]) == WHITE
    assert label((7, 7)) == "H8" and label((0, 14)) == "A1" and label((14, 0)) == "O15"


def test_threats_are_named() -> None:
    three = _play([(5, 7), (6, 7), (7, 7)], [(0, 0), (0, 1)])
    assert threat(Board.of(three), (7, 7)) == "three"
    four = _play([(5, 7), (6, 7), (7, 7), (8, 7)], [(0, 0), (0, 1), (0, 2)])
    assert threat(Board.of(four), (8, 7)) == "four"
    lone = [(7, 7)]
    assert threat(Board.of(lone), (7, 7)) is None


def test_she_takes_a_five_and_stops_yours() -> None:
    # Hers (white) four in a row with an open end: she wins rather than blocks.
    moves = _play([(3, 3), (4, 3), (5, 3), (9, 9)], [(3, 10), (4, 10), (5, 10), (6, 10)])
    assert to_move(moves) == BLACK
    moves = _play([(3, 3), (4, 3), (5, 3), (9, 9), (12, 12)], [(3, 10), (4, 10), (5, 10), (6, 10)])
    for level in ("easy", "normal", "hard"):
        spot = choose(moves, level, random.Random(1).random)
        assert spot in ((2, 10), (7, 10)), level  # five for white
    # Your open three: 认真 and 全力 block it at an end.
    yours = _play([(5, 7), (6, 7), (7, 7)], [(7, 8), (1, 1)])
    for level in ("normal", "hard"):
        assert choose(yours, level, random.Random(2).random) in ((4, 7), (8, 7), (3, 7), (9, 7)), level


def test_a_run_of_fours_is_found() -> None:
    # Black: two closed threes meeting at one point; a stone there makes two fours at once.
    black = [(3, 3), (4, 3), (5, 3), (6, 4), (6, 5), (6, 6)]
    white = [(2, 3), (6, 7), (12, 12), (1, 12), (10, 0), (13, 9)]
    moves = _play(black, white)
    assert to_move(moves) == BLACK
    found = win_by_fours(Board.of(moves), BLACK, 6)
    assert found is not None and found[0] == (6, 3)
    assert choose(moves, "normal", random.Random(3).random) == (6, 3)
    assert win_by_fours(Board.of(moves), WHITE, 6) is None


def test_the_engine_answers_quickly_and_beats_a_random_player() -> None:
    rnd = random.Random(7)
    moves: list[Move] = []
    while True:
        if to_move(moves) == BLACK:
            empty = [(x, y) for x in range(4, 11) for y in range(4, 11) if (x, y) not in moves]
            spot = rnd.choice(empty)
        else:
            spot = choose(moves, "normal", rnd.random)
        moves.append(spot)
        if winner_line(Board.of(moves), spot):
            break
    assert to_move(moves) == BLACK  # white made the last move: she won
    assert len(moves) < 60


def test_how_the_game_looks_to_her() -> None:
    assert situation([], WHITE) == "还没开始下"
    yours = _play([(5, 7), (6, 7), (7, 7), (8, 7)], [(4, 7), (1, 1), (2, 2)])
    assert "差一步" in situation(yours, WHITE)
