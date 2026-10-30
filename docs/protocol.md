# OMGI protocol (version 1)

Transport: TCP, one JSON object per line (UTF-8, `\n`-terminated). The game
listens on `127.0.0.1` on the phone; a PC reaches it with `adb forward
tcp:<port> tcp:<port>`, Termux on the phone connects directly.

## Messages

Request (client → game):

```json
{"id": 7, "method": "act", "params": {"action": "play_hand", "args": {"cards": [1, 2]}}}
```

Response — exactly one per request, same `id`:

```json
{"id": 7, "result": {"ok": true, "played": ["A♥", "A♠"], "hand": "Pair"}}
{"id": 7, "error": {"code": "bad_params", "message": "card 9 is out of range (1-8)"}}
```

Event (game → client, no `id`), only to clients that said `hello` with
`subscribe: true`:

```json
{"event": "phase", "data": {"phase": "shop", "stable": true, "frame": 9120}}
```

## Methods

| Method | Params | Result |
|--------|--------|--------|
| `hello` | `protocol`, `client`, `token?`, `subscribe?` | `{protocol, game: {id, name, version}, runtime, methods}` |
| `ping` | — | `{pong: true}` |
| `state` | — | full state (below) including `actions` |
| `actions` | — | `{phase, stable, actions}` |
| `act` | `action`, `args`, `force?` | adapter-specific result, usually `{ok, message?}` |
| `queries` | — | `{queries: [...]}` same shape as actions |
| `query` | `query`, `args` | adapter-specific, read-only |

If the mod was patched with a token, `hello` must carry it before anything else
works (`unauthorized` otherwise).

## State

Every adapter's state has at least:

```json
{"phase": "selecting_hand", "stable": true, "waiting_on": null, "actions": [...]}
```

- `phase` — the game's current screen/state, lowercase.
- `stable` — the game is waiting for input (no animation running). `actions` is
  empty while it's false and `act` answers `busy`. Clients wait for stable
  after each action (`Client.act_and_wait` polls until it reads stable twice).
- `waiting_on` — why it isn't stable, for humans.

Everything else is per game; see the adapter's README.

## Actions

```json
{"name": "play_hand",
 "description": "Play 1-5 cards from your hand",
 "params": {"type": "object",
            "properties": {"cards": {"type": "array", "items": {"type": "integer"}}},
            "required": ["cards"]}}
```

`params` is a JSON-Schema object, so it can be handed to an LLM as a tool
schema as-is. Only actions valid *right now* are listed; `act` with anything
else answers `invalid_action`. Positions are 1-based, left to right as on screen.

## Error codes

| Code | Meaning |
|------|---------|
| `busy` | Game is animating; wait for `stable` |
| `invalid_action` | Not available in this phase |
| `bad_params` | Args wrong (out of range, wrong type…) |
| `not_allowed` | Valid but the game wouldn't let you (can't afford, no slot…) |
| `failed` | The game refused or the adapter couldn't find what it needed |
| `unauthorized` | Missing/wrong token |
| `unknown_method`, `unknown_query`, `bad_request`, `internal` | Protocol-level problems |

Client-side only (`omgi.Client`): `unreachable`, `timeout`, `disconnected`, `bad_response`.
