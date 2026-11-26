"""黑白棋 (小游戏): the board, its rules and how a companion plays.

8 × 8, two of each colour in the middle to begin with, black moves first. A move must close at least one line
of the other colour between the new disc and one of yours; those discs turn over. A side that cannot move
passes (PASS in the move list, so the colours still alternate); when neither can, the one with more discs
wins. Her moves come from this little engine: the corners are worth most, the points next to them least, and
having more moves than the other side is good; 轻松 looks one move ahead with some luck, 认真 three, 全力 four
and to the very end once few points are left.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

SIZE = 8
EMPTY, BLACK, WHITE = 0, 1, 2
PASS = (-1, -1)
LEVELS = ("easy", "normal", "hard")
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
CORNERS = {(0, 0), (7, 0), (0, 7), (7, 7)}
DEPTH = {"easy": 1, "normal": 3, "hard": 4}
TO_THE_END = 10  # 全力: with this many points left or fewer, she reads to the end
BIG_FLIP = 5  # a move that turns this many or more is worth a word

Move = tuple[int, int]
Grid = list[int]  # SIZE * SIZE, row by row

# What a disc on each point is worth to its side, early on.
WEIGHTS = [
    120, -20, 20, 5, 5, 20, -20, 120,
    -20, -40, -5, -5, -5, -5, -40, -20,
    20, -5, 15, 3, 3, 15, -5, 20,
    5, -5, 3, 3, 3, 3, -5, 5,
    5, -5, 3, 3, 3, 3, -5, 5,
    20, -5, 15, 3, 3, 15, -5, 20,
    -20, -40, -5, -5, -5, -5, -40, -20,
    120, -20, 20, 5, 5, 20, -20, 120,
]  # fmt: skip


def color_of(index: int) -> int:
    return BLACK if index % 2 == 0 else WHITE


def to_move(moves: Sequence[Move]) -> int:
    return color_of(len(moves))


def other(color: int) -> int:
    return WHITE if color == BLACK else BLACK


def start() -> Grid:
    grid = [EMPTY] * (SIZE * SIZE)
    grid[3 * SIZE + 3] = grid[4 * SIZE + 4] = WHITE
    grid[3 * SIZE + 4] = grid[4 * SIZE + 3] = BLACK
    return grid


def flips(grid: Grid, move: Move, color: int) -> list[int]:
    """The points (as indexes) a disc of `color` on `move` turns over; [] when it may not go there."""
    x, y = move
    if not (0 <= x < SIZE and 0 <= y < SIZE) or grid[y * SIZE + x] != EMPTY:
        return []
    them = other(color)
    turned: list[int] = []
    for dx, dy in DIRECTIONS:
        line: list[int] = []
        px, py = x + dx, y + dy
        while 0 <= px < SIZE and 0 <= py < SIZE and grid[py * SIZE + px] == them:
            line.append(py * SIZE + px)
            px, py = px + dx, py + dy
        if line and 0 <= px < SIZE and 0 <= py < SIZE and grid[py * SIZE + px] == color:
            turned.extend(line)
    return turned


def legal_moves(grid: Grid, color: int) -> list[Move]:
    return [
        (x, y)
        for y in range(SIZE)
        for x in range(SIZE)
        if grid[y * SIZE + x] == EMPTY and flips(grid, (x, y), color)
    ]


def place(grid: Grid, move: Move, color: int) -> Grid:
    """The board after `color` plays `move` (a new list; PASS changes nothing)."""
    out = grid[:]
    if move == PASS:
        return out
    for index in flips(grid, move, color):
        out[index] = color
    out[move[1] * SIZE + move[0]] = color
    return out


def board_of(moves: Sequence[Move]) -> Grid:
    grid = start()
    for index, move in enumerate(moves):
        grid = place(grid, tuple(move), color_of(index))  # type: ignore[arg-type]
    return grid


def legal(moves: Sequence[Move], move: Move) -> bool:
    return move != PASS and bool(flips(board_of(moves), move, to_move(moves)))


def over(grid: Grid) -> bool:
    return not legal_moves(grid, BLACK) and not legal_moves(grid, WHITE)


def with_passes(moves: Sequence[Move]) -> list[Move]:
    """The moves, with a PASS added for the side to move when it cannot move but the game goes on."""
    out = list(moves)
    grid = board_of(out)
    if not over(grid) and not legal_moves(grid, to_move(out)):
        out.append(PASS)
    return out


def score(grid: Grid) -> dict[int, int]:
    return {BLACK: grid.count(BLACK), WHITE: grid.count(WHITE)}


def result(moves: Sequence[Move]) -> int | None:
    """When the game is over: the colour with more discs, 0 for a draw; None while it goes on."""
    grid = board_of(moves)
    if not over(grid):
        return None
    counts = score(grid)
    return BLACK if counts[BLACK] > counts[WHITE] else WHITE if counts[WHITE] > counts[BLACK] else 0


def turned_by_last(moves: Sequence[Move]) -> int:
    if not moves or tuple(moves[-1]) == PASS:
        return 0
    return len(flips(board_of(moves[:-1]), tuple(moves[-1]), color_of(len(moves) - 1)))  # type: ignore[arg-type]


def moment(moves: Sequence[Move]) -> str | None:
    """What the last move did worth a word: "corner", "flip" (many turned over), "pass"."""
    if not moves:
        return None
    last = tuple(moves[-1])
    if last == PASS:
        return "pass"
    if last in CORNERS:
        return "corner"
    return "flip" if turned_by_last(moves) >= BIG_FLIP else None


# -- how she plays -----------------------------------------------------------------------------------------


def _value(grid: Grid, color: int) -> float:
    """How good the board is for `color`."""
    them = other(color)
    empties = grid.count(EMPTY)
    if empties == 0:
        diff = grid.count(color) - grid.count(them)
        return 10_000 * (diff > 0) - 10_000 * (diff < 0) + diff
    place_value = sum(WEIGHTS[i] for i, c in enumerate(grid) if c == color) - sum(
        WEIGHTS[i] for i, c in enumerate(grid) if c == them
    )
    mine, theirs = len(legal_moves(grid, color)), len(legal_moves(grid, them))
    mobility = 0.0 if mine + theirs == 0 else 60 * (mine - theirs) / (mine + theirs)
    discs = grid.count(color) - grid.count(them)
    late = 1.0 if empties < 16 else 0.0
    return place_value + mobility + late * 4 * discs


def _search(grid: Grid, color: int, depth: int, alpha: float, beta: float, passed: bool = False) -> float:
    """Negamax with alpha-beta: the value for `color`, to move."""
    moves = legal_moves(grid, color)
    if not moves:
        if passed or not legal_moves(grid, other(color)):
            diff = grid.count(color) - grid.count(other(color))
            return 10_000 * (diff > 0) - 10_000 * (diff < 0) + diff
        return -_search(grid, other(color), depth, -beta, -alpha, True)
    if depth <= 0:
        return _value(grid, color)
    # Corners first: the cuts come sooner.
    moves.sort(key=lambda m: -WEIGHTS[m[1] * SIZE + m[0]])
    best = -float("inf")
    for move in moves:
        value = -_search(place(grid, move, color), other(color), depth - 1, -beta, -alpha)
        if value > best:
            best = value
        alpha = max(alpha, value)
        if alpha >= beta:
            break
    return best


def choose(moves: Sequence[Move], level: str = "normal", chance: Callable[[], float] = random.random) -> Move:
    """Her next move (PASS when she cannot move)."""
    grid = board_of(moves)
    color = to_move(moves)
    options = legal_moves(grid, color)
    if not options:
        return PASS
    depth = DEPTH.get(level, 3)
    if level == "hard" and grid.count(EMPTY) <= TO_THE_END:
        depth = grid.count(EMPTY)
    scored = []
    for move in sorted(options, key=lambda m: -WEIGHTS[m[1] * SIZE + m[0]]):
        value = -_search(place(grid, move, color), other(color), depth - 1, -float("inf"), float("inf"))
        scored.append((value, move))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if level == "easy":
        # One of her better moves by luck, and now and then just any.
        if chance() < 0.25:
            return options[int(chance() * len(options)) % len(options)]
        top = scored[:3]
        return top[int(chance() * len(top)) % len(top)][1]
    best = scored[0][0]
    equal = [move for value, move in scored if value >= best - 1]
    return equal[int(chance() * len(equal)) % len(equal)]


def situation(moves: Sequence[Move], her_color: int) -> str:
    """How the game looks to her, in a few words for her prompt."""
    grid = board_of(moves)
    counts = score(grid)
    hers, theirs = counts[her_color], counts[other(her_color)]
    corners_hers = sum(grid[y * SIZE + x] == her_color for x, y in CORNERS)
    corners_theirs = sum(grid[y * SIZE + x] == other(her_color) for x, y in CORNERS)
    turn = "轮到你" if to_move(moves) == her_color else "轮到对方"
    lead = "你的子多" if hers > theirs else "对方的子多" if theirs > hers else "子数一样"
    corners = (
        f"，你占了 {corners_hers} 个角，对方 {corners_theirs} 个" if corners_hers + corners_theirs else ""
    )
    return f"{turn}，你 {hers} 子、对方 {theirs} 子（{lead}）{corners}"
