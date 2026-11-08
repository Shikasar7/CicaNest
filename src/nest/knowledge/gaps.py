"""Project questions the docs did not answer: recorded for the console's
「资料」 page and forwarded by private message to whoever keeps the docs, so the next answer can be added and
the same question gets answered next time.

Forwarding merges: at most one message per base every GAP seconds (questions in between go into the next
one) and at most DAILY a day; nothing is forwarded while a base has nobody to forward to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from nest.storage.knowledge import GapRepo

log = logging.getLogger(__name__)

GAP = 600.0
DAILY = 10
SHOWN = 5  # questions written out in one message; the rest are counted
QUESTION_CHARS = 120
ANSWER_CHARS = 60
ANALYSIS_CHARS = 300
REASON = {"not_found": "资料里没找到", "uncertain": "答得不确定"}

# (bot account id, QQ number, text) -> whether it went out
Send = Callable[[str, str, str], Awaitable[bool]]


def message(base_name: str, gaps: list[dict]) -> str:
    lines = [f"{base_name} 有 {len(gaps)} 个问题资料里没答上："]
    for n, gap in enumerate(gaps[:SHOWN], start=1):
        question = " ".join(gap["question"].split())[:QUESTION_CHARS]
        answer = " ".join(gap["answer"].split())[:ANSWER_CHARS]
        said = f"（回了：{answer}）" if answer else ""
        lines.append(
            f"{n}. {gap['asker'] or '有人'}问：{question}——{REASON.get(gap['reason'], gap['reason'])}{said}"
        )
        if gap.get("analysis"):
            where = "，已在群里补答" if gap.get("analysis_sure") else "，没找到代码依据，没在群里说"
            analysis = " ".join(gap["analysis"].split())[:ANALYSIS_CHARS]
            lines.append(f"   看了源码{where}：{analysis}")
    if len(gaps) > SHOWN:
        lines.append(f"……还有 {len(gaps) - SHOWN} 个")
    lines.append("在控制台「资料」页补一条回答（看源码的结论可以直接拿来改），下次就能答上。")
    return "\n".join(lines)


class GapForwarder:
    def __init__(self, gaps: GapRepo, send: Send, *, gap: float = GAP, daily: int = DAILY) -> None:
        self.gaps = gaps
        self.send = send
        self.gap = gap
        self.daily = daily
        self._last: dict[int, float] = {}
        self._sent: dict[int, list[float]] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def notify(self, base_id: int, base_name: str) -> None:
        """A gap was recorded: forward it now, or with the next message if one went out recently."""
        if base_id in self._tasks and not self._tasks[base_id].done():
            return
        wait = max(0.0, self._last.get(base_id, 0.0) + self.gap - time.time())
        self._tasks[base_id] = asyncio.create_task(
            self._flush(base_id, base_name, wait), name="knowledge-gaps"
        )

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _flush(self, base_id: int, base_name: str, wait: float) -> None:
        await asyncio.sleep(wait)
        try:
            to = await self.gaps.forward_to(base_id)
            pending = await self.gaps.unforwarded(base_id)
            if not to or not pending:
                return
            today = [at for at in self._sent.get(base_id, []) if at > time.time() - 86400]
            if len(today) >= self.daily:
                log.info("资料「%s」今天已经转了 %d 次没答上的问题，先不转了", base_name, len(today))
                return
            if await self.send(pending[-1]["account_id"], to, message(base_name, pending)):
                await self.gaps.mark_forwarded([gap["id"] for gap in pending])
                self._last[base_id] = time.time()
                self._sent[base_id] = [*today, time.time()]
        except Exception:
            log.exception("转发没答上的问题失败")
