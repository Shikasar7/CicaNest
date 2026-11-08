"""发语音: a bot's reply read out in its own voice, as a QQ voice message.

The services are the user's own (nest.llm.speech); their keys live in .env, everything else in
settings["speech"]. A bot's voice is `<service>:<voice id>` (bots.voice). 「发语音」 per bot and group
(bot_channels.voice_mode) offers the model the [语音] mark; "trial" only records that it would have.
Limits count decisions.voice_state, like stickers count theirs. Whenever speech cannot go out the words
go out as text, so a reply is never lost to a voice service.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import httpx

from nest.core.events import ChannelRef, Segment
from nest.core.prompt import sung
from nest.llm.speech import (
    FISH_MODELS,
    LABELS,
    MINIMAX_MODELS,
    Service,
    SpeechError,
    account_voices,
    known_voices,
    synth,
)
from nest.secrets import SecretStore
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

HOUR = 3600.0
SETTINGS_KEY = "speech"
# Fish sometimes reads the lyrics instead of singing them. Sung ones go at
# 2.6 characters a second at most, read ones at 2.9 or more.
SUNG_PACE = 2.75
SING_TAKES = 3
FISH_BYTES_PER_SECOND = 16000  # the 128 kbit/s request_spec asks for
KEY_REFS = {
    "fish": "NEST_SPEECH_FISH_KEY",
    "minimax": "NEST_SPEECH_MINIMAX_KEY",
    "openai": "NEST_SPEECH_OPENAI_KEY",
}
SENT_STATES = ("sent", "trial", "unknown")  # what counts as the bot having (or as good as) spoken


@dataclass(frozen=True)
class SpeechConfig:
    """Saved in settings["speech"]; everything else is a default."""

    fish_model: str = FISH_MODELS[0]
    minimax_model: str = MINIMAX_MODELS[0]
    minimax_region: str = "cn"
    openai_url: str = ""
    openai_model: str = ""
    proxy: str = ""
    hourly_per_bot: int = 6  # voice messages one bot may send in one group per hour
    group_gap: float = 60.0  # seconds between two voice messages from any of our bots in one group
    max_chars: int = 100  # a longer reply goes out as text
    library: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # 音色库: service -> [{id, name}]


EDITABLE = tuple(SpeechConfig.__dataclass_fields__)


def split_ref(voice: str) -> tuple[str, str]:
    """(service, voice id) of `<service>:<voice id>`; the voice id may have colons of its own."""
    service, _, voice_id = voice.partition(":")
    return (service, voice_id) if service in KEY_REFS and voice_id else ("", "")


class SpeechKeeper:
    def __init__(
        self, db: Database, secrets: SecretStore, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.db = db
        self.settings = SettingsRepo(db)
        self.secrets = secrets
        self.config = SpeechConfig()
        self._transport = transport

    async def reload(self) -> None:
        stored = (await self.settings.all()).get(SETTINGS_KEY)
        values = {k: v for k, v in stored.items() if k in EDITABLE} if isinstance(stored, dict) else {}
        try:
            self.config = replace(SpeechConfig(), **values)
        except TypeError:
            self.config = SpeechConfig()

    async def save_config(self, values: dict[str, Any]) -> None:
        merged = asdict(self.config) | {k: v for k, v in values.items() if k in EDITABLE}
        await self.settings.set_many({SETTINGS_KEY: merged})
        await self.reload()

    def service(self, service_id: str) -> Service:
        config = self.config
        model = {
            "fish": config.fish_model,
            "minimax": config.minimax_model,
            "openai": config.openai_model,
        }[service_id]
        return Service(
            id=service_id,
            key=self.secrets.get(KEY_REFS[service_id]),
            model=model,
            region=config.minimax_region,
            base_url=config.openai_url,
            proxy=config.proxy or None,
        )

    def configured(self, service_id: str) -> bool:
        service = self.service(service_id)
        if service_id == "openai":
            return bool(service.base_url and service.model)
        return bool(service.key and service.model)

    def ready(self, voice: str) -> bool:
        """Whether a bot with this voice can speak at all."""
        service_id, _ = split_ref(voice)
        return bool(service_id) and self.configured(service_id)

    async def say(self, voice: str, text: str) -> bytes:
        """`text` in this voice, as audio. SpeechError says why not."""
        service_id, voice_id = split_ref(voice)
        if not service_id:
            raise SpeechError("还没选音色")
        return await synth(self.service(service_id), voice_id, text, transport=self._transport)

    async def sing(self, voice: str, lyrics: str) -> bytes:
        """The lyrics sung in this Fish voice: taken again when it only read them, and the slowest take if
        it never sang. SpeechError when no take came back."""
        letters = len(re.sub(r"\W", "", lyrics))
        best = b""
        for take in range(1, SING_TAKES + 1):
            try:
                audio = await self.say(voice, sung(lyrics))
            except SpeechError:
                if best:
                    break
                raise
            pace = letters / max(len(audio) / FISH_BYTES_PER_SECOND, 0.1)
            log.info("唱歌第 %d 遍：%d 字，每秒 %.1f 字", take, letters, pace)
            best = max(best, audio, key=len)
            if pace <= SUNG_PACE:
                break
        return best

    async def voices(self, service_id: str) -> list[dict[str, str]]:
        """The voices cloned or designed on the account. SpeechError says why not."""
        return await account_voices(self.service(service_id), transport=self._transport)

    def view(self) -> dict[str, Any]:
        return {
            "settings": asdict(self.config),
            "services": [
                {
                    "id": service_id,
                    "label": LABELS[service_id],
                    "key_set": bool(self.secrets.get(ref)),
                    "key_from_env": self.secrets.source(ref) == "env",
                    "ready": self.configured(service_id),
                    "known": known_voices(self.service(service_id)),
                }
                for service_id, ref in KEY_REFS.items()
            ],
            "fish_models": list(FISH_MODELS),
            "minimax_models": list(MINIMAX_MODELS),
        }

    async def _spoken(self, channel: ChannelRef, since: float, bot_id: str | None = None) -> int:
        marks = ",".join("?" * len(SENT_STATES))
        row = await self.db.fetchone(
            f"""SELECT count(*) FROM decisions d JOIN channels c ON c.id = d.channel_id
                WHERE c.kind = ? AND c.external_id = ? AND d.voice_state IN ({marks})
                  AND d.created_at >= ? AND (? IS NULL OR d.bot_id = ?)""",
            (channel.kind, channel.id, *SENT_STATES, since, bot_id, bot_id),
        )
        return int(row[0]) if row else 0

    async def blocked(self, channel: ChannelRef, bot_id: str, now: float, *, asked: bool = False) -> str:
        """Why this bot may not speak in this group right now; "" when it may. `asked`: someone asked it to,
        which the gap between voices (there so the bots do not talk over each other) does not hold up."""
        config = self.config
        if not asked and await self._spoken(channel, now - config.group_gap):
            return f"{config.group_gap:.0f} 秒内群里刚发过语音"
        spoken = await self._spoken(channel, now - HOUR, bot_id)
        if spoken >= config.hourly_per_bot:
            return f"这个小时已经发了 {spoken} 条语音，到上限了"
        return ""

    @staticmethod
    def segment(audio: bytes) -> Segment:
        """The audio as a OneBot voice segment: base64, since NapCat in Docker cannot read nest's files."""
        return Segment("record", {"file": f"base64://{base64.b64encode(audio).decode('ascii')}"})
