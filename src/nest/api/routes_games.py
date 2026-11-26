"""小游戏 in the desktop app (伙伴模式): start a game with a companion (五子棋, 黑白棋, 海龟汤, 你画我猜),
play, take a move back, give up; in 海龟汤 ask, guess and ask for a hint; in 你画我猜 change the word and
hand a drawing over. Each change also comes as a "game" event (nest.core.games)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.storage.bots import BotConfig

router = APIRouter(prefix="/api/games", dependencies=[Depends(require_token)])

GameId = Annotated[int, Path(ge=1, le=2**63 - 1)]
NO_BOT = "没有这个 bot"
NO_GAME = "没有这局"


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    kind: Literal["gomoku", "reversi", "soup", "draw"] = "gomoku"
    level: Literal["easy", "normal", "hard"] = "normal"
    first: Literal["you", "her", ""] = ""
    flavor: Literal["clear", "red", "funny"] = "clear"  # 海龟汤
    word: Annotated[str, Field(max_length=12)] = ""  # 你画我猜: a word of your own to draw
    mode: Literal["you", "her"] = "you"  # 你画我猜: who draws


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    x: Annotated[int, Field(ge=0, le=14)]
    y: Annotated[int, Field(ge=0, le=14)]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    text: Annotated[str, Field(min_length=1, max_length=200)]
    guess: bool = False  # what the user thinks the 汤底 is, not a yes-or-no question


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    word: Annotated[str, Field(max_length=12)] = ""  # '' : another from the list


class Which(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str


def _bot(request: Request, bot_id: str) -> BotConfig:
    bot = request.app.state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail=NO_BOT)
    return bot


def _done(found: tuple[dict[str, Any] | None, str]) -> dict[str, Any]:
    shown, why = found
    if shown is None:
        raise HTTPException(status_code=404 if why == NO_GAME else 409, detail=why)
    return shown


@router.get("/current")
async def current(request: Request, bot_id: str) -> dict[str, Any]:
    """Her newest game (playing or just finished), or {"game": null}."""
    _bot(request, bot_id)
    return {"game": await request.app.state.games.current(bot_id)}


@router.post("", status_code=201)
async def start(request: Request, body: Start) -> dict[str, Any]:
    bot = _bot(request, body.bot_id)
    games = request.app.state.games
    return _done(
        await games.start(
            bot,
            kind=body.kind,
            level=body.level,
            first=body.first,
            flavor=body.flavor,
            word=body.word,
            mode=body.mode,
        )
    )


@router.post("/{game_id}/moves")
async def move(request: Request, game_id: GameId, body: Move) -> dict[str, Any]:
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.move(bot, game_id, body.x, body.y))


@router.post("/{game_id}/undo")
async def undo(request: Request, game_id: GameId, body: Which) -> dict[str, Any]:
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.undo(bot, game_id))


@router.post("/{game_id}/resign")
async def resign(request: Request, game_id: GameId, body: Which) -> dict[str, Any]:
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.resign(bot, game_id))


@router.post("/{game_id}/ask")
async def ask(request: Request, game_id: GameId, body: Question) -> dict[str, Any]:
    """海龟汤: a question (or a guess); her answer follows as a "game" event."""
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.ask(bot, game_id, body.text, guess=body.guess))


@router.post("/{game_id}/hint")
async def hint(request: Request, game_id: GameId, body: Which) -> dict[str, Any]:
    """海龟汤: the next hint."""
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.give_hint(bot, game_id))


@router.post("/{game_id}/word")
async def new_word(request: Request, game_id: GameId, body: Word) -> dict[str, Any]:
    """你画我猜: another word to draw (yours, or another from the list)."""
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.new_word(bot, game_id, body.word))


@router.put("/{game_id}/sketches")
async def hand_over(request: Request, game_id: GameId, bot_id: str) -> dict[str, Any]:
    """你画我猜: the drawing so far, as the PNG body; her guess follows as a "game" event."""
    bot = _bot(request, bot_id)
    png = await request.body()
    return _done(await request.app.state.games.hand_over(bot, game_id, png))


@router.get("/{game_id}/sketches/{number}")
async def drawing(
    request: Request, game_id: GameId, number: Annotated[int, Path(ge=0, le=99)]
) -> FileResponse:
    """A drawing of the game: yours, handed over (1, 2, …), or hers (0, 她画你猜) once she has finished it."""
    path = request.app.state.games.sketch_file(game_id, number)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这幅画")
    return FileResponse(path, media_type="image/png")


@router.post("/{game_id}/answer")
async def answer(request: Request, game_id: GameId, body: Question) -> dict[str, Any]:
    """你画我猜, when she draws: what you think it is."""
    bot = _bot(request, body.bot_id)
    return _done(await request.app.state.games.answer(bot, game_id, body.text))
