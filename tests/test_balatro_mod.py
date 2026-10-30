"""Drive the real Lua mod (running in a fake Balatro) through a round."""

from __future__ import annotations

import pytest

from omgi import Client, OMGIError, render_state


def names(state):
    return [a["name"] for a in state.get("actions") or []]


def test_hello_and_menu(fake_balatro):
    with Client(port=fake_balatro) as c:
        assert c.hello_info["protocol"] == 1
        assert c.game_id == "balatro"
        assert "query" in c.hello_info["methods"]
        s = c.wait_stable()
        assert s["phase"] == "menu"
        assert "start_run" in names(s)
        start = next(a for a in s["actions"] if a["name"] == "start_run")
        assert start["params"]["properties"]["deck"]["enum"] == ["b_red"]  # blue is locked


def test_full_round(fake_balatro):
    with Client(port=fake_balatro) as c:
        c.wait_stable()
        _, s = c.act_and_wait("start_run", {"stake": 1})
        assert s["phase"] == "blind_select"
        assert [b["kind"] for b in s["blinds"]] == ["Small", "Big", "Boss"]
        assert s["blinds"][2]["description"] == "Discards 2 random cards per hand played"
        assert s["blinds"][0]["skip_tag"]["name"] == "Economy Tag"
        assert {"select_blind", "skip_blind", "use_consumable"} <= set(names(s))

        _, s = c.act_and_wait("select_blind")
        assert s["phase"] == "selecting_hand"
        assert len(s["hand"]) == 8
        assert s["hand"][0]["label"] == "2♠"
        assert s["blind"]["chips_needed"] == 300
        assert s["run"]["hands_left"] == 4

        info = c.query("hand_info", {"cards": [1, 2]})
        assert info["hand"] == "High Card" and info["scoring_cards"] == [1]

        result, s = c.act_and_wait("discard", {"cards": [1, 2]})
        assert result["discarded"] == ["2♠", "3♠"]
        assert s["run"]["discards_left"] == 2
        assert len(s["hand"]) == 8

        result, s = c.act_and_wait("play_hand", {"cards": [1]})
        assert result["played"] == ["4♠"] and result["hand"] == "High Card"
        assert s["phase"] == "selecting_hand" and s["score"] == 40
        assert s["run"]["hands_left"] == 3
        _, s = c.act_and_wait("play_hand", {"cards": [4, 5, 6, 7, 8]})
        assert s["phase"] == "round_eval", s
        assert names(s)[0] == "cash_out"

        _, s = c.act_and_wait("cash_out")
        assert s["phase"] == "shop"
        assert s["run"]["dollars"] == 9
        cards = s["shop"]["cards"]
        assert cards[0]["name"] == "Joker" and cards[0]["description"] == "+4 Mult"
        assert cards[1]["affordable"] is False

        with pytest.raises(OMGIError) as e:
            c.act("buy", {"index": 2})
        assert e.value.code == "not_allowed"

        _, s = c.act_and_wait("buy", {"index": 1})
        assert [j["name"] for j in s["jokers"]] == ["Joker"]
        assert s["run"]["dollars"] == 4

        text = render_state("balatro", s)
        assert "Jokers (1/5):" in text and "buy(index)" in text

        _, s = c.act_and_wait("leave_shop")
        assert s["phase"] == "blind_select" and s["on_deck"] == "Big"


def test_errors(fake_balatro):
    with Client(port=fake_balatro) as c:
        c.wait_stable()
        with pytest.raises(OMGIError) as e:
            c.act("play_hand", {"cards": [1]})
        assert e.value.code == "invalid_action"

        c.act("start_run", {})
        with pytest.raises(OMGIError) as e:   # still animating
            c.act("select_blind")
        assert e.value.code == "busy"

        c.wait_stable()
        c.act_and_wait("select_blind")
        for bad in ({"cards": []}, {"cards": [1, 1]}, {"cards": [99]}, {"cards": [1, 2, 3, 4, 5, 6]}, {"cards": "1"}):
            with pytest.raises(OMGIError) as e:
                c.act("play_hand", bad)
            assert e.value.code == "bad_params", bad
        with pytest.raises(OMGIError) as e:
            c.call("nope")
        assert e.value.code == "unknown_method"


def test_skip_blind_and_consumable(fake_balatro):
    with Client(port=fake_balatro) as c:
        c.wait_stable()
        c.act_and_wait("start_run", {})
        _, s = c.act_and_wait("skip_blind")
        assert s["on_deck"] == "Big"
        assert s["tags"][0]["name"] == "Economy Tag"
        _, s = c.act_and_wait("use_consumable", {"index": 1})
        assert s["consumables"] == []
        assert next(h for h in s["hand_levels"] if h["name"] == "High Card")["level"] == 2


def test_subscribe_gets_phase_events(fake_balatro):
    c = Client(port=fake_balatro, subscribe=True)
    with c:
        c.wait_stable()
        c.act_and_wait("start_run", {})
        phases = [e["data"]["phase"] for e in c.events if e.get("event") == "phase"]
        assert "blind_select" in phases
