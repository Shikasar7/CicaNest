"""Speech from the user's own key: Fish Audio, MiniMax and any OpenAI-compatible /audio/speech.

Plain HTTP requests: service → URL,
headers, body, never falling back to another service or voice. All three are asked for MP3, which NapCat
turns into a QQ voice message by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from nest.llm.http import client, scrub

FISH_URL = "https://api.fish.audio"
# 国内站 / 国际站: a key works on the site it was made on only.
MINIMAX_URLS = {"cn": "https://api.minimaxi.com", "intl": "https://api.minimax.io"}
# Fish answers an unknown model with its default one instead of an error: only these are sent.
FISH_MODELS = ("s2.1-pro-free", "s2.1-pro", "s2-pro", "drama-3")  # the first is free (fair use)
MINIMAX_MODELS = ("speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd")
MINIMAX_VOICES = (
    ("female-shaonv", "少女"),
    ("female-yujie", "御姐"),
    ("female-chengshu", "成熟女性"),
    ("female-tianmei", "甜美女性"),
    ("male-qn-qingse", "青涩青年"),
    ("male-qn-jingying", "精英青年"),
    ("male-qn-badao", "霸道青年"),
    ("male-qn-daxuesheng", "青年大学生"),
)
OPENAI_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)
# SiliconFlow's CosyVoice2 system voices are named after the model: <model>:<voice>.
COSY_VOICES = (
    ("anna", "沉稳女声"),
    ("bella", "激情女声"),
    ("claire", "温柔女声"),
    ("diana", "欢快女声"),
    ("alex", "沉稳男声"),
    ("benjamin", "低沉男声"),
    ("charles", "磁性男声"),
    ("david", "欢快男声"),
)
LABELS = {"fish": "Fish Audio", "minimax": "MiniMax", "openai": "OpenAI 兼容接口"}
SPEECH_TIMEOUT = 60.0  # up to a few hundred characters, then the MP3: well under half a minute
LIST_TIMEOUT = 20.0
FISH_PAGE = 100
STATUS_TEXT = {
    400: "请求参数或音色不匹配",
    401: "密钥无效或已过期",
    402: "额度不足",
    403: "没有权限",
    404: "模型、音色或接口地址不对",
    429: "限流或额度不足",
}
MINIMAX_CODES = {
    1002: "限流，稍后再试",
    1004: "密钥不对，或者和站点（国内站 / 国际站）不一致",
    1008: "余额不足",
    1039: "限流，稍后再试",
    2013: "参数不对：检查模型和音色 ID",
}
HEX = re.compile(r"^(?:[0-9a-fA-F]{2})+$")
FISH_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.IGNORECASE)
VOICE_ID = re.compile(r"^[\w.:/@+-]{1,200}$")
LIBRARY_LIMIT = 200  # voices kept per service


class SpeechError(Exception):
    """Why there is no audio, in Chinese, without the key."""


@dataclass(frozen=True, slots=True)
class Service:
    """One speech service as the user set it up."""

    id: str  # "fish" | "minimax" | "openai"
    key: str
    model: str
    region: str = "cn"  # MiniMax's site
    base_url: str = ""  # openai: e.g. https://api.siliconflow.cn/v1
    proxy: str | None = None


def request_spec(service: Service, voice: str, text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """(url, headers, JSON body) of one piece of speech."""
    if not service.key and service.id != "openai":  # a local OpenAI-compatible server may need no key
        raise SpeechError(f"{LABELS[service.id]} 还没填密钥")
    if not service.model or not voice:
        raise SpeechError("还没选模型或音色")
    bearer = {"authorization": f"Bearer {service.key}"} if service.key else {}
    if service.id == "fish":
        if service.model not in FISH_MODELS:
            raise SpeechError(f"Fish 不认模型 {service.model}：只能选 {' / '.join(FISH_MODELS)}")
        body = {"text": text, "reference_id": voice, "format": "mp3", "mp3_bitrate": 128, "latency": "normal"}
        return f"{FISH_URL}/v1/tts", {**bearer, "model": service.model}, body
    if service.id == "minimax":
        return (
            f"{MINIMAX_URLS[service.region]}/v1/t2a_v2",
            bearer,
            {
                "model": service.model,
                "text": text,
                "stream": False,
                "output_format": "hex",
                "language_boost": "auto",
                "voice_setting": {"voice_id": voice, "speed": 1, "vol": 1, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            },
        )
    if not service.base_url:
        raise SpeechError("OpenAI 兼容接口还没填地址")
    body = {"model": service.model, "input": text, "voice": voice, "response_format": "mp3"}
    return f"{service.base_url.rstrip('/')}/audio/speech", bearer, body


def _json(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise SpeechError(f"{label} 返回的不是 JSON") from None
    return data if isinstance(data, dict) else {}


def _check_minimax(data: dict[str, Any]) -> None:
    """MiniMax answers 200 with a status of its own, which may be an error."""
    status = data.get("base_resp") or {}
    code = status.get("status_code")
    if code:
        said = MINIMAX_CODES.get(code) or str(status.get("status_msg") or "")[:120]
        raise SpeechError(f"MiniMax 错误 {code}：{said}")


def looks_like_audio(data: bytes) -> bool:
    """MP3 (ID3 or a frame sync), WAV, Ogg, FLAC: what NapCat can turn into a voice message."""
    head = data[:12]
    if head[:3] == b"ID3" or head[:4] in (b"OggS", b"fLaC"):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return True
    return len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0


def unpack(service: Service, response: httpx.Response) -> bytes:
    """The audio in a successful answer. MiniMax sends JSON with the audio as hex."""
    label = LABELS[service.id]
    if service.id == "minimax":
        data = _json(response, label)
        _check_minimax(data)
        encoded = (data.get("data") or {}).get("audio")
        if not isinstance(encoded, str) or not HEX.match(encoded):
            raise SpeechError("MiniMax 没有返回音频：检查音色 ID 和额度")
        audio = bytes.fromhex(encoded)
    else:
        audio = response.content
    if not looks_like_audio(audio):
        raise SpeechError(f"{label} 返回的不是音频：{scrub(response.text[:80], service.key)}")
    return audio


def _failure(service: Service, exc: Exception) -> SpeechError:
    label = LABELS[service.id]
    if isinstance(exc, httpx.TimeoutException):
        return SpeechError(f"连不上 {label}：等了太久没有回应，检查网络和代理")
    return SpeechError(f"连不上 {label}：{type(exc).__name__}，检查网络和代理")


def _status_failure(service: Service, response: httpx.Response) -> SpeechError:
    said = scrub(response.text[:120], service.key).strip()
    words = STATUS_TEXT.get(response.status_code, "服务暂时异常")
    return SpeechError(f"{LABELS[service.id]} HTTP {response.status_code}：{words}（{said}）")


async def synth(
    service: Service, voice: str, text: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """One piece of speech as audio bytes. SpeechError says why not."""
    url, headers, body = request_spec(service, voice, text)
    async with client(timeout=SPEECH_TIMEOUT, proxy=service.proxy, transport=transport) as http:
        try:
            response = await http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise _failure(service, exc) from None
    if response.status_code >= 400:
        raise _status_failure(service, response)
    return unpack(service, response)


def clean_library(service_id: str, voices: list[dict[str, str]]) -> list[dict[str, str]]:
    """A service's 音色库 as saved: trimmed, no repeats. For Fish a voice page's link works as the id."""
    cleaned: dict[str, str] = {}
    for voice in voices:
        voice_id, name = voice["id"].strip(), " ".join(voice["name"].split())
        fish_id = FISH_ID.search(voice_id) if service_id == "fish" else None
        voice_id = fish_id.group().lower() if fish_id else voice_id
        if not VOICE_ID.match(voice_id):
            raise SpeechError(f"音色 ID「{voice_id[:60]}」不对：不能有空格，最多 200 个字符")
        cleaned.setdefault(voice_id, name)
    if len(cleaned) > LIBRARY_LIMIT:
        raise SpeechError(f"一个服务最多记 {LIBRARY_LIMIT} 个音色")
    return [{"id": voice_id, "name": name} for voice_id, name in cleaned.items()]


def known_voices(service: Service) -> list[dict[str, str]]:
    """Voices that need no account to list: the service's system voices, when known."""
    if service.id == "minimax":
        return [{"id": voice, "name": name} for voice, name in MINIMAX_VOICES]
    if service.id == "openai" and "cosyvoice2" in service.model.lower():
        return [{"id": f"{service.model}:{voice}", "name": name} for voice, name in COSY_VOICES]
    if service.id == "openai" and re.match(r"(gpt-4o(-mini)?-tts|tts-1)", service.model, re.IGNORECASE):
        return [{"id": voice, "name": ""} for voice in OPENAI_VOICES]
    return []


async def _fish_voices(http: httpx.AsyncClient, service: Service) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    headers = {"authorization": f"Bearer {service.key}"}
    for page in range(1, 21):
        response = await http.get(
            f"{FISH_URL}/model",
            params={"self": "true", "page_size": FISH_PAGE, "page_number": page},
            headers=headers,
        )
        if response.status_code >= 400:
            raise _status_failure(service, response)
        data = _json(response, LABELS["fish"])
        items = data.get("items") if isinstance(data.get("items"), list) else []
        for model in items:
            if model.get("_id"):
                unready = "" if model.get("state", "trained") == "trained" else "（未训练完）"
                found.append(
                    {"id": str(model["_id"]), "name": f"{str(model.get('title') or '')[:30]}{unready}"}
                )
        if len(items) < FISH_PAGE or data.get("has_more") is False:
            break
    return found


async def _minimax_voices(http: httpx.AsyncClient, service: Service) -> list[dict[str, str]]:
    response = await http.post(
        f"{MINIMAX_URLS[service.region]}/v1/get_voice",
        json={"voice_type": "all"},
        headers={"authorization": f"Bearer {service.key}"},
    )
    if response.status_code >= 400:
        raise _status_failure(service, response)
    data = _json(response, LABELS["minimax"])
    _check_minimax(data)
    found = []
    for group, kind in (("voice_cloning", "复刻"), ("voice_generation", "设计")):
        for voice in data.get(group) or []:
            if voice.get("voice_id"):
                name = str(voice.get("voice_name") or "")[:30]
                found.append({"id": str(voice["voice_id"]), "name": f"{name}（{kind}）" if name else kind})
    return found


async def account_voices(
    service: Service, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, str]]:
    """The voices cloned or designed on the account, not the system ones (Fish and MiniMax)."""
    if service.id == "openai":
        return []
    if not service.key:
        raise SpeechError(f"{LABELS[service.id]} 还没填密钥")
    async with client(timeout=LIST_TIMEOUT, proxy=service.proxy, transport=transport) as http:
        try:
            if service.id == "fish":
                return await _fish_voices(http, service)
            return await _minimax_voices(http, service)
        except httpx.HTTPError as exc:
            raise _failure(service, exc) from None
