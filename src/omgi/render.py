"""Turn a game's state into compact text for a language model."""

from __future__ import annotations

import json
from typing import Any

from omgi import adapters


def format_actions(actions: list[dict[str, Any]]) -> str:
    """One line per action: name(arg, arg?) — description."""
    lines = []
    for a in actions or []:
        params = a.get("params") or {}
        props = params.get("properties") or {}
        required = set(params.get("required") or [])
        args = []
        ordered = sorted(props.items(), key=lambda kv: kv[0] not in required)
        for name, spec in ordered:
            label = name if name in required else f"{name}?"
            enum = spec.get("enum") if isinstance(spec, dict) else None
            if enum:
                label += "=" + "|".join(str(e) for e in enum[:12])
            args.append(label)
        sig = f"{a.get('name')}({', '.join(args)})"
        desc = a.get("description") or ""
        lines.append(f"- {sig} — {desc}" if desc else f"- {sig}")
    return "\n".join(lines)


def render_state(game_id: str, state: dict[str, Any]) -> str:
    adapter = adapters.get(game_id)
    if adapter and adapter.render:
        return adapter.render(state)
    body = {k: v for k, v in state.items() if k != "actions"}
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    acts = format_actions(state.get("actions") or [])
    return text + ("\nActions:\n" + acts if acts else "")
