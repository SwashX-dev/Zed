"""Authorisation of zedd clients.

The caller's identity comes from the kernel (SO_PEERCRED on the Unix
socket), never from the request.  Policy:

  read methods    any local user
  admin methods   uid 0, or members of the configured admin group

The admin group is unset by default, so ordinary users obtain admin rights
by running the client through sudo, which performs the authentication.
zedd never receives or checks passwords.
"""

from __future__ import annotations

import grp
import pwd
import socket
import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import PermissionDenied


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int

    @property
    def user(self) -> str:
        try:
            return pwd.getpwuid(self.uid).pw_name
        except KeyError:
            return str(self.uid)


def peer_of(sock: socket.socket) -> Peer:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", creds)
    return Peer(pid=pid, uid=uid, gid=gid)


def _process_groups(pid: int, proc: Path = Path("/proc")) -> set[int]:
    """Supplementary groups of the connecting process, as the kernel sees them."""
    try:
        for line in (proc / str(pid) / "status").read_text().splitlines():
            if line.startswith("Groups:"):
                return {int(g) for g in line.split()[1:]}
    except (OSError, ValueError):
        pass
    return set()


class Policy:
    def __init__(self, admin_group: str | None = None, proc: Path = Path("/proc")):
        self.admin_group = admin_group
        self.proc = proc

    def _admin_gid(self) -> int | None:
        if not self.admin_group:
            return None
        try:
            return grp.getgrnam(self.admin_group).gr_gid
        except KeyError:
            return None

    def is_admin(self, peer: Peer) -> bool:
        if peer.uid == 0:
            return True
        gid = self._admin_gid()
        if gid is None:
            return False
        return gid == peer.gid or gid in _process_groups(peer.pid, self.proc)

    def require_admin(self, peer: Peer, what: str) -> None:
        if not self.is_admin(peer):
            raise PermissionDenied(
                f"{what} requires administrator rights",
                hint="run the command with sudo, for example 'sudo zed install NAME'",
            )
