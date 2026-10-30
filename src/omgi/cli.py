"""omgi command line: patch games, link to them, and poke at them by hand."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from omgi import adapters
from omgi.apk import PatchError
from omgi.client import Client, OMGIError
from omgi.render import format_actions, render_state


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw[:1] in "[{\"" or raw in ("true", "false", "null"):
        return json.loads(raw)
    if "," in raw:
        return [_parse_value(p) for p in raw.split(",") if p.strip()]
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_args_kv(pairs: list[str]) -> dict[str, Any]:
    """['cards=1,2,3', 'by=suit'] -> {'cards': [1, 2, 3], 'by': 'suit'}."""
    out: dict[str, Any] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"expected key=value, got '{p}'")
        k, v = p.split("=", 1)
        out[k.strip()] = _parse_value(v)
    # "cards" is always a list, even with a single position.
    if "cards" in out and not isinstance(out["cards"], list):
        out["cards"] = [out["cards"]]
    return out


def _port(args: argparse.Namespace) -> int:
    if args.port:
        return args.port
    a = adapters.get(args.game) if args.game else None
    return a.default_port if a else 47800


def _client(args: argparse.Namespace) -> Client:
    return Client(args.host, _port(args), token=args.token or "", timeout=args.timeout)


def _show_state(c: Client, state: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
    else:
        print(render_state(c.game_id, state))


def cmd_games(_: argparse.Namespace) -> int:
    for a in adapters.all_adapters():
        print(f"{a.id:10} {a.name:20} engine={a.engine:6} port={a.default_port}  {', '.join(a.packages)}")
        if a.notes:
            print(f"{'':10} {a.notes}")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    if args.game != "balatro":
        print(f"No patcher for '{args.game}' yet. Known: balatro", file=sys.stderr)
        return 2
    from omgi.adapters.balatro import patch

    out = Path(args.output or f"out/{args.game}")
    try:
        res = patch.patch_apks(
            Path(args.source), out, port=args.port or patch.DEFAULT_PORT, token=args.token or "",
            sign=not args.no_sign, keystore=Path(args.keystore) if args.keystore else None,
        )
    except PatchError as e:
        print(f"Patch failed: {e}", file=sys.stderr)
        return 1
    print("Patched:")
    for p in res.outputs:
        print(f"  {p}")
    for n in res.notes:
        print(f"Note: {n}")
    if res.outputs and res.outputs[0].suffix == ".apk":
        print(f"\nThe link will listen on 127.0.0.1:{res.port} inside the game.")
        print(f"Install with: omgi install {out}")
    return 0


def _adb(args: argparse.Namespace) -> list[str]:
    adb = shutil.which("adb")
    if not adb:
        raise SystemExit("adb not found on PATH")
    cmd = [adb]
    if args.device:
        cmd += ["-s", args.device]
    return cmd


def cmd_install(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    apks = sorted(folder.glob("*.apk"))
    if not apks:
        print(f"No APKs in {folder}", file=sys.stderr)
        return 1
    if not args.yes:
        print(
            "Installing a patched build needs the store version uninstalled first (different\n"
            "signature). Uninstalling deletes that copy's local save data, and the patched\n"
            "build won't get store updates. Re-run with --yes to continue.\n"
            f"  1) adb uninstall {args.package}\n"
            f"  2) adb install-multiple {' '.join(str(a) for a in apks)}"
        )
        return 1
    adb = _adb(args)
    if args.uninstall:
        subprocess.run(adb + ["uninstall", args.package])
    proc = subprocess.run(adb + ["install-multiple", "-r"] + [str(a) for a in apks])
    return proc.returncode


def cmd_forward(args: argparse.Namespace) -> int:
    port = _port(args)
    proc = subprocess.run(_adb(args) + ["forward", f"tcp:{port}", f"tcp:{port}"])
    if proc.returncode == 0:
        print(f"Forwarded localhost:{port} -> phone:{port}")
    return proc.returncode


def cmd_state(args: argparse.Namespace) -> int:
    with _client(args) as c:
        _show_state(c, c.state(), args.json)
    return 0


def cmd_hello(args: argparse.Namespace) -> int:
    with _client(args) as c:
        print(json.dumps(c.hello_info, indent=2, ensure_ascii=False))
    return 0


def cmd_actions(args: argparse.Namespace) -> int:
    with _client(args) as c:
        res = c.actions()
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"phase={res.get('phase')} stable={res.get('stable')}")
            print(format_actions(res.get("actions") or []) or "(none right now)")
    return 0


def cmd_act(args: argparse.Namespace) -> int:
    params = parse_args_kv(args.args)
    with _client(args) as c:
        if args.no_wait:
            print(json.dumps(c.act(args.action, params, force=args.force), indent=2, ensure_ascii=False))
            return 0
        result, state = c.act_and_wait(args.action, params, force=args.force, timeout=args.wait)
        print(json.dumps(result, ensure_ascii=False))
        _show_state(c, state, args.json)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    with _client(args) as c:
        if not args.name:
            print(format_actions(c.queries()))
            return 0
        print(json.dumps(c.query(args.name, parse_args_kv(args.args)), indent=2, ensure_ascii=False))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    c = Client(args.host, _port(args), token=args.token or "", timeout=args.timeout, subscribe=True)
    with c:
        print(f"Connected to {c.game_id}; Ctrl+C to stop.")
        try:
            while True:
                c.ping()
                for ev in c.events:
                    print(json.dumps(ev, ensure_ascii=False))
                c.events.clear()
                time.sleep(0.25)
        except KeyboardInterrupt:
            return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omgi", description="Ophelia Mobile Game Integration")
    sub = p.add_subparsers(dest="cmd", required=True)

    def link_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--game", "-g", default="balatro", help="adapter id, picks the default port")
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=0)
        sp.add_argument("--token", default="")
        sp.add_argument("--timeout", type=float, default=5.0)
        sp.add_argument("--json", action="store_true", help="raw JSON instead of text")

    sp = sub.add_parser("games", help="List supported games")
    sp.set_defaults(fn=cmd_games)

    sp = sub.add_parser("patch", help="Inject the OMGI mod into a game")
    sp.add_argument("game", help="adapter id, e.g. balatro")
    sp.add_argument("source", help=".apk, .apks/.xapk, a folder of APKs, or a .love file")
    sp.add_argument("-o", "--output", help="output folder (default out/<game>)")
    sp.add_argument("--port", type=int, default=0, help="port the mod listens on")
    sp.add_argument("--token", default="", help="require this token from clients")
    sp.add_argument("--no-sign", action="store_true")
    sp.add_argument("--keystore", help="signing keystore (default ~/.omgi/omgi-patch.keystore)")
    sp.set_defaults(fn=cmd_patch)

    sp = sub.add_parser("install", help="Install a patched APK set over ADB")
    sp.add_argument("folder")
    sp.add_argument("--package", default="com.playstack.balatro.android")
    sp.add_argument("--device", "-s", default="")
    sp.add_argument("--uninstall", action="store_true", help="uninstall the store version first")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_install)

    sp = sub.add_parser("forward", help="adb forward the game's port to this machine")
    sp.add_argument("--game", "-g", default="balatro")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--device", "-s", default="")
    sp.set_defaults(fn=cmd_forward)

    for name, fn, helptext in (
        ("hello", cmd_hello, "Show what the game reports about itself"),
        ("state", cmd_state, "Show the game state"),
        ("actions", cmd_actions, "List what can be done right now"),
        ("watch", cmd_watch, "Stream phase-change events"),
    ):
        sp = sub.add_parser(name, help=helptext)
        link_opts(sp)
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("act", help="Do an action: omgi act play_hand cards=1,2,3")
    link_opts(sp)
    sp.add_argument("action")
    sp.add_argument("args", nargs="*", help="key=value; lists as 1,2,3 or JSON")
    sp.add_argument("--no-wait", action="store_true")
    sp.add_argument("--wait", type=float, default=20.0, help="seconds to wait for the game to settle")
    sp.add_argument("--force", action="store_true", help="act even if the game reads busy")
    sp.set_defaults(fn=cmd_act)

    sp = sub.add_parser("query", help="Read-only question, e.g. omgi query hand_info cards=1,2")
    link_opts(sp)
    sp.add_argument("name", nargs="?")
    sp.add_argument("args", nargs="*")
    sp.set_defaults(fn=cmd_query)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except OMGIError as e:
        print(f"Game link error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
