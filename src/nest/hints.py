"""How to start and stop nest on this machine, for messages shown to the user."""

from __future__ import annotations

import os

WINDOWS = os.name == "nt"
START = "双击 启动.bat" if WINDOWS else "运行 uv run nest start"
STOP = "运行 停止.bat" if WINDOWS else "运行 uv run nest stop"
PROCESSES = "任务管理器" if WINDOWS else "活动监视器"
