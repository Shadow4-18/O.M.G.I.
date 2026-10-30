"""Registry of game integrations O.M.G.I. knows about."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Adapter:
    id: str
    name: str
    engine: str
    default_port: int
    packages: tuple[str, ...] = ()
    render: Callable[[dict[str, Any]], str] | None = field(default=None, compare=False)
    notes: str = ""


def _balatro() -> Adapter:
    from omgi.adapters.balatro.render import render

    return Adapter(
        id="balatro",
        name="Balatro",
        engine="love",
        default_port=47801,
        packages=("com.playstack.balatro.android",),
        render=render,
        notes="LÖVE mod injected into assets/game.love; `omgi patch balatro`.",
    )


_FACTORIES = {"balatro": _balatro}
_CACHE: dict[str, Adapter] = {}


def get(adapter_id: str) -> Adapter | None:
    key = (adapter_id or "").strip().lower()
    if key not in _FACTORIES:
        return None
    if key not in _CACHE:
        _CACHE[key] = _FACTORIES[key]()
    return _CACHE[key]


def all_adapters() -> list[Adapter]:
    return [a for a in (get(k) for k in _FACTORIES) if a]


def for_package(package: str) -> Adapter | None:
    for a in all_adapters():
        if package in a.packages:
            return a
    return None
