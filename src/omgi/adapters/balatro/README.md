# Balatro adapter

Balatro is a LÖVE game; on Android its whole Lua source ships as
`assets/game.love` inside the APK. The patch adds the `omgi/` Lua files to that
archive and appends one guarded line to `main.lua`:

```lua
-- >>> OMGI (Ophelia Mobile Game Integration) >>>
do
  local ok, err = pcall(function() require("omgi").start("balatro") end)
  if not ok then print("[omgi] failed to start: " .. tostring(err)) end
end
-- <<< OMGI <<<
```

`start` wraps `love.update`, so every frame the mod answers requests on
`127.0.0.1:47801` on the game's main thread. Actions call the same `G.FUNCS`
handlers the buttons do (`play_cards_from_highlighted`, `buy_from_shop`,
`select_blind`, `cash_out`, …), so the game applies its own rules, animations
and autosaves. If anything in the mod fails, it logs and steps aside; it can't
take the game down.

## Install

You need: Python 3.11+, `apksigner` and `keytool` (Android build-tools + a JDK;
on Termux `pkg install apksigner openjdk-17`), and `adb` from a PC (or Shizuku).

```bash
ophelia apk pull com.playstack.balatro.android        # or App Extractor → .apks
omgi patch balatro ~/.ophelia/apks/com.playstack.balatro.android/<version>/ -o out/balatro
omgi install out/balatro --uninstall --yes
```

**Before you uninstall the store copy:** Android won't install a build signed
with a different key over it, and uninstalling deletes its local save. Sync or
accept losing the run in progress and profile. The patched build won't get
store updates either: when the store version updates, pull and patch again.

Every split APK must be signed with the same key; `omgi patch` re-signs them all
with `~/.omgi/omgi-patch.keystore` (made on first use; keep it so later patched
builds install as updates without uninstalling again).

If the game doesn't declare the INTERNET permission (a local socket needs it),
the patcher adds it with [apktool](https://apktool.org) when that's installed.

## Checks before installing

`omgi patch` checks every build and writes `omgi-diagnostics.txt` next to the
APKs; `omgi install` refuses a build without a PASSED report.

- **Protection**: a build that ships anti-tamper protection (such as Google
  Play's, `libpairipcore.so`) is refused before anything is built. Such apps
  won't run once modified or re-signed, and patching one would only cost you
  the store install.
- every original split is present, native libraries are byte-identical,
  uncompressed `.so` files sit on 16 KB boundaries
- only the expected files changed, and whether the manifest was rebuilt with
  apktool
- every APK verifies with apksigner and all share one signer

`omgi patch balatro <source> --noop` rebuilds and re-signs without injecting
anything, to test the rebuild on its own.

## Connect

- **Ophelia on Termux, same phone:** nothing to do; it connects to 127.0.0.1.
- **From a PC over ADB:** `omgi forward` (`adb forward tcp:47801 tcp:47801`);
  Ophelia does this herself when her phone body is ADB.

```bash
omgi hello
omgi state                 # readable summary; --json for the raw state
omgi act start_run stake=1
omgi act select_blind
omgi query hand_info cards=1,2,5
omgi act play_hand cards=1,2,5
omgi watch                 # phase changes as they happen
```

Debug the in-game side with `adb logcat | grep omgi` (LÖVE's `print` goes to
logcat): it logs `listening on …` at start-up and any errors.

## State

`omgi state --json` returns, depending on the phase:

| Key | What |
|-----|------|
| `phase` | `menu`, `blind_select`, `selecting_hand`, `hand_played`, `draw_to_hand`, `round_eval`, `shop`, `tarot_pack`, `planet_pack`, `spectral_pack`, `standard_pack`, `buffoon_pack`, `game_over`, … |
| `stable`, `waiting_on`, `overlay` | waiting for input / why not / a menu is open |
| `run` | ante, round, dollars, hands/discards left, hand size, max selected, slots, deck size |
| `hand` | cards in hand, left to right: rank, suit, label (`A♥`), enhancement, edition, seal, debuffed, selected; face-down cards show nothing else |
| `jokers`, `consumables` | name, kind, edition, stickers, sell value, **description with the live numbers** (from the game's own tooltip) |
| `blinds`, `on_deck` | blind select: each blind's name, chips needed, reward, status, Boss effect, skip tag |
| `blind`, `score` | the blind being played and the score so far |
| `shop` | cards, vouchers, packs with cost and `affordable`; reroll cost |
| `pack` | open booster: its cards and picks left (Tarot/Spectral packs also deal a `hand` to target) |
| `hand_levels`, `deck_remaining`, `tags`, `vouchers` | poker hand levels, counts by suit/rank of the draw pile, held tags, redeemed vouchers |
| `selected_hand` | what the currently selected cards make |
| `actions` | what can be done right now (below) |

## Actions

| Phase | Actions |
|-------|---------|
| menu / game over | `start_run(deck?, stake?, seed?)`, `continue_run` |
| blind select | `select_blind`, `skip_blind` |
| choosing a hand | `play_hand(cards)`, `discard(cards)`, `sort_hand(by)` |
| round won | `cash_out` |
| shop | `buy(index)`, `redeem_voucher(index)`, `open_pack(index)`, `reroll`, `leave_shop` |
| booster pack | `pick(index, cards?)`, `skip_pack` |
| most phases | `use_consumable(index, cards?)`, `sell_consumable(index)`, `sell_joker(index)`, `move_joker(from, to)` |
| a menu is open | `close_menu` |

Queries (read-only): `hand_info(cards)` (poker hand, level, base chips×mult,
which cards score, whether the Boss blocks it) and `deck` (the remaining draw
pile, sorted, not in draw order).

## Fair play

The link shows what's on screen and nothing more: face-down cards stay hidden,
the deck's order and upcoming shop/pack contents are never read, and seeds or
RNG state aren't exposed. Locked decks and stakes can't be started.

## Status

**The current Google Play build (1.8) ships Google Play's anti-tamper
protection**, so it can't be patched; `omgi patch` refuses it. Builds without
that protection, and the desktop game, are what this adapter can work with.

Built against Balatro's Lua globals as they are in the 1.0.x code and tested
here against a stand-in of the game (`tests/lua/fake_balatro.lua`), not yet on
a phone. The mobile build may name some UI elements differently; if an action
fails on the device, `adb logcat | grep omgi` and the error message say which
handler, and the fix is usually a one-line change in `lua/omgi/adapters/balatro.lua`.
