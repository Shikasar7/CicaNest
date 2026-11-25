"""How to start and stop nest on this machine, for messages shown to the user."""

from __future__ import annotations

import os

WINDOWS = os.name == "nt"
# Run by the installed desktop app with the Python it brings (desktop/src/nest.js): there is no 启动.bat.
BUNDLED = bool(os.environ.get("NEST_BUNDLED"))
if BUNDLED:
    START = "从 nest 桌面应用"
    STOP = "在任务栏右下角 nest 图标的菜单里选「退出并停止 nest」"
else:
    START = "双击 启动.bat" if WINDOWS else "运行 uv run nest start"
    STOP = "运行 停止.bat" if WINDOWS else "运行 uv run nest stop"
PROCESSES = "任务管理器" if WINDOWS else "活动监视器"
