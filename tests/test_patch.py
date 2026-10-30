"""The Balatro patcher, on a synthetic APK shaped like the real one."""

from __future__ import annotations

import io
import zipfile

import pytest

from omgi import apk
from omgi.adapters.balatro import patch

MAIN = "require 'engine/object'\nfunction love.load() end\nfunction love.update(dt) end\n"


def make_love(main: str = MAIN) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("main.lua", main)
        z.writestr("game.lua", "Game = {}\n")
        z.writestr("globals.lua", "G = {}\n")
    return buf.getvalue()


def make_apk(*, internet: bool = True, love: bytes | None = None) -> bytes:
    manifest = b"\x03\x00\x08\x00" + ("android.permission.INTERNET" if internet else "android.permission.VIBRATE").encode("utf-16-le")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", manifest, zipfile.ZIP_DEFLATED)
        z.writestr("classes.dex", b"dex\n035\0", zipfile.ZIP_DEFLATED)
        z.writestr("resources.arsc", b"\x02\x00" * 7, zipfile.ZIP_STORED)
        z.writestr("lib/arm64-v8a/liblove.so", b"\x7fELF" + b"\0" * 33, zipfile.ZIP_STORED)
        z.writestr("assets/game.love", love or make_love(), zipfile.ZIP_STORED)
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.writestr("META-INF/CERT.RSA", b"sig")
    return buf.getvalue()


def read_love(apk_bytes: bytes) -> zipfile.ZipFile:
    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as z:
        return zipfile.ZipFile(io.BytesIO(z.read("assets/game.love")))


def test_inject_main_is_idempotent():
    once = patch.inject_main(MAIN)
    twice = patch.inject_main(once)
    assert once == twice
    assert once.count('require("omgi").start("balatro")') == 1
    assert once.startswith(MAIN)


def test_inject_main_refuses_trailing_return():
    with pytest.raises(apk.PatchError):
        patch.inject_main("local t = {}\nreturn t\n")


def test_patch_apk_folder(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    (src / "base.apk").write_bytes(make_apk())
    (src / "split_config.arm64_v8a.apk").write_bytes(make_apk())
    out = tmp_path / "out"
    res = patch.patch_apks(src, out, port=50000, token="t0k", sign=False)

    assert sorted(p.name for p in res.outputs) == ["base.apk", "split_config.arm64_v8a.apk"]
    base = (out / "base.apk").read_bytes()
    with zipfile.ZipFile(io.BytesIO(base)) as z:
        names = z.namelist()
        assert "META-INF/CERT.RSA" not in names and "META-INF/MANIFEST.MF" not in names
        assert z.getinfo("assets/game.love").compress_type == zipfile.ZIP_STORED
        assert z.read("classes.dex") == b"dex\n035\0"
    assert apk.check_alignment(base) == []

    love = read_love(base)
    files = set(love.namelist())
    assert {"omgi/init.lua", "omgi/json.lua", "omgi/server.lua", "omgi/config.lua",
            "omgi/adapters/balatro.lua", "game.lua", "globals.lua"} <= files
    assert 'start("balatro")' in love.read("main.lua").decode()
    config = love.read("omgi/config.lua").decode()
    assert "port = 50000" in config and '"t0k"' in config

    split = (out / "split_config.arm64_v8a.apk").read_bytes()
    with zipfile.ZipFile(io.BytesIO(split)) as z:
        assert "META-INF/CERT.RSA" not in z.namelist()


def test_repatching_a_patched_apk(tmp_path):
    first = tmp_path / "a"
    (tmp_path / "in.apk").write_bytes(make_apk())
    patch.patch_apks(tmp_path / "in.apk", first, sign=False)
    second = tmp_path / "b"
    patch.patch_apks(first / "in.apk", second, port=47999, sign=False)
    love = read_love((second / "in.apk").read_bytes())
    assert love.read("main.lua").decode().count("OMGI (Ophelia") == 1
    assert "port = 47999" in love.read("omgi/config.lua").decode()


def test_apks_bundle_and_love_file(tmp_path):
    bundle = tmp_path / "Balatro.apks"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("base.apk", make_apk())
        z.writestr("info.json", "{}")
    res = patch.patch_apks(bundle, tmp_path / "out", sign=False)
    assert [p.name for p in res.outputs] == ["base.apk"]

    love = tmp_path / "Balatro.love"
    love.write_bytes(make_love())
    res = patch.patch_apks(love, tmp_path / "love-out", sign=False)
    patched = zipfile.ZipFile(res.outputs[0])
    assert "omgi/adapters/balatro.lua" in patched.namelist()


def test_not_balatro(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", b"")
        z.writestr("classes.dex", b"")
    (tmp_path / "other.apk").write_bytes(buf.getvalue())
    with pytest.raises(apk.PatchError, match="game files"):
        patch.patch_apks(tmp_path / "other.apk", tmp_path / "out", sign=False)


def test_missing_internet_needs_apktool(tmp_path, monkeypatch):
    monkeypatch.setattr(apk, "find_tool", lambda name: None)
    (tmp_path / "in.apk").write_bytes(make_apk(internet=False))
    with pytest.raises(apk.PatchError, match="INTERNET"):
        patch.patch_apks(tmp_path / "in.apk", tmp_path / "out", sign=False)


def test_sign_without_apksigner(tmp_path, monkeypatch):
    monkeypatch.setattr(apk, "find_tool", lambda name: None)
    (tmp_path / "in.apk").write_bytes(make_apk())
    with pytest.raises(apk.PatchError, match="apksigner"):
        patch.patch_apks(tmp_path / "in.apk", tmp_path / "out")


def test_patched_archive_runs(tmp_path):
    """Unpack a patched game.love and run the fake game from those files."""
    import shutil
    import subprocess

    from conftest import ROOT, _lua_with_socket, free_port
    from omgi import Client

    lua = _lua_with_socket()
    if not lua:
        pytest.skip("needs luajit with luasocket")
    port = free_port()
    love = patch.patch_love(make_love(), port=port)
    game_dir = tmp_path / "game"
    zipfile.ZipFile(io.BytesIO(love)).extractall(game_dir)
    shutil.copy(ROOT / "tests/lua/fake_balatro.lua", game_dir / "harness.lua")
    proc = subprocess.Popen([lua, str(game_dir / "harness.lua"), str(game_dir), str(port), "20"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert proc.stdout.readline().strip().endswith(f"listening on 127.0.0.1:{port} for balatro")
        assert proc.stdout.readline().strip() == "READY"
        with Client(port=port) as c:
            assert c.game_id == "balatro"
            assert c.wait_stable()["phase"] == "menu"
    finally:
        proc.kill()
        proc.wait()
