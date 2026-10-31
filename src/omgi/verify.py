"""Checks run on every patched APK set before anything gets installed.

`protection()` runs first: apps that ship anti-tamper protection refuse to run
once modified or re-signed, so patching them only costs the user their store
install. `diagnose()` compares the rebuilt set with the original:

- every original APK split is present (and nothing extra)
- native libraries are byte-identical
- uncompressed .so files are 16 KB aligned (other stored entries 4-byte)
- only the expected entries changed; the manifest is untouched unless apktool
  rebuilt it, and that is reported
- every APK verifies with apksigner and all share one signing certificate
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from omgi import apk
from omgi.apk import PatchError

# Native libraries that mark an app as tamper-protected.
PROTECTION_LIBS = {
    "libpairipcore.so": "Google Play's app integrity protection",
}

_SIGNATURE_RE = re.compile(r"^META-INF/([^/]+\.(SF|RSA|DSA|EC)|MANIFEST\.MF)$", re.IGNORECASE)


@dataclass
class Check:
    name: str
    ok: bool | None  # None = skipped / informational
    detail: str

    def line(self) -> str:
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[self.ok]
        return f"[{mark}] {self.name}: {self.detail}"


def protection(apks: apk.ApkSet) -> list[str]:
    """Descriptions of anti-tamper protection found in the set (empty = none)."""
    found = []
    for name, data in apks.apks.items():
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for entry in z.namelist():
                lib = entry.rsplit("/", 1)[-1]
                if entry.startswith("lib/") and lib in PROTECTION_LIBS:
                    found.append(f"{PROTECTION_LIBS[lib]} ({lib} in {name})")
    return sorted(set(found))


def refuse_if_protected(apks: apk.ApkSet) -> None:
    found = protection(apks)
    if found:
        raise PatchError(
            "This build uses anti-tamper protection: " + "; ".join(found) + ". "
            "It won't run once modified or re-signed, so O.M.G.I. can't integrate with it. "
            "Nothing was built; keep the store version installed.")


def _entries(data: bytes) -> dict[str, tuple[int, str]]:
    """entry -> (compress_type, sha256 of contents), signature files excluded."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if _SIGNATURE_RE.match(info.filename):
                continue
            out[info.filename] = (info.compress_type, hashlib.sha256(z.read(info.filename)).hexdigest())
    return out


def _check_splits(original: apk.ApkSet, outputs: dict[str, bytes]) -> Check:
    missing = sorted(set(original.apks) - set(outputs))
    extra = sorted(set(outputs) - set(original.apks))
    if missing or extra:
        bits = []
        if missing:
            bits.append("missing " + ", ".join(missing))
        if extra:
            bits.append("unexpected " + ", ".join(extra))
        return Check("splits present", False, "; ".join(bits))
    return Check("splits present", True, f"all {len(outputs)} APKs: " + ", ".join(sorted(outputs)))


def _check_native_libs(original: apk.ApkSet, outputs: dict[str, bytes]) -> Check:
    total, bad = 0, []
    for name, data in original.apks.items():
        if name not in outputs:
            continue
        before = {k: v for k, v in _entries(data).items() if k.endswith(".so")}
        after = {k: v for k, v in _entries(outputs[name]).items() if k.endswith(".so")}
        for lib, (ctype, digest) in before.items():
            total += 1
            if lib not in after:
                bad.append(f"{name}:{lib} missing")
            elif after[lib][1] != digest:
                bad.append(f"{name}:{lib} contents changed")
            elif after[lib][0] != ctype:
                bad.append(f"{name}:{lib} compression changed")
    if bad:
        return Check("native libraries unchanged", False, "; ".join(bad))
    return Check("native libraries unchanged", True, f"{total} .so file(s) byte-identical")


def _check_alignment(outputs: dict[str, bytes]) -> Check:
    stored_so, bad = 0, []
    for name, data in outputs.items():
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            stored_so += sum(1 for i in z.infolist()
                             if i.filename.endswith(".so") and i.compress_type == zipfile.ZIP_STORED)
        bad += [f"{name}:{e}" for e in apk.check_alignment(data)]
    if bad:
        return Check("alignment", False, "misaligned: " + ", ".join(bad))
    return Check("alignment", True,
                 f"{stored_so} uncompressed .so file(s) on 16 KB boundaries; other stored entries 4-byte")


def _check_changes(original: apk.ApkSet, outputs: dict[str, bytes], expected: set[str],
                   apktool_rebuilt: set[str]) -> list[Check]:
    changed, manifest_changed, rebuilt_changes = [], [], 0
    for name, data in original.apks.items():
        if name not in outputs:
            continue
        before, after = _entries(data), _entries(outputs[name])
        for entry in sorted(set(before) | set(after)):
            if before.get(entry) == after.get(entry):
                continue
            if entry == "AndroidManifest.xml":
                manifest_changed.append(name)
            elif name in apktool_rebuilt and f"{name}:{entry}" not in expected:
                rebuilt_changes += 1  # apktool re-encodes resources; reported below
            else:
                changed.append(f"{name}:{entry}")
    checks = []
    unexpected = [c for c in changed if c not in expected]
    missing = sorted(expected - set(changed))
    if unexpected or missing:
        bits = []
        if unexpected:
            bits.append("also changed: " + ", ".join(unexpected))
        if missing:
            bits.append("expected but unchanged: " + ", ".join(missing))
        checks.append(Check("only expected files changed", False, "; ".join(bits)))
    else:
        what = ", ".join(sorted(expected)) if expected else "nothing (no-op rebuild)"
        checks.append(Check("only expected files changed", True, what))

    if apktool_rebuilt:
        checks.append(Check("manifest", None,
                            "REBUILT with apktool (added INTERNET) in " + ", ".join(sorted(apktool_rebuilt))
                            + f"; {rebuilt_changes} other entr{'y' if rebuilt_changes == 1 else 'ies'} re-encoded"))
    elif manifest_changed:
        checks.append(Check("manifest", False, "changed without apktool in " + ", ".join(manifest_changed)))
    else:
        checks.append(Check("manifest", True, "not rebuilt; original bytes kept in every APK"))
    return checks


def _signer_digest(apksigner: str, path: Path, min_sdk: int | None) -> tuple[bool, str]:
    cmd = [apksigner, "verify", "--print-certs", "-v"]
    if min_sdk:
        cmd += ["--min-sdk-version", str(min_sdk)]
    proc = subprocess.run(cmd + [str(path)], capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        last = [ln for ln in out.splitlines() if ln.strip() and "JAVA_TOOL_OPTIONS" not in ln]
        return False, (last[-1] if last else f"exit {proc.returncode}")
    m = re.search(r"certificate SHA-256 digest:\s*([0-9a-f]+)", out)
    return True, m.group(1) if m else "?"


def _check_signatures(paths: list[Path], signed: bool, min_sdk: int | None) -> Check:
    if not signed:
        return Check("signatures", None, "skipped (not signed)")
    apksigner = apk.find_tool("apksigner")
    if not apksigner:
        return Check("signatures", None, "skipped (apksigner not found)")
    digests, bad = {}, []
    for p in paths:
        ok, info = _signer_digest(apksigner, p, min_sdk)
        if ok:
            digests[p.name] = info
        else:
            bad.append(f"{p.name}: {info}")
    if bad:
        return Check("signatures", False, "; ".join(bad))
    if len(set(digests.values())) != 1:
        return Check("signatures", False, "APKs signed with different keys: "
                     + ", ".join(f"{n}={d[:12]}" for n, d in digests.items()))
    digest = next(iter(digests.values()), "?")
    return Check("signatures", True, f"all {len(digests)} verify, one signer (SHA-256 {digest[:16]}…)")


def diagnose(
    original: apk.ApkSet,
    outputs: list[Path],
    *,
    expected_changes: set[str],
    apktool_rebuilt: set[str] | None = None,
    signed: bool = False,
    min_sdk: int | None = None,
) -> list[Check]:
    blobs = {p.name: p.read_bytes() for p in outputs}
    checks = [
        _check_splits(original, blobs),
        _check_native_libs(original, blobs),
        _check_alignment(blobs),
        *_check_changes(original, blobs, expected_changes, apktool_rebuilt or set()),
        _check_signatures(outputs, signed, min_sdk),
    ]
    return checks


def report(checks: list[Check]) -> str:
    return "\n".join(c.line() for c in checks)


def failed(checks: list[Check]) -> bool:
    return any(c.ok is False for c in checks)
