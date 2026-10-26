"""Fixtures: a fake Zed host under a temporary root and a fake Nix layer.

FakeNix reproduces the profile semantics the engine depends on: numbered
system-N-link symlinks, the `system` link selecting the boot default, and
/run/current-system following activation.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from zedcore.catalog import Catalog
from zedcore.errors import BuildFailed, NotFound
from zedcore.nixops import NixOps, PackageInfo
from zedcore.paths import Paths
from zedcore.registry import Registry
from zedcore.runner import Result
from zedcore.transactions import Engine

PACKAGES = {
    "firefox": {"isDerivation": True, "version": "158.0.1", "licenses": [{"spdx": "MPL-2.0", "free": True}]},
    "hello": {"isDerivation": True, "version": "2.12", "licenses": [{"spdx": "GPL-3.0", "free": True}]},
    "wireshark": {"isDerivation": True, "version": "4.4", "licenses": [{"spdx": "GPL-2.0", "free": True}]},
    "nmap": {"isDerivation": True, "version": "7.95", "licenses": [{"spdx": "NPSL", "free": True}]},
    "brokenpkg": {"isDerivation": True, "version": "1", "broken": True, "licenses": []},
    "python3Packages": {"isDerivation": False},
    "unfreepkg": {
        "isDerivation": True,
        "version": "1",
        "unfree": True,
        "licenses": [{"name": "Proprietary", "free": False}],
    },
}


class FakeNix(NixOps):
    def __init__(self, paths: Paths):
        super().__init__(runner=None)
        self.paths = paths
        self.builds: list[dict] = []
        self.fail_build = False
        self.fail_switch_for: set[str] = set()
        self.switch_calls: list[tuple[str, str]] = []
        self.upgrade_lock: str | None = None

    def package_info(self, host_dir, attr):
        if attr not in PACKAGES:
            raise NotFound(f"no package named '{attr}' in this Zed release")
        return PackageInfo(attr, dict(PACKAGES[attr]))

    def build_system(self, flake_dir, out_link, *, on_line, log, cancel):
        if on_line:
            on_line("building fake system")
        if self.fail_build:
            raise BuildFailed("building the new system failed; nothing was changed")
        state = json.loads((flake_dir / "managed" / "state.json").read_text())
        n = len(self.builds) + 100
        top = self.paths.root / "nix" / "store" / f"fake{n}-system"
        (top / "zed").mkdir(parents=True)
        (top / "zed" / "managed-state.json").write_text(json.dumps(state))
        (top / "zed" / "version").write_text("0.1.0-alpha")
        (top / "bin").mkdir()
        self.builds.append({"flake": str(flake_dir), "state": state, "toplevel": str(top)})
        out_link.parent.mkdir(parents=True, exist_ok=True)
        if out_link.is_symlink():
            out_link.unlink()
        out_link.symlink_to(top)
        return top

    def update_input(self, flake_dir, input_name, *, log, on_line):
        if self.upgrade_lock is not None:
            (flake_dir / "flake.lock").write_text(self.upgrade_lock)

    def diff_closures(self, old, new):
        return ["hello: ∅ → 2.12, +100.0 KiB"]

    def _profile_links(self):
        d = self.paths.system_profile.parent
        out = {}
        for e in d.iterdir():
            m = re.match(r"^system-(\d+)-link$", e.name)
            if m:
                out[int(m.group(1))] = e
        return out

    def set_profile(self, profile, toplevel, *, log):
        links = self._profile_links()
        n = max(links, default=0) + 1
        link = profile.parent / f"system-{n}-link"
        link.symlink_to(toplevel)
        _repoint(profile, link.name)

    def switch_generation(self, profile, number, *, log):
        if number not in self._profile_links():
            raise NotFound(f"no generation {number}")
        _repoint(profile, f"system-{number}-link")

    def rollback_profile(self, profile, *, log):
        raise AssertionError("engine should select generations explicitly")

    def switch(self, toplevel, action, *, on_line, log):
        self.switch_calls.append((str(toplevel), action))
        if action == "dry-activate":
            return Result([], 0, "would activate\n", ["would restart:", "  zed-dbus"])
        if str(toplevel) in self.fail_switch_for:
            return Result([], 4, "", ["switch-to-configuration: some services failed to start"])
        _repoint(self.paths.current_system, os.path.realpath(toplevel))
        return Result([], 0, "", [])


def _repoint(link: Path, target: str) -> None:
    tmp = link.with_name(link.name + ".tmp")
    if tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths(tmp_path)
    p.host_dir.mkdir(parents=True)
    (p.host_dir / "flake.nix").write_text("{ }\n")
    (p.host_dir / "host.nix").write_text("{ ... }: { }\n")
    p.lock_file.write_text('{"nodes": {"zed": {"locked": {"rev": "aaaa"}}}}\n')
    (p.host_dir / "managed").mkdir()
    p.system_profile.parent.mkdir(parents=True)
    p.current_system.parent.mkdir(parents=True, exist_ok=True)
    p.run_dir.mkdir(parents=True, exist_ok=True)
    # Generation 1: an empty managed state, booted and running.
    top = tmp_path / "nix" / "store" / "fake1-system"
    (top / "zed").mkdir(parents=True)
    (top / "zed" / "managed-state.json").write_text(
        '{"version": 1, "packages": [], "collections": [], "settings": {}}'
    )
    (top / "bin").mkdir()
    link = p.system_profile.parent / "system-1-link"
    link.symlink_to(top)
    p.system_profile.symlink_to("system-1-link")
    p.current_system.symlink_to(top)
    p.booted_system.symlink_to(top)
    return p


@pytest.fixture
def registry() -> Registry:
    return Registry.load()


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture
def nix(paths: Paths) -> FakeNix:
    return FakeNix(paths)


@pytest.fixture
def engine(paths, registry, catalog, nix) -> Engine:
    return Engine(paths, registry, catalog, nix)
