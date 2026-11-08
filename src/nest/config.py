"""Process-level settings.

Only what the process needs before the database is open lives here (where to listen, where data goes).
Accounts, bots, personas and models are stored in the database and edited from the console.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_NAMES = {"localhost"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEST_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    home: Path = Field(default_factory=Path.cwd)
    host: str = "127.0.0.1"
    port: int = 18800
    # Also listen for the local network and Tailscale (all IPv4 interfaces); remote devices sign in with
    # the password. Off unless set in .env (NEST_REMOTE=true); takes effect at the next start.
    remote: bool = False
    # More names this machine answers to besides its host name, .local name and Tailscale name, e.g.
    # "nest.home.arpa, 192.168.1.20" (comma-separated).
    allowed_hosts: str = ""
    log_level: str = "INFO"
    message_retention_days: float = 30
    # 项目资料: the local embedding and rerank servers (llama-server --embedding / --reranking). Without them
    # project questions are answered from keyword search.
    knowledge_embed_url: str = "http://127.0.0.1:3062"
    knowledge_embed_model: str = "bge-m3"
    knowledge_rerank_url: str = "http://127.0.0.1:3064"
    knowledge_rerank_model: str = "bge-reranker-v2-m3"

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        # The console and API control real accounts: `host` is how this machine reaches nest. Other
        # devices are let in by NEST_REMOTE (all interfaces) and the checks in nest.api.auth, never by this.
        if value in LOOPBACK_NAMES:
            return value
        try:
            if ipaddress.ip_address(value).is_loopback:
                return value
        except ValueError:
            pass
        raise ValueError("nest 只允许监听本机地址（127.0.0.1 / ::1 / localhost）")

    @property
    def bind_host(self) -> str:
        """The address to listen on: every IPv4 interface with remote access, else `host`."""
        return "0.0.0.0" if self.remote else self.host  # who may connect is checked in nest.api.auth

    @property
    def extra_hosts(self) -> list[str]:
        return [name.strip() for name in self.allowed_hosts.split(",") if name.strip()]

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nest.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def runtime_file(self) -> Path:
        return self.data_dir / "runtime.json"

    @property
    def web_dist(self) -> Path:
        return self.home / "web" / "dist"

    @property
    def base_url(self) -> str:
        """How this machine's own programs (the CLI, the launcher) reach nest."""
        # With remote access nest listens on IPv4 only: an IPv6 `host` (::1) would not answer.
        host = "127.0.0.1" if self.remote else self.host
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{self.port}"
