# O.M.G.I. — Ophelia Mobile Game Integration

Lets [Ophelia](https://github.com/Shadow4-18/Ophelia) play mobile games **through
the game itself** instead of through screenshots and taps. A small mod (or, for
compiled games, a reverse-engineered bridge) runs inside the game and speaks a
tiny JSON protocol. Over that link Ophelia reads the real game state (cards,
money, score, what's in the shop) and acts through the game's own logic.

```
 Ophelia ──(omgi Python client)──TCP 127.0.0.1:<port>──▶ mod inside the game
            state / actions / act / query        ◀── JSON lines
```

This repo has:

| Part | Where |
|------|-------|
| Protocol spec | [docs/protocol.md](docs/protocol.md) |
| Python client, CLI, renderers | `src/omgi/` (`pip install`, stdlib only) |
| LÖVE runtime (the in-game socket server + JSON) | `src/omgi/runtimes/love/omgi/` |
| Game adapters | `src/omgi/adapters/<game>/` |
| Writing a new adapter | [docs/adapters.md](docs/adapters.md) |

## Supported games

| Game | Engine | How | Port |
|------|--------|-----|------|
| **Balatro** | LÖVE (Lua) | Mod injected into `assets/game.love` — [guide](src/omgi/adapters/balatro/README.md) | 47801 |

Next candidates are other LÖVE/Godot/Unity-Mono games, which can reuse the same
approach. IL2CPP games need a Frida-style bridge (planned).

## Quick start (Balatro)

```bash
pip install "omgi @ git+https://github.com/Shadow4-18/O.M.G.I..git"

# 1. Get the installed game off the phone (Ophelia does this for you):
ophelia apk pull com.playstack.balatro.android
# 2. Patch it (needs apksigner + keytool for signing)
omgi patch balatro ~/.ophelia/apks/com.playstack.balatro.android/<version>/ -o out/balatro
# 3. Install (replaces the store copy; read the warning it prints first)
omgi install out/balatro --uninstall --yes
# 4. Open the game, then from a PC:  omgi forward    (on Termux, skip this)
omgi state
omgi act select_blind
omgi act play_hand cards=1,3,4
```

In Ophelia, set `omgi: true` on the game in `~/.ophelia/games.yaml`. She then
gets `phone_game_state` / `phone_game_act` and plays through the link, falling
back to screen + taps whenever the link isn't up.

## Ground rules

- **Single-player / offline games only.** Mods and memory hooks in online or
  competitive games break their terms and get accounts banned. Adapters for
  games with online features must say so loudly.
- **Fair play.** Adapters report what a player could see on screen: no hidden
  cards, no deck order, no RNG peeking. The point is for Ophelia to *play*.
- **Your copy, your device.** Patch games you own, for yourself. Never commit
  game files (APKs, `.love` archives, extracted assets) to this repo.

## Development

```bash
pip install -e '.[dev]'
python -m pytest -q
```

The Balatro tests run the real Lua mod inside `tests/lua/fake_balatro.lua`, a
minimal stand-in for the game, and need `luajit` (or `lua5.1`) with luasocket
(`apt install luajit lua-socket`). Without them those tests are skipped.
