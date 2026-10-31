"""APK plumbing for patchers: read app bundles, swap files, align, re-sign.

Pure Python except signing, which needs `apksigner` (Android build-tools, or
`pkg install apksigner` on Termux) and `keytool` (any JDK) to make the key.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

OMGI_HOME = Path(os.environ.get("OMGI_HOME") or Path.home() / ".omgi")
KEY_ALIAS = "omgi"
KEY_PASS = "omgi-patch"  # local, throwaway signing key; not a secret

_SIGNATURE_RE = re.compile(r"^META-INF/([^/]+\.(SF|RSA|DSA|EC)|MANIFEST\.MF)$", re.IGNORECASE)
_ALIGN_EXTRA_ID = 0xD935


class PatchError(Exception):
    """Something about the input or the environment stops the patch."""


@dataclass
class ApkSet:
    """A base APK and its split APKs, as bytes keyed by file name."""

    base_name: str
    apks: dict[str, bytes] = field(default_factory=dict)

    @property
    def base(self) -> bytes:
        return self.apks[self.base_name]

    @property
    def splits(self) -> list[str]:
        return [n for n in self.apks if n != self.base_name]


def _pick_base(names: list[str], has: dict[str, bytes]) -> str:
    for n in names:
        if Path(n).name == "base.apk":
            return n
    # The split that has the app's code/assets is the base.
    for n in names:
        with zipfile.ZipFile(io.BytesIO(has[n])) as z:
            if "classes.dex" in z.namelist():
                return n
    return names[0]


def load_apk_set(path: Path) -> ApkSet:
    """Read an .apk, an .apks/.xapk bundle, or a folder of APKs (e.g. Ophelia's
    ~/.ophelia/apks/<package>/<version>/)."""
    path = Path(path).expanduser()
    found: dict[str, bytes] = {}
    if path.is_dir():
        for p in sorted(path.glob("*.apk")):
            found[p.name] = p.read_bytes()
    elif path.suffix.lower() in (".apks", ".xapk", ".apkm", ".zip"):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.lower().endswith(".apk"):
                    found[Path(n).name] = z.read(n)
    elif path.is_file():
        found[path.name] = path.read_bytes()
    else:
        raise PatchError(f"{path} doesn't exist")
    if not found:
        raise PatchError(f"no APK files found in {path}")
    names = list(found)
    return ApkSet(base_name=_pick_base(names, found), apks=found)


def manifest_has_permission(apk: bytes, permission: str) -> bool:
    """True if the binary AndroidManifest.xml's string pool names the permission."""
    with zipfile.ZipFile(io.BytesIO(apk)) as z:
        try:
            manifest = z.read("AndroidManifest.xml")
        except KeyError:
            return False
    return permission.encode("utf-16-le") in manifest or permission.encode() in manifest


def _alignment_for(info: zipfile.ZipInfo) -> int:
    if info.compress_type != zipfile.ZIP_STORED:
        return 0
    if info.filename.endswith(".so"):
        return 16384  # page-aligned so the system can map native libs in place
    return 4


def rewrite_zip(
    src: bytes,
    replace: dict[str, bytes] | None = None,
    *,
    drop_signatures: bool = True,
    align: bool = True,
) -> bytes:
    """Copy a zip/APK, swapping in `replace` contents, keeping each entry's
    compression and order, and zipalign-ing uncompressed entries."""
    replace = dict(replace or {})
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        names_written: set[str] = set()

        def write(info: zipfile.ZipInfo, data: bytes) -> None:
            new = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new.compress_type = info.compress_type
            new.external_attr = info.external_attr
            new.create_system = info.create_system
            n = _alignment_for(new) if align else 0
            if n:
                header_end = out.tell() + 30 + len(new.filename.encode())
                pad = (-(header_end + 6)) % n
                new.extra = struct.pack("<HHH", _ALIGN_EXTRA_ID, 2 + pad, n) + b"\0" * pad
            zout.writestr(new, data)
            names_written.add(info.filename)

        for info in zin.infolist():
            if drop_signatures and _SIGNATURE_RE.match(info.filename):
                continue
            if info.filename in replace:
                write(info, replace.pop(info.filename))
            else:
                write(info, zin.read(info.filename))
        for name, data in replace.items():
            if name not in names_written:
                info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                write(info, data)
    return out.getvalue()


def check_alignment(apk: bytes) -> list[str]:
    """Names of uncompressed entries whose data isn't aligned (empty = good)."""
    bad = []
    with zipfile.ZipFile(io.BytesIO(apk)) as z:
        for info in z.infolist():
            n = _alignment_for(info)
            if not n:
                continue
            off = info.header_offset
            name_len, extra_len = struct.unpack("<HH", apk[off + 26: off + 30])
            if (off + 30 + name_len + extra_len) % n:
                bad.append(info.filename)
    return bad


# Signing -------------------------------------------------------------------

def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if not root:
            continue
        tools = sorted(Path(root, "build-tools").glob(f"*/{name}"), reverse=True)
        if tools:
            return str(tools[0])
    return None


def ensure_keystore(path: Path | None = None) -> Path:
    path = Path(path or OMGI_HOME / "omgi-patch.keystore").expanduser()
    if path.is_file():
        return path
    keytool = find_tool("keytool")
    if not keytool:
        raise PatchError("keytool not found (install a JDK: `pkg install openjdk-17` on Termux)")
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [keytool, "-genkeypair", "-keystore", str(path), "-alias", KEY_ALIAS,
         "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
         "-storepass", KEY_PASS, "-keypass", KEY_PASS,
         "-dname", "CN=OMGI local patch"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PatchError(f"keytool failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return path


def sign(apks: list[Path], keystore: Path | None = None, *, min_sdk: int | None = None) -> None:
    """Sign APKs in place with one key (every split must share the base's key)."""
    apksigner = find_tool("apksigner")
    if not apksigner:
        raise PatchError(
            "apksigner not found. Install Android build-tools (or `pkg install apksigner` "
            "on Termux), or pass --no-sign and sign the APKs yourself.")
    ks = ensure_keystore(keystore)
    for apk in apks:
        proc = subprocess.run(
            [apksigner, "sign", "--ks", str(ks), "--ks-key-alias", KEY_ALIAS,
             "--ks-pass", f"pass:{KEY_PASS}", "--key-pass", f"pass:{KEY_PASS}"]
            + (["--min-sdk-version", str(min_sdk)] if min_sdk else []) + [str(apk)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise PatchError(f"apksigner failed on {apk.name}: {proc.stderr.strip() or proc.stdout.strip()}")
        idsig = apk.with_name(apk.name + ".idsig")
        if idsig.exists():
            idsig.unlink()


def add_permission_with_apktool(apk: bytes, permission: str) -> bytes:
    """Rebuild an APK with an extra <uses-permission>, using apktool."""
    apktool = find_tool("apktool")
    if not apktool:
        raise PatchError(
            f"The game doesn't declare {permission}, which the link needs, and apktool "
            "isn't installed to add it. Install apktool (https://apktool.org) and run again.")
    with tempfile.TemporaryDirectory(prefix="omgi-apktool-") as tmp:
        src, dec, out = Path(tmp, "in.apk"), Path(tmp, "decoded"), Path(tmp, "out.apk")
        src.write_bytes(apk)
        proc = subprocess.run([apktool, "d", "-s", "-f", "-o", str(dec), str(src)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise PatchError(f"apktool decode failed: {proc.stderr.strip()[-800:]}")
        manifest = dec / "AndroidManifest.xml"
        text = manifest.read_text(encoding="utf-8")
        tag = f'<uses-permission android:name="{permission}"/>'
        text = re.sub(r"(<manifest\b[^>]*>)", r"\1\n    " + tag, text, count=1)
        manifest.write_text(text, encoding="utf-8")
        proc = subprocess.run([apktool, "b", "-o", str(out), str(dec)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise PatchError(f"apktool build failed: {proc.stderr.strip()[-800:]}")
        return out.read_bytes()
