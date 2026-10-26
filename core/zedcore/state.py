"""The managed state: the part of a Zed host's configuration owned by zedd.

It is plain JSON at /etc/zed/managed/state.json:

    {
      "version": 1,
      "packages": ["firefox", "wireshark"],
      "collections": ["forensics"],
      "settings": {"firewall.allowedTCPPorts": [22]}
    }

nix/modules/managed.nix turns it into NixOS option definitions.  Users'
own configuration lives in host.nix, which zedd never writes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import names
from .errors import InvalidInput
from .registry import Registry
import contextlib

SCHEMA_VERSION = 1


@dataclass
class ManagedState:
    packages: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    # --- serialisation -------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "packages": sorted(set(self.packages)),
            "collections": sorted(set(self.collections)),
            "settings": {k: self.settings[k] for k in sorted(self.settings)},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def copy(self) -> ManagedState:
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, d: dict, registry: Registry, *, zoneinfo_dir: Path | None = None) -> ManagedState:
        """Parse and fully validate state, as read from disk or a Forge profile."""
        if not isinstance(d, dict):
            raise InvalidInput("managed state must be a JSON object")
        version = d.get("version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise InvalidInput(f"unsupported managed-state version {version!r}")
        unknown = set(d) - {"version", "packages", "collections", "settings"}
        if unknown:
            raise InvalidInput(f"unknown keys in managed state: {', '.join(sorted(unknown))}")
        pkgs = d.get("packages", [])
        cols = d.get("collections", [])
        sets = d.get("settings", {})
        if not isinstance(pkgs, list) or not isinstance(cols, list) or not isinstance(sets, dict):
            raise InvalidInput("managed state has the wrong shape")
        state = cls(
            packages=sorted({names.package_attr(p) for p in pkgs}),
            collections=sorted({names.collection_id(c) for c in cols}),
            settings={},
        )
        for k, v in sets.items():
            state.settings[k] = registry.validate(k, v, zoneinfo_dir=zoneinfo_dir)
        return state

    # --- mutations -----------------------------------------------------

    def add_packages(self, attrs: list[str]) -> list[str]:
        added = [a for a in attrs if a not in self.packages]
        self.packages = sorted(set(self.packages) | set(attrs))
        return added

    def remove_packages(self, attrs: list[str]) -> list[str]:
        removed = [a for a in attrs if a in self.packages]
        self.packages = sorted(set(self.packages) - set(attrs))
        return removed

    def add_collections(self, ids: list[str]) -> list[str]:
        added = [c for c in ids if c not in self.collections]
        self.collections = sorted(set(self.collections) | set(ids))
        return added

    def remove_collections(self, ids: list[str]) -> list[str]:
        removed = [c for c in ids if c in self.collections]
        self.collections = sorted(set(self.collections) - set(ids))
        return removed

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value

    def reset_setting(self, key: str) -> bool:
        return self.settings.pop(key, None) is not None


def load(path: Path, registry: Registry, *, zoneinfo_dir: Path | None = None) -> ManagedState:
    if not path.exists():
        return ManagedState()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise InvalidInput(
            f"cannot read {path}: {e}",
            hint="restore it from the running generation with 'zed doctor --repair'",
        ) from e
    return ManagedState.from_dict(data, registry, zoneinfo_dir=zoneinfo_dir)


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Replace `path` with `text` so readers see either the old or the new file.

    The data is fsynced before the rename and the directory after it, so a
    crash or power loss never leaves a truncated state file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    dfd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
