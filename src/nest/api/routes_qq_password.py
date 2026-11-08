"""回退密码 for a NapCat account (the 系统 page, each QQ container): NapCat logs in with the saved login, and
when QQ no longer takes it, with this password before it falls back to a QR code.

Only the password's MD5 is kept, which NapCat takes as it is (NAPCAT_QUICK_PASSWORD_MD5): the password
itself is never stored, logged or shown again. NapCat reads it when the container starts, so setting or
clearing it makes the container again, the same way it was made, and the account drops for a few seconds:

- a container of deploy/mac/docker-compose.yml (the first three accounts): the MD5 goes into that compose
  project's .env as NAPCAT_PASSWORD_MD5_<QQ number>, which the service passes on, and `docker compose up -d`
  makes the container again with the same host name and MAC address, so the saved login keeps working;
- one nest made (增加 bot): the MD5 goes into nest's .env as NEST_NAPCAT_<QQ number>_PASSWORD_MD5 and the
  container is deleted and run again with the same ports, folders, host name and MAC address (nest.napcat).

Runs under process_lock, like starting and stopping the containers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nest import docker, housekeeping, napcat, system
from nest.api.auth import require_token
from nest.api.routes_system_admin import NAPCAT_PAGE, _container_names
from nest.secrets import SecretStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

PASSWORD_LIMIT = 64


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: str
    password: str = Field(max_length=PASSWORD_LIMIT)  # "" clears it


def compose_variable(self_id: str) -> str:
    return f"NAPCAT_PASSWORD_MD5_{self_id}"


def nest_variable(self_id: str) -> str:
    return f"NEST_NAPCAT_{self_id}_PASSWORD_MD5"


def _compose_passes_it_on(compose: docker.Compose, variable: str) -> bool:
    """The service's compose file hands `variable` to NapCat (see deploy/mac/docker-compose.yml)."""
    for name in compose.config_files:
        path = Path(name) if Path(name).is_absolute() else Path(compose.working_dir) / name
        try:
            if f"${{{variable}" in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


async def _through_compose(compose: docker.Compose, self_id: str, md5: str) -> None:
    variable = compose_variable(self_id)
    if not await asyncio.to_thread(_compose_passes_it_on, compose, variable):
        raise HTTPException(
            status_code=409,
            detail=f"docker-compose.yml 里 {compose.service} 还没有接回退密码"
            f"（要有 NAPCAT_QUICK_PASSWORD_MD5: ${{{variable}:-}}）：先更新 nest 的代码",
        )
    env = SecretStore(Path(compose.working_dir) / ".env")
    before = env.file_value(variable)
    if md5:
        env.set(variable, md5)
    else:
        env.unset(variable)
    try:
        await asyncio.to_thread(docker.compose_up, compose)
    except system.ProcessError as exc:
        if before is None:
            env.unset(variable)
        else:
            env.set(variable, before)
        raise HTTPException(status_code=500, detail=f"重建容器失败：{exc}") from None


async def _through_nest(state: Any, name: str, box: docker.Container, md5: str) -> None:
    plan = napcat.existing_plan(name, box, housekeeping.qq_cache_root(state.settings.home))
    webui_token = state.secrets.get(f"NEST_NAPCAT_{box.account}_WEBUI_TOKEN")
    if plan is None or not webui_token:
        raise HTTPException(
            status_code=409, detail=f"{name} 不是 nest 建的容器，也不是 docker compose 建的：没法重建它"
        )
    variable = nest_variable(box.account)
    before = state.secrets.get(variable)
    await asyncio.to_thread(docker.remove, name)
    try:
        await asyncio.to_thread(
            docker.create, napcat.run_args(plan, webui_token=webui_token, password_md5=md5)
        )
    except system.ProcessError as exc:
        log.error("用新的回退密码重建 %s 失败：%s；换回原来的设置再建", name, exc)
        try:
            args = napcat.run_args(plan, webui_token=webui_token, password_md5=before)
            await asyncio.to_thread(docker.create, args)
        except system.ProcessError as again:
            log.error("重建 %s 又失败了：%s", name, again)
        raise HTTPException(status_code=500, detail=f"重建容器失败：{exc}") from None
    if md5:
        state.secrets.set(variable, md5)
    else:
        state.secrets.unset(variable)


@router.put("/system/qq/password")
async def set_password(request: Request, body: PasswordChange) -> dict[str, Any]:
    state = request.app.state
    name = body.container
    if await _container_names(state, name) is None:
        raise HTTPException(status_code=400, detail="QQ 不在 Docker 容器里，没有回退密码")
    md5 = hashlib.md5(body.password.encode("utf-8")).hexdigest() if body.password else ""
    async with state.process_lock:
        box = await asyncio.to_thread(docker.container, name)
        if box.problem:
            raise HTTPException(status_code=400, detail=box.problem)
        if not box.found or NAPCAT_PAGE not in dict(box.ports) or not box.account:
            raise HTTPException(status_code=400, detail=f"{name} 不是一个号的 NapCat 容器，没有回退密码")
        if box.compose is not None:
            await _through_compose(box.compose, box.account, md5)
        else:
            await _through_nest(state, name, box, md5)
    log.info("已%s %s 的回退密码，容器 %s 已重建", "设置" if md5 else "清除", box.account, name)
    state.broadcaster.publish("system", {"change": "qq_password"})
    return {"ok": True, "password_set": bool(md5)}
