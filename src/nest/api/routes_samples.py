"""示例伙伴 in the chat window: who comes with nest, and taking one in as a companion on the desk."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.routes_companion_packs import bring
from nest.core.plugins import PluginError
from nest.core.samples import Sample, samples
from nest.storage.avatars import image_type

router = APIRouter(prefix="/api/samples", dependencies=[Depends(require_token)])

taking = asyncio.Lock()  # two clicks make one companion


class Taking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None  # None: she does not talk until she has a model


def _made(state: Any, sample: Sample) -> str | None:
    """The companion made from it that is still here."""
    for plugin_id in state.plugins.installed:
        if plugin_id.startswith(f"{sample.id}.for"):
            for bot_id in state.brain.bots:
                if state.plugins.on_for(plugin_id, bot_id):
                    return str(bot_id)
    return None


async def _sample(sample_id: str) -> Sample:
    for sample in await asyncio.to_thread(samples):
        if sample.id == sample_id:
            return sample
    raise HTTPException(status_code=404, detail="没有这个示例伙伴")


@router.get("")
async def listed(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "id": sample.id,
            "name": sample.pack.name,
            "description": sample.manifest.description,
            "persona": sample.pack.persona,
            "extras": [k for k in sample.manifest.view()["kinds"] if k != "companion"],
            "bot_id": _made(request.app.state, sample),
        }
        for sample in await asyncio.to_thread(samples)
    ]


@router.get("/{sample_id}/avatar")
async def avatar(sample_id: str) -> Response:
    data = (await _sample(sample_id)).pack.avatar
    content_type = image_type(data)
    if content_type is None:
        raise HTTPException(status_code=404, detail="没有头像")
    return Response(data, media_type=content_type, headers={"cache-control": "private, max-age=3600"})


@router.post("/{sample_id}", status_code=201)
async def take_in(request: Request, sample_id: str, body: Taking) -> dict[str, Any]:
    state = request.app.state
    sample = await _sample(sample_id)
    async with taking:
        made = _made(state, sample)
        if made is not None:
            raise HTTPException(status_code=409, detail=f"{state.brain.bots[made].name}已经在这里了")
        data = await asyncio.to_thread(sample.zipped)
        try:
            staged = await state.plugins.stage(data)
        except PluginError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return await bring(state, staged["stage"], provider_id=body.provider_id)
