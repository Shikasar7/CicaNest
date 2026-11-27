"""小游戏: 五子棋 with a companion in the desktop app — the moves go through the API, hers come by themselves
after a moment, she says a word at the start and the end (and now and then), and her answers in the chat know
how the game stands."""

from __future__ import annotations

from typing import Any

from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_desk import private_chat, rows
from tests.test_tools import model


async def _ready(w: World) -> int:
    chat = await private_chat(w)
    games = w.state.games
    games.think = (0.0, 0.0)
    games.chance = lambda: 0.0  # she always has something to say, and plays the first of equals
    model(w).default = "SKIP"  # what she has not been given a line for she leaves unsaid
    return chat


async def _start(w: World, **body: Any) -> dict[str, Any]:
    response = await w.http.post("/api/games", json={"bot_id": WHALE} | body)
    assert response.status_code == 201, response.text
    return response.json()


async def _current(w: World) -> dict[str, Any]:
    return (await w.http.get("/api/games/current", params={"bot_id": WHALE})).json()["game"]


async def _moves(w: World, count: int) -> dict[str, Any] | None:
    game = await _current(w)
    return game if len(game["moves"]) == count and not game["thinking"] else None


async def _hers(w: World, chat: int) -> list[str]:
    said = await rows(w, chat)
    return [r["text"] for r in said if r["from_self"] and r["external_id"].startswith("app:g:")]


async def test_a_game_from_the_first_move_to_giving_up(world: World) -> None:  # noqa: F811
    chat = await _ready(world)
    assert await _current(world) is None
    model(world).queue("来吧，我不会让你的～")
    game = await _start(world, level="normal", first="you")
    assert (game["kind"], game["her_color"], game["turn"], game["moves"]) == ("gomoku", 2, "you", [])
    assert await eventually(lambda: _hers(world, chat)) == ["来吧，我不会让你的～"]
    asked = model(world).requests[-1][1].content
    assert "五子棋" in asked and "对方先下" in asked and "不要报坐标" in asked

    played = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 7, "y": 7})
    assert played.status_code == 200 and played.json()["thinking"] is True
    after = await eventually(lambda: _moves(world, 2))
    assert after["moves"][0] == [7, 7] and after["turn"] == "you"
    assert any(kind == "game" and d["id"] == game["id"] for kind, d in world.events)

    # Taken already, or off the board: no.
    taken = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 7, "y": 7})
    assert taken.status_code == 409 and taken.json()["detail"] == "这里不能下"
    off = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 15, "y": 7})
    assert off.status_code == 422

    # 悔棋: your move and her answer go back.
    undone = await world.http.post(f"/api/games/{game['id']}/undo", json={"bot_id": WHALE})
    assert undone.status_code == 200 and undone.json()["moves"] == [] and undone.json()["undos"] == 1

    gave_up = await world.http.post(f"/api/games/{game['id']}/resign", json={"bot_id": WHALE})
    assert gave_up.json()["status"] == "resigned" and gave_up.json()["turn"] is None
    assert gave_up.json()["record"] == {"won": 0, "lost": 1, "draw": 0}
    again = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 1, "y": 1})
    assert again.status_code == 409 and again.json()["detail"] == "这局已经结束了"


async def test_not_your_turn_while_she_thinks(world: World) -> None:  # noqa: F811
    await _ready(world)
    world.state.games.think = (0.5, 0.5)
    game = await _start(world, first="you")
    first = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 7, "y": 7})
    assert first.status_code == 200
    second = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 8, "y": 8})
    assert second.status_code == 409 and "在想" in second.json()["detail"]
    undo = await world.http.post(f"/api/games/{game['id']}/undo", json={"bot_id": WHALE})
    assert undo.status_code == 409
    await eventually(lambda: _moves(world, 2))


async def test_five_in_a_row_wins_and_she_says_so(world: World) -> None:  # noqa: F811
    chat = await _ready(world)
    game = await _start(world, first="you")
    await eventually(lambda: model(world).requests)  # her word at the start (nothing to say)
    # Four of yours in a row already (hers far away), the fifth to come.
    near = [(3, 7), (0, 0), (4, 7), (0, 2), (5, 7), (0, 4), (6, 7), (14, 14)]
    await world.state.games.store.save(game["id"], near)
    model(world).queue("哼，这局算你厉害 [表情1]")
    won = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 7, "y": 7})
    shown = won.json()
    assert shown["status"] == "won" and shown["win"] == [[3, 7], [4, 7], [5, 7], [6, 7], [7, 7]]
    assert shown["record"] == {"won": 1, "lost": 0, "draw": 0}
    said = await eventually(lambda: _hers(world, chat))
    assert said == ["哼，这局算你厉害"]  # no sticker was on offer: the mark is dropped
    assert "这局对方赢了" in model(world).requests[-1][1].content


async def test_she_can_go_first_and_takes_turns_by_herself(world: World) -> None:  # noqa: F811
    await _ready(world)
    game = await _start(world, first="her", level="easy")
    assert game["her_color"] == 1 and game["thinking"] is True
    after = await eventually(lambda: _moves(world, 1))
    assert after["moves"] == [[7, 7]] and after["turn"] == "you"
    # The next game: whoever did not begin this one.
    nxt = await _start(world)
    assert nxt["her_color"] == 2 and nxt["turn"] == "you"
    left = await world.state.games.store.get(game["id"])
    assert left is not None and left["status"] == "left"


async def test_her_answers_in_the_chat_know_the_game(world: World) -> None:  # noqa: F811
    chat = await _ready(world)
    game = await _start(world, first="you")
    await eventually(lambda: model(world).requests)
    await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 7, "y": 7})
    await eventually(lambda: _moves(world, 2))
    model(world).queue("嘿嘿，你这步有点意思")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "你看我这步怎么样"})
    await eventually(lambda: _answered(world, chat, "嘿嘿，你这步有点意思"))
    prompt = model(world).requests[-1][1].content
    assert "正在桌面上用棋盘下五子棋" in prompt and "你执白子" in prompt and "已经下了 2 手" in prompt


async def _answered(w: World, chat: int, text: str) -> bool:
    return any(r["from_self"] and r["text"] == text for r in await rows(w, chat))


async def _cards(w: World, chat: int) -> list[dict[str, Any]]:
    return [
        s["data"] for r in await rows(w, chat) if r["from_self"] for s in r["segments"] if s["type"] == "game"
    ]


async def test_she_invites_you_with_a_card_instead_of_playing_in_words(world: World) -> None:  # noqa: F811
    chat = await _ready(world)
    model(world).queue("好呀，这次让你先下！[一起玩：五子棋]")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "想下五子棋"})
    cards = await eventually(lambda: _cards(world, chat))
    assert cards == [{"kind": "gomoku", "mode": "you", "name": "五子棋"}]
    said = [r for r in await rows(world, chat) if r["from_self"]]
    assert [r["text"] for r in said] == ["好呀，这次让你先下！", "[邀请你一起玩五子棋]"]
    assert "[一起玩：五子棋]" in model(world).requests[-1][1].content  # she was told how to invite
    assert await _current(world) is None  # nothing starts until the card is tapped

    # She draws, you guess; a game we do not have is only taken out of her words.
    model(world).queue("我来画，你猜！[一起玩：我画你猜]")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "换一个"})
    await eventually(lambda: _answered(world, chat, "我来画，你猜！"))
    assert (await eventually(lambda: _two(world, chat)))[1] == {
        "kind": "draw",
        "mode": "her",
        "name": "她画你猜",
    }
    model(world).queue("飞行棋我不会啦[一起玩：飞行棋]")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "飞行棋呢"})
    await eventually(lambda: _answered(world, chat, "飞行棋我不会啦"))
    assert len(await _cards(world, chat)) == 2


async def _two(w: World, chat: int) -> list[dict[str, Any]] | None:
    cards = await _cards(w, chat)
    return cards if len(cards) == 2 else None


async def test_no_game_without_a_chat_or_with_an_unknown_bot(world: World) -> None:  # noqa: F811
    response = await world.http.post("/api/games", json={"bot_id": WHALE})
    assert response.status_code == 409 and "聊天" in response.json()["detail"]
    response = await world.http.post("/api/games", json={"bot_id": "nobody"})
    assert response.status_code == 404
    assert (await world.http.post("/api/games", json={"bot_id": WHALE, "level": "insane"})).status_code == 422


# -- 黑白棋 -----------------------------------------------------------------------------------------------


async def test_reversi_shows_the_board_where_you_may_play_and_the_count(world: World) -> None:  # noqa: F811
    await _ready(world)
    game = await _start(world, kind="reversi", first="you")
    assert game["size"] == 8 and game["score"] == {"1": 2, "2": 2}
    assert sorted(map(tuple, game["options"])) == [(2, 3), (3, 2), (4, 5), (5, 4)]
    assert game["board"][3][4] == 1 and game["board"][3][3] == 2
    bad = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 0, "y": 0})
    assert bad.status_code == 409 and bad.json()["detail"] == "这里不能下"
    played = await world.http.post(f"/api/games/{game['id']}/moves", json={"bot_id": WHALE, "x": 2, "y": 3})
    assert played.status_code == 200 and played.json()["score"] == {"1": 4, "2": 1}
    after = await eventually(lambda: _moves(world, 2))
    assert after["turn"] == "you" and after["options"] and sum(after["score"].values()) == 6
    # The next one: she begins, since you began this one.
    nxt = await _start(world, kind="reversi")
    assert nxt["her_color"] == 1


# -- 海龟汤 -----------------------------------------------------------------------------------------------

PUZZLE = (
    '好呀，题目来了：{"title": "半杯咖啡", "surface": "阿星每天只喝半杯咖啡，今天却喝光了，然后笑着哭了。",'
    ' "bottom": "阿星的咖啡一直是和室友分着喝的，室友搬走那天留下一整杯，阿星喝完才发现杯底写着再见。",'
    ' "keys": ["和室友分着喝", "室友搬走", "杯底留言"],'
    ' "hints": ["半杯不是因为不爱喝", "和别人有关", "看看杯底"]}'
)


async def _soup(w: World) -> dict[str, Any]:
    model(w).queue(PUZZLE)
    game = await _start(w, kind="soup", flavor="clear", level="easy")
    assert game["thinking"] is True and game["soup"]["preparing"] is True

    async def ready() -> dict[str, Any] | None:
        now = await _current(w)
        return now if now["soup"]["title"] else None

    return await eventually(ready)


async def _answered_soup(w: World, count: int) -> dict[str, Any] | None:
    now = await _current(w)
    qs = now["soup"]["questions"]
    return now if len(qs) == count and not now["thinking"] else None


async def test_soup_she_makes_a_puzzle_and_answers_questions_without_telling(world: World) -> None:  # noqa: F811
    await _ready(world)
    game = await _soup(world)
    shown = game["soup"]
    assert (shown["title"], shown["hints"], shown["hints_left"]) == ("半杯咖啡", [], 3)
    assert "bottom" not in shown and "keys" not in shown  # the 汤底 is hers alone while it goes on
    assert "清汤" in model(world).requests[-1][1].content

    model(world).queue('{"answer": "是", "close": true, "line": "嗯哼，有点意思 [表情1]"}')
    asked = await world.http.post(
        f"/api/games/{game['id']}/ask", json={"bot_id": WHALE, "text": "和别人有关吗？"}
    )
    assert asked.status_code == 200 and asked.json()["thinking"] is True
    busy = await world.http.post(f"/api/games/{game['id']}/ask", json={"bot_id": WHALE, "text": "还有吗"})
    assert busy.status_code == 409
    after = await eventually(lambda: _answered_soup(world, 1))
    (q,) = after["soup"]["questions"]
    assert (q["q"], q["answer"], q["close"], q["line"]) == ("和别人有关吗？", "是", True, "嗯哼，有点意思")
    judged = model(world).requests[-1][1].content
    assert "汤底" in judged and "杯底写着再见" in judged and "和别人有关吗？" in judged

    hint = await world.http.post(f"/api/games/{game['id']}/hint", json={"bot_id": WHALE})
    assert hint.json()["soup"]["hints"] == ["半杯不是因为不爱喝"] and hint.json()["soup"]["hints_left"] == 2

    # While it goes on, her answers in the chat know the puzzle (and keep the 汤底 to herself).
    assert "汤底只有你知道" in await world.state.games.hint(WHALE)

    model(world).queue('{"solved": true, "close": true, "line": "猜中啦！"}')
    await world.http.post(
        f"/api/games/{game['id']}/ask",
        json={"bot_id": WHALE, "text": "室友搬走了，杯底留了字", "guess": True},
    )
    done = await eventually(lambda: _answered_soup(world, 2))
    assert done["status"] == "won" and done["record"] == {"won": 1, "lost": 0, "draw": 0}
    assert "杯底写着再见" in done["soup"]["bottom"] and done["soup"]["keys"][0] == "和室友分着喝"
    assert "对方猜中了汤底" in await world.state.games.hint(WHALE)


async def test_soup_unreadable_answers_and_giving_up(world: World) -> None:  # noqa: F811
    await _ready(world)
    game = await _soup(world)
    model(world).queue("我想想……", "还是不知道")  # twice nothing readable
    await world.http.post(f"/api/games/{game['id']}/ask", json={"bot_id": WHALE, "text": "是晚上吗"})
    after = await eventually(lambda: _answered_soup(world, 0))
    assert "没听清" in after["soup"]["error"]
    gave_up = await world.http.post(f"/api/games/{game['id']}/resign", json={"bot_id": WHALE})
    assert gave_up.json()["status"] == "resigned" and "杯底写着再见" in gave_up.json()["soup"]["bottom"]
    assert gave_up.json()["record"]["lost"] == 1


async def test_soup_a_puzzle_she_could_not_write_says_so(world: World) -> None:  # noqa: F811
    await _ready(world)
    model(world).queue("今天不想出题")
    await _start(world, kind="soup")

    async def failed() -> dict[str, Any] | None:
        now = await _current(world)
        return now if now["status"] == "left" else None

    game = await eventually(failed)
    assert "没写完整" in game["soup"]["error"]


# -- 你画我猜 ---------------------------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64  # what the app sends is a PNG; the fake model never opens it


def _eyes(w: World) -> None:
    w.state.games.vision = lambda: w.state.providers.get("ark")


async def _drawn(w: World, count: int) -> dict[str, Any] | None:
    now = await _current(w)
    done = [g for g in now["draw"]["guesses"] if g["guess"]]
    return now if len(done) == count and not now["thinking"] else None


async def test_draw_she_looks_at_the_drawing_and_guesses(world: World) -> None:  # noqa: F811
    await _ready(world)
    _eyes(world)
    game = await _start(world, kind="draw", level="easy")
    assert game["draw"]["word"] and game["draw"]["tries_left"] == 3 and game["turn"] is None
    other = await world.http.post(f"/api/games/{game['id']}/word", json={"bot_id": WHALE, "word": "西瓜"})
    assert other.json()["draw"] == other.json()["draw"] | {"word": "西瓜", "own": True}

    model(world).queue(
        "一个圆圆的东西，外面绿色、有深色条纹，像西瓜或者球。", '{"guess": "大西瓜", "line": "是西瓜吧！"}'
    )
    handed = await world.http.put(f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=PNG)
    assert handed.status_code == 200 and handed.json()["thinking"] is True
    done = await eventually(lambda: _drawn(world, 1))
    assert done["status"] == "won" and done["record"]["won"] == 1
    (guess,) = done["draw"]["guesses"]
    assert (guess["guess"], guess["right"], guess["line"]) == ("大西瓜", True, "是西瓜吧！")
    looked, guessed = model(world).requests[-2], model(world).requests[-1]
    assert looked[0].images and "简笔画" in looked[0].content
    assert "绿色" in guessed[1].content and "西瓜" not in guessed[1].content.split("画上是：")[0]
    picture = await world.http.get(f"/api/games/{game['id']}/sketches/1")
    assert picture.status_code == 200 and picture.content == PNG
    assert "答案是「西瓜」" in await world.state.games.hint(WHALE)


async def test_draw_three_wrong_guesses_and_the_word_stays_hers_to_find(world: World) -> None:  # noqa: F811
    await _ready(world)
    _eyes(world)
    game = await _start(world, kind="draw", word="雨伞")
    for n, wrong in enumerate(("蘑菇", "台灯", "帽子"), 1):
        model(world).queue("上面一个弧形，下面一根弯的竖线。", f'{{"guess": "{wrong}", "line": "嗯……"}}')
        await world.http.put(f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=PNG)
        now = await eventually(lambda n=n: _drawn(world, n))
        if n == 1:
            assert "正在桌面上玩你画我猜" in await world.state.games.hint(WHALE)
            assert all("雨伞" not in m.content for m in model(world).requests[-1])  # she is never told
        if n == 2:
            assert "蘑菇" in model(world).requests[-1][1].content  # what she guessed before
    assert now["status"] == "lost" and now["draw"]["tries_left"] == 0
    late = await world.http.put(f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=PNG)
    assert late.status_code == 409


async def test_draw_needs_eyes_and_a_picture(world: World) -> None:  # noqa: F811
    await _ready(world)
    game = await _start(world, kind="draw")
    blind = await world.http.put(f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=PNG)
    assert blind.status_code == 409 and "识图模型" in blind.json()["detail"]
    _eyes(world)
    junk = await world.http.put(
        f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=b"hello"
    )
    assert junk.status_code == 409


def test_a_right_guess_names_the_word() -> None:
    from nest.core.sketch import is_right

    assert is_right("猫", "猫") and is_right("一只小猫！", "猫") and is_right("猫咪", "猫")
    assert is_right("西瓜", "大西瓜") and not is_right("瓜", "大西瓜")
    assert is_right("伞", "雨伞") and not is_right("太阳", "月亮") and not is_right("", "猫")


# -- 你画我猜, she draws ---------------------------------------------------------------------------------


class _Painter:
    """Stands in for nest.core.drawing.Drawing: a tiny real picture, at once."""

    def __init__(self, ready: bool = True) -> None:
        self.ready, self.busy, self.asked = ready, set(), []

    def claim(self, bot: Any) -> str:
        if not self.ready:
            return "没画：现在没有能画图的模型"
        self.busy.add(bot.id)
        return ""

    def release(self, bot: Any) -> None:
        self.busy.discard(bot.id)

    async def picture(self, prompt: str) -> bytes:
        import io

        from PIL import Image

        self.asked.append(prompt)
        out = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(out, format="PNG")
        return out.getvalue()


async def _painted(w: World) -> dict[str, Any] | None:
    now = await _current(w)
    return now if now["draw"]["painted"] else None


async def test_she_draws_and_you_guess(world: World) -> None:  # noqa: F811
    await _ready(world)
    painter = _Painter()
    world.state.games.drawing = painter
    game = await _start(world, kind="draw", mode="her", level="easy")
    assert game["draw"]["mode"] == "her" and game["draw"]["word"] == "" and game["thinking"] is True
    ready = await eventually(lambda: _painted(world))
    word = (await world.state.games.store.get(game["id"]))["state"]["word"]
    assert ready["draw"]["word"] == "" and ready["thinking"] is False  # hidden while it goes on
    assert f"「{word}」" in painter.asked[0] and "文字" in painter.asked[0] and not painter.busy
    picture = await world.http.get(f"/api/games/{game['id']}/sketches/0")
    assert picture.status_code == 200 and picture.content.startswith(b"\x89PNG")
    assert f"「{word}」" in await world.state.games.hint(WHALE)  # she knows what she drew

    wrong = await world.http.post(f"/api/games/{game['id']}/answer", json={"bot_id": WHALE, "text": "不是它"})
    assert wrong.json()["draw"]["guesses"][0] == wrong.json()["draw"]["guesses"][0] | {"right": False}
    assert wrong.json()["draw"]["tries_left"] == 4
    hint = await world.http.post(f"/api/games/{game['id']}/hint", json={"bot_id": WHALE})
    assert hint.json()["draw"]["hints"] == [f"{len(word)} 个字"] and hint.json()["draw"]["hints_left"] == 1

    model(world).queue("嘿嘿，被你猜中啦")
    right = await world.http.post(f"/api/games/{game['id']}/answer", json={"bot_id": WHALE, "text": word})
    assert right.json()["status"] == "won" and right.json()["draw"]["word"] == word

    async def said() -> dict[str, Any] | None:
        now = await _current(world)
        return now if now["draw"]["line"] else None

    assert (await eventually(said))["draw"]["line"] == "嘿嘿，被你猜中啦"
    assert (
        word in model(world).requests[-1][1].content and "对方猜中了" in model(world).requests[-1][1].content
    )


async def test_she_draws_five_wrong_guesses_and_no_drawing_model(world: World) -> None:  # noqa: F811
    await _ready(world)
    world.state.games.drawing = _Painter(ready=False)
    no = await world.http.post("/api/games", json={"bot_id": WHALE, "kind": "draw", "mode": "her"})
    assert no.status_code == 409 and no.json()["detail"] == "现在没有能画图的模型"
    world.state.games.drawing = _Painter()
    game = await _start(world, kind="draw", mode="her")
    await eventually(lambda: _painted(world))
    early = await world.http.put(f"/api/games/{game['id']}/sketches", params={"bot_id": WHALE}, content=PNG)
    assert early.status_code == 409  # she draws this time
    for n in range(5):
        r = await world.http.post(
            f"/api/games/{game['id']}/answer", json={"bot_id": WHALE, "text": f"乱猜{n}"}
        )
    assert r.json()["status"] == "lost" and r.json()["draw"]["word"] and r.json()["draw"]["tries_left"] == 0
