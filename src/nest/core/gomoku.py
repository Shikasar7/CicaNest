"""五子棋 (小游戏): the board, its rules and how a companion plays.

15 × 15, black moves first, five or more in a row wins (no forbidden moves). The board is a list of moves
[(x, y), …]: black's are the even ones. A companion's move comes from this little engine, not from her model
(a model plays it badly and slowly); her model only talks about the game (nest.core.games).

How it plays: every empty point near the stones is scored by the shapes it would make, for her (attack) and
for the other side (defence), from the patterns of the four lines through it. 轻松 picks among the good
points with some luck and may miss a threat; 认真 takes the best; 全力 also looks one answer ahead.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))
LEVELS = ("easy", "normal", "hard")
COLUMNS = "ABCDEFGHIJKLMNO"

Move = tuple[int, int]

# A line through the point, 4 each side, read as text: X hers (the point included), O the other side or the
# edge, _ empty. The best shape that covers the point counts.
FIVE = 100_000
OPEN_FOUR = 12_000
FOUR = 1_500
OPEN_THREE = 1_200
THREE = 150
OPEN_TWO = 120
TWO = 15
SHAPES: tuple[tuple[str, int], ...] = (
    ("XXXXX", FIVE),
    ("_XXXX_", OPEN_FOUR),
    ("XXXX_", FOUR),
    ("_XXXX", FOUR),
    ("XXX_X", FOUR),
    ("X_XXX", FOUR),
    ("XX_XX", FOUR),
    ("__XXX_", OPEN_THREE),
    ("_XXX__", OPEN_THREE),
    ("_XX_X_", OPEN_THREE),
    ("_X_XX_", OPEN_THREE),
    ("XXX__", THREE),
    ("__XXX", THREE),
    ("XX_X_", THREE),
    ("_X_XX", THREE),
    ("XX__X", THREE),
    ("X__XX", THREE),
    ("X_X_X", THREE),
    ("_XXX_", THREE),
    ("__XX__", OPEN_TWO),
    ("_X_X_", OPEN_TWO),
    ("XX___", TWO),
    ("___XX", TWO),
    ("_XX__", TWO),
    ("__XX_", TWO),
)
THREAT_NAMES = {FIVE: "five", OPEN_FOUR: "four", FOUR: "four", OPEN_THREE: "three"}


def color_of(index: int) -> int:
    """The colour of the move at this index: black first."""
    return BLACK if index % 2 == 0 else WHITE


def to_move(moves: Sequence[Move]) -> int:
    return color_of(len(moves))


def other(color: int) -> int:
    return WHITE if color == BLACK else BLACK


def label(move: Move) -> str:
    """「H8」: column letter, row number from the bottom (as people read a board)."""
    x, y = move
    return f"{COLUMNS[x]}{SIZE - y}"


@dataclass
class Board:
    grid: list[list[int]]

    @classmethod
    def of(cls, moves: Sequence[Move]) -> Board:
        grid = [[EMPTY] * SIZE for _ in range(SIZE)]
        for index, (x, y) in enumerate(moves):
            grid[y][x] = color_of(index)
        return cls(grid)

    def at(self, x: int, y: int) -> int:
        return self.grid[y][x]

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < SIZE and 0 <= y < SIZE

    def empty(self) -> bool:
        return all(c == EMPTY for row in self.grid for c in row)

    def full(self) -> bool:
        return all(c != EMPTY for row in self.grid for c in row)

    def line(self, x: int, y: int, dx: int, dy: int, color: int) -> str:
        """The 9 points through (x, y) along (dx, dy), as if `color` stood on (x, y)."""
        out = []
        for step in range(-4, 5):
            px, py = x + dx * step, y + dy * step
            if step == 0:
                out.append("X")
            elif not self.inside(px, py):
                out.append("O")
            else:
                c = self.grid[py][px]
                out.append("_" if c == EMPTY else "X" if c == color else "O")
        return "".join(out)


def _shape(line: str) -> int:
    """The best shape in a 9-point line that covers its middle point."""
    best = 0
    for shape, value in SHAPES:
        if value <= best:
            continue
        start = line.find(shape)
        while start != -1:
            if start <= 4 < start + len(shape):
                best = value
                break
            start = line.find(shape, start + 1)
    return best


def shapes_at(board: Board, move: Move, color: int) -> list[int]:
    x, y = move
    return [_shape(board.line(x, y, dx, dy, color)) for dx, dy in DIRECTIONS]


def point_value(board: Board, move: Move, color: int) -> int:
    """What a stone of `color` here would make: its shapes, with two threats at once worth nearly a win."""
    found = sorted(shapes_at(board, move, color), reverse=True)
    total = sum(found)
    big = [v for v in found if v >= OPEN_THREE]
    if found[0] < FIVE and len(big) >= 2:
        # Two fours, a four and an open three, or two open threes: the other side cannot stop both.
        total += OPEN_FOUR
    return total


def winner_line(board: Board, move: Move) -> list[Move] | None:
    """The five (or more) in a row the stone just placed on `move` made, if it did."""
    x, y = move
    color = board.at(x, y)
    if color == EMPTY:
        return None
    for dx, dy in DIRECTIONS:
        run = [(x, y)]
        for sign in (1, -1):
            px, py = x + dx * sign, y + dy * sign
            while board.inside(px, py) and board.at(px, py) == color:
                run.append((px, py))
                px, py = px + dx * sign, py + dy * sign
        if len(run) >= 5:
            return sorted(run)
    return None


def threat(board: Board, move: Move) -> str | None:
    """What the stone just placed on `move` threatens: "five", "four" (one away from five) or "three" (an
    open three), else None."""
    x, y = move
    color = board.at(x, y)
    if color == EMPTY:
        return None
    grid = [row[:] for row in board.grid]
    grid[y][x] = EMPTY
    best = max(shapes_at(Board(grid), move, color))
    for value, name in sorted(THREAT_NAMES.items(), reverse=True):
        if best >= value:
            return name
    return None


def candidates(board: Board, reach: int = 2) -> list[Move]:
    """Empty points within `reach` of a stone (the middle on an empty board)."""
    if board.empty():
        return [(SIZE // 2, SIZE // 2)]
    near: set[Move] = set()
    for y in range(SIZE):
        for x in range(SIZE):
            if board.at(x, y) == EMPTY:
                continue
            for dy in range(-reach, reach + 1):
                for dx in range(-reach, reach + 1):
                    px, py = x + dx, y + dy
                    if board.inside(px, py) and board.at(px, py) == EMPTY:
                        near.add((px, py))
    return sorted(near)


def scored(board: Board, color: int) -> list[tuple[float, Move]]:
    """Each candidate point for `color` to play, best first: attack plus most of the defence."""
    out = []
    for move in candidates(board):
        attack = point_value(board, move, color)
        defence = point_value(board, move, other(color))
        # In order: five now; stop their five; an open four (or four and three) of hers, which wins; stop
        # their open four before it is made (an open three of theirs); else the best shapes for both.
        if attack >= FIVE:
            value = 10 * FIVE
        elif defence >= FIVE:
            value = 5 * FIVE
        elif attack >= OPEN_FOUR:
            value = 2 * FIVE + attack
        elif defence >= OPEN_FOUR:
            value = FIVE + defence + attack
        else:
            value = attack + defence * 0.85
        out.append((value, move))
    out.sort(key=lambda item: (-item[0], item[1]))
    return out


def _after(board: Board, move: Move, color: int) -> Board:
    grid = [row[:] for row in board.grid]
    grid[move[1]][move[0]] = color
    return Board(grid)


def _fives(board: Board, color: int) -> list[Move]:
    return [m for m in candidates(board, 1) if max(shapes_at(board, m, color)) >= FIVE]


def win_by_fours(board: Board, color: int, depth: int, budget: list[int] | None = None) -> list[Move] | None:
    """连续冲四 (VCF): a run of fours for `color`, each forcing the one answer, that ends in five. Her moves
    of it, or None. `budget`: how many positions may still be looked at (shared down the search)."""
    budget = budget if budget is not None else [VCF_NODES]
    budget[0] -= 1
    if mine := _fives(board, color):
        return [mine[0]]
    if depth <= 0 or budget[0] <= 0 or _fives(board, other(color)):
        return None  # they would make five first, or she has thought long enough
    for move in candidates(board, 1):
        if max(shapes_at(board, move, color)) < FOUR:
            continue
        after = _after(board, move, color)
        threats = _fives(after, color)
        if len(threats) >= 2:
            return [move, threats[0]]  # two ways to five: they can stop one
        if len(threats) == 1:
            rest = win_by_fours(_after(after, threats[0], other(color)), color, depth - 1, budget)
            if rest:
                return [move, *rest]
    return None


VCF_DEPTH = {"easy": 0, "normal": 4, "hard": 8}
VCF_NODES = 400  # positions one search may look at


def choose(moves: Sequence[Move], level: str = "normal", chance: Callable[[], float] = random.random) -> Move:
    """Her next move on this board (she plays whoever is to move)."""
    board = Board.of(moves)
    color = to_move(moves)
    ranked = scored(board, color)
    if not ranked:
        raise ValueError("棋盘满了")
    best_value, best = ranked[0]
    depth = VCF_DEPTH.get(level, 0)
    if best_value < 5 * FIVE and depth and (forced := win_by_fours(board, color, depth)):
        return forced[0]
    if level == "easy":
        # Five in a row she always sees, their five most of the time; else she plays her own game and now
        # and then misses what they are building, choosing among the good points by luck.
        if best_value >= 10 * FIVE or (best_value >= 5 * FIVE and chance() < 0.85):
            return best
        if chance() < 0.3:
            ranked = sorted(
                (
                    (point_value(board, m, color) + 0.3 * point_value(board, m, other(color)), m)
                    for _, m in ranked
                ),
                key=lambda item: (-item[0], item[1]),
            )
            best_value = ranked[0][0]
        top = [item for item in ranked[:8] if item[0] >= best_value * 0.25] or ranked[:1]
        weights = [(value + 1) ** 0.5 for value, _ in top]
        pick = chance() * sum(weights)
        for weight, (_, move) in zip(weights, top, strict=True):
            pick -= weight
            if pick <= 0:
                return move
        return top[-1][1]
    if level == "hard" and best_value < 5 * FIVE:
        # One answer ahead: of her best points, the one that leaves their best reply weakest.
        them = other(color)
        best_after, choice = None, best
        for value, move in ranked[:8]:
            after = _after(board, move, color)
            reply = scored(after, them)
            worst = reply[0][0] if reply else 0
            net = value - 0.7 * worst
            if worst < 5 * FIVE and win_by_fours(after, them, 6, [120]):
                net -= 4 * FIVE  # this leaves them a run of fours to five
            if best_after is None or net > best_after:
                best_after, choice = net, move
        return choice
    # 认真: the best; when nothing is at stake, a little luck between near equals so no two games are alike.
    slack = min(best_value * 0.03, 30) if best_value < FIVE else 0
    equal = [move for value, move in ranked if value >= best_value - slack]
    return equal[int(chance() * len(equal)) % len(equal)]


def situation(moves: Sequence[Move], her_color: int) -> str:
    """How the game looks to her, in a few words for her prompt."""
    if not moves:
        return "还没开始下"
    board = Board.of(moves)
    color = to_move(moves)

    def strongest(side: int) -> int:
        return max((point_value(board, m, side) for m in candidates(board)), default=0)

    hers, theirs = strongest(her_color), strongest(other(her_color))
    mine = color == her_color
    turn = "轮到你" if mine else "轮到对方"
    if hers >= FIVE and mine:
        return f"{turn}，你这步就能连成五个"
    if theirs >= FIVE:
        return f"{turn}，对方差一步就连成五个了" + ("，你得赶紧堵" if mine else "")
    if hers >= FIVE:
        return f"{turn}，你差一步就连成五个了，看对方堵不堵得住"
    if theirs >= OPEN_FOUR or (theirs > hers * 2 and theirs >= OPEN_THREE):
        return f"{turn}，对方攻势很猛，你得小心"
    if hers >= OPEN_FOUR or (hers > theirs * 2 and hers >= OPEN_THREE):
        return f"{turn}，你占上风"
    return f"{turn}，势均力敌"
