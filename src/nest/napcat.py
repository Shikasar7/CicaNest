"""A NapCat container for a new bot account (增加 bot on the console's Bot page, in Docker): where
its QQ data goes, which ports it publishes, its OneBot config, and the `docker run` that starts it the way
deploy/mac/docker-compose.yml starts the first three.

What keeps a QQ logged in across restarts is fixed per account (deploy/mac/README.md): the host name, the MAC
address (both derived from the QQ number, so adding the same account again gets the same ones and its saved
login still works) and ACCOUNT, which makes NapCat log in with the saved login instead of asking for a scan.
Ports are published on 127.0.0.1 only. Every function here only plans or writes files; nest.docker runs it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path

from nest import docker

ANSI = re.compile(r"\x1b\[[0-9;]*m")
BOOT = "NapCat Shell App Loading"
QUICK_FAILED = re.compile(r"快速登录错误[：:]\s*(\S[^\n]*)")
PASSWORD_FAILED = re.compile(r"密码登录(?:失败|错误)[：:]?\s*([^\n]*)")
SCAN_FAILED = re.compile(r"\[Login\] Login Error\s*,\s*Err(?:Type|Info):\s*([^\n]*)")
SCAN_EXPIRED = re.compile(r"1\s+ErrCode:\s*3\b")  # the code ran out: NapCat shows a new one by itself
ASKS_FOR_SCAN = "请扫描下面的二维码"

IMAGE = "mlikiowa/napcat-docker:latest"
FIRST_ONEBOT = 3046  # 3040–3045 are the first three accounts' (HTTP, WebSocket pairs)
LAST_ONEBOT = 3199
FIRST_WEBUI = 6100  # 6097–6099 are theirs
LAST_WEBUI = 6199
INSIDE_HTTP, INSIDE_WS, INSIDE_WEBUI = 3040, 3041, 6099


@dataclass(frozen=True, slots=True)
class Plan:
    name: str  # container name, also its host name
    self_id: str
    folder: Path  # qq/napcat/<QQ number>: qq, config, plugins
    http_port: int
    ws_port: int
    webui_port: int
    mac: str

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.ws_port}"


def container_name(self_id: str) -> str:
    return f"napcat-{self_id}"


def mac_for(self_id: str) -> str:
    """A locally administered, unicast MAC address that is always the same for one QQ number."""
    digest = hashlib.sha256(f"nest-napcat-{self_id}".encode()).digest()
    return "02:42:" + ":".join(f"{b:02x}" for b in digest[:4])


def _free_on_host(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def plan(self_id: str, qq_root: Path, taken: set[int]) -> Plan:
    """Where the account's container goes. `taken`: host ports other containers publish (running or not).
    ValueError when no ports are free."""
    busy = set(taken)

    def free(port: int) -> bool:
        return port not in busy and _free_on_host(port)

    http = next((p for p in range(FIRST_ONEBOT, LAST_ONEBOT, 2) if free(p) and free(p + 1)), None)
    webui = next((p for p in range(FIRST_WEBUI, LAST_WEBUI) if free(p)), None)
    if http is None or webui is None:
        raise ValueError("没有空闲的端口了")
    return Plan(
        name=container_name(self_id),
        self_id=self_id,
        folder=qq_root / "napcat" / self_id,
        http_port=http,
        ws_port=http + 1,
        webui_port=webui,
        mac=mac_for(self_id),
    )


def existing_plan(name: str, box: docker.Container, qq_root: Path) -> Plan | None:
    """The plan of a container nest made before (to make it again, e.g. with a 回退密码): its published
    ports and its account's folder. None when it does not publish NapCat's three ports."""
    published = dict(box.ports)
    try:
        http, ws, webui = (published[f"{inside}/tcp"] for inside in (INSIDE_HTTP, INSIDE_WS, INSIDE_WEBUI))
    except KeyError:
        return None
    return Plan(
        name=name,
        self_id=box.account,
        folder=qq_root / "napcat" / box.account,
        http_port=http,
        ws_port=ws,
        webui_port=webui,
        mac=mac_for(box.account),
    )


def onebot_config(http_token: str, ws_token: str) -> dict:
    """NapCat's onebot11.json: one HTTP and one WebSocket server inside the container, with the tokens nest
    connects with (the same shape as the first three accounts')."""
    return {
        "network": {
            "httpServers": [
                {
                    "enable": True,
                    "name": "nest-http",
                    "host": "0.0.0.0",
                    "port": INSIDE_HTTP,
                    "enableCors": False,
                    "enableWebsocket": False,
                    "messagePostFormat": "array",
                    "token": http_token,
                    "debug": False,
                }
            ],
            "httpSseServers": [],
            "httpClients": [],
            "websocketServers": [
                {
                    "enable": True,
                    "name": "nest-ws",
                    "host": "0.0.0.0",
                    "port": INSIDE_WS,
                    "messagePostFormat": "array",
                    "reportSelfMessage": True,  # or the bot never sees what it said itself
                    "token": ws_token,
                    "enableForcePushEvent": True,
                    "debug": False,
                    "heartInterval": 30000,
                }
            ],
            "websocketClients": [],
            "plugins": [],
        },
        "musicSignUrl": "",
        "enableLocalFile2Url": False,
        "parseMultMsg": False,
    }


def write_config(plan: Plan, http_token: str, ws_token: str) -> None:
    """Blocking: the account's folders and OneBot config (readable by this user only, it holds the tokens).
    Existing QQ data (a saved login) is left alone."""
    for part in ("qq", "config", "plugins"):
        (plan.folder / part).mkdir(parents=True, exist_ok=True)
    text = json.dumps(onebot_config(http_token, ws_token), ensure_ascii=False, indent=2)
    for name in ("onebot11.json", f"onebot11_{plan.self_id}.json"):
        path = plan.folder / "config" / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def local_timezone() -> str:
    """The machine's time zone name (e.g. Asia/Shanghai), so the container's log times match nest's."""
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return "UTC"
    _, _, zone = target.partition("zoneinfo/")
    return zone or "UTC"


def run_args(plan: Plan, *, webui_token: str, password_md5: str = "") -> list[str]:
    """The arguments after `docker`, as deploy/mac/docker-compose.yml's services. `password_md5`: the
    account's 回退密码 as NapCat takes it (32 hex digits), used when the saved login stops working."""
    uid = os.getuid() if hasattr(os, "getuid") else 501
    gid = os.getgid() if hasattr(os, "getgid") else 20
    env = {
        "ACCOUNT": plan.self_id,
        "WEBUI_TOKEN": webui_token,
        "NAPCAT_UID": str(uid),
        "NAPCAT_GID": str(gid),
        "TZ": local_timezone(),
    }
    if password_md5:
        env["NAPCAT_QUICK_PASSWORD_MD5"] = password_md5
    args = [
        "run",
        "-d",
        "--name",
        plan.name,
        "--hostname",
        plan.name,
        "--mac-address",
        plan.mac,
        "--restart",
        "unless-stopped",
        "--log-driver",
        "json-file",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
        "--label",
        f"nest.account={plan.self_id}",
    ]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    for host, inside in (
        (plan.webui_port, INSIDE_WEBUI),
        (plan.http_port, INSIDE_HTTP),
        (plan.ws_port, INSIDE_WS),
    ):
        args += ["-p", f"127.0.0.1:{host}:{inside}"]
    for part, inside in (
        ("qq", "/app/.config/QQ"),
        ("config", "/app/napcat/config"),
        ("plugins", "/app/napcat/plugins"),
    ):
        args += ["-v", f"{plan.folder / part}:{inside}"]
    return [*args, IMAGE]


def scan_failed(log: str) -> str | None:
    """Why the last scan did not log in, when it did not. QQ then asks for no new code, and the old one stays
    in its file looking as good as ever: only a restart brings a new one (ErrCode 5)."""
    text = ANSI.sub("", log)
    text = text[text.rfind(BOOT) :] if BOOT in text else text
    failures = list(SCAN_FAILED.finditer(text))
    if not failures:
        return None
    last = failures[-1]
    if SCAN_EXPIRED.match(last[1]) or ASKS_FOR_SCAN in text[last.end() :]:
        return None
    said = " ".join(last[1].split())[:60]
    return (
        f"扫码后 QQ 没让登录（{said}），也不会再出新二维码。"
        "点上面的「重启」换一张，用这个号的手机 QQ 扫，并在手机上确认"
    )


def login_trouble(log: str) -> str | None:
    """Why NapCat's QQ is stuck, neither logged in nor waiting for a scan, read from the container's log
    since it last started; None when the log shows nothing stuck. Never repeats the log's links."""
    text = ANSI.sub("", log)
    text = text[text.rfind(BOOT) :] if BOOT in text else text
    quick = QUICK_FAILED.findall(text)
    said = f"（QQ 说：{quick[-1].strip()[:60]}）" if quick else ""
    if "需要验证码" in text:
        return (
            f"保存的登录用不了{said}，改用回退密码登录时 QQ 要求短信验证码，nest 没法替你完成。"
            "点「回退密码」→「清除」，容器重建后就会出二维码，再扫码登录"
        )
    failed = PASSWORD_FAILED.findall(text)
    if failed:
        why = f"（{failed[-1].strip()[:60]}）" if failed[-1].strip() else ""
        return (
            f"保存的登录用不了{said}，回退密码也没登上{why}。密码改过就在「回退密码」里重新填，"
            "或者清除回退密码，容器重建后扫码登录"
        )
    return None
