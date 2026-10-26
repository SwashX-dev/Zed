"""Client for zedd, used by the zed CLI and the Zed desktop applications."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import ZedError, from_dict
from .paths import default_paths
from .protocol import ProtocolError, encode, read_message

EventCallback = Callable[[dict], None]


class DaemonUnavailable(ZedError):
    code = "daemon_unavailable"


class Client:
    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or default_paths().socket

    def call(self, method: str, params: dict | None = None, on_event: EventCallback | None = None) -> Any:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(self.socket_path))
        except (FileNotFoundError, ConnectionRefusedError) as e:
            s.close()
            raise DaemonUnavailable(
                "the Zed management daemon (zedd) is not running",
                hint="check it with 'sudo s6-svstat /run/service/zedd'",
            ) from e
        try:
            s.sendall(encode({"method": method, "params": params or {}}))
            f = s.makefile("rb")
            while True:
                try:
                    msg = read_message(f, limit=64 << 20)
                except ProtocolError as e:
                    raise ZedError(f"bad reply from zedd: {e}") from e
                if msg is None:
                    raise ZedError("zedd closed the connection unexpectedly")
                if "event" in msg:
                    if on_event:
                        on_event(msg["event"])
                    continue
                if "error" in msg:
                    raise from_dict(msg["error"])
                return msg.get("result")
        finally:
            s.close()
