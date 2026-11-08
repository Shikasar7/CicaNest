"""企业微信 through the console's API: a bot that is online is never probed, and a group gets its name from
the user, since the platform tells none of them."""

from __future__ import annotations

from typing import Any

import pytest

from nest.core.events import ChannelRef
from tests.test_platform_api import SECRET, World, joined, world  # noqa: F401


async def test_a_wecom_bot_that_exists_is_never_probed_again(world: World) -> None:  # noqa: F811
    assert (await world.add_wecom()).status_code == 201
    assert len(world.probes) == 1
    assert (await world.add_wecom()).status_code == 409
    fields = {"bot_id": "aibtest", "secret": SECRET}
    test = await world.http.post("/api/accounts/test", json={"platform": "wecom", "fields": fields})
    assert test.status_code == 409 and "顶下线" in test.json()["detail"]
    saved = await world.http.post("/api/accounts/wecom:aibtest/connection/test", json={"fields": {}})
    assert saved.status_code == 200 and not saved.json()["ok"] and "顶下线" in saved.json()["detail"]
    assert len(world.probes) == 1  # a probe would have kicked the bot that is online


async def test_saving_a_kicked_wecom_bot_with_nothing_changed_connects_it_again(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    before = len(world.built)
    response = await world.http.put("/api/accounts/wecom:aibtest/connection", json={})
    assert response.status_code == 200 and len(world.built) == before + 1


@pytest.mark.parametrize(
    ("patch", "says"),
    [
        ({"activity": "occasional"}, "只回点名"),
        ({"activity": "active"}, "只回点名"),
        ({"recorder": True}, "问题记录官"),
        ({"use_slang": True}, "黑话"),
        ({"voice_mode": "on"}, "语音"),
        ({"voice_mode": "trial"}, "语音"),
        ({"sticker_mode": "trial"}, "表情"),
    ],
)
async def test_a_wecom_channel_refuses_what_the_platform_cannot_do(
    world: World,  # noqa: F811
    patch: dict[str, Any],
    says: str,
) -> None:
    await world.add_wecom()
    path, _ = await joined(world, "wecom:aibtest")
    response = await world.http.patch(path, json=patch)
    assert response.status_code == 400 and says in response.json()["detail"]


async def test_a_wecom_channel_takes_what_it_can_do(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    path, _ = await joined(world, "wecom:aibtest")
    good = {"activity": "mentions", "voice_mode": "off", "sticker_mode": "off", "answer_questions": True}
    assert (await world.http.patch(path, json=good)).status_code == 200


async def test_adding_a_group_above_mentions_is_refused_on_wecom(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    channel = await world.state.store.channel_id(ChannelRef("wecom", "aibtest", "group", "wr2"))
    body = {"channel_id": channel, "activity": "active"}
    assert (await world.http.post("/api/bots/wecom:aibtest/channels", json=body)).status_code == 400


async def test_a_wecom_group_gets_its_name_from_the_user(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    _, channel = await joined(world, "wecom:aibtest")
    response = await world.http.patch(f"/api/channels/{channel}", json={"name": " 研发群 "})
    assert response.status_code == 200 and response.json()["name"] == "研发群"
    [bot] = (await world.http.get("/api/bots")).json()
    assert bot["channels"][0]["name"] == "研发群"
    groups = (await world.http.get("/api/groups")).json()
    assert groups[0]["name"] == "研发群"


@pytest.mark.parametrize("name", ["", "   ", "字" * 41, "两\n行"])
async def test_a_group_name_has_limits(world: World, name: str) -> None:  # noqa: F811
    await world.add_wecom()
    _, channel = await joined(world, "wecom:aibtest")
    assert (await world.http.patch(f"/api/channels/{channel}", json={"name": name})).status_code == 422


async def test_connections_show_it_without_its_secret(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    [view] = (await world.http.get("/api/accounts/connections")).json()
    assert view["fields"] == {} and view["secrets"] == {"secret": True} and view["self_id"] == "aibtest"
    assert SECRET not in str(view)


async def test_its_features_are_refused_with_a_reason(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    await world.state.store.channel_id(ChannelRef("wecom", "aibtest", "group", "wr"))
    path = "/api/groups/wecom:group:wr/features"
    assert (await world.http.patch(path, json={"learn_slang": True})).status_code == 400
    assert (await world.http.patch(path, json={"collect_stickers": True})).status_code == 400


async def test_people_are_named_by_userid(world: World) -> None:  # noqa: F811
    await world.add_wecom()
    private = "/api/bots/wecom:aibtest/private"
    assert (await world.http.post(private, json={"user_id": "zhangsan"})).status_code == 201
    body = {"to": "wecom:zhangsan", "times": ["21:00"]}
    assert (await world.http.put("/api/issues/digest", json=body)).status_code == 200


async def test_the_form_refuses_a_bot_id_that_would_break_the_account_id(world: World) -> None:  # noqa: F811
    for fields, says in (({"secret": "s"}, "BotID要填"), ({"bot_id": "a:b", "secret": "s"}, "BotID")):
        response = await world.http.post("/api/accounts/test", json={"platform": "wecom", "fields": fields})
        assert response.status_code == 422 and says in response.json()["detail"]
