"""Blocking client for the OMGI protocol.

    with Client(port=47801) as game:
        state = game.state()
        result, state = game.act_and_wait("play_hand", {"cards": [1, 2, 3]})

Async callers (Ophelia) run it in a thread with asyncio.to_thread.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from omgi.protocol import DEFAULT_HOST, DEFAULT_PORT, PROTOCOL_VERSION


class OMGIError(Exception):
    """An error from the game (code is one of omgi.protocol's codes) or the link."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Client:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        token: str = "",
        timeout: float = 5.0,
        subscribe: bool = False,
        client_name: str = "omgi-python",
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout
        self.subscribe = subscribe
        self.client_name = client_name
        self.hello_info: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_id = 1

    # Connection ------------------------------------------------------------

    def connect(self) -> dict[str, Any]:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise OMGIError("unreachable", f"no game listening on {self.host}:{self.port} ({e})") from e
        self._sock.settimeout(self.timeout)
        params: dict[str, Any] = {"protocol": PROTOCOL_VERSION, "client": self.client_name}
        if self.token:
            params["token"] = self.token
        if self.subscribe:
            params["subscribe"] = True
        self.hello_info = self.call("hello", **params)
        return self.hello_info

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""

    def __enter__(self) -> Client:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def game_id(self) -> str:
        return str(((self.hello_info or {}).get("game") or {}).get("id") or "")

    # Wire ------------------------------------------------------------------

    def _read_message(self, deadline: float) -> dict[str, Any]:
        assert self._sock is not None
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise OMGIError("timeout", "the game didn't answer in time")
            self._sock.settimeout(left)
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError as e:
                raise OMGIError("timeout", "the game didn't answer in time") from e
            except OSError as e:
                self.close()
                raise OMGIError("disconnected", f"link to the game dropped ({e})") from e
            if not chunk:
                self.close()
                raise OMGIError("disconnected", "the game closed the link (was it closed or restarted?)")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            raise OMGIError("bad_response", f"the game sent invalid JSON ({e})") from e
        if not isinstance(msg, dict):
            raise OMGIError("bad_response", "the game sent a non-object message")
        return msg

    def call(self, method: str, **params: Any) -> Any:
        if self._sock is None:
            if method == "hello":
                raise OMGIError("disconnected", "not connected")
            self.connect()
        assert self._sock is not None
        req_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
        try:
            self._sock.sendall(payload.encode())
        except OSError as e:
            self.close()
            raise OMGIError("disconnected", f"link to the game dropped ({e})") from e
        deadline = time.monotonic() + self.timeout
        while True:
            msg = self._read_message(deadline)
            if "event" in msg:
                self.events.append(msg)
                continue
            if msg.get("id") != req_id:
                if "error" in msg and msg.get("id") is None:
                    err = msg["error"] or {}
                    raise OMGIError(str(err.get("code", "error")), str(err.get("message", "")))
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise OMGIError(str(err.get("code", "error")), str(err.get("message", "")))
            return msg.get("result")

    # Methods ---------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return self.call("ping")

    def state(self) -> dict[str, Any]:
        return self.call("state")

    def actions(self) -> dict[str, Any]:
        return self.call("actions")

    def queries(self) -> list[dict[str, Any]]:
        return (self.call("queries") or {}).get("queries") or []

    def act(self, action: str, args: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {"action": action, "args": args or {}}
        if force:
            params["force"] = True
        return self.call("act", **params)

    def query(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call("query", query=name, args=args or {})

    def wait_stable(
        self,
        timeout: float = 20.0,
        *,
        poll: float = 0.2,
        settle: float = 0.0,
        confirmations: int = 2,
    ) -> dict[str, Any]:
        """Poll until the game is waiting for input; returns that state.

        settle waits before the first poll (right after an action, the game
        may not have started its animation yet). The game has to read stable
        `confirmations` times in a row so a one-frame gap between two
        animations isn't mistaken for the end.
        """
        if settle > 0:
            time.sleep(settle)
        deadline = time.monotonic() + timeout
        streak = 0
        state: dict[str, Any] = {}
        while True:
            state = self.state()
            streak = streak + 1 if state.get("stable") else 0
            if streak >= confirmations:
                return state
            if time.monotonic() >= deadline:
                state["timed_out"] = True
                return state
            time.sleep(poll)

    def act_and_wait(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Do an action, then wait for the game to settle. Returns (result, state)."""
        result = self.act(action, args, force=force)
        state = self.wait_stable(timeout, settle=0.35)
        return result, state
