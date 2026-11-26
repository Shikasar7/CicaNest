"""画图: a companion draws a picture for the user, with the model that can draw (Codex),
and shows it in their chat in the app. It takes about a minute, so it is drawn in the background: her answer
does not wait for it. Every picture costs the subscription's quota: at most a few a day, one at a time.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import Any

from nest.core.stickers import data_url, preview_png
from nest.core.workspace import FOR_YOU, WorkspaceError
from nest.llm.base import ProviderError
from nest.llm.purpose import using
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotChannel, BotConfig

log = logging.getLogger(__name__)

PER_DAY = 10  # pictures a companion may draw in a day
IDEA_MAX = 600
PROMPT = "画一张图：{idea}\n{likeness}不要水印，不要签名；除非要求里写了，图里不要出现文字。"
LIKENESS = "画里的「{name}」照参考图里的样子画（发型、发色、眼睛、服装特征一致）。"


class Drawing:
    """`avatar`: a bot's picture to draw her from (None: she has none)."""

    def __init__(
        self,
        providers: Any,
        desk: Any,
        desk_media: Any,
        workspace: Any,
        broadcaster: Any,
        avatar: Callable[[str], Any],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.providers = providers
        self.desk = desk
        self.desk_media = desk_media
        self.workspace = workspace
        self.broadcaster = broadcaster
        self._avatar = avatar
        self._clock = clock
        self._drawn: dict[tuple[str, str], int] = {}  # (bot, day) -> how many
        self._busy: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def ready(self) -> bool:
        return self.providers.drawing() is not None

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def claim(self, bot: BotConfig) -> str:
        """Take one of her pictures for today and mark her busy; why not ('' when she may draw)."""
        if not self.ready:
            return "没画：现在没有能画图的模型"
        if bot.id in self._busy:
            return "上一张还没画完，画完再画下一张"
        day = dt.datetime.fromtimestamp(self._clock()).astimezone().strftime("%Y-%m-%d")
        if self._drawn.get((bot.id, day), 0) >= PER_DAY:
            return f"没画：今天已经画了 {PER_DAY} 张，明天再画"
        self._drawn = {k: n for k, n in self._drawn.items() if k[1] == day}
        self._drawn[(bot.id, day)] = self._drawn.get((bot.id, day), 0) + 1
        self._busy.add(bot.id)
        return ""

    def release(self, bot: BotConfig) -> None:
        self._busy.discard(bot.id)

    async def picture(self, prompt: str) -> bytes:
        """A picture for something else than her chat (小游戏's 她画你猜), after `claim`. Raises
        ProviderUnavailable or ProviderError."""
        runtime = self.providers.drawing()
        if runtime is None:
            raise ProviderUnavailable("没有能画图的模型")
        with using("draw"):
            return await runtime.draw(prompt, ())

    def start(self, bot: BotConfig, channel: BotChannel, idea: str, *, herself: bool) -> str:
        """Begin drawing; what she is told."""
        idea = " ".join(idea.split())[:IDEA_MAX]
        if not idea:
            return "没画：要写清楚画什么"
        if why := self.claim(bot):
            return why
        task = asyncio.create_task(self._draw(bot, channel, idea, herself), name=f"drawing:{bot.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "开始画了，大约一分钟，画好会直接发到聊天里（也会放进「给你的」）。现在先回对方一句。"

    async def _draw(self, bot: BotConfig, channel: BotChannel, idea: str, herself: bool) -> None:
        try:
            with using("draw"):
                await self._make(bot, channel, idea, herself)
        except (ProviderError, ProviderUnavailable) as exc:
            log.info("%s 没画成：%s", bot.name, exc)
            await self.desk.note(bot, channel, f"刚才那张图没画成：{exc}")
        except Exception:
            log.exception("%s 画图出错", bot.name)
            await self.desk.note(bot, channel, "刚才那张图没画成，出了点问题。")
        finally:
            self._busy.discard(bot.id)

    async def _make(self, bot: BotConfig, channel: BotChannel, idea: str, herself: bool) -> None:
        runtime = self.providers.drawing()
        if runtime is None:
            raise ProviderUnavailable("没有能画图的模型")
        face = await self._avatar(bot.id) if herself else None
        images = (data_url(await asyncio.to_thread(preview_png, face)),) if face else ()
        likeness = LIKENESS.format(name=bot.name) if face else ""
        picture = await runtime.draw(PROMPT.format(idea=idea, likeness=likeness), images)
        name = await asyncio.to_thread(self.desk_media.save_picture, picture)
        if name is None:
            raise ProviderError("模型给的不是认得的图片", retryable=False)
        await self.desk.show_picture(bot, channel, name, idea)
        if self.workspace is not None:
            stamp = dt.datetime.fromtimestamp(self._clock()).astimezone().strftime("%m%d-%H%M%S")
            ending = name.rsplit(".", 1)[-1]
            try:
                path = f"{FOR_YOU}/画-{stamp}.{ending}"
                await asyncio.to_thread(self.workspace.write, bot.id, path, picture)
                self.broadcaster.publish("workspace", {"bot_id": bot.id})
            except WorkspaceError as exc:
                log.info("%s 画的图没放进工作区：%s", bot.name, exc)
