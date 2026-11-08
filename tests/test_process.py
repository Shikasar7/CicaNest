"""One nest per data directory, a shutdown that cannot hang, and a `nest stop` that waits for the exit."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from nest import cli
from nest.app import create_app
from nest.config import Settings
from nest.core.hub import Hub
from nest.runtime import InstanceLock, RuntimeInfo, pid_alive
from tests.conftest import TOKEN
from tests.helpers import eventually, free_socket

AUTH = {"authorization": f"Bearer {TOKEN}"}


def unused_port() -> int:
    with free_socket() as sock:
        return sock.getsockname()[1]


@pytest.fixture
def quiet_settings(tmp_path: Path) -> Settings:
    # A port nothing listens on: these tests must never reach the real nest on 18800.
    return Settings(home=tmp_path, port=unused_port(), _env_file=None)


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path / "nest.lock"), InstanceLock(tmp_path / "nest.lock")
    assert first.acquire()
    assert not second.acquire()
    assert second.held_elsewhere()
    first.release()
    assert not second.held_elsewhere()
    assert second.acquire()
    second.release()


def test_pid_alive() -> None:
    import os

    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    done = subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert done.returncode == 0
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert not pid_alive(finished.pid)


async def test_server_exits_with_console_stream_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The bug: uvicorn waited forever for the console's open event stream, so the app's shutdown never ran
    # and a "stopped" nest kept its QQ connections (and would have kept replying).
    monkeypatch.setattr(cli, "GRACEFUL_SECONDS", 1)
    stopped: list[bool] = []
    original_stop = Hub.stop

    async def recording_stop(self: Hub) -> None:
        stopped.append(True)
        await original_stop(self)

    monkeypatch.setattr(Hub, "stop", recording_stop)
    sock = free_socket()
    settings = Settings(home=tmp_path, port=sock.getsockname()[1], _env_file=None)
    server = cli.make_server(create_app(settings, token=TOKEN))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await eventually(lambda: server.started)
        async with (
            httpx.AsyncClient(trust_env=False, timeout=10) as http,
            http.stream("GET", settings.base_url + "/api/events", headers=AUTH) as stream,
        ):
            chunks = stream.aiter_text()  # keep a reference: a collected iterator would close the connection
            assert "hello" in await anext(chunks)
            server.should_exit = True
            await asyncio.wait_for(asyncio.shield(serving), 6)
    finally:
        server.should_exit = True
        if not serving.done():
            serving.cancel()
        sock.close()
    assert stopped == [True]


async def test_shutdown_request_ends_console_streams_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `nest stop` goes through /api/shutdown: streams end cleanly there, without waiting out the cap.
    monkeypatch.setattr(cli, "GRACEFUL_SECONDS", None)
    sock = free_socket()
    settings = Settings(home=tmp_path, port=sock.getsockname()[1], _env_file=None)
    server: list = []
    app = create_app(settings, token=TOKEN, request_shutdown=lambda: setattr(server[0], "should_exit", True))
    server.append(cli.make_server(app))
    serving = asyncio.create_task(server[0].serve(sockets=[sock]))
    try:
        await eventually(lambda: server[0].started)
        async with (
            httpx.AsyncClient(trust_env=False, timeout=10) as http,
            http.stream("GET", settings.base_url + "/api/events", headers=AUTH) as stream,
        ):
            chunks = stream.aiter_text()
            assert "hello" in await anext(chunks)
            assert (await http.post(settings.base_url + "/api/shutdown", headers=AUTH)).status_code == 200
            assert "event: closed" in "".join([chunk async for chunk in chunks])
            await asyncio.wait_for(asyncio.shield(serving), 3)
    finally:
        server[0].should_exit = True
        if not serving.done():
            serving.cancel()
        sock.close()


def test_start_refuses_while_an_old_process_holds_the_lock(quiet_settings: Settings) -> None:
    holder = cli.lock_for(quiet_settings)
    assert holder.acquire()
    try:
        assert cli.start(quiet_settings, open_browser=False) == 1
    finally:
        holder.release()


def test_serve_refuses_while_locked(quiet_settings: Settings) -> None:
    holder = cli.lock_for(quiet_settings)
    assert holder.acquire()
    try:
        assert cli.serve(quiet_settings, console=False) == 1
    finally:
        holder.release()


def test_stop_ends_a_process_that_stopped_answering(quiet_settings: Settings) -> None:
    # Stand-in for a nest that closed its port but never exited: holds the lock, answers nothing.
    lock_path = quiet_settings.data_dir / "nest.lock"
    # It reports its own pid, as runtime.json does: the venv's python.exe is a launcher with a child.
    script = (
        "import os, time\nfrom pathlib import Path\nfrom nest.runtime import InstanceLock\n"
        f"lock = InstanceLock(Path({str(lock_path)!r}))\nassert lock.acquire()\n"
        "print(os.getpid(), flush=True)\ntime.sleep(60)\n"
    )
    lingering = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert lingering.stdout is not None
        pid = int(lingering.stdout.readline())
        RuntimeInfo(pid, quiet_settings.host, quiet_settings.port, "t", time.time(), "test").write(
            quiet_settings.runtime_file
        )
        assert cli.stop(quiet_settings) == 0
        assert not pid_alive(pid)
        lingering.wait(5)
        assert not quiet_settings.runtime_file.exists()
        assert not cli.lock_for(quiet_settings).held_elsewhere()
    finally:
        if lingering.poll() is None:
            lingering.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="signals are a POSIX thing")
def test_stop_ends_a_process_that_ignores_sigterm(quiet_settings: Settings) -> None:
    # A hung nest whose shutdown handler never finishes acts as if SIGTERM never came.
    lock_path = quiet_settings.data_dir / "nest.lock"
    script = (
        "import os, signal, time\nfrom pathlib import Path\nfrom nest.runtime import InstanceLock\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"lock = InstanceLock(Path({str(lock_path)!r}))\nassert lock.acquire()\n"
        "print(os.getpid(), flush=True)\ntime.sleep(60)\n"
    )
    lingering = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert lingering.stdout is not None
        pid = int(lingering.stdout.readline())
        RuntimeInfo(pid, quiet_settings.host, quiet_settings.port, "t", time.time(), "test").write(
            quiet_settings.runtime_file
        )
        assert cli.stop(quiet_settings) == 0
        assert not pid_alive(pid)
    finally:
        if lingering.poll() is None:
            lingering.kill()


def test_stop_when_nothing_runs(quiet_settings: Settings) -> None:
    assert cli.stop(quiet_settings) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="SO_REUSEADDR means sharing the port on Windows")
def test_a_restart_can_listen_while_old_connections_close(tmp_path: Path) -> None:
    """A connection the server closed lingers in TIME_WAIT; the next nest must still be able to listen."""
    settings = Settings(home=tmp_path, port=unused_port(), _env_file=None)
    first = cli._bind(settings)
    first.listen()
    client = socket.create_connection(("127.0.0.1", settings.port))
    accepted, _ = first.accept()
    accepted.close()  # the server side closes first: its end goes to TIME_WAIT
    client.close()
    first.close()
    second = cli._bind(settings)
    assert second.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    second.close()
