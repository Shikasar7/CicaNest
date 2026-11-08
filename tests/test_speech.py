"""发语音: the three speech services' requests, the limits, and a reply going out as a voice message or,
when it cannot, as text."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest.core.brain import Draft
from nest.core.events import ActionResult, MessageEvent, Segment, SendMessage
from nest.core.prompt import SING_HINT, VOICE_HINT, build_prompt, readable, split_voice
from nest.core.speech import KEY_REFS, SpeechKeeper, split_ref
from nest.llm.speech import (
    Service,
    SpeechError,
    account_voices,
    clean_library,
    known_voices,
    looks_like_audio,
    request_spec,
    synth,
)
from nest.secrets import SecretStore
from nest.storage.bots import BotChannel, BotConfig
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import World, world  # noqa: F401 (fixture)
from tests.test_sending import FakeAdapter, dm, go_live, private_with

MP3 = b"ID3\x04audio"
BOT = "qq:10001"
FISH = Service(id="fish", key="fish-key-123456", model="s2.1-pro-free")
MINIMAX = Service(id="minimax", key="mm-key-123456", model="speech-2.8-hd")
OPENAI = Service(id="openai", key="sk-abcdefgh", model="tts-1", base_url="https://relay.example/v1/")


def mock(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_requests_match_what_each_service_takes() -> None:
    url, headers, body = request_spec(FISH, "abc", "你好")
    assert url == "https://api.fish.audio/v1/tts"
    assert headers == {"authorization": "Bearer fish-key-123456", "model": "s2.1-pro-free"}
    assert body["reference_id"] == "abc" and body["format"] == "mp3"
    url, _, body = request_spec(replace(MINIMAX, region="intl"), "female-shaonv", "你好")
    assert url == "https://api.minimax.io/v1/t2a_v2"
    assert body["voice_setting"]["voice_id"] == "female-shaonv" and body["output_format"] == "hex"
    url, _, body = request_spec(OPENAI, "nova", "你好")
    assert url == "https://relay.example/v1/audio/speech"
    assert body == {"model": "tts-1", "input": "你好", "voice": "nova", "response_format": "mp3"}
    assert request_spec(replace(OPENAI, key=""), "nova", "你好")[1] == {}  # a local server needs no key


def test_what_cannot_be_sent_says_why() -> None:
    with pytest.raises(SpeechError, match="还没填密钥"):
        request_spec(replace(FISH, key=""), "abc", "你好")
    with pytest.raises(SpeechError, match="Fish 不认模型"):
        request_spec(replace(FISH, model="made-up"), "abc", "你好")
    with pytest.raises(SpeechError, match="音色"):
        request_spec(MINIMAX, "", "你好")
    with pytest.raises(SpeechError, match="地址"):
        request_spec(replace(OPENAI, base_url=""), "nova", "你好")


async def test_minimax_audio_is_hex_and_its_own_errors_are_read() -> None:
    def answer(payload: dict[str, Any]) -> httpx.MockTransport:
        return mock(lambda _: httpx.Response(200, json=payload))

    ok = {"data": {"audio": MP3.hex()}, "base_resp": {"status_code": 0}}
    assert await synth(MINIMAX, "v", "你好", transport=answer(ok)) == MP3
    with pytest.raises(SpeechError, match="余额不足"):
        await synth(MINIMAX, "v", "你好", transport=answer({"base_resp": {"status_code": 1008}}))
    with pytest.raises(SpeechError, match="没有返回音频"):
        await synth(MINIMAX, "v", "你好", transport=answer({"data": {"audio": "zz"}}))


async def test_failures_never_show_the_key() -> None:
    echo = mock(lambda _: httpx.Response(401, text="bad key fish-key-123456"))
    with pytest.raises(SpeechError, match="密钥无效") as failure:
        await synth(FISH, "v", "你好", transport=echo)
    assert "fish-key-123456" not in str(failure.value)
    with pytest.raises(SpeechError, match="不是音频"):
        await synth(FISH, "v", "你好", transport=mock(lambda _: httpx.Response(200, text="<html>")))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SpeechError, match="连不上 Fish Audio"):
        await synth(FISH, "v", "你好", transport=mock(down))


def test_audio_is_told_from_anything_else() -> None:
    for audio in (MP3, b"\xff\xfb\x90", b"RIFF0000WAVEfmt", b"OggS000", b"fLaC000"):
        assert looks_like_audio(audio)
    assert not looks_like_audio(b'{"error": 1}') and not looks_like_audio(b"")


async def test_account_voices_of_fish_and_minimax() -> None:
    def fish(request: httpx.Request) -> httpx.Response:
        assert request.url.params["self"] == "true"
        items = [
            {"_id": "a", "title": "小马", "state": "trained"},
            {"_id": "b", "title": "鱼", "state": "training"},
        ]
        return httpx.Response(200, json={"items": items})

    assert await account_voices(FISH, transport=mock(fish)) == [
        {"id": "a", "name": "小马"},
        {"id": "b", "name": "鱼（未训练完）"},
    ]

    def minimax(_: httpx.Request) -> httpx.Response:
        payload = {"voice_cloning": [{"voice_id": "c", "voice_name": "龙"}], "base_resp": {"status_code": 0}}
        return httpx.Response(200, json=payload)

    assert await account_voices(MINIMAX, transport=mock(minimax)) == [{"id": "c", "name": "龙（复刻）"}]
    assert await account_voices(OPENAI) == []


def test_known_voices_follow_the_model() -> None:
    assert known_voices(MINIMAX)[0] == {"id": "female-shaonv", "name": "少女"}
    assert known_voices(OPENAI)[0]["id"] == "alloy"
    cosy = replace(OPENAI, model="FunAudioLLM/CosyVoice2-0.5B")
    assert known_voices(cosy)[0]["id"] == "FunAudioLLM/CosyVoice2-0.5B:anna"
    assert known_voices(FISH) == []


def test_the_library_is_cleaned_before_it_is_saved() -> None:
    fish_id = "0123456789abcdef0123456789ABCDEF"
    voices = [
        {"id": f" https://fish.audio/m/{fish_id}/ ", "name": " 阿黑  小号 "},
        {"id": fish_id.lower(), "name": "重复的"},
        {"id": "abc", "name": ""},
    ]
    assert clean_library("fish", voices) == [
        {"id": fish_id.lower(), "name": "阿黑 小号"},
        {"id": "abc", "name": ""},
    ]
    assert clean_library("openai", [{"id": "Fun/CosyVoice2-0.5B:anna", "name": "安娜"}])[0]["name"] == "安娜"
    with pytest.raises(SpeechError, match="不能有空格"):
        clean_library("minimax", [{"id": "两 段", "name": ""}])
    with pytest.raises(SpeechError, match="最多记"):
        clean_library("minimax", [{"id": f"v{n}", "name": ""} for n in range(201)])


def test_voice_marks_and_references() -> None:
    assert split_voice("[语音] 哼，才不是") == ("哼，才不是", True)
    assert split_voice("[ 语音 ]好耶\n[语音]") == ("好耶", True)
    assert split_voice("好耶") == ("好耶", False)
    assert split_ref("openai:FunAudioLLM/CosyVoice2-0.5B:anna") == (
        "openai",
        "FunAudioLLM/CosyVoice2-0.5B:anna",
    )
    assert split_ref("fish:") == ("", "") and split_ref("nope:x") == ("", "") and split_ref("") == ("", "")


def test_the_prompt_only_mentions_voice_when_it_may_be_used() -> None:
    args: dict[str, Any] = {
        "persona": "",
        "bot_name": "鱼",
        "context": [],
        "batch": [],
        "called": set(),
        "peers": {},
    }
    assert VOICE_HINT not in build_prompt(**args)[1].content
    assert VOICE_HINT in build_prompt(**args, voice=True)[1].content


# -- a reply spoken -------------------------------------------------------------------------------------


@dataclass
class Net:
    fails: bool = False
    texts: list[str] = field(default_factory=list)
    audio: list[bytes] = field(default_factory=list)  # the next answers, before MP3

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.texts.append(json.loads(request.content)["text"])
        if self.fails:
            return httpx.Response(500, text="down")
        return httpx.Response(200, content=self.audio.pop(0) if self.audio else MP3)


class TwoAnswers(FakeAdapter):
    """Refuses the first message, takes the rest."""

    async def perform(self, action: SendMessage) -> ActionResult:
        self.actions.append(action)
        if len(self.actions) == 1:
            return ActionResult("failed", error="不让发")
        return self.result


@dataclass
class Kit:
    w: World
    keeper: SpeechKeeper
    net: Net
    adapter: FakeAdapter

    @property
    def bot(self) -> BotConfig:
        return self.w.brain.bots[BOT]

    @property
    def channel(self) -> BotChannel:
        return next(iter(self.bot.channels.values()))

    async def set_mode(self, mode: str) -> None:
        await self.w.repo.set_channel(BOT, self.channel.channel_id, voice_mode=mode)
        await self.w.brain.load()

    async def reply(self, raw: str) -> dict[str, Any]:
        """The model's answer to one mention, and the decision it became."""
        self.w.providers["10001"].queue(raw)
        await self.w.say("阿鲸 在吗", to=["10001"], accounts=["10001"])
        return (await self.w.settle(1))[0]  # type: ignore[no-any-return]

    def sent(self) -> list[list[str]]:
        return [[s.type for s in action.segments] for action in self.adapter.actions]


@pytest.fixture
async def kit(world: World, tmp_path: Path) -> AsyncIterator[Kit]:  # noqa: F811
    secrets = SecretStore(tmp_path / ".env")
    secrets.set(KEY_REFS["fish"], "fish-key-123456")
    net = Net()
    keeper = SpeechKeeper(world.db, secrets, transport=httpx.MockTransport(net.handler))
    world.brain.speech = keeper
    await world.repo.update_bot(BOT, voice="fish:abc")
    adapter = await go_live(world)
    yield Kit(world, keeper, net, adapter)


async def test_a_marked_reply_goes_out_as_one_voice_message(kit: Kit) -> None:
    await kit.set_mode("on")
    decision = await kit.reply("[语音] 哼\n才不是")
    assert decision["outcome"] == "replied" and decision["voice_state"] == "sent"
    assert decision["reply"] == "哼\n才不是" and decision["reason"] == "已发送语音"
    assert kit.net.texts == ["哼，才不是"]
    assert kit.sent() == [["record"]]
    data = kit.adapter.actions[0].segments[0].data
    assert data["file"] == "base64://" + base64.b64encode(MP3).decode()
    assert VOICE_HINT in kit.w.providers["10001"].requests[0][1].content


async def test_a_bot_remembers_what_it_said_in_a_voice_message(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.reply("[语音] 哼\n才不是")
    await kit.w.store.save(  # what comes back from QQ has no words
        MessageEvent(
            channel=kit.channel.channel,
            message_id="900",
            sender_id="10001",
            sender_name="阿鲸",
            segments=(Segment("record", {"file": "x.amr"}),),
            text="[语音]",
            timestamp=time.time(),
            from_self=True,
        )
    )
    kit.w.providers["10001"].queue("嗯")
    await kit.w.say("阿鲸 你刚说啥", to=["10001"], accounts=["10001"])
    await kit.w.settle(2)
    assert "你（阿鲸）：（语音）哼 才不是" in kit.w.providers["10001"].requests[-1][1].content


async def test_someone_who_asks_for_voice_gets_it_right_after_another_bots(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.reply("[语音] 第一条")
    kit.w.providers["10001"].queue("第二条")  # not marked: it was asked for all the same
    await kit.w.say("阿鲸 发段语音", to=["10001"], accounts=["10001"])
    second = next(d for d in await kit.w.settle(2) if d["reply"] == "第二条")
    assert second["voice_state"] == "sent" and kit.net.texts == ["第一条", "第二条"]

    kit.w.providers["10001"].queue("[语音] 第三条")  # chose to on its own: the gap still holds
    await kit.w.say("阿鲸 在吗", to=["10001"], accounts=["10001"])
    third = next(d for d in await kit.w.settle(3) if d["reply"] == "第三条")
    assert third["voice_state"] is None and len(kit.net.texts) == 2


async def test_being_asked_for_voice_stays_within_the_hourly_limit_and_never_reads_links(kit: Kit) -> None:
    await kit.set_mode("on")
    kit.keeper.config = replace(kit.keeper.config, hourly_per_bot=1)
    await kit.reply("[语音] 第一条")
    kit.w.providers["10001"].queue("第二条")
    await kit.w.say("阿鲸 发段语音", to=["10001"], accounts=["10001"])
    second = next(d for d in await kit.w.settle(2) if d["reply"] == "第二条")
    assert second["voice_state"] is None and len(kit.net.texts) == 1

    kit.keeper.config = replace(kit.keeper.config, hourly_per_bot=9)
    kit.w.providers["10001"].queue("看这里 https://example.com/a")
    await kit.w.say("阿鲸 语音念一下", to=["10001"], accounts=["10001"])
    link = next(d for d in await kit.w.settle(3) if "example" in (d["reply"] or ""))
    assert link["voice_state"] is None and len(kit.net.texts) == 1


async def test_saying_voice_without_calling_the_bot_asks_for_nothing(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, activity="active")
    await kit.w.brain.load()
    kit.w.providers["10001"].queue("是吗")
    await kit.w.say("语音功能真好玩", accounts=["10001"])
    assert (await kit.w.settle(1))[0]["voice_state"] is None and kit.net.texts == []


SUNG = MP3 + b"s" * (16000 * 4)  # four seconds for six characters: sung
READ = MP3 + b"r" * (16000 * 2)  # two: read


async def test_a_song_that_came_out_read_is_taken_again(kit: Kit) -> None:
    kit.net.audio = [READ, SUNG, READ]
    assert await kit.keeper.sing("fish:abc", "星星亮\n小鱼摇") == SUNG
    assert len(kit.net.texts) == 2 and kit.net.audio == [READ]


async def test_a_song_never_sung_goes_out_as_the_slowest_take(kit: Kit) -> None:
    longer = READ + b"r" * 100
    kit.net.audio = [READ, longer, READ]
    assert await kit.keeper.sing("fish:abc", "星星亮\n小鱼摇") == longer
    assert len(kit.net.texts) == 3

    kit.net.fails = True  # no take at all: the words go out as text
    with pytest.raises(SpeechError):
        await kit.keeper.sing("fish:abc", "星星亮")


async def ask_for_a_song(kit: Kit, lyrics: str = "星星亮\n小鱼摇") -> dict[str, Any]:
    kit.w.providers["10001"].queue(lyrics)
    await kit.w.say("阿鲸 唱首歌", to=["10001"], accounts=["10001"])
    return (await kit.w.settle(1))[0]  # type: ignore[no-any-return]


async def test_asked_to_sing_a_bot_writes_lyrics_and_sings_them(kit: Kit) -> None:
    await kit.set_mode("on")
    kit.net.audio = [SUNG]
    decision = await ask_for_a_song(kit)
    assert decision["voice_state"] == "sent" and decision["reason"] == "已唱歌"
    assert decision["reply"] == "星星亮\n小鱼摇"
    assert kit.net.texts == ["[singing] 星星亮~ ♪ 小鱼摇~ ♪"]
    prompt = kit.w.providers["10001"].requests[0][1].content
    assert SING_HINT in prompt and VOICE_HINT not in prompt


async def test_a_song_keeps_its_lines_whatever_the_group_allows(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, reply_lines=1)
    await kit.w.brain.load()
    kit.net.audio = [SUNG]
    decision = await ask_for_a_song(kit)
    assert decision["reply"] == "星星亮\n小鱼摇"
    assert "这次只说一句" not in kit.w.providers["10001"].requests[0][0].content


async def test_a_song_is_not_taken_for_a_question(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, answer_questions=True)
    await kit.w.brain.load()
    decision = await ask_for_a_song(kit)
    assert decision["voice_state"] == "sent" and len(kit.w.providers["10001"].requests) == 1


async def test_without_voice_a_song_request_is_ordinary_chat(kit: Kit) -> None:
    decision = await ask_for_a_song(kit, "我不会唱")
    assert decision["voice_state"] is None and kit.net.texts == []
    assert SING_HINT not in kit.w.providers["10001"].requests[0][1].content


async def test_a_private_chat_with_voice_on_speaks_and_sings(kit: Kit) -> None:
    ref = await private_with(kit.w)
    provider = kit.w.providers["10001"]
    provider.queue("[语音] 没开语音")
    await dm(kit.w, "你好")
    await kit.w.settle(1)
    assert kit.net.texts == [] and VOICE_HINT not in provider.requests[0][1].content  # off by default

    channel = await kit.w.store.channel_id(ref)
    await kit.w.repo.set_channel(BOT, channel, voice_mode="on")
    await kit.w.brain.load()
    provider.queue("[语音] 听到了吗")
    await dm(kit.w, "说句话")
    await kit.w.settle(2)
    assert kit.net.texts == ["听到了吗"] and VOICE_HINT in provider.requests[1][1].content
    assert kit.adapter.actions[-1].channel == ref and kit.sent()[-1] == ["record"]

    provider.queue("星星亮\n小鱼摇")
    await dm(kit.w, "唱首歌")
    await kit.w.settle(3)
    assert kit.net.texts[-1] == "[singing] 星星亮~ ♪ 小鱼摇~ ♪"
    assert SING_HINT in provider.requests[2][1].content


async def test_when_speech_fails_the_words_go_out_as_text(kit: Kit) -> None:
    await kit.set_mode("on")
    kit.net.fails = True
    decision = await kit.reply("[语音] 好耶")
    assert decision["outcome"] == "replied" and decision["voice_state"] is None
    assert "语音没合成出来" in decision["reason"]
    assert "text" in kit.sent()[0]


async def test_a_voice_message_qq_refuses_goes_out_as_text(kit: Kit) -> None:
    await kit.set_mode("on")
    kit.adapter = kit.w.brain.adapters["qq:10001"] = TwoAnswers()
    decision = await kit.reply("[语音] 好耶")
    assert decision["outcome"] == "replied" and decision["voice_state"] == "failed"
    assert kit.sent()[0] == ["record"] and "text" in kit.sent()[1]


async def test_trial_records_it_and_sends_the_words(kit: Kit) -> None:
    await kit.set_mode("trial")
    decision = await kit.reply("[语音] 好耶")
    assert decision["voice_state"] == "trial" and "语音试运行" in decision["reason"]
    assert kit.net.texts == [] and "text" in kit.sent()[0]


async def test_switched_off_the_model_is_not_offered_it(kit: Kit) -> None:
    decision = await kit.reply("[语音] 好耶")
    assert decision["reply"] == "好耶" and decision["voice_state"] is None
    assert VOICE_HINT not in kit.w.providers["10001"].requests[0][1].content
    assert kit.net.texts == []


async def test_a_bot_without_a_voice_or_outside_a_chat_is_not_offered_it(kit: Kit) -> None:
    await kit.set_mode("on")
    assert await kit.w.brain._speakable(kit.bot, kit.channel, False)
    assert not await kit.w.brain._speakable(kit.bot, kit.channel, True)  # an answer
    await kit.w.repo.update_bot(BOT, voice="")
    await kit.w.brain.load()
    assert not await kit.w.brain._speakable(kit.bot, kit.channel, False)


async def test_the_rate_speaks_replies_the_model_did_not_mark(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, voice_rate=40)
    await kit.w.brain.load()
    kit.w.brain.chance = lambda: 0.39
    decision = await kit.reply("今天天气不错")
    assert decision["voice_state"] == "sent" and kit.sent() == [["record"]]


async def test_above_the_rate_a_reply_stays_text(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, voice_rate=40)
    await kit.w.brain.load()
    kit.w.brain.chance = lambda: 0.4
    decision = await kit.reply("今天天气不错")
    assert decision["voice_state"] is None and "text" in kit.sent()[0]


async def test_with_no_rate_only_marked_replies_are_spoken(kit: Kit) -> None:
    await kit.set_mode("on")
    kit.w.brain.chance = lambda: 0.0
    decision = await kit.reply("今天天气不错")
    assert decision["voice_state"] is None and kit.net.texts == []


async def test_the_rate_never_reads_out_links_or_code(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.w.repo.set_channel(BOT, kit.channel.channel_id, voice_rate=100)
    await kit.w.brain.load()
    decision = await kit.reply("看这里 https://example.com/a")
    assert decision["voice_state"] is None and kit.net.texts == []
    assert not readable("用 `uv run` 就行") and readable("好耶")


async def test_limits_and_length_keep_it_to_text(kit: Kit) -> None:
    await kit.set_mode("on")
    await kit.reply("[语音] 好耶")
    ref = kit.channel.channel
    assert "刚发过语音" in await kit.keeper.blocked(ref, BOT, time.time())
    assert not await kit.w.brain._speakable(kit.bot, kit.channel, False)
    later = time.time() + 120
    assert await kit.keeper.blocked(ref, BOT, later) == ""
    kit.keeper.config = replace(kit.keeper.config, hourly_per_bot=1)
    assert "到上限" in await kit.keeper.blocked(ref, BOT, later)

    kit.keeper.config = replace(kit.keeper.config, max_chars=10)
    draft = Draft("would_reply", reply="很长" * 20)
    spoken, said = await kit.w.brain._speak(kit.bot, kit.channel, kit.bot, draft)
    assert not said and "太长" in spoken.voice_note


# -- the console's API ----------------------------------------------------------------------------------


async def test_api_keys_are_saved_but_never_shown(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    view = (await http.get("/api/speech")).json()
    assert [s["id"] for s in view["services"]] == ["fish", "minimax", "openai"]
    assert not any(s["ready"] for s in view["services"])

    response = await http.patch("/api/speech", json={"fish_key": "secret-key-1", "hourly_per_bot": 3})
    assert response.status_code == 200, response.text
    assert "secret-key-1" not in response.text
    view = response.json()
    assert view["settings"]["hourly_per_bot"] == 3
    assert view["services"][0]["key_set"] is True and view["services"][0]["ready"] is True
    assert (await http.patch("/api/speech", json={"fish_key": ""})).json()["services"][0]["key_set"] is False

    assert (await http.patch("/api/speech", json={})).status_code == 422
    assert (await http.patch("/api/speech", json={"fish_model": "made-up"})).status_code == 422
    assert (await http.patch("/api/speech", json={"openai_url": "relay.example"})).status_code == 422
    assert (await http.patch("/api/speech", json={"hourly_per_bot": 0})).status_code == 422


async def test_api_keeps_a_voice_library_per_service(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    fish = {"library": {"fish": [{"id": "abc", "name": "小马"}]}}
    assert (await http.patch("/api/speech", json=fish)).json()["settings"]["library"] == fish["library"]
    minimax = {"library": {"minimax": [{"id": "female-shaonv"}]}}
    library = (await http.patch("/api/speech", json=minimax)).json()["settings"]["library"]
    assert library == {
        "fish": [{"id": "abc", "name": "小马"}],
        "minimax": [{"id": "female-shaonv", "name": ""}],
    }
    assert (await http.patch("/api/speech", json={"library": {"fish": []}})).json()["settings"][
        "library"
    ] == {
        "fish": [],
        "minimax": [{"id": "female-shaonv", "name": ""}],
    }
    assert (await http.get("/api/speech")).json()["settings"]["library"]["fish"] == []
    assert (await http.patch("/api/speech", json={"library": {"tts": []}})).status_code == 422
    bad = {"library": {"fish": [{"id": "两 段"}]}}
    response = await http.patch("/api/speech", json=bad)
    assert response.status_code == 422 and "不能有空格" in response.json()["detail"]
    assert (await http.get("/api/speech")).json()["services"][1]["known"][0]["id"] == "female-shaonv"


async def test_api_bot_voice_and_group_switch(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    bot = (await http.get("/api/bots")).json()[0]
    path = f"/api/bots/{bot['id']}"
    assert bot["voice"] == ""
    assert (await http.patch(path, json={"voice": "nope"})).status_code == 422
    assert (await http.patch(path, json={"voice": "fish:abc"})).status_code == 200
    bot = (await http.get("/api/bots")).json()[0]
    assert bot["voice"] == "fish:abc"
    channel = bot["channels"][0]
    assert channel["voice_mode"] == "off"
    url = f"{path}/channels/{channel['channel_id']}"
    assert (await http.patch(url, json={"voice_mode": "trial"})).status_code == 200
    assert (await http.get("/api/bots")).json()[0]["channels"][0]["voice_mode"] == "trial"
    assert (await http.patch(url, json={"voice_mode": "loud"})).status_code == 422
    assert channel["voice_rate"] == 0
    assert (await http.patch(url, json={"voice_rate": 40})).status_code == 200
    assert (await http.get("/api/bots")).json()[0]["channels"][0]["voice_rate"] == 40
    assert (await http.patch(url, json={"voice_rate": 101})).status_code == 422
    assert channel["reply_lines"] == 3 and channel["split_rate"] == 100
    assert (await http.patch(url, json={"reply_lines": 1, "split_rate": 30})).status_code == 200
    saved = (await http.get("/api/bots")).json()[0]["channels"][0]
    assert saved["reply_lines"] == 1 and saved["split_rate"] == 30
    assert (await http.patch(url, json={"reply_lines": 6})).status_code == 422
    assert (await http.patch(url, json={"reply_lines": 0})).status_code == 422
    assert (await http.patch(url, json={"split_rate": 101})).status_code == 422


async def test_api_preview_says_why_it_cannot(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    refused = await http.post("/api/speech/preview", json={"voice": "fish:abc"})
    assert refused.status_code == 502 and "还没填密钥" in refused.json()["detail"]
    assert (await http.post("/api/speech/voices", json={"service": "fish"})).status_code == 502
    assert (await http.post("/api/speech/voices", json={"service": "tts"})).status_code == 422
