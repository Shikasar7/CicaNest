"""Windows 执行端: the desktop app on the user's Windows PC asks nest for work
(a long poll) and hands back what came of it. nest only queues the steps and waits: whether one may be done,
and the user's nod, are decided on that PC by rules kept there (desktop/src/executor), so a nest that went
wrong can only ask. One PC at a time: while one keeps asking, another is turned away.

A step carries `scope`: which answer or task it belongs to, and how often she has read something from outside
since (nest.core.tools taints it). "这个任务里同类不再问" is kept per scope on the PC, so it ends there when
she reads something new, the same as for plugin tools.

The PC is known by its key (a random secret its desktop app made once), not by the name it shows:
another signed-in device cannot take its steps or hand back made-up
results while it is connected. A desktop app from before keys sends none ("") and is known by its name.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

POLL_WAIT = 25.0  # how long one ask of the PC is held when there is nothing for it
ONLINE_SECONDS = 60.0  # asked within this long: the PC is there
RESULT_WAIT = 21 * 60.0  # its confirm window waits 10 minutes for the user, a command runs 10 at most
GIVEN_MAX = 1_100_000  # a step's arguments as JSON (a file she writes is at most a million characters)
RESULT_CHARS = 30_000
DEVICE_MAX = 80
KEY_MAX = 128
FOLDERS_MAX = 50
DECIDED = {"once": "allowed", "kind": "allowed_kind", "denied": "denied"}
OFFLINE = "对方的电脑现在没连上（桌面端没开，或者关了「电脑上做事」），这一步没做。"


@dataclass
class Step:
    id: str
    bot_id: str
    bot_name: str
    task_id: int | None
    scope: str
    tool: str
    given: dict[str, Any]
    created: float
    unattended: bool = False  # a routine's task: the PC does not ask the user, it says no
    delivered: bool = False
    decided: str = ""  # what the user said to it on the PC, as an approval's status
    answer: asyncio.Future[str] = field(default_factory=lambda: asyncio.get_running_loop().create_future())

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "task_id": self.task_id,
            "scope": self.scope,
            "tool": self.tool,
            "given": self.given,
            "unattended": self.unattended,
        }


class ExecutorHub:
    def __init__(
        self,
        broadcaster: Any = None,
        *,
        approvals: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.broadcaster = broadcaster
        self.approvals = approvals  # where the nods given on the PC are kept
        self._clock = clock
        self.device: str | None = None
        self._key = ""  # the connected PC's key ("" for an older desktop app)
        self.last_seen = float("-inf")
        self.folders: list[dict[str, Any]] = []  # what the PC says it lets her into (for her hint)
        # Whether the PC has told them since nest started (or since another PC took over): when not, the
        # poll's answer asks for them, or after a restart of nest she would not know of any folder.
        self.folders_known = False
        self.allow_all = False  # the PC's 完全允许, as it says
        self._steps: dict[str, Step] = {}
        self._wake = asyncio.Event()
        self._came = asyncio.Event()  # set each time the PC comes online

    @property
    def online(self) -> bool:
        return self.device is not None and self._clock() - self.last_seen < ONLINE_SECONDS

    def status(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "device": self.device if self.online else None,
            "folders": self.folders if self.online else [],
            "allow_all": self.allow_all and self.online,
            "waiting": sum(1 for s in self._steps.values() if s.delivered),
        }

    def _seen(self, device: str, key: str = "") -> bool:
        """The PC `device` (with its `key`) asked. False when another one is connected."""
        key = key[:KEY_MAX]
        if self.online and (device != self.device or key != self._key):
            return False
        was = self.online
        if device != self.device or key != self._key:
            self.folders = []
            self.folders_known = False
            self.allow_all = False
        self.device = device
        self._key = key
        self.last_seen = self._clock()
        if not was:
            log.info("Windows 执行端连上了（%s）", device)
            self._came.set()
            self._publish()
        return True

    async def wait_online(self, seconds: float) -> bool:
        """Until the PC is online (True), or `seconds` went by without it (False). For a task that
        needs the PC while the desktop app is closed (等电脑上线)."""
        deadline = self._clock() + seconds
        while not self.online:
            left = deadline - self._clock()
            if left <= 0:
                return False
            self._came.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._came.wait(), min(left, ONLINE_SECONDS))
        return True

    def _publish(self) -> None:
        if self.broadcaster is not None:
            self.broadcaster.publish("executor", {"online": self.online})

    async def poll(
        self, device: str, *, key: str = "", fresh: bool = False, wait: float = POLL_WAIT
    ) -> list[dict[str, Any]] | None:
        """The steps for the PC `device` (waits up to `wait` for one). `fresh`: it has just started, so what
        it was given before and did not answer is given again (it keeps which ones it did, and does not do
        them twice). None when another PC is connected."""
        device = device.strip()[:DEVICE_MAX] or "?"
        if not self._seen(device, key):
            return None
        if fresh:
            for step in self._steps.values():
                step.delivered = False
        ready = [s for s in self._steps.values() if not s.delivered]
        if not ready:
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), wait)
            if not self._seen(device, key):
                return None
            ready = [s for s in self._steps.values() if not s.delivered]
        for step in ready:
            step.delivered = True
        return [s.view() for s in ready]

    def report(
        self, device: str, folders: list[dict[str, Any]], *, key: str = "", allow_all: bool = False
    ) -> bool:
        """The folders the PC lets her into, as it says (only shown to her; it decides by its own list)."""
        if not self._seen(device.strip()[:DEVICE_MAX] or "?", key):
            return False
        self.folders = [
            {
                "path": str(f.get("path") or "")[:400],
                "read_only": bool(f.get("read_only")),
                "free_change": not f.get("read_only") and bool(f.get("free_change")),
            }
            for f in folders[:FOLDERS_MAX]
            if isinstance(f, dict) and f.get("path")
        ]
        self.folders_known = True
        self.allow_all = bool(allow_all)
        return True

    def answer(self, device: str, step_id: str, text: str, *, key: str = "", decided: str = "") -> bool:
        """What came of a step, and what the user said to it there (`decided`: "once", "kind", "denied"; "":
        not asked). False when it is not waiting (any more), or `device` is not the PC."""
        if device.strip()[:DEVICE_MAX] != self.device or key[:KEY_MAX] != self._key:
            return False
        step = self._steps.get(step_id)
        if step is None or step.answer.done():
            return False
        self.last_seen = self._clock()
        step.decided = DECIDED.get(decided, "")
        cut = "\n……（后面略）" if len(text) > RESULT_CHARS else ""
        step.answer.set_result(text[:RESULT_CHARS] + cut)
        return True

    async def call(
        self,
        *,
        bot_id: str,
        bot_name: str,
        task_id: int | None,
        scope: str,
        tool: str,
        given: dict[str, Any],
        unattended: bool = False,
        channel_id: int | None = None,
        wait: float = RESULT_WAIT,
    ) -> str:
        """Have the PC do a step; what it says came of it. A step the user was asked about there is kept with
        the other nods (nest.core.approvals)."""
        if not self.online:
            return OFFLINE
        if len(json.dumps(given, ensure_ascii=False)) > GIVEN_MAX:
            return "这一步给的内容太长了，没做。"
        step = Step(
            secrets.token_hex(8), bot_id, bot_name, task_id, scope, tool, given, self._clock(), unattended
        )
        self._steps[step.id] = step
        self._wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(step.answer), wait)
        except TimeoutError:
            return "电脑那边一直没回话（可能没人在电脑前点确认），这一步没做。"
        finally:
            self._steps.pop(step.id, None)
            if step.decided and self.approvals is not None:
                await asyncio.shield(
                    self.approvals.record(
                        bot_id=bot_id,
                        channel_id=channel_id,
                        task_id=task_id,
                        name="电脑上做事",
                        tool=tool,
                        given=given,
                        status=step.decided,
                        by=self.device or "",
                    )
                )
