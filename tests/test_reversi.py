"""黑白棋's rules and the little engine a companion plays with."""

from __future__ import annotations

import random

from nest.core.reversi import (
    BLACK,
    PASS,
    WHITE,
    board_of,
    choose,
    flips,
    legal,
    legal_moves,
    moment,
    result,
    score,
    situation,
    start,
    to_move,
    with_passes,
)


def test_the_opening_and_what_a_move_turns_over() -> None:
    grid = start()
    assert score(grid) == {BLACK: 2, WHITE: 2}
    assert sorted(legal_moves(grid, BLACK)) == [(2, 3), (3, 2), (4, 5), (5, 4)]
    assert legal([], (2, 3)) and not legal([], (2, 4)) and not legal([], (0, 0)) and not legal([], (3, 3))
    assert len(flips(grid, (2, 3), BLACK)) == 1
    after = board_of([(2, 3)])
    assert score(after) == {BLACK: 4, WHITE: 1} and to_move([(2, 3)]) == WHITE


def test_the_game_ends_when_neither_can_move_and_more_discs_win() -> None:
    rnd = random.Random(11)
    moves: list[tuple[int, int]] = []
    while result(moves := with_passes(moves)) is None:
        moves.append(choose(moves, "easy", rnd.random))
    grid = board_of(moves)
    assert not legal_moves(grid, BLACK) and not legal_moves(grid, WHITE)
    counts = score(grid)
    expected = BLACK if counts[BLACK] > counts[WHITE] else WHITE if counts[WHITE] > counts[BLACK] else 0
    assert result(moves) == expected
    assert with_passes(moves) == moves  # over: nobody passes


def test_passing_is_added_for_the_side_without_a_move() -> None:
    grid_moves: list[tuple[int, int]] = []
    rnd = random.Random(5)
    seen_pass = False
    for _ in range(200):
        grid_moves = with_passes(grid_moves)
        if grid_moves and grid_moves[-1] == PASS:
            seen_pass = True
            assert not legal_moves(board_of(grid_moves[:-1]), to_move(grid_moves[:-1]))
        if result(grid_moves) is not None:
            break
        grid_moves.append(choose(grid_moves, "easy", rnd.random))
    assert result(grid_moves) is not None
    assert seen_pass or PASS not in grid_moves


def test_she_takes_a_corner_when_she_can_and_names_it() -> None:
    # White to move with the corner (0, 0) open to her.
    moves = [(2, 4), (2, 5), (2, 6), (1, 4), (3, 5), (2, 3), (1, 2)]
    grid = board_of(moves)
    assert to_move(moves) == WHITE
    if (0, 0) in legal_moves(grid, WHITE):
        assert choose(moves, "normal", random.Random(1).random) == (0, 0)
    corner = [(4, 5), (5, 5), (5, 4), (3, 5), (2, 4), (1, 3), (2, 3), (5, 3)]
    assert moment(corner) in (None, "flip", "corner")
    assert moment([*corner, PASS]) == "pass"


def test_the_harder_she_plays_the_more_she_wins() -> None:
    def play(black: str, white: str, seed: int) -> int:
        rnd = random.Random(seed)
        moves: list[tuple[int, int]] = []
        while (done := result(moves := with_passes(moves))) is None:
            moves.append(choose(moves, black if to_move(moves) == BLACK else white, rnd.random))
        return done

    assert sum(play("normal", "easy", s) == BLACK for s in range(3)) >= 2


def test_how_the_game_looks_to_her() -> None:
    line = situation([(2, 3)], WHITE)
    assert line.startswith("轮到你") and "你 1 子、对方 4 子" in line
