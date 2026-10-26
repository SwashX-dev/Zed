"""Filesystem locations used by zedd.

ZED_ROOT prefixes every path so the unit tests can run the transaction
engine against a temporary directory.  It is never set on a real system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path

    def _p(self, rel: str) -> Path:
        return self.root / rel.lstrip("/")

    # The machine's flake.  host.nix and hardware-configuration.nix belong to
    # the user; managed/ belongs to zedd.
    @property
    def host_dir(self) -> Path:
        return self._p("/etc/zed")

    @property
    def state_file(self) -> Path:
        return self.host_dir / "managed" / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.host_dir / "flake.lock"

    @property
    def run_dir(self) -> Path:
        return self._p("/run/zed")

    @property
    def socket(self) -> Path:
        return self.run_dir / "zedd.sock"

    @property
    def plans_dir(self) -> Path:
        return self.run_dir / "plans"

    @property
    def var_dir(self) -> Path:
        return self._p("/var/lib/zed")

    @property
    def history_file(self) -> Path:
        return self.var_dir / "history.jsonl"

    @property
    def logs_dir(self) -> Path:
        return self._p("/var/log/zed/transactions")

    @property
    def cache_dir(self) -> Path:
        return self._p("/var/cache/zed")

    @property
    def system_profile(self) -> Path:
        return self._p("/nix/var/nix/profiles/system")

    @property
    def current_system(self) -> Path:
        return self._p("/run/current-system")

    @property
    def booted_system(self) -> Path:
        return self._p("/run/booted-system")

    @property
    def boot_status(self) -> Path:
        return self.run_dir / "boot-status"

    @property
    def reboot_required(self) -> Path:
        return self.run_dir / "reboot-required"

    @property
    def live_marker(self) -> Path:
        # Present only in the live ISO environment.
        return self._p("/etc/zed-live")


def default_paths() -> Paths:
    return Paths(Path(os.environ.get("ZED_ROOT", "/")))
