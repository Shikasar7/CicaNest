"""回忆草稿: before her chat with the user is cleaned away, she writes up what was worth keeping — a draft she
does not use until the user has read and kept it."""

from __future__ import annotations

import time
from typing import Any

import pytest

from nest.core.desk import MAX_CHARS
from nest.core.events import MessageEvent, Segment
from nest.core.keepsake_drafts import SOURCE, DraftConfig, KeepsakeDrafter, clean, title_of
from tests.test_bot_settings_api import World, world  # noqa: F401 (fixture)

DAY = 86_400.0


async def companion(w: World) -> tuple[str, int]:
    made = (await w.http.post("/api/companions", json={"name": "阿星", "provider_id": "ark"})).json()
    return made["bot_id"], made["channel_id"]


async def talk(w: World, bot_id: str, chat: int, lines: int, *, at: float) -> None:
    """`lines` from the user and as many answers of hers, one minute apart, from `at` on."""
    ref = w.state.brain.bots[bot_id].channels[chat].channel
    for n in range(lines):
        for mine, words in ((False, f"今天第{n}件事"), (True, f"嗯嗯，第{n}件我记住了")):
            await w.state.store.save(
                MessageEvent(
                    channel=ref,
                    message_id=f"t{at:.0f}-{n}{int(mine)}",
                    sender_id=bot_id.partition(":")[2] if mine else ref.id,
                    sender_name="阿星" if mine else "阿月",
                    segments=(Segment("text", {"text": words}),),
                    text=words,
                    timestamp=at + n * 60 + (30 if mine else 0),
                    from_self=mine,
                )
            )


def drafter(w: World, now: list[float], days: float = 30, **config: Any) -> KeepsakeDrafter:
    async def history_days() -> float:
        return days

    return KeepsakeDrafter(
        w.state.brain,
        w.state.keepsakes,
        w.state.broadcaster,
        history_days,
        config=DraftConfig(**config),
        clock=lambda: now[0],
    )


def model(w: World) -> Any:
    return w.state.providers.get("ark").provider


async def test_she_writes_a_draft_and_uses_it_only_once_kept(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    start = time.time() - 2 * DAY
    await talk(world, bot_id, chat, 12, at=start)
    now = [time.time()]
    model(world).queue("## 11月22日\n\n阿月说了十二件事，最要紧的是第三件：周末一起看海。")
    assert await drafter(world, now).run() == 1

    ask = model(world).requests[-1][1].content
    assert "阿月：今天第0件事" in ask and "你：嗯嗯，第0件我记住了" in ask and "SKIP" in ask
    [draft] = (await world.http.get(f"/api/bots/{bot_id}/keepsakes")).json()
    assert draft["draft"] is True and draft["source"] == SOURCE and draft["title"].startswith("回忆草稿：")
    assert "周末一起看海" in draft["body"]
    # Waiting for the user: counted in the chat list, not looked up by her yet.
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[bot_id]["drafts"] == 1
    assert await world.state.keepsakes.look_up(bot_id, "看海") == []

    response = await world.http.patch(f"/api/keepsakes/{draft['id']}", json={"keep": True})
    assert response.status_code == 200
    assert (await world.http.get(f"/api/bots/{bot_id}/keepsakes")).json()[0]["draft"] is False
    assert await world.state.keepsakes.look_up(bot_id, "看海")
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[bot_id]["drafts"] == 0

    # Written up: not again until there is enough new, and a week has gone by.
    assert await drafter(world, now).run() == 0


async def test_not_before_there_is_enough_or_it_is_time(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 4, at=now[0] - DAY)
    assert await drafter(world, now).run() == 0  # too little said yet
    await talk(world, bot_id, chat, 12, at=now[0] - DAY + 3600)
    model(world).queue("SKIP")
    assert await drafter(world, now).run() == 0  # she found nothing worth keeping
    assert (await world.http.get(f"/api/bots/{bot_id}/keepsakes")).json() == []
    upto, run_at = await world.state.keepsakes.store.progress(bot_id)
    assert run_at == now[0] and upto > now[0] - DAY

    # More said, but a week has not gone by, and none of it is near being cleaned away.
    await talk(world, bot_id, chat, 12, at=now[0] + 60)
    now[0] += 2 * DAY
    assert await drafter(world, now).run() == 0
    # Close to the cleanup (history kept 3 days, so written 1.5 ahead): now, even within the week.
    model(world).queue("## 记下的事\n\n第五件事要记住。")
    assert await drafter(world, now, days=3).run() == 1


async def test_a_long_stretch_is_written_in_parts(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 30, at=now[0] - DAY)
    model(world).queue("第一部分")
    assert await drafter(world, now, max_lines=40).run() == 1
    model(world).queue("第二部分")
    assert await drafter(world, now, max_lines=40).run() == 1  # the rest at the next look, not a week later
    titles = [k["body"] for k in (await world.http.get(f"/api/bots/{bot_id}/keepsakes")).json()]
    assert titles == ["第一部分", "第二部分"]


async def test_long_messages_are_read_to_their_end(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 12, at=now[0] - DAY)
    long = "字" * (MAX_CHARS - 1) + "完"
    async with world.state.store.db.transaction() as conn:
        await conn.execute("UPDATE messages SET text = ? WHERE text = '今天第3件事'", (long,))
    model(world).queue("第一部分")
    assert await drafter(world, now, read_chars=MAX_CHARS + 100).run() == 1
    assert long in model(world).requests[-1][1].content
    assert "今天第11件事" not in model(world).requests[-1][1].content  # enough for one draft
    later = drafter(world, now, read_chars=MAX_CHARS + 100, min_user_lines=1)
    model(world).queue("第二部分")
    assert await later.run() == 1  # the rest, at the next look
    ask = model(world).requests[-1][1].content
    assert "今天第11件事" in ask and long not in ask
    assert await later.run() == 0


async def test_a_little_said_long_ago_is_written_up_before_it_is_gone(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 2, at=now[0] - 26 * DAY)
    model(world).queue("两句话，但很要紧。")
    assert await drafter(world, now).run() == 1
    assert await drafter(world, now).run() == 0


async def test_kept_one_day_not_a_draft_per_line(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 3, at=now[0] - 3600)
    assert await drafter(world, now, days=1).run() == 0


async def test_a_failed_save_is_tried_again(world: World, monkeypatch: Any) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    now = [time.time()]
    await talk(world, bot_id, chat, 12, at=now[0] - DAY)
    keeper = drafter(world, now)

    async def broken(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(keeper.store, "add", broken)
        model(world).queue("第一次")
        with pytest.raises(RuntimeError):
            await keeper.run()
    assert await keeper.store.progress(bot_id) == (0.0, 0.0)
    model(world).queue("第二次")
    assert await keeper.run() == 1


async def test_drafts_are_apart_from_what_she_uses(world: World) -> None:  # noqa: F811
    bot_id, _ = await companion(world)
    store = world.state.keepsakes.store
    await store.add(bot_id, "回忆草稿：11月24日", "还没看过的", source=SOURCE, draft=True)
    await store.add(bot_id, "旧的回忆", "看过的")
    assert [k["title"] for k in await store.of_bot(bot_id, drafts=False)] == ["旧的回忆"]
    assert await store.drafts() == {bot_id: 1}


def test_what_she_wrote() -> None:
    assert clean("SKIP") == "" and clean("  skip，没什么要记的") == ""
    assert clean("```markdown\n## 11月24日\n看海\n```") == "## 11月24日\n看海"
    day = time.mktime((2026, 11, 24, 10, 0, 0, 0, 0, -1))
    assert title_of(day, day + 3600) == "回忆草稿：11月24日"
    assert title_of(day - 6 * DAY, day) == "回忆草稿：11月18日–11月24日"
