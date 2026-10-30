from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lua_with_socket() -> str | None:
    for exe in ("luajit", "lua5.1", "lua"):
        path = shutil.which(exe)
        if path and subprocess.run([path, "-e", "require('socket')"], capture_output=True).returncode == 0:
            return path
    return None


@pytest.fixture
def fake_balatro():
    """Run the real mod inside tests/lua/fake_balatro.lua; yields its port."""
    lua = _lua_with_socket()
    if not lua:
        pytest.skip("needs luajit (or lua5.1) with luasocket")
    port = free_port()
    proc = subprocess.Popen(
        [lua, str(ROOT / "tests/lua/fake_balatro.lua"), str(SRC), str(port), "60"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    lines = []
    for line in proc.stdout:
        lines.append(line)
        if line.strip() == "READY":
            break
    else:
        proc.wait()
        pytest.fail("fake game didn't start:\n" + "".join(lines))
    yield port
    proc.kill()
    proc.wait()
