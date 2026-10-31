"""Patcher safety checks: protection refusal and post-build diagnostics."""

from __future__ import annotations

import io
import zipfile

import pytest
from test_patch import make_apk, make_love

from omgi import apk, verify
from omgi.adapters.balatro import patch
from omgi.cli import main as cli


def _zip(entries: dict[str, tuple[bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, (data, ctype) in entries.items():
            z.writestr(name, data, ctype)
    return buf.getvalue()


def _split(lib: bytes = b"\x7fELF" + b"\1" * 40) -> bytes:
    return _zip({"AndroidManifest.xml": (b"split", zipfile.ZIP_DEFLATED),
                 "lib/arm64-v8a/libgame.so": (lib, zipfile.ZIP_STORED)})


def _source(tmp_path, *, extra: dict[str, bytes] | None = None):
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.apk").write_bytes(make_apk())
    (src / "split_config.arm64_v8a.apk").write_bytes(_split())
    for name, data in (extra or {}).items():
        (src / name).write_bytes(data)
    return src


def by_name(checks):
    return {c.name: c for c in checks}


def test_protected_app_is_refused_before_building(tmp_path):
    protected = _zip({"AndroidManifest.xml": (b"x", zipfile.ZIP_DEFLATED),
                      "lib/arm64-v8a/libpairipcore.so": (b"\x7fELF", zipfile.ZIP_STORED)})
    src = _source(tmp_path, extra={"split_protect.apk": protected})
    out = tmp_path / "out"
    with pytest.raises(apk.PatchError, match="anti-tamper"):
        patch.patch_apks(src, out, sign=False)
    assert not out.exists()
    with pytest.raises(apk.PatchError, match="anti-tamper"):
        patch.patch_apks(src, out, sign=False, noop=True)


def test_clean_patch_passes_every_check(tmp_path):
    res = patch.patch_apks(_source(tmp_path), tmp_path / "out", sign=False)
    checks = by_name(res.checks)
    assert res.ok, verify.report(res.checks)
    assert checks["splits present"].ok is True
    assert "2 .so file(s) byte-identical" in checks["native libraries unchanged"].detail
    assert "2 uncompressed .so" in checks["alignment"].detail
    assert checks["only expected files changed"].detail == "base.apk:assets/game.love"
    assert checks["manifest"].ok is True and "not rebuilt" in checks["manifest"].detail
    assert checks["signatures"].ok is None  # unsigned


def test_noop_rebuild_changes_nothing(tmp_path):
    res = patch.patch_apks(_source(tmp_path), tmp_path / "out", sign=False, noop=True)
    assert res.ok, verify.report(res.checks)
    assert by_name(res.checks)["only expected files changed"].detail == "nothing (no-op rebuild)"
    love = zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(tmp_path / "out/base.apk").read("assets/game.love")))
    assert "omgi/init.lua" not in love.namelist()


def test_diagnose_catches_broken_builds(tmp_path):
    original = apk.load_apk_set(_source(tmp_path))
    out = tmp_path / "bad"
    out.mkdir()
    # base: native lib contents swapped and left unaligned; the split is dropped.
    base = _zip({"AndroidManifest.xml": (b"changed", zipfile.ZIP_DEFLATED),
                 "lib/arm64-v8a/liblove.so": (b"\x7fELF" + b"\2" * 33, zipfile.ZIP_STORED),
                 "assets/game.love": (make_love(), zipfile.ZIP_STORED)})
    (out / "base.apk").write_bytes(base)
    checks = by_name(verify.diagnose(original, [out / "base.apk"], expected_changes=set()))
    assert checks["splits present"].ok is False and "missing split_config.arm64_v8a.apk" in checks["splits present"].detail
    assert "liblove.so contents changed" in checks["native libraries unchanged"].detail
    assert checks["manifest"].ok is False
    assert checks["only expected files changed"].ok is False
    assert "lib/arm64-v8a/liblove.so" in apk.check_alignment(base)  # plain zipfile doesn't align
    assert checks["alignment"].ok is False and "base.apk:lib/arm64-v8a/liblove.so" in checks["alignment"].detail


def test_apktool_rebuild_is_reported(tmp_path, monkeypatch):
    def fake_apktool(data, permission):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = {i.filename: (z.read(i.filename), i.compress_type) for i in z.infolist()}
        entries["AndroidManifest.xml"] = (permission.encode("utf-16-le"), zipfile.ZIP_DEFLATED)
        entries["resources.arsc"] = (b"\3\0" * 7, zipfile.ZIP_STORED)
        return apk.rewrite_zip(_zip(entries))

    monkeypatch.setattr(apk, "add_permission_with_apktool", fake_apktool)
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.apk").write_bytes(make_apk(internet=False))
    res = patch.patch_apks(src, tmp_path / "out", sign=False)
    manifest = by_name(res.checks)["manifest"]
    assert manifest.ok is None and "REBUILT with apktool" in manifest.detail and "1 other entry" in manifest.detail
    assert res.ok, verify.report(res.checks)


def test_signatures_verified_with_one_signer(tmp_path, monkeypatch):
    if not apk.find_tool("apksigner") or not apk.find_tool("keytool"):
        pytest.skip("needs apksigner and keytool")
    monkeypatch.setattr(apk, "OMGI_HOME", tmp_path / "home")
    res = patch.patch_apks(_source(tmp_path), tmp_path / "out", min_sdk=24)
    sig = by_name(res.checks)["signatures"]
    assert sig.ok is True and "all 2 verify, one signer" in sig.detail, sig.detail


def test_install_refuses_unchecked_or_failed_builds(tmp_path, capsys):
    folder = tmp_path / "build"
    folder.mkdir()
    (folder / "base.apk").write_bytes(make_apk())
    assert cli(["install", str(folder), "--yes"]) == 1
    assert "re-run `omgi patch`" in capsys.readouterr().err
    (folder / "omgi-diagnostics.txt").write_text("FAILED\n[FAIL] splits present: missing x\n")
    assert cli(["install", str(folder), "--yes"]) == 1
    assert "failed its checks" in capsys.readouterr().err


def test_cli_patch_writes_diagnostics(tmp_path, capsys):
    out = tmp_path / "out"
    assert cli(["patch", "balatro", str(_source(tmp_path)), "-o", str(out), "--no-sign", "--noop"]) == 0
    text = capsys.readouterr().out
    assert "No-op rebuild:" in text and "Diagnostics (PASSED)" in text
    assert (out / "omgi-diagnostics.txt").read_text().startswith("PASSED")
