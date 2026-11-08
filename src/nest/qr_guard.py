"""A QQ that waits for a scan is stopped, not left asking for QR codes all day.

When a NapCat account's saved login stops working (QQ ended it, or froze the account until a face
check), NapCat shows a login QR code and asks QQ for a new one about every 2 minutes, for as long as
nobody scans. Asking over and over for an account QQ is already wary of is not wanted, so the container
is stopped instead once it has been waiting for a scan through two checks in a row while nobody had its
code open in the console. The account stays logged out until the user logs it in again: 「扫码登录」 on
the 系统 page starts the container and shows the code. While the console shows it (GET
/api/system/qq/login asks every few seconds, see want()), the container is left alone.

Only NapCat containers (the ones whose code nest can read) in the docker setup are watched.
Each check also keeps every account's connection in step with its container (Hub.hold): a container
stopped by hand is not connected to over and over, and one started by hand is connected again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from nest import docker, system
from nest.api.routes_system_admin import (
    NAPCAT_PAGE,
    QR_FRESH,
    container_names,
    machine_settings,
    sync_connection,
)

log = logging.getLogger(__name__)

CHECK_SECONDS = 60
# The console asks for the code every 3 seconds while it shows it; a container asked about within this long is
# being scanned (or about to be).
WANTED_SECONDS = 180


class QRGuard:
    def __init__(self, state: Any) -> None:
        self._state = state
        self._wanted: dict[str, float] = {}  # container -> when the console last asked for its code
        self._waiting: set[str] = set()  # waiting for a scan at the last check
        self._task: asyncio.Task[None] | None = None

    def want(self, container: str) -> None:
        """The console is showing this container's code: leave it running."""
        self._wanted[container] = time.monotonic()

    def _is_wanted(self, container: str) -> bool:
        asked = self._wanted.get(container)
        return asked is not None and time.monotonic() - asked < WANTED_SECONDS

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="qr-guard")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                await self.check()
            except Exception:
                log.exception("检查 QQ 是否在等扫码时出错")

    async def check(self) -> list[str]:
        """One pass; the containers it stopped."""
        state = self._state
        paths = await machine_settings(state)
        if paths["snowluma_mode"] != "docker":
            self._waiting.clear()
            return []
        views = state.hub.account_views()
        connected = {v["self_id"] for v in views if v["status"]["state"] in ("connected", "deaf")}
        names = {v["self_id"]: v["name"] for v in views}
        stopped: list[str] = []
        for name in container_names(paths["snowluma_container"]):
            try:
                box = await asyncio.to_thread(docker.container, name)
                await sync_connection(state, box)
                if not await self._waits_for_scan(name, box, connected):
                    self._waiting.discard(name)
                    continue
                if name not in self._waiting or self._is_wanted(name):
                    self._waiting.add(name)  # first time, or being scanned: look again next time
                    continue
                async with state.process_lock:
                    await asyncio.to_thread(docker.act, name, "stop")
                if box.account:
                    await state.hub.hold(f"qq:{box.account}")
            except system.ProcessError as exc:
                log.debug("没能检查 QQ 容器 %s：%s", name, exc)
                continue
            self._waiting.discard(name)
            stopped.append(name)
            who = names.get(box.account) or box.account or name
            log.warning(
                "%s 的 QQ 登录失效了，一直在等扫码：已停下容器 %s，不再刷新二维码。"
                "要登录时在「系统」页点它的「扫码登录」",
                who,
                name,
            )
        if stopped:
            state.broadcaster.publish("system", {"change": "qq_waiting_stopped"})
        return stopped

    async def _waits_for_scan(self, name: str, box: docker.Container, connected: set[str]) -> bool:
        if not box.running or NAPCAT_PAGE not in dict(box.ports):
            return False
        if box.account and box.account in connected:
            return False
        found = await asyncio.to_thread(docker.login_qr, name)
        return found is not None and found[1] <= QR_FRESH
