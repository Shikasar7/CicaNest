"""The .env secret store: literal values, where a value comes from, writes, removal, overlays."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nest.secrets import SecretStore

KEY = "sk-test-LEAKCHECK-123"


def test_values_are_literal_never_interpolated(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "QQ_TOKEN='qq-secret'\nNEST_KEY_X='a-${QQ_TOKEN}-b'\nPLAIN=${QQ_TOKEN}\n", encoding="utf-8"
    )
    secrets = SecretStore(env)
    assert secrets.get("NEST_KEY_X") == "a-${QQ_TOKEN}-b"
    assert secrets.get("PLAIN") == "${QQ_TOKEN}"
    # Written values survive a round trip, including quotes, backslashes and ${...}.
    tricky = "k'\"\\\\x${QQ_TOKEN}$HOME#1"
    secrets.set("NEST_KEY_Y", tricky)
    assert SecretStore(env).get("NEST_KEY_Y") == tricky


def test_source_and_environment_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("NEST_KEY_A", "from-file")
    assert secrets.source("NEST_KEY_A") == "file" and secrets.source("NEST_KEY_NONE") == ""
    assert secrets.source("") == "" and secrets.get("") == ""
    monkeypatch.setenv("NEST_KEY_A", "from-env")
    assert secrets.source("NEST_KEY_A") == "env" and secrets.get("NEST_KEY_A") == "from-env"
    monkeypatch.setenv("NEST_KEY_A", "")  # an empty variable does not override the file
    assert secrets.source("NEST_KEY_A") == "file" and secrets.get("NEST_KEY_A") == "from-file"
    assert secrets.file_value("NEST_KEY_A") == "from-file" and secrets.file_value("NEST_KEY_NONE") is None


def test_unset_removes_only_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env = tmp_path / ".env"
    secrets = SecretStore(env)
    with caplog.at_level(logging.DEBUG):
        secrets.unset("NEST_KEY_MISSING")  # no file at all: a no-op, without python-dotenv's warning
        secrets.set("KEEP", "1")
        secrets.unset("NEST_KEY_MISSING")
    assert caplog.text == ""
    secrets.set("NEST_KEY_GONE", "v")
    monkeypatch.setenv("NEST_KEY_GONE", "env-value")
    secrets.unset("NEST_KEY_GONE")
    assert "NEST_KEY_GONE" not in env.read_text(encoding="utf-8")
    assert secrets.file_value("NEST_KEY_GONE") is None and secrets.get("KEEP") == "1"
    assert secrets.get("NEST_KEY_GONE") == "env-value"  # the process environment is never touched


@pytest.mark.parametrize("ref", ["", "1ABC", "A-B", "A B", "A=B", "键"])
def test_refs_must_be_plain_names(tmp_path: Path, ref: str) -> None:
    secrets = SecretStore(tmp_path / ".env")
    with pytest.raises(ValueError):
        secrets.set(ref, "v")
    with pytest.raises(ValueError):
        secrets.unset(ref)
    assert not (tmp_path / ".env").exists()


def test_overlay_replaces_one_value_without_writing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    secrets = SecretStore(env)
    secrets.set("NEST_KEY_A", "old")
    secrets.set("OTHER", "other")
    before = env.read_bytes()
    view = secrets.overlay("NEST_KEY_A", KEY)
    assert view.get("NEST_KEY_A") == KEY and view.get("OTHER") == "other" and view.get("") == ""
    assert secrets.overlay("NEST_KEY_A", "").get("NEST_KEY_A") == ""  # "" means cleared
    assert KEY not in repr(view)
    assert env.read_bytes() == before and secrets.get("NEST_KEY_A") == "old"
