"""zedd's wire protocol: newline-delimited JSON over a Unix stream socket.

A client sends one request per connection:

    {"method": "plan.create", "params": {...}}

zedd answers with any number of progress events followed by exactly one
final message:

    {"event": {"type": "log", "line": "..."}}
    {"result": {...}}            or            {"error": {"code": ..., "message": ...}}
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

MAX_REQUEST_BYTES = 1 << 20


class ProtocolError(Exception):
    pass


def encode(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


def read_message(f: BinaryIO, limit: int = MAX_REQUEST_BYTES) -> dict | None:
    line = f.readline(limit + 1)
    if not line:
        return None
    if len(line) > limit:
        raise ProtocolError("message too large")
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProtocolError(f"malformed message: {e}") from e
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    return msg
