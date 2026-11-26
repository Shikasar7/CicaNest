"""平台和模型插件: plugins whose Python runs inside nest — new kinds of model, new chat
platforms. They can touch everything nest can (accounts, keys, chats), so one is only installed when the
user says they trust it, and its code is loaded only when nest starts (switching one on or off, installing
or removing it takes a restart).

A plugin names its module in plugin.json: "provides": {"python": {"module": "weather_models"}} — a file
<module>.py or a folder <module>/ with __init__.py in the plugin's folder. The module has

    def register(nest):            # nest: PluginApi
        nest.add_model_kind("echo", label="回声", build=lambda config, secrets: EchoModel(config))
        nest.add_platform(spec)    # a nest.adapters.registry.PlatformSpec

A model kind's provider is built like nest's own (nest.llm.runtime.build_provider asks here for kinds it
does not know) and answers complete(messages, *, max_tokens, temperature=None) -> Completion. Its form in
the console offers the options named (of proxy, timeout, temperature, extra_body). A platform is offered on
the 系统 page like QQ or Discord. Neither may take a name nest already uses.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KIND = re.compile(r"[a-z][a-z0-9_]{1,30}")
BUILTIN_KINDS = ("openai_chat", "openai_responses", "anthropic", "gemini", "codex")
BUILTIN_PLATFORMS = ("qq", "discord", "telegram", "slack", "wecom", "desk")
GENERIC_OPTIONS = ("proxy", "timeout", "temperature", "extra_body")


@dataclass(frozen=True)
class ModelKind:
    kind: str
    label: str
    build: Callable[[Any, Any], Any]  # (ProviderConfig, SecretReader) -> Provider
    options: tuple[str, ...]
    tools: bool  # its provider can call tools (complete with tools / run, and converse)
    plugin: str


# kind -> what a plugin registered; read by nest.llm.runtime and nest.api.routes_models
MODEL_KINDS: dict[str, ModelKind] = {}
# platform key -> the plugin that added it (the spec itself is in nest.adapters.registry.PLATFORMS)
PLATFORM_OWNERS: dict[str, str] = {}


class PluginCodeError(Exception):
    """Why a plugin's code could not be used, in one Chinese sentence."""


@dataclass
class PluginApi:
    """What a plugin's register(nest) is handed."""

    plugin: str
    added: list[str] = field(default_factory=list)  # what it registered, for the console

    def add_model_kind(
        self,
        kind: str,
        *,
        label: str,
        build: Callable[[Any, Any], Any],
        options: tuple[str, ...] = ("proxy", "timeout", "extra_body"),
        tools: bool = False,
    ) -> None:
        if not KIND.fullmatch(kind) or kind in BUILTIN_KINDS or kind in MODEL_KINDS:
            raise PluginCodeError(f"模型种类名 {kind!r} 不能用（要小写字母开头，不能和已有的重名）")
        unknown = [o for o in options if o not in GENERIC_OPTIONS]
        if unknown:
            raise PluginCodeError(f"模型种类只能用这些设置：{'、'.join(GENERIC_OPTIONS)}")
        MODEL_KINDS[kind] = ModelKind(
            kind, str(label)[:40] or kind, build, tuple(options), bool(tools), self.plugin
        )
        self.added.append(f"模型种类 {label}（{kind}）")

    def add_platform(self, spec: Any) -> None:
        from nest.adapters.registry import PLATFORMS, PlatformSpec

        if not isinstance(spec, PlatformSpec):
            raise PluginCodeError("add_platform 要给一个 nest.adapters.registry.PlatformSpec")
        if not KIND.fullmatch(spec.key) or spec.key in BUILTIN_PLATFORMS or spec.key in PLATFORMS:
            raise PluginCodeError(f"平台名 {spec.key!r} 不能用（不能和已有的重名）")
        PLATFORMS[spec.key] = spec
        PLATFORM_OWNERS[spec.key] = self.plugin
        self.added.append(f"平台 {spec.label}（{spec.key}）")


def forget(plugin: str) -> None:
    """Take back what a plugin registered (its code stays imported until nest restarts)."""
    from nest.adapters.registry import PLATFORMS

    for kind in [k for k, spec in MODEL_KINDS.items() if spec.plugin == plugin]:
        MODEL_KINDS.pop(kind, None)
    for key in [k for k, owner in PLATFORM_OWNERS.items() if owner == plugin]:
        PLATFORM_OWNERS.pop(key, None)
        PLATFORMS.pop(key, None)


def load(plugin: str, folder: Path, module: str) -> list[str]:
    """Import a plugin's module and call its register(nest). What it added; PluginCodeError on failure
    (whatever it registered before failing is taken back)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module):
        raise PluginCodeError(f"模块名 {module!r} 不对")
    package = folder / module / "__init__.py"
    single = folder / f"{module}.py"
    path = package if package.is_file() else single
    if not path.is_file():
        raise PluginCodeError(f"插件里找不到模块 {module}")
    name = "nest_plugin_" + re.sub(r"[^a-z0-9]", "_", plugin.lower())
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if path == package else None
    )
    if spec is None or spec.loader is None:
        raise PluginCodeError(f"模块 {module} 读不了")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    api = PluginApi(plugin)
    try:
        spec.loader.exec_module(loaded)
        register = getattr(loaded, "register", None)
        if not callable(register):
            raise PluginCodeError("模块里没有 register(nest)")
        register(api)
    except PluginCodeError:
        forget(plugin)
        sys.modules.pop(name, None)
        raise
    except Exception as exc:
        forget(plugin)
        sys.modules.pop(name, None)
        raise PluginCodeError(f"插件的代码出错了：{type(exc).__name__}: {str(exc)[:200]}") from exc
    log.info("插件 %s 的代码载入了：%s", plugin, "、".join(api.added) or "什么也没加")
    return api.added
