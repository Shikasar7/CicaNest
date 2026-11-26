"""Starting command-line programs (docker, git, tailscale…) without a console window popping up on Windows.

nest's server runs with no console of its own (`nest start` and the desktop app start it detached). Windows
then gives every console program it starts a new console window of its own, shown for as long as the
program runs: with Docker installed, a black box flashing up each time nest asks docker about the QQ
containers. Every subprocess nest starts passes `creationflags=NO_WINDOW` (tests/test_proc.py checks).
"""

from __future__ import annotations

import subprocess

# 0 elsewhere: creationflags must be 0 on macOS and Linux
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
