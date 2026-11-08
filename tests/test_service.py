"""The macOS launchd agent (nest.service), with launchctl replaced by a recorder."""

from __future__ import annotations

import os
import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from nest import service


class Launchctl:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail or set()

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        code = 1 if args[1] in self.fail else 0
        return subprocess.CompletedProcess(
            list(args), code, "", "Bootstrap failed: 5: Input/output error" if code else ""
        )


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "supported", lambda: True)
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)


def test_the_agent_restarts_only_after_failures(tmp_path: Path) -> None:
    job = service.agent(tmp_path / "nest", "/Users/me/nest/.venv/bin/python")
    assert job["ProgramArguments"] == ["/Users/me/nest/.venv/bin/python", "-m", "nest", "serve", "--quiet"]
    assert job["WorkingDirectory"] == str(tmp_path / "nest")
    assert job["RunAtLoad"] is True
    assert job["KeepAlive"] == {"SuccessfulExit": False}  # `nest stop` exits 0: stays stopped
    assert job["StandardOutPath"] == str(tmp_path / "nest" / "data" / "logs" / "launchd.log")


@pytest.mark.usefixtures("mac")
def test_install(tmp_path: Path) -> None:
    launchctl = Launchctl()
    result = service.install(tmp_path / "nest", python="/py", run=launchctl, home_dir=tmp_path)
    assert result.ok
    path = tmp_path / "Library" / "LaunchAgents" / "com.nest.server.plist"
    assert plistlib.loads(path.read_bytes())["Label"] == "com.nest.server"
    assert launchctl.calls == [
        ["launchctl", "bootout", "gui/501/com.nest.server"],
        ["launchctl", "bootstrap", "gui/501", str(path)],
    ]
    assert (tmp_path / "nest" / "data" / "logs").is_dir()
    assert service.installed(tmp_path)


@pytest.mark.usefixtures("mac")
def test_install_reports_launchctl_failing(tmp_path: Path) -> None:
    result = service.install(
        tmp_path / "nest", python="/py", run=Launchctl(fail={"bootstrap"}), home_dir=tmp_path
    )
    assert not result.ok
    assert "Input/output error" in result.message


@pytest.mark.usefixtures("mac")
def test_uninstall(tmp_path: Path) -> None:
    service.install(tmp_path / "nest", python="/py", run=Launchctl(), home_dir=tmp_path)
    launchctl = Launchctl()
    assert service.uninstall(run=launchctl, home_dir=tmp_path).message.startswith("已卸载")
    assert launchctl.calls == [["launchctl", "bootout", "gui/501/com.nest.server"]]
    assert not service.installed(tmp_path)
    assert service.uninstall(run=Launchctl(), home_dir=tmp_path).message == "没有安装过 nest 服务"


@pytest.mark.usefixtures("mac")
def test_kickstart() -> None:
    launchctl = Launchctl()
    assert service.kickstart(run=launchctl)
    assert launchctl.calls == [["launchctl", "kickstart", "gui/501/com.nest.server"]]


def test_elsewhere_it_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "supported", lambda: False)
    launchctl = Launchctl()
    assert not service.install(tmp_path, run=launchctl, home_dir=tmp_path).ok
    assert not service.uninstall(run=launchctl, home_dir=tmp_path).ok
    assert not service.installed(tmp_path)
    assert launchctl.calls == []


def test_the_command_line_reaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `nest service ...` once failed before doing anything: its subparser shadowed the function's name.
    from nest import cli
    from nest.config import Settings

    monkeypatch.setattr(cli, "Settings", lambda: Settings(home=tmp_path, _env_file=None))
    monkeypatch.setattr(service, "installed", lambda *a, **k: False)
    assert cli.main(["service", "status"]) == 0
    assert "没有安装 nest 后台服务" in capsys.readouterr().out
