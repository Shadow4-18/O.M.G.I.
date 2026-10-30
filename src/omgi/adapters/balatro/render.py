"""Balatro state -> short text an agent can read at a glance."""

from __future__ import annotations

from typing import Any

from omgi.render import format_actions


def _money(v: Any) -> str:
    return f"${v}" if v is not None else "$?"


def _num(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int) and abs(v) >= 10_000:
        return f"{v:,}"
    return str(v) if v is not None else "?"


def _tags(c: dict[str, Any]) -> str:
    bits = []
    for key in ("enhancement", "edition", "seal"):
        if c.get(key):
            bits.append(str(c[key]) + (" seal" if key == "seal" else ""))
    bits.extend(c.get("stickers") or [])
    if c.get("debuffed"):
        bits.append("debuffed")
    return f" [{', '.join(bits)}]" if bits else ""


def _playing_card(c: dict[str, Any]) -> str:
    if c.get("face_down"):
        return f"{c['index']}) (face down)"
    mark = "*" if c.get("selected") else ""
    return f"{c['index']}) {c.get('label', '?')}{mark}{_tags(c)}"


def _item(c: dict[str, Any], *, price: bool = False, sell: bool = False) -> str:
    if c.get("kind") == "Playing card":
        line = _playing_card(c)
    else:
        line = f"{c['index']}) {c.get('name') or c.get('key')}"
        if c.get("kind") and c.get("kind") != "Joker":
            line += f" ({c['kind']})"
        line += _tags(c)
    if price:
        line += f" {_money(c.get('cost'))}" + ("" if c.get("affordable", True) else " (can't afford)")
    if sell and c.get("sell_value") is not None:
        line += f" [sells {_money(c['sell_value'])}]"
    if c.get("description"):
        line += f" — {c['description']}"
    return line


def _section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    return [title] + [f"  {i}" for i in items]


def render(state: dict[str, Any]) -> str:
    phase = state.get("phase", "?")
    head = f"Balatro — {phase.replace('_', ' ')}"
    if not state.get("stable"):
        head += f" (busy: {state.get('waiting_on') or 'animating'}; wait and look again)"
    if state.get("overlay"):
        head += " · a menu/popup is open"
    lines = [head]

    run = state.get("run")
    if run:
        lines.append(
            f"Ante {run.get('ante')} · Round {run.get('round')} · {_money(run.get('dollars'))} · "
            f"Hands {run.get('hands_left')} · Discards {run.get('discards_left')} · "
            f"Deck {run.get('deck_remaining')}/{run.get('deck_total')}"
        )

    blind = state.get("blind")
    if blind:
        line = f"Blind: {blind.get('name')} — score {_num(state.get('score'))} / {_num(blind.get('chips_needed'))}"
        if blind.get("description"):
            line += f" — {blind['description']}"
        lines.append(line)

    blind_lines = []
    for b in state.get("blinds") or []:
        mark = " ← up next" if b.get("kind") == state.get("on_deck") else ""
        line = (f"{b.get('kind')}: {b.get('name')} needs {_num(b.get('chips_needed'))}, "
                f"reward {_money(b.get('reward'))} [{b.get('status')}]{mark}")
        if b.get("description") and b.get("kind") == "Boss":
            line += f" — {b['description']}"
        tag = b.get("skip_tag")
        if tag:
            line += f" · skip for {tag.get('name')}" + (f" ({tag['description']})" if tag.get("description") else "")
        blind_lines.append(line)
    lines += _section("Blinds:", blind_lines)

    if run:
        lines += _section(
            f"Jokers ({len(state.get('jokers') or [])}/{run.get('joker_slots')}):",
            [_item(c, sell=True) for c in state.get("jokers") or []],
        )
        lines += _section(
            f"Consumables ({len(state.get('consumables') or [])}/{run.get('consumable_slots')}):",
            [_item(c, sell=True) for c in state.get("consumables") or []],
        )

    hand = state.get("hand") or []
    if hand:
        lines.append(f"Hand (select up to {run.get('max_selected') if run else 5}; * = selected):")
        lines.append("  " + "  ".join(_playing_card(c) for c in hand))
        described = [c for c in hand if c.get("description")]
        for c in described:
            lines.append(f"    {c['index']}) {c.get('label')}: {c['description']}")
    sel = state.get("selected_hand")
    if sel and sel.get("hand"):
        lines.append(f"Selected makes: {sel['hand']} (lvl {sel.get('level')}, {sel.get('chips')}×{sel.get('mult')})")

    shop = state.get("shop")
    if shop:
        lines.append(f"Shop (reroll {_money(shop.get('reroll_cost'))}"
                     + (f", {shop['free_rerolls']} free" if shop.get("free_rerolls") else "") + "):")
        lines += [f"  {_item(c, price=True)}" for c in shop.get("cards") or []]
        lines += _section(" Vouchers:", [_item(c, price=True) for c in shop.get("vouchers") or []])
        lines += _section(" Packs:", [_item(c, price=True) for c in shop.get("packs") or []])

    pack = state.get("pack")
    if pack:
        lines.append(f"Open pack ({pack.get('kind', '').replace('_', ' ')}, picks left {pack.get('picks_left')}):")
        lines += [f"  {_item(c)}" for c in pack.get("cards") or []]

    levels = [h for h in state.get("hand_levels") or [] if (h.get("level") or 1) > 1]
    if levels:
        lines.append("Leveled hands: " + ", ".join(
            f"{h['name']} lvl {h['level']} ({h.get('chips')}×{h.get('mult')})" for h in levels))
    if state.get("tags"):
        lines.append("Tags: " + ", ".join(t.get("name") or t.get("key") for t in state["tags"]))
    if state.get("vouchers"):
        lines.append("Vouchers: " + ", ".join(state["vouchers"]))

    acts = state.get("actions") or []
    if acts:
        lines.append("Actions (positions are 1-based, left to right):")
        lines.append(format_actions(acts))
    elif state.get("stable"):
        lines.append("No actions available here.")
    return "\n".join(lines)
