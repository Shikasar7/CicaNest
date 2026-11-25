"""Application assembly: settings → database → models → brain → adapters (hub) → 监控 → API → console."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware

from nest.adapters.registry import build_adapter
from nest.api.auth import Access, LocalOnly
from nest.api.console import mount_console
from nest.api.errors import validation_handler
from nest.api.routes_accounts import router as accounts_router
from nest.api.routes_appearance import router as appearance_router
from nest.api.routes_approvals import router as approvals_router
from nest.api.routes_avatars import router as avatars_router
from nest.api.routes_bot_admin import router as bot_admin_router
from nest.api.routes_bots import router as bots_router
from nest.api.routes_companion_packs import router as companion_packs_router
from nest.api.routes_companions import router as companions_router
from nest.api.routes_desk import router as desk_router
from nest.api.routes_desk_groups import router as desk_groups_router
from nest.api.routes_executor import router as executor_router
from nest.api.routes_issues import digest_settings
from nest.api.routes_issues import router as issues_router
from nest.api.routes_knowledge import router as knowledge_router
from nest.api.routes_life import router as life_router
from nest.api.routes_live import router as live_router
from nest.api.routes_memories import router as memories_router
from nest.api.routes_models import router as models_router
from nest.api.routes_persona_sets import router as persona_sets_router
from nest.api.routes_personas import router as personas_router
from nest.api.routes_plugins import router as plugins_router
from nest.api.routes_qq_password import router as qq_password_router
from nest.api.routes_reminders import router as reminders_router
from nest.api.routes_remote import public as remote_public_router
from nest.api.routes_remote import router as remote_router
from nest.api.routes_routines import router as routines_router
from nest.api.routes_slang import router as slang_router
from nest.api.routes_speech import router as speech_router
from nest.api.routes_sticker_packs import router as sticker_packs_router
from nest.api.routes_stickers import router as stickers_router
from nest.api.routes_system import router as system_router
from nest.api.routes_system_admin import offline_alert_settings, storage_plan
from nest.api.routes_system_admin import router as system_admin_router
from nest.api.routes_tasks import router as tasks_router
from nest.api.routes_tones import router as tones_router
from nest.api.routes_usage import router as usage_router
from nest.api.routes_watches import router as watches_router
from nest.api.routes_workspaces import router as workspaces_router
from nest.config import Settings
from nest.core.approvals import ApprovalKeeper
from nest.core.birthday import BirthdayKeeper
from nest.core.brain import Brain, Policy
from nest.core.broadcast import Broadcaster
from nest.core.chatter import ChatterKeeper
from nest.core.deaf import DeafWatch
from nest.core.desk import Desk
from nest.core.desk_media import DeskMedia
from nest.core.drawing import Drawing
from nest.core.egress import is_online
from nest.core.executor import ExecutorHub
from nest.core.groups import DeskGroups
from nest.core.hub import AdapterFactory, Hub
from nest.core.initiative import InitiativeKeeper, apply_to
from nest.core.issues import IssueKeeper
from nest.core.keepsake_drafts import KeepsakeDrafter
from nest.core.keepsakes import KeepsakeKeeper
from nest.core.life import LifeKeeper
from nest.core.market import MarketWatch
from nest.core.mcp_plugins import McpHub
from nest.core.memory import MemoryKeeper
from nest.core.offline_alert import OfflineAlert
from nest.core.outreach import OutreachKeeper
from nest.core.plugins import PluginManager
from nest.core.reminders import ReminderKeeper
from nest.core.routines import RoutineKeeper
from nest.core.sandbox import Docker, Sandbox, Spawner
from nest.core.screenshots import ScreenshotReader
from nest.core.slang import SlangLearner
from nest.core.speech import SpeechKeeper
from nest.core.sticker_packs import PackKeeper
from nest.core.stickers import StickerKeeper
from nest.core.tasks import TaskKeeper
from nest.core.tools import CompanionTools
from nest.core.workspace import Workspace
from nest.docker import cli as docker_cli
from nest.housekeeping import Housekeeper
from nest.knowledge.dense import EmbeddingModel
from nest.knowledge.gaps import GapForwarder
from nest.knowledge.rerank import RerankModel
from nest.knowledge.service import KnowledgeService
from nest.llm.runtime import ProviderFactory, ProviderRegistry, build_provider
from nest.logs import LogBuffer, attached
from nest.net import machine_names, tailscale_self
from nest.qr_guard import QRGuard
from nest.remote import RemoteAccess, Throttle
from nest.secrets import SecretStore
from nest.storage.accounts import list_accounts
from nest.storage.avatars import AvatarStore
from nest.storage.birthdays import BirthdayStore
from nest.storage.bots import BotRepo
from nest.storage.db import Database
from nest.storage.knowledge import KnowledgeRepo
from nest.storage.memories import MemoryStore
from nest.storage.messages import MessageStore
from nest.storage.settings import SettingsRepo
from nest.storage.tones import ToneRepo
from nest.watch.watcher import Watcher, WatchPolicy

log = logging.getLogger(__name__)

# .env entries that are nest's own settings (NEST_PORT=18800), not secrets: never scrubbed from logs.
SETTING_NAMES = frozenset(f"NEST_{name}".upper() for name in Settings.model_fields)


async def _face(avatars: AvatarStore, bot_id: str) -> bytes | None:
    """The picture a bot shows, to draw her from."""
    chosen = (await avatars.views()).get(bot_id, {}).get("chosen")
    picture = await avatars.get(bot_id, chosen) if chosen else None
    return picture.data if picture else None


def _secret_values(app: FastAPI) -> list[str]:
    """What the live log must never show: the console token and every secret in .env."""
    values = [app.state.token]
    secrets = getattr(app.state, "secrets", None)
    if secrets is not None:
        values += [value for name, value in secrets.entries().items() if name.upper() not in SETTING_NAMES]
    return values


NAMES_REFRESH_SECONDS = 300.0


async def _history_days(state: Any) -> float:
    """How many days of chat the daily cleanup keeps (the 系统 page): her 回忆草稿 come before that."""
    return float((await storage_plan(state)).history_days)


async def learn_names(app: FastAPI) -> None:
    """This machine's names for the Host check, the Tailscale one included when Tailscale is up."""
    names = machine_names(app.state.settings.extra_hosts)
    tailscale = await asyncio.to_thread(tailscale_self)
    if tailscale and tailscale[0]:
        names |= {tailscale[0], tailscale[0].split(".")[0]}
    if names != app.state.access.names:
        app.state.access.names = names
        log.info("这台电脑在远程访问里认这些名字：%s", "、".join(sorted(names)))


async def _keep_learning_names(app: FastAPI) -> None:
    # Tailscale may come up after nest (after a reboot), or this machine may be renamed.
    while True:
        await asyncio.sleep(NAMES_REFRESH_SECONDS)
        try:
            await learn_names(app)
        except Exception:
            log.exception("更新这台电脑的名字失败")


async def _open_up(app: FastAPI) -> None:
    """Remote access: let the local network and Tailscale in, under this machine's names."""
    settings = app.state.settings
    await learn_names(app)
    app.state.access.remote = True
    app.state.name_learner = asyncio.create_task(_keep_learning_names(app))
    log.info("远程访问已开启：局域网和 Tailscale 里的设备可以用密码登录（端口 %d）", settings.port)
    if not await app.state.remote.password_set():
        log.warning("还没有设置登录密码，其他设备暂时登录不了：在「系统」页设置，或运行 nest set-password")


def create_app(
    settings: Settings,
    *,
    token: str,
    request_shutdown: Callable[[], None] | None = None,
    adapter_factory: AdapterFactory = build_adapter,
    provider_factory: ProviderFactory = build_provider,
    policy: Policy | None = None,
    watch_policy: WatchPolicy | None = None,
    watch_transport: httpx.AsyncBaseTransport | None = None,  # tests: a MockTransport for every outside fetch
    knowledge_transport: httpx.AsyncBaseTransport | None = None,  # tests: the embedding and rerank servers
    sandbox_docker: Docker | None = None,  # tests: a pretend `docker` for the workspace containers
    sandbox_spawner: Spawner | None = None,  # tests: how an MCP server is started instead of `docker exec`
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.broadcaster = Broadcaster()
        # First, so the live log has every line of this run; removed last, so no app leaks a handler.
        app.state.logs = LogBuffer(
            partial(app.state.broadcaster.publish, "log"),
            asyncio.get_running_loop(),
            secrets=partial(_secret_values, app),
        )
        with attached(app.state.logs):
            db = await Database.open(settings.db_path)
            secrets = SecretStore(settings.home / ".env")
            repo = BotRepo(db)
            app.state.db = db
            app.state.repo = repo
            app.state.secrets = secrets
            app.state.store = MessageStore(db)
            app.state.avatars = AvatarStore(db)
            app.state.remote = RemoteAccess(db)
            if settings.remote:
                await _open_up(app)
            # Plugins first: a trusted one's code may add kinds of model and platforms the rest is built with.
            app.state.plugins = PluginManager(db, settings.data_dir / "plugins")
            await app.state.plugins.load()
            await app.state.plugins.load_code()
            app.state.providers = ProviderRegistry.build(
                await repo.providers(), secrets, repo.record_llm_call, factory=app.state.provider_factory
            )
            app.state.brain = Brain(
                repo,
                app.state.store,
                app.state.providers,
                app.state.broadcaster,
                policy=policy,
                our_accounts={a.self_id for a in await list_accounts(db)},
            )
            await ToneRepo(db).seed()  # 语气: the built-in ones, never overwriting an edited one
            await app.state.brain.load()
            app.state.hub = Hub(
                db,
                app.state.store,
                secrets,
                app.state.broadcaster,
                factory=adapter_factory,
                brain=app.state.brain,
                retention_days=settings.message_retention_days,
            )
            app.state.brain.adapters = app.state.hub.adapters  # filled by hub.start(), keyed by account id
            app.state.desk = Desk(app.state.store, app.state.hub.emit)
            app.state.desk_media = DeskMedia(settings.data_dir / "desk-media")
            app.state.brain.desk_media = app.state.desk_media  # her voice in the desk chat
            app.state.hub.desk_media = app.state.desk_media  # cleared with the messages
            app.state.groups = DeskGroups(
                db,
                app.state.repo,
                app.state.store,
                app.state.broadcaster,
                lambda: app.state.brain.bots,
                app.state.brain.load,
            )
            app.state.desk.groups = app.state.groups
            app.state.brain.groups = app.state.groups  # 她拉群 (nest.core.tools)
            app.state.life = LifeKeeper(
                db, app.state.brain, app.state.broadcaster, lambda: app.state.brain.bots
            )
            app.state.brain.life = app.state.life
            await app.state.life.start()
            app.state.brain.desk = app.state.desk
            app.state.keepsakes = KeepsakeKeeper(db)
            app.state.brain.keepsakes = app.state.keepsakes
            app.state.reminders = ReminderKeeper(db, app.state.brain, app.state.desk, app.state.broadcaster)
            app.state.brain.reminders = app.state.reminders
            app.state.chatter = ChatterKeeper(db, app.state.brain, app.state.desk)
            app.state.workspace = Workspace(settings.workspaces)
            app.state.sandbox = (
                Sandbox(
                    app.state.workspace,
                    docker_call=sandbox_docker,
                    egress_log=settings.data_dir / "egress",
                    online=partial(is_online, db),
                    plugin_homes=settings.data_dir / "plugin-homes",
                    spawner=sandbox_spawner,
                )
                if sandbox_docker is not None or docker_cli() is not None
                else None
            )
            app.state.approvals = ApprovalKeeper(db, app.state.broadcaster)
            app.state.executor = ExecutorHub(app.state.broadcaster, approvals=app.state.approvals)
            await app.state.approvals.start()
            app.state.mcp = McpHub(db, app.state.plugins, app.state.sandbox, secrets, app.state.broadcaster)
            await app.state.mcp.load()
            app.state.drawing = Drawing(
                app.state.providers,
                app.state.desk,
                app.state.desk_media,
                app.state.workspace,
                app.state.broadcaster,
                functools.partial(_face, app.state.avatars),
            )
            app.state.brain.tools = CompanionTools(
                app.state.brain,
                proxy=SettingsRepo(db).fetch_proxy,
                transport=watch_transport,
                workspace=app.state.workspace,
                sandbox=app.state.sandbox,
                broadcaster=app.state.broadcaster,
                plugins=app.state.plugins,
                mcp=app.state.mcp,
                approvals=app.state.approvals,
                executor=app.state.executor,
                secrets=secrets,
                drawing=app.state.drawing,
            )
            app.state.tasks = TaskKeeper(db, app.state.brain, app.state.broadcaster)
            app.state.brain.tasks = app.state.tasks
            app.state.routines = RoutineKeeper(db, app.state.brain, app.state.broadcaster)
            app.state.brain.routines = app.state.routines
            app.state.stickers = StickerKeeper(
                db,
                app.state.providers,
                app.state.broadcaster,
                settings.data_dir / "stickers",
                our_accounts=app.state.brain.our_accounts,
                transport=watch_transport,
            )
            await app.state.stickers.start()
            app.state.stickers.adapters = app.state.hub.adapters
            app.state.brain.stickers = app.state.hub.stickers = app.state.stickers
            app.state.sticker_packs = PackKeeper(
                db, app.state.stickers, app.state.avatars, app.state.broadcaster, lambda: app.state.brain.bots
            )
            await app.state.sticker_packs.start()
            app.state.brain.packs = app.state.sticker_packs
            app.state.speech = SpeechKeeper(db, app.state.secrets, transport=watch_transport)
            await app.state.speech.reload()
            app.state.brain.speech = app.state.speech
            app.state.slang = SlangLearner(
                db,
                app.state.store,
                app.state.providers,
                app.state.broadcaster,
                default_provider=app.state.brain.default_provider,
                our_accounts=app.state.brain.our_accounts,
            )
            await app.state.slang.start()
            app.state.brain.slang = app.state.slang
            app.state.memory = MemoryKeeper(
                MemoryStore(db),
                app.state.store,
                app.state.providers,
                app.state.broadcaster,
                SettingsRepo(db),
                bots=app.state.brain,
            )
            await app.state.memory.start()
            app.state.brain.memory = app.state.memory
            app.state.knowledge = KnowledgeService(
                KnowledgeRepo(db),
                embed=EmbeddingModel(settings.knowledge_embed_model, settings.knowledge_embed_url),
                # bge-reranker answers raw scores; RerankModel turns them into 0..1 (knowledge/rerank.py).
                rerank=RerankModel(
                    settings.knowledge_rerank_model, settings.knowledge_rerank_url, logits=True
                ),
                client=httpx.AsyncClient(timeout=20.0, trust_env=False, transport=knowledge_transport)
                if knowledge_transport
                else None,
            )
            await app.state.knowledge.start()
            app.state.brain.knowledge = app.state.knowledge
            app.state.knowledge.forwarder = GapForwarder(app.state.knowledge.gaps, app.state.brain.forward)
            # 截图读字 for 答题: the same vision model as 表情包.
            app.state.brain.screenshots = ScreenshotReader(
                app.state.stickers.vision,
                transport=watch_transport,
                notes_per_hour=lambda: app.state.stickers.config.notes_per_hour,
                adapters=app.state.hub.adapters,
            )
            app.state.brain.screenshots.desk_media = app.state.desk_media
            app.state.started_at = time.time()
            app.state.backing_up = asyncio.Lock()
            await app.state.hub.start()
            app.state.watcher = Watcher(
                db,
                brain=app.state.brain,
                hub=app.state.hub,
                providers=app.state.providers,
                broadcaster=app.state.broadcaster,
                policy=watch_policy,
                transport=watch_transport,
            )
            await app.state.watcher.start()
            app.state.housekeeper = Housekeeper(
                db,
                app.state.hub,
                partial(storage_plan, app.state),
                stickers=app.state.stickers,
                slang=app.state.slang,
                memory=app.state.memory,
            )
            await app.state.housekeeper.start()
            await app.state.qr_guard.start()
            app.state.deaf_watch = DeafWatch(app.state.hub, app.state.store)
            await app.state.deaf_watch.start()
            app.state.offline_alert = OfflineAlert(
                app.state.hub, app.state.brain.send_private, partial(offline_alert_settings, app.state)
            )
            await app.state.offline_alert.start()
            app.state.issues = IssueKeeper(
                db,
                app.state.store,
                app.state.brain,
                app.state.hub,
                app.state.broadcaster,
                partial(digest_settings, app.state),
            )
            await app.state.issues.start()
            app.state.birthdays = BirthdayKeeper(BirthdayStore(db), app.state.brain)
            await app.state.birthdays.start()
            await app.state.reminders.start()
            app.state.outreach = OutreachKeeper(
                app.state.brain,
                app.state.desk,
                app.state.life,
                app.state.groups,
                desk_open=app.state.reminders.desk_open,
            )
            app.state.market = MarketWatch(
                SettingsRepo(db), app.state.plugins, app.state.broadcaster, lambda: app.state.fetch_transport
            )
            await app.state.market.start()
            app.state.initiative = InitiativeKeeper(
                db,
                lambda values: apply_to(values, app.state.chatter, app.state.outreach, app.state.brain),
            )
            app.state.outreach.rhythm = app.state.initiative.rhythm
            await app.state.initiative.start()
            await app.state.outreach.start()
            app.state.keepsake_drafts = KeepsakeDrafter(
                app.state.brain,
                app.state.keepsakes,
                app.state.broadcaster,
                partial(_history_days, app.state),
            )
            await app.state.keepsake_drafts.start()
            await app.state.tasks.start()
            await app.state.routines.start()
            if app.state.sandbox is not None:
                await app.state.sandbox.start()
            log.info(
                "nest 已启动，数据目录 %s，%d 个账号，%d 个 bot",
                settings.data_dir,
                len(app.state.hub.adapters),
                len(app.state.brain.bots),
            )
            try:
                yield
            finally:
                learner = getattr(app.state, "name_learner", None)
                if learner is not None:
                    learner.cancel()
                await app.state.routines.stop()
                await app.state.tasks.stop()
                await app.state.drawing.stop()
                await app.state.mcp.close()
                if app.state.sandbox is not None:
                    await app.state.sandbox.stop()
                await app.state.keepsake_drafts.stop()
                await app.state.outreach.stop()
                await app.state.initiative.rhythm.save()
                await app.state.market.stop()
                await app.state.reminders.stop()
                await app.state.birthdays.stop()
                await app.state.issues.stop()
                await app.state.offline_alert.stop()
                await app.state.deaf_watch.stop()
                await app.state.qr_guard.stop()
                await app.state.housekeeper.stop()
                await app.state.watcher.stop()
                await app.state.hub.stop()
                await app.state.brain.stop()
                await app.state.slang.stop()
                await app.state.memory.stop()
                await app.state.knowledge.stop()
                await app.state.life.stop()
                await app.state.sticker_packs.stop()
                await app.state.stickers.stop()
                await app.state.providers.aclose()
                await db.close()
                log.info("nest 已停止")

    app = FastAPI(title="nest", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.token = token
    app.state.request_shutdown = request_shutdown or (lambda: None)
    app.state.provider_factory = provider_factory
    app.state.fetch_transport = watch_transport  # 监控 gets it too; None: the real network
    # Held by every endpoint that changes configuration, from validation through brain.load(), so edits
    # never interleave. Nothing under it waits on the network.
    app.state.config_lock = asyncio.Lock()
    # Held while starting or stopping SnowLuma or QQ, so two clicks never start two copies.
    app.state.process_lock = asyncio.Lock()
    # Stops a QQ container that waits for a scan nobody is making (nest.qr_guard); runs while nest does.
    app.state.qr_guard = QRGuard(app.state)
    app.state.access = Access(port=settings.port)
    app.state.throttle = Throttle()
    # Compresses the console's script (~500 KB) and larger answers, for phones on Tailscale; the live event
    # stream is left alone. Added first so LocalOnly, added last, runs before it.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(LocalOnly, access=app.state.access)
    app.add_exception_handler(RequestValidationError, validation_handler)  # type: ignore[arg-type]
    app.include_router(system_router)
    app.include_router(remote_public_router)
    app.include_router(remote_router)
    app.include_router(system_admin_router)
    app.include_router(qq_password_router)
    app.include_router(accounts_router)
    app.include_router(live_router)
    app.include_router(bots_router)
    app.include_router(desk_router)
    app.include_router(desk_groups_router)
    app.include_router(life_router)
    app.include_router(companions_router)
    app.include_router(reminders_router)
    app.include_router(tasks_router)
    app.include_router(routines_router)
    app.include_router(plugins_router)
    app.include_router(appearance_router)
    app.include_router(approvals_router)
    app.include_router(executor_router)
    app.include_router(companion_packs_router)
    app.include_router(workspaces_router)
    app.include_router(bot_admin_router)
    app.include_router(issues_router)
    app.include_router(speech_router)
    app.include_router(avatars_router)
    app.include_router(models_router)
    app.include_router(personas_router)
    app.include_router(persona_sets_router)
    app.include_router(tones_router)
    app.include_router(usage_router)
    app.include_router(watches_router)
    app.include_router(stickers_router)
    app.include_router(sticker_packs_router)
    app.include_router(slang_router)
    app.include_router(memories_router)
    app.include_router(knowledge_router)
    mount_console(app, settings.web_dist)  # catch-all route: must stay last
    return app
