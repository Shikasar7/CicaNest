"""发语音: the speech services (keys, models, limits), listing voices and 试听. A bot's voice is a bot
setting (PATCH /api/bots/{id}), 「发语音」 a group setting (PATCH /api/bots/{id}/channels/{channel}).

Writes hold app.state.config_lock; asking a service for voices or speech runs outside it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import can
from nest.api.auth import require_token
from nest.api.routes_stickers import group_names
from nest.api.text import has_bad_chars
from nest.core.speech import KEY_REFS
from nest.llm.speech import FISH_MODELS, SpeechError, clean_library

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

PREVIEW_TEXT = "你好呀，很高兴见到你。今天想听我讲点什么呢？"
PREVIEW_LIMIT = 200
Service = Literal["fish", "minimax", "openai"]
Line = Annotated[str, Field(max_length=300)]


class Voice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=300)]  # for Fish, a voice page's link works too
    name: Annotated[str, Field(max_length=40)] = ""


class SpeechPatch(BaseModel):
    """Every field optional. A key: "" removes it; null or left out keeps it."""

    model_config = ConfigDict(extra="forbid")

    fish_key: Line | None = None
    minimax_key: Line | None = None
    openai_key: Line | None = None
    fish_model: Line | None = None
    minimax_model: Line | None = None
    minimax_region: Literal["cn", "intl"] | None = None
    openai_url: Line | None = None
    openai_model: Line | None = None
    proxy: Line | None = None
    hourly_per_bot: Annotated[int, Field(strict=True, ge=1, le=60)] | None = None
    group_gap: Annotated[int, Field(strict=True, ge=10, le=3600)] | None = None
    max_chars: Annotated[int, Field(strict=True, ge=10, le=300)] | None = None
    library: dict[Service, list[Voice]] | None = None  # the services sent get this whole list


class VoiceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Service


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: Annotated[str, Field(min_length=3, max_length=260)]
    text: Annotated[str, Field(max_length=PREVIEW_LIMIT)] = ""


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _address(value: str, what: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://", "socks5://")):
        raise _invalid(f"{what}要以 http:// 或 https:// 开头")
    return value


def _library(sent: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    for voices in sent.values():
        if any(has_bad_chars(voice["name"]) for voice in voices):
            raise _invalid("音色名字里不能有控制字符")
    try:
        return {service: clean_library(service, voices) for service, voices in sent.items()}
    except SpeechError as exc:
        raise _invalid(str(exc)) from None


@router.get("/speech")
async def speech(request: Request) -> dict[str, Any]:
    """The services, voices and limits, and the groups that can 欢迎新人 with whether they do."""
    state = request.app.state
    features = await state.stickers.groups.all()
    names = await group_names(state)
    groups = [
        {"key": key, "name": name, "welcome": bool(features.get(key) and features[key].welcome)}
        for key, name in sorted(names.items(), key=lambda item: (item[1] or "~", item[0]))
        if can(key, "member_joins")
    ]
    return {**state.speech.view(), "groups": groups}


@router.patch("/speech")
async def update_speech(request: Request, patch: SpeechPatch) -> dict[str, Any]:
    state = request.app.state
    values = patch.model_dump(exclude_none=True)
    if not values:
        raise _invalid("没有要改的内容")
    for value in values.values():
        if isinstance(value, str) and has_bad_chars(value):
            raise _invalid("不能有控制字符")
    if "fish_model" in values and values["fish_model"] not in FISH_MODELS:
        raise _invalid(f"Fish 的模型只能选 {' / '.join(FISH_MODELS)}")
    if "openai_url" in values:
        values["openai_url"] = _address(values["openai_url"], "接口地址")
    if "proxy" in values:
        values["proxy"] = _address(values["proxy"], "代理地址")
    async with state.config_lock:
        if "library" in values:
            values["library"] = state.speech.config.library | _library(values["library"])
        for service, ref in KEY_REFS.items():
            key = values.pop(f"{service}_key", None)
            if key is None:
                continue
            if state.secrets.source(ref) == "env":
                raise HTTPException(status_code=400, detail="这个密钥写在环境变量里，这里改不了")
            if key.strip():
                state.secrets.set(ref, key.strip())
            elif state.secrets.file_value(ref) is not None:
                state.secrets.unset(ref)
        await state.speech.save_config(values)
    state.broadcaster.publish("bots", {"change": "speech"})
    return state.speech.view()  # type: ignore[no-any-return]


@router.post("/speech/voices")
async def speech_voices(request: Request, body: VoiceList) -> dict[str, Any]:
    try:
        return {"voices": await request.app.state.speech.voices(body.service)}
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/speech/preview")
async def preview(request: Request, body: Preview) -> Response:
    """「试听」: the audio itself, for the console to play."""
    text = " ".join(body.text.split()) or PREVIEW_TEXT
    if has_bad_chars(text):
        raise _invalid("不能有控制字符")
    try:
        audio = await request.app.state.speech.say(body.voice, text)
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(audio, media_type="audio/mpeg")
