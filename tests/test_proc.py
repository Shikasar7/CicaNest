"""Every program nest starts runs without a console window on Windows (nest.proc)."""

from __future__ import annotations

import ast
from pathlib import Path

import nest

STARTERS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},  # not asyncio.run
}


def _starts() -> list[tuple[str, int, ast.Call]]:
    found = []
    for path in sorted(Path(nest.__file__).parent.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and node.func.attr in STARTERS.get(owner.id, ()):
                found.append((path.name, node.lineno, node))
    return found


def test_every_subprocess_says_how_its_window_is_handled() -> None:
    starts = _starts()
    assert len(starts) >= 10  # the scan still finds them
    missing = [
        f"{name}:{line}"
        for name, line, call in starts
        if not any(k.arg == "creationflags" for k in call.keywords)
    ]
    assert missing == [], "没传 creationflags（Windows 上会闪黑框）：" + ", ".join(missing)
