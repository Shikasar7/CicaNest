"""平台和模型插件: a trusted plugin's Python is installed only when the user says they
trust it, loaded when nest starts, and adds a kind of model or a chat platform; broken code adds nothing."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest

from nest import plugin_code
from nest.adapters.registry import PLATFORMS
from nest.llm.base import ChatMessage
from nest.llm.runtime import build_provider
from nest.storage.bots import ProviderConfig
from tests.test_bot_settings_api import World, world  # noqa: F401

MODULE = """
import re
from nest.adapters.base import FormField, Probed
from nest.adapters.registry import PlatformSpec
from nest.llm.base import Completion


class Echo:
    def __init__(self, config):
        self.name = config.id

    async def complete(self, messages, *, max_tokens, temperature=None):
        return Completion(text=messages[-1].content[::-1], latency_ms=1)

    async def aclose(self):
        pass


async def probe(fields):
    return Probed(ok=True, self_id="1", name="demo")


def register(nest):
    build = lambda config, secrets: Echo(config)
    nest.add_model_kind("echo", label="回声", build=build, options=("timeout",))
    nest.add_platform(
        PlatformSpec(
            key="demochat",
            label="演示聊天",
            capabilities=frozenset({"reply"}),
            fields=(FormField("token", "令牌", secret=True, required=True),),
            user_id=re.compile(r"[a-z]{3,10}"),
            user_label="演示账号",
            user_error="演示账号是小写字母",
            place=("演示群", "演示私聊"),
            build=lambda account, secrets, on_status, backfill: None,
            probe=probe,
        )
    )
"""


def package(module: str = MODULE, *, plugin_id: str = "demo.echo") -> bytes:
    manifest = {
        "id": plugin_id,
        "name": "回声模型",
        "version": "1.0.0",
        "provides": {"python": {"module": "echo_models"}},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("echo_models.py", module)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def forgotten() -> Iterator[None]:
    """What a test's plugins registered never reaches another test."""
    yield
    for plugin in ("demo.echo", "demo.broken"):
        plugin_code.forget(plugin)


async def _install(world: World, data: bytes, **body: Any) -> Any:  # noqa: F811
    staged = (await world.http.put("/api/plugin-packages", content=data)).json()
    assert staged["plugin"]["trust"] == "trusted"
    return await world.http.post(f"/api/plugin-packages/{staged['stage']}/install", json=body or None)


async def test_a_trusted_plugin_adds_a_kind_of_model_and_a_platform(world: World) -> None:  # noqa: F811
    refused = await _install(world, package())
    assert refused.status_code == 409 and "信任" in refused.json()["detail"]
    installed = await _install(world, package(), trust=True)
    assert installed.status_code == 201
    # Not loaded until nest starts again.
    shown = next(
        p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "demo.echo"
    )
    assert shown["code"] is None and (await world.http.get("/api/model-kinds")).json() == []

    await world.state.plugins.load_code()  # what the next start does
    shown = next(
        p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "demo.echo"
    )
    assert shown["code"] == {"added": ["模型种类 回声（echo）", "平台 演示聊天（demochat）"]}
    assert (await world.http.get("/api/model-kinds")).json() == [
        {"kind": "echo", "label": "回声", "options": ["timeout"], "plugin": "demo.echo"}
    ]
    made = await world.http.post(
        "/api/providers",
        json={
            "id": "echo1",
            "kind": "echo",
            "base_url": "https://echo.example",
            "model": "e",
            "options": {"timeout": 30},
        },
    )
    assert made.status_code == 201, made.text
    wrong = await world.http.post(
        "/api/providers",
        json={
            "id": "echo2",
            "kind": "echo",
            "base_url": "https://echo.example",
            "model": "e",
            "options": {"temperature": 1},
        },
    )
    assert wrong.status_code == 422
    config = ProviderConfig(
        id="echo1", kind="echo", base_url="https://echo.example", model="e", key_ref="", options={}
    )
    provider = build_provider(config, world.state.secrets)
    assert (await provider.complete([ChatMessage("user", "你好呀")], max_tokens=10)).text == "呀好你"
    platforms = {p["key"]: p for p in (await world.http.get("/api/platforms")).json()}
    assert platforms["demochat"]["label"] == "演示聊天" and platforms["demochat"]["user_label"] == "演示账号"

    plugin_code.forget("demo.echo")
    assert "echo" not in plugin_code.MODEL_KINDS and "demochat" not in PLATFORMS


async def test_broken_code_adds_nothing(world: World) -> None:  # noqa: F811
    half = MODULE.replace("    nest.add_platform(", "    raise RuntimeError('oops')\n    nest.add_platform(")
    await _install(world, package(half, plugin_id="demo.broken"), trust=True)
    await world.state.plugins.load_code()
    assert world.state.plugins.code["demo.broken"]["problem"].startswith("插件的代码出错了：RuntimeError")
    assert "echo" not in plugin_code.MODEL_KINDS  # what it registered before failing is taken back
    clash = MODULE.replace('"echo"', '"anthropic"')
    with pytest.raises(plugin_code.PluginCodeError, match="不能用"):
        api = plugin_code.PluginApi("demo.echo")
        namespace: dict[str, Any] = {}
        exec(compile(clash, "clash", "exec"), namespace)
        namespace["register"](api)


async def test_switched_off_it_is_not_loaded(world: World) -> None:  # noqa: F811
    await _install(world, package(), trust=True)
    await world.http.put("/api/plugins/demo.echo", json={"enabled": False})
    await world.state.plugins.load_code()
    assert "demo.echo" not in world.state.plugins.code and "echo" not in plugin_code.MODEL_KINDS


async def test_code_without_the_users_trust_on_record_is_not_loaded(world: World) -> None:  # noqa: F811
    """Put in some way other than the install that asks (copied in by hand, an older nest): not loaded."""
    await _install(world, package(), trust=True)
    plugins = world.state.plugins
    assert plugins.state["demo.echo"]["trusted_at"]
    plugins.state["demo.echo"].pop("trusted_at")
    await plugins.load_code()
    assert "没有你信任它的记录" in plugins.code["demo.echo"]["problem"]
    assert "echo" not in plugin_code.MODEL_KINDS
