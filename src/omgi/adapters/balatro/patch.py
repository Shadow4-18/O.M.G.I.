"""Inject the OMGI mod into Balatro.

Balatro is a LÖVE game: on Android its Lua source ships as assets/game.love
(a zip) inside the APK. The patch adds the omgi/ Lua files to that archive,
appends one guarded `require("omgi").start("balatro")` to main.lua, and
rebuilds and re-signs the APK set. A plain .love file can be patched too
(handy for testing with desktop LÖVE).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from omgi import apk, verify
from omgi.apk import PatchError

ADAPTER = "balatro"
PACKAGE = "com.playstack.balatro.android"
DEFAULT_PORT = 47801
INTERNET = "android.permission.INTERNET"

BEGIN = "-- >>> OMGI (Ophelia Mobile Game Integration) >>>"
END = "-- <<< OMGI <<<"
_BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)


@dataclass
class PatchResult:
    outputs: list[Path] = field(default_factory=list)
    port: int = DEFAULT_PORT
    notes: list[str] = field(default_factory=list)
    signed: bool = False
    noop: bool = False
    apktool_rebuilt: set[str] = field(default_factory=set)
    checks: list[verify.Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not verify.failed(self.checks)


def _lua_tree(package: str) -> dict[str, bytes]:
    """Lua files under package's data dir, keyed by their path from that dir."""
    out: dict[str, bytes] = {}
    root = resources.files(package)

    def walk(node, prefix: str) -> None:
        for child in node.iterdir():
            rel = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, rel + "/")
            elif child.name.endswith(".lua"):
                out[rel] = child.read_bytes()

    walk(root, "")
    return out


def mod_files(port: int = DEFAULT_PORT, token: str = "", host: str = "127.0.0.1") -> dict[str, bytes]:
    """Every file the mod adds to the game archive."""
    files = _lua_tree("omgi.runtimes.love")
    files.update(_lua_tree("omgi.adapters.balatro.lua"))
    config = (
        "-- Written by `omgi patch balatro`.\n"
        f"return {{ host = {json.dumps(host)}, port = {int(port)}, token = {json.dumps(token)} }}\n"
    )
    files["omgi/config.lua"] = config.encode()
    return files


def inject_main(main_lua: str) -> str:
    """Append (or refresh) the start-up block at the end of main.lua."""
    text = _BLOCK_RE.sub("", main_lua).rstrip("\n") + "\n"
    last = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("--")]
    if last and re.match(r"\s*return\b", last[-1]):
        raise PatchError("main.lua ends with a return statement; can't append the mod safely")
    block = (
        f"\n{BEGIN}\n"
        f'do\n'
        f'  local ok, err = pcall(function() require("omgi").start("{ADAPTER}") end)\n'
        f'  if not ok then print("[omgi] failed to start: " .. tostring(err)) end\n'
        f'end\n'
        f"{END}\n"
    )
    return text + block


def patch_love(love: bytes, *, port: int = DEFAULT_PORT, token: str = "", prefix: str = "") -> bytes:
    """Patch a .love archive (or an APK whose game files sit under `prefix`)."""
    with zipfile.ZipFile(io.BytesIO(love)) as z:
        names = set(z.namelist())
        main_name = prefix + "main.lua"
        if main_name not in names:
            raise PatchError(f"no {main_name} in the game archive — is this really Balatro?")
        main = z.read(main_name).decode("utf-8")
        if prefix == "" and "game.lua" not in names and "globals.lua" not in names:
            raise PatchError("this .love doesn't look like Balatro (no game.lua/globals.lua)")
    replace = {main_name: inject_main(main).encode("utf-8")}
    for rel, data in mod_files(port, token).items():
        replace[prefix + rel] = data
    return apk.rewrite_zip(love, replace, drop_signatures=False, align=bool(prefix))


def _find_game(apks: apk.ApkSet) -> tuple[str, str, str]:
    """(apk name, entry name, kind) where kind is 'love' or 'assets'."""
    for name, data in apks.apks.items():
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = z.namelist()
        loves = [e for e in entries if e.startswith("assets/") and e.endswith(".love")]
        if loves:
            pick = "assets/game.love" if "assets/game.love" in loves else loves[0]
            return name, pick, "love"
        if "assets/main.lua" in entries:
            return name, "assets/", "assets"
    raise PatchError("couldn't find Balatro's game files (assets/game.love) in these APKs")


def patch_apks(
    source: Path,
    out_dir: Path,
    *,
    port: int = DEFAULT_PORT,
    token: str = "",
    sign: bool = True,
    keystore: Path | None = None,
    noop: bool = False,
    min_sdk: int | None = None,
) -> PatchResult:
    """Build the patched APK set and check it. noop=True rebuilds and re-signs
    without injecting anything, to test the rebuild on its own."""
    source = Path(source).expanduser()
    out_dir = Path(out_dir).expanduser()
    result = PatchResult(port=port, noop=noop)

    if source.suffix.lower() == ".love":
        if noop:
            raise PatchError("--noop applies to APKs; a .love file has nothing to rebuild")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / source.name
        dest.write_bytes(patch_love(source.read_bytes(), port=port, token=token))
        result.outputs.append(dest)
        result.notes.append(f"Run it with LÖVE 11: `love {dest}` — the link listens on 127.0.0.1:{port}.")
        return result

    original = apk.load_apk_set(source)
    verify.refuse_if_protected(original)
    apks = apk.ApkSet(base_name=original.base_name, apks=dict(original.apks))
    expected: set[str] = set()
    apk_name = None

    if not noop:
        apk_name, entry, kind = _find_game(apks)
        data = apks.apks[apk_name]
        if kind == "love":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                love = z.read(entry)
            patched = apk.rewrite_zip(data, {entry: patch_love(love, port=port, token=token)})
            expected.add(f"{apk_name}:{entry}")
        else:
            patched = patch_love(data, port=port, token=token, prefix=entry)
            expected.add(f"{apk_name}:{entry}main.lua")
            expected |= {f"{apk_name}:{entry}{rel}" for rel in mod_files(port, token)}

        if not apk.manifest_has_permission(apks.base if apk_name != apks.base_name else patched, INTERNET):
            if apk_name != apks.base_name:
                apks.apks[apks.base_name] = apk.add_permission_with_apktool(apks.base, INTERNET)
            else:
                patched = apk.add_permission_with_apktool(patched, INTERNET)
            result.apktool_rebuilt.add(apks.base_name)
            result.notes.append("Added the INTERNET permission (the game didn't have it) so the link can listen.")
        apks.apks[apk_name] = patched
    else:
        result.notes.append("No-op rebuild: every APK rewritten and re-signed, nothing injected.")

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.apk"):
        old.unlink()
    for name, blob in apks.apks.items():
        if name != apk_name and name not in result.apktool_rebuilt:
            blob = apk.rewrite_zip(blob)  # drop the store signature so ours is the only one
        dest = out_dir / name
        dest.write_bytes(blob)
        result.outputs.append(dest)

    if sign:
        apk.sign(result.outputs, keystore, min_sdk=min_sdk)
        result.signed = True
    else:
        result.notes.append("Not signed (--no-sign): sign every APK with the same key before installing.")

    result.checks = verify.diagnose(
        original, result.outputs, expected_changes=expected,
        apktool_rebuilt=result.apktool_rebuilt, signed=result.signed, min_sdk=min_sdk,
    )
    return result
