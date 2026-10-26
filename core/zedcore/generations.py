"""System generations, read from the profile links in /nix/var/nix/profiles.

Every Zed generation carries a copy of the managed state it was built from
(zed/managed-state.json in the system toplevel), so a rollback can restore
the matching state file and generations can be compared package by package.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .paths import Paths

_LINK = re.compile(r"^system-(\d+)-link$")


@dataclass
class Generation:
    number: int
    path: Path
    created: datetime
    current: bool
    booted: bool
    default: bool

    def read(self, rel: str) -> str | None:
        try:
            return (self.path / rel).read_text().strip()
        except OSError:
            return None

    @property
    def nixos_version(self) -> str | None:
        return self.read("nixos-version")

    @property
    def zed_version(self) -> str | None:
        return self.read("zed/version")

    @property
    def kernel_version(self) -> str | None:
        try:
            return os.readlink(self.path / "kernel").split("-linux-")[-1].split("/")[0]
        except OSError:
            return None

    def managed_state(self) -> dict | None:
        text = self.read("zed/managed-state.json")
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def to_dict(self) -> dict:
        state = self.managed_state()
        return {
            "number": self.number,
            "path": str(self.path),
            "created": self.created.isoformat(),
            "current": self.current,
            "booted": self.booted,
            "default": self.default,
            "nixosVersion": self.nixos_version,
            "zedVersion": self.zed_version,
            "kernel": self.kernel_version,
            "packages": state.get("packages") if state else None,
            "collections": state.get("collections") if state else None,
        }


def _resolve(p: Path) -> Path | None:
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


def list_generations(paths: Paths) -> list[Generation]:
    profile_dir = paths.system_profile.parent
    if not profile_dir.is_dir():
        return []
    current = _resolve(paths.current_system)
    booted = _resolve(paths.booted_system)
    default = _resolve(paths.system_profile)
    gens = []
    for entry in profile_dir.iterdir():
        m = _LINK.match(entry.name)
        if not m:
            continue
        target = _resolve(entry)
        if target is None:
            continue
        try:
            created = datetime.fromtimestamp(entry.lstat().st_mtime, tz=UTC)
        except OSError:
            continue
        gens.append(
            Generation(
                number=int(m.group(1)),
                path=target,
                created=created,
                current=target == current,
                booted=target == booted,
                default=target == default,
            )
        )
    return sorted(gens, key=lambda g: g.number)


def find(paths: Paths, number: int) -> Generation | None:
    return next((g for g in list_generations(paths) if g.number == number), None)


def default_generation(paths: Paths) -> Generation | None:
    return next((g for g in list_generations(paths) if g.default), None)
