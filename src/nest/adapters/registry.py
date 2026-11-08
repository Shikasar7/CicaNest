"""Which platforms nest can run, and what each one can do. Everything outside the adapters asks here instead
of importing an adapter or comparing a platform name, so capabilities are known for an offline account too.

Capabilities:
  reply, mention        quote a message, @ someone
  send_image, send_voice
  sticker_source        its pictures can be collected as 表情包
  sees_all              it receives every message of a group, not only the ones calling it
  self_echo             the platform sends the bot's own messages back; otherwise the adapter makes that event
  fetch_history, list_channels
  proactive             it can speak without being spoken to (监控, 掉线提醒, 汇总)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from nest.adapters.base import (
    Adapter,
    BackfillSource,
    FormField,
    Probed,
    StatusSink,
    parse_recipient,
)
from nest.adapters.discord.adapter import DiscordAdapter
from nest.adapters.discord.probe import check_proxy
from nest.adapters.discord.probe import probe as probe_discord
from nest.adapters.onebot11.adapter import OneBotAdapter
from nest.adapters.wecom.adapter import WecomAdapter
from nest.adapters.wecom.probe import probe as probe_wecom
from nest.secrets import SecretReader
from nest.storage.accounts import Account

QQ_NUMBER = re.compile(r"[1-9][0-9]{4,11}")
BOT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")  # the account's self id: never a colon

Builder = Callable[[Account, SecretReader, StatusSink, BackfillSource], Adapter]


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    capabilities: frozenset[str]
    fields: tuple[FormField, ...]  # the console's form, in order
    user_id: re.Pattern[str]  # what a person's id looks like: private chats and who gets told things
    user_label: str  # that id's name, for hints: "QQ 号"
    user_error: str  # what to say when one does not look like it
    place: tuple[str, str]  # for prompts: where a group chat and a private chat happen
    build: Builder
    probe: Callable[[Mapping[str, str]], Awaitable[Probed]]
    # The self id's secrets: field name -> name in .env. QQ keeps its tokens under its own columns.
    refs: Callable[[str], dict[str, str]] = lambda self_id: {}
    # Each field, tidied. ValueError says what is wrong with one.
    clean: Callable[[Mapping[str, str]], dict[str, str]] = dict
    # A probe connects as the bot and throws out whoever is connected: never for an account that exists.
    probe_kicks: bool = False


def _onebot(
    account: Account, secrets: SecretReader, on_status: StatusSink, backfill: BackfillSource
) -> Adapter:
    return OneBotAdapter(
        account=account.self_id,
        http_url=account.http_url,
        ws_url=account.ws_url,
        http_token=secrets.get(account.http_token_ref),
        ws_token=secrets.get(account.ws_token_ref),
        on_status=on_status,
        backfill_source=backfill,
    )


def _discord(
    account: Account, secrets: SecretReader, on_status: StatusSink, backfill: BackfillSource
) -> Adapter:
    return DiscordAdapter(
        account=account.self_id,
        token=secrets.get(account.refs.get("token", "")),
        proxy=str(account.config.get("proxy") or ""),
        on_status=on_status,
        backfill_source=backfill,
    )


def _clean_discord(fields: Mapping[str, str]) -> dict[str, str]:
    return {**fields, "token": fields.get("token", "").strip(), "proxy": check_proxy(fields.get("proxy", ""))}


def _wecom(
    account: Account, secrets: SecretReader, on_status: StatusSink, backfill: BackfillSource
) -> Adapter:
    return WecomAdapter(
        account=account.self_id,
        secret=secrets.get(account.refs.get("secret", "")),
        name=account.name,
        on_status=on_status,
    )


def _clean_wecom(fields: Mapping[str, str]) -> dict[str, str]:
    bot_id = fields.get("bot_id", "").strip()
    if bot_id and not BOT_ID.fullmatch(bot_id):
        raise ValueError("BotID 只有字母、数字、下划线和连字符，到企业微信后台再复制一遍")
    return {**fields, "bot_id": bot_id, "secret": fields.get("secret", "").strip()}


def _wecom_refs(bot_id: str) -> dict[str, str]:
    """A name in .env holds letters, digits and underscores; two ids that only differ elsewhere stay two."""
    slug = re.sub(r"[^A-Za-z0-9]", "_", bot_id)
    if slug != bot_id:
        slug += "_" + hashlib.sha256(bot_id.encode()).hexdigest()[:8]
    return {"secret": f"NEST_WECOM_{slug}_SECRET"}


async def _probe_qq(fields: Mapping[str, str]) -> Probed:
    """A QQ logs in by scanning a code after the bot exists: only its number can be looked at beforehand."""
    self_id = fields.get("self_id", "").strip()
    if not QQ_NUMBER.fullmatch(self_id):
        return Probed(False, detail="QQ 号要填 5–12 位数字")
    return Probed(True, self_id=self_id, detail="保存以后扫码登录")


PLATFORMS: dict[str, PlatformSpec] = {
    "qq": PlatformSpec(
        key="qq",
        label="QQ",
        capabilities=OneBotAdapter.capabilities,
        fields=(FormField("self_id", "QQ 号", required=True, identity=True, hint="这个 bot 要登录的 QQ 号"),),
        user_id=QQ_NUMBER,
        user_label="QQ 号",
        user_error="QQ 号要填 5–12 位数字",
        place=("QQ 群", "QQ 私聊"),
        build=_onebot,
        probe=_probe_qq,
    ),
    "discord": PlatformSpec(
        key="discord",
        label="Discord",
        capabilities=DiscordAdapter.capabilities,
        fields=(
            FormField(
                "token",
                "Bot Token",
                secret=True,
                required=True,
                hint="Developer Portal 的 Bot 页，点 Reset Token 后复制；只显示一次",
            ),
            FormField(
                "proxy",
                "代理",
                default="http://127.0.0.1:7890",
                hint="这台机器访问 Discord 用的 HTTP 代理，留空表示直连",
            ),
        ),
        user_id=re.compile(r"[1-9][0-9]{16,19}"),
        user_label="Discord 用户 ID",
        user_error="Discord 用户 ID 是 17–20 位数字：在 Discord 设置里开开发者模式，右键用户复制",
        place=("Discord 频道", "Discord 私聊"),
        build=_discord,
        probe=probe_discord,
        refs=lambda self_id: {"token": f"NEST_DISCORD_{self_id}_TOKEN"},
        clean=_clean_discord,
    ),
    "wecom": PlatformSpec(
        key="wecom",
        label="企业微信",
        capabilities=WecomAdapter.capabilities,
        fields=(
            FormField(
                "bot_id",
                "BotID",
                required=True,
                identity=True,
                hint="管理后台的智能机器人页，API 模式、长连接方式；用超级管理员账号建",
            ),
            FormField("secret", "Secret", secret=True, required=True, hint="和 BotID 在同一页"),
        ),
        user_id=re.compile(r"[A-Za-z0-9._@-]{1,64}"),
        user_label="企业微信 userid",
        user_error="企业微信 userid 只有字母、数字和 . _ - @",
        place=("企业微信群", "企业微信私聊"),
        build=_wecom,
        probe=probe_wecom,
        refs=_wecom_refs,
        clean=_clean_wecom,
        probe_kicks=True,
    ),
}


def build_adapter(
    account: Account, secrets: SecretReader, on_status: StatusSink, backfill: BackfillSource
) -> Adapter:
    return PLATFORMS[account.platform].build(account, secrets, on_status, backfill)


def capabilities_of(platform: str) -> frozenset[str]:
    spec = PLATFORMS.get(platform)
    return spec.capabilities if spec is not None else frozenset()


def can(account_id: str, capability: str) -> bool:
    """Whether the account "<platform>:<self id>" can do this."""
    return capability in capabilities_of(account_id.partition(":")[0])


def check_user(platform: str, user: str) -> str:
    """A person's id on this platform, trimmed. ValueError says what is wrong with it."""
    spec = PLATFORMS[platform]
    user = user.strip()
    if not spec.user_id.fullmatch(user):
        raise ValueError(spec.user_error)
    return user


def check_recipient(value: str) -> str:
    """Whom a setting tells something: "" for nobody, a bare QQ number, or "<platform>:<their id>". ValueError
    says what is wrong with it."""
    value = value.strip()
    if not value:
        return ""
    platform, user = parse_recipient(value)
    if platform not in PLATFORMS:
        raise ValueError("要填 QQ 号，别的平台写成「平台:对方的 ID」，比如 discord:123456789012345678")
    check_user(platform, user)
    return value


def label_of(platform: str) -> str:
    spec = PLATFORMS.get(platform)
    return spec.label if spec is not None else platform


def place_of(platform: str) -> tuple[str, str]:
    spec = PLATFORMS.get(platform)
    return spec.place if spec is not None else ("群", "私聊")
