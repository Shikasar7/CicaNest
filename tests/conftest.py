from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from nest import app as nest_app
from nest import docker, system
from nest.app import create_app
from nest.config import Settings
from nest.watch import fetch as watch_fetch

TOKEN = "test-token"


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The real SnowLuma and QQ run on this machine: no test may find, start or stop them, or knock on
    SnowLuma's port. Tests that need processes install fakes over these (tests/test_system_api.py)."""
    absent = tmp_path_factory.mktemp("absent")
    monkeypatch.setattr(system, "DEFAULT_SNOWLUMA_DIR", str(absent / "SnowLuma"))
    monkeypatch.setattr(system, "DEFAULT_QQ_EXECUTABLE", str(absent / "QQ.exe"))
    monkeypatch.setattr(system, "snowluma_pids", lambda folder: [])
    monkeypatch.setattr(system, "webui_listening", lambda timeout=0.3: False)
    monkeypatch.setattr(system, "qq_clients", lambda: 0)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("测试不能启动或停止真实的进程")

    for name in ("start_snowluma", "stop_snowluma", "open_qq"):
        monkeypatch.setattr(system, name, refuse)
    # Nor Docker (SnowLuma's container).
    monkeypatch.setattr(
        docker, "container", lambda name: docker.Container(False, problem="测试里没有 Docker")
    )
    monkeypatch.setattr(docker, "act", refuse)
    monkeypatch.setattr(docker, "login_qr", refuse)
    monkeypatch.setattr(docker, "login_log", refuse)
    # Remote access asks this machine's Tailscale for its name: not in tests (tests/test_remote_access.py).
    monkeypatch.setattr(nest_app, "tailscale_self", lambda: None)


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """监控 fetch from the internet: no test may. Tests hand the app an httpx.MockTransport instead; a fetch
    that would still reach the network is refused here and fails the test."""
    attempts: list[str] = []

    def refuse(proxy: str | None) -> httpx.AsyncBaseTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            raise AssertionError("测试不能访问网络")

        return httpx.MockTransport(handler)

    monkeypatch.setattr(watch_fetch, "network_transport", refuse)
    yield
    assert not attempts, f"有测试想访问网络：{attempts}"


def models_down() -> httpx.MockTransport:
    """项目资料's embedding and rerank servers, not running: no test reaches the real ones on this machine."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path, port=18800, _env_file=None)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, token=TOKEN, knowledge_transport=models_down())
    app.state.shutdown_requests = 0

    def request_shutdown() -> None:
        app.state.shutdown_requests += 1

    app.state.request_shutdown = request_shutdown
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url) as http:
            http.app = app  # type: ignore[attr-defined]
            yield http
