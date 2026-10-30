"""Wire protocol shared by every OMGI integration.

Newline-delimited JSON over TCP. The client sends requests

    {"id": 1, "method": "state", "params": {}}

and the game answers each one with either

    {"id": 1, "result": {...}}
    {"id": 1, "error": {"code": "busy", "message": "..."}}

A client that said ``hello`` with ``subscribe: true`` also receives events,
which carry no id:

    {"event": "phase", "data": {"phase": "shop", "stable": true}}

See docs/protocol.md for the methods and the shape of state.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47800

# Error codes a game may return.
BAD_REQUEST = "bad_request"
BAD_PARAMS = "bad_params"
BUSY = "busy"
FAILED = "failed"
INTERNAL = "internal"
INVALID_ACTION = "invalid_action"
NOT_ALLOWED = "not_allowed"
UNAUTHORIZED = "unauthorized"
UNKNOWN_METHOD = "unknown_method"
UNKNOWN_QUERY = "unknown_query"
