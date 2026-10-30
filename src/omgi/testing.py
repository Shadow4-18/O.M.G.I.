"""A stand-in game server for tests: speaks the OMGI protocol from Python.

    with FakeGame(state={"phase": "shop", "stable": True}, actions=[...]) as game:
        client = Client(port=game.port)

Actions are handled by `on_act(name, args) -> (result, new_state)`; queries
by `on_query(name, args) -> result`. Raise OMGIError to answer with an error.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any

from omgi.client import OMGIError
from omgi.protocol import PROTOCOL_VERSION


class FakeGame:
    def __init__(
        self,
        *,
        game_id: str = "fake",
        state: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        on_act: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
        on_query: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        token: str = "",
    ) -> None:
        self.game_id = game_id
        self.state = dict(state or {"phase": "idle", "stable": True})
        self.actions = list(actions or [])
        self.on_act = on_act
        self.on_query = on_query
        self.token = token
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen()
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeGame:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass

    def _serve(self) -> None:
        self._srv.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _answer(self, method: str, params: dict[str, Any], authed: bool) -> Any:
        if method == "hello":
            if self.token and params.get("token") != self.token:
                raise OMGIError("unauthorized", "wrong or missing token")
            return {"protocol": PROTOCOL_VERSION, "game": {"id": self.game_id, "name": self.game_id},
                    "runtime": {"name": "fake"}, "methods": ["hello", "ping", "state", "actions", "act", "query"]}
        if self.token and not authed:
            raise OMGIError("unauthorized", "send hello with the token first")
        if method == "ping":
            return {"pong": True}
        if method == "state":
            out = dict(self.state)
            out["actions"] = self.actions if out.get("stable") else []
            return out
        if method == "actions":
            return {"phase": self.state.get("phase"), "stable": self.state.get("stable"), "actions": self.actions}
        if method == "act":
            name = params.get("action")
            if not self.state.get("stable") and not params.get("force"):
                raise OMGIError("busy", "the game is mid-animation")
            if name not in {a.get("name") for a in self.actions}:
                raise OMGIError("invalid_action", f"'{name}' isn't available")
            if not self.on_act:
                return {"ok": True}
            result, new_state = self.on_act(name, params.get("args") or {})
            self.state = new_state
            return result
        if method == "query":
            if not self.on_query:
                raise OMGIError("unknown_query", "no queries")
            return self.on_query(params.get("query"), params.get("args") or {})
        if method == "queries":
            return {"queries": []}
        raise OMGIError("unknown_method", f"no method '{method}'")

    def _client(self, conn: socket.socket) -> None:
        authed = False
        buf = b""
        with conn:
            conn.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line)
                    method = msg.get("method", "")
                    params = msg.get("params") or {}
                    self.calls.append((method, params))
                    try:
                        result = self._answer(method, params, authed)
                        if method == "hello":
                            authed = True
                        reply = {"id": msg.get("id"), "result": result}
                    except OMGIError as e:
                        reply = {"id": msg.get("id"), "error": {"code": e.code, "message": e.message}}
                    conn.sendall((json.dumps(reply) + "\n").encode())
