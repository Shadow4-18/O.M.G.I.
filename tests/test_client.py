from __future__ import annotations

import pytest

from omgi import Client, OMGIError, render_state
from omgi.cli import parse_args_kv
from omgi.testing import FakeGame

ACTIONS = [{"name": "poke", "description": "Poke it",
            "params": {"type": "object", "properties": {"times": {"type": "integer"}}, "required": ["times"]}}]


def test_round_trip_and_wait():
    def on_act(name, args):
        return {"ok": True, "times": args["times"]}, {"phase": "poked", "stable": True}

    with FakeGame(state={"phase": "idle", "stable": True}, actions=ACTIONS, on_act=on_act) as game:
        with Client(port=game.port) as c:
            assert c.game_id == "fake"
            result, state = c.act_and_wait("poke", {"times": 2})
            assert result == {"ok": True, "times": 2}
            assert state["phase"] == "poked"


def test_errors_map_to_exceptions():
    with FakeGame(state={"phase": "x", "stable": False}, actions=ACTIONS) as game:
        with Client(port=game.port) as c:
            with pytest.raises(OMGIError) as e:
                c.act("poke")
            assert e.value.code == "busy"
            s = c.wait_stable(timeout=0.3, poll=0.05)
            assert s["timed_out"] is True


def test_token():
    with FakeGame(token="sekret") as game:
        with pytest.raises(OMGIError) as e:
            Client(port=game.port).connect()
        assert e.value.code == "unauthorized"
        with Client(port=game.port, token="sekret") as c:
            assert c.ping()["pong"] is True


def test_unreachable():
    with pytest.raises(OMGIError) as e:
        Client(port=1, timeout=0.5).connect()
    assert e.value.code == "unreachable"


def test_generic_render_lists_actions():
    text = render_state("unknown-game", {"phase": "idle", "stable": True, "actions": ACTIONS})
    assert '"phase":"idle"' in text
    assert "- poke(times) — Poke it" in text


def test_parse_args_kv():
    assert parse_args_kv(["cards=1,2,3", "by=suit", "index=2"]) == {"cards": [1, 2, 3], "by": "suit", "index": 2}
    assert parse_args_kv(["cards=4"]) == {"cards": [4]}
    assert parse_args_kv(['seed="0042"', "x=[1, 2]"]) == {"seed": "0042", "x": [1, 2]}
