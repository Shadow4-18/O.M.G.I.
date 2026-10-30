# Writing an adapter

An adapter is everything one game needs: the code that runs inside the game,
the patcher that puts it there, and a renderer that turns its state into text.

```
src/omgi/adapters/<game>/
  README.md        install guide + what state/actions it exposes
  patch.py         puts the mod into the game's APK
  render.py        state dict -> compact text for the agent
  lua/omgi/adapters/<game>.lua   (LÖVE games) the in-game half
```

Register it in `src/omgi/adapters/__init__.py` (id, engine, default port,
package names, renderer). Give each game its own port.

## LÖVE games

The runtime in `src/omgi/runtimes/love/omgi/` does the protocol, socket and
frame hook. An adapter module returns a table with:

| Function | Returns |
|----------|---------|
| `info()` | `{id, name, version}` |
| `phase()` | `phase, stable` — called every frame, keep it cheap |
| `snapshot()` | the full state table |
| `actions()` | list of actions valid now |
| `act(name, args)` | result table, or `nil, {code, message}` |
| `queries()` / `query(name, args)` | optional read-only extras |

Rules of thumb:

- **Act through the game's own handlers** (the functions its buttons call),
  not by editing state, so its rules, animations and saves stay correct.
- **Validate everything** and answer with a clear `bad_params` / `not_allowed`
  message; an LLM reads those and corrects itself.
- **Never crash the game.** The runtime wraps calls in `pcall`, but avoid
  anything that could throw inside the game's own code paths.
- **Fair play.** Report what's visible on screen, nothing more.
- **Test it** with a fake of the game's globals under LuaJIT, like
  `tests/lua/fake_balatro.lua`, before trying it on the phone.

## Other engines

- **Godot**: GDScript mods can be loaded from a `.pck` patch; the runtime would
  be a GDScript port of `server.lua`.
- **Unity Mono**: managed DLLs can be patched (BepInEx-style); the runtime is a
  C# TCP server on the main thread.
- **Unity IL2CPP / native**: no source to patch; plan is a Frida gadget that
  hooks known functions and speaks the same protocol.

Whatever the engine, the protocol in [protocol.md](protocol.md) stays the same,
so Ophelia needs no changes to play a new game, only a new renderer.
