#!/usr/bin/env python3
"""Calamares job: write the Zed host flake and run nixos-install.

Runs after Calamares has partitioned and mounted the target at
rootMountPoint.  The user's password is set afterwards by Calamares' own
`users` job; it is never written into the Nix store.
"""

import re
import subprocess
from pathlib import Path

import libcalamares
from zedcore.catalog import Catalog
from zedcore.registry import Registry
from zedinstaller import calamares as zc
from zedinstaller import gpu as gpumod
from zedinstaller import hostgen


def pretty_name():
    return "Installing Zed"


class _GS:
    def get(self, key, default=None):
        gs = libcalamares.globalstorage
        return gs.value(key) if gs.contains(key) else default


_PROGRESS = [
    (re.compile(r"^building the system configuration"), 0.15),
    (re.compile(r"^copying channel|^copying path"), 0.35),
    (re.compile(r"^installing the boot loader"), 0.85),
    (re.compile(r"^setting up /etc"), 0.9),
    (re.compile(r"^installation finished"), 1.0),
]


def run():
    gs = _GS()
    root = Path(gs.get("rootMountPoint") or "")
    if not root.is_dir():
        return ("Installation target missing", f"{root} is not mounted")

    sel, want_nvidia = zc.selections(gs)
    plan = zc.gpu_plan(sel, want_nvidia)
    registry = Registry.load()
    catalog = Catalog.load()
    libcalamares.job.setprogress(0.02)

    # Hardware configuration for the mounted target.
    try:
        hw = subprocess.run(
            ["nixos-generate-config", "--root", str(root), "--show-hardware-config"],
            check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        return ("Could not detect the hardware configuration", e.stderr[-2000:])

    files = hostgen.host_files(sel, plan, zc.source(), registry, catalog)
    files["hardware-configuration.nix"] = hw
    data = zc.data_mounts(gs.get("partitions") or [])
    extra = zc.data_mounts_nix(data)
    if extra:
        files["data-mounts.nix"] = extra
        files["host.nix"] = files["host.nix"].replace(
            "imports = [ ./zed-hardware.nix ];", "imports = [ ./zed-hardware.nix ./data-mounts.nix ];"
        )
    zc.write_files(root, files)
    readme = root / "etc" / "nixos" / "README"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("This Zed system is configured in /etc/zed, not here.\n")
    libcalamares.job.setprogress(0.08)

    cmd = [
        "nixos-install",
        "--root", str(root),
        "--flake", f"path:{root}/etc/zed#zed",
        "--no-root-passwd",
        "--no-channel-copy",
        "--option", "experimental-features", "nix-command flakes",
    ]
    if not gs.get("zedOnline", False):
        cmd += ["--option", "substituters", ""]
    libcalamares.utils.debug("zedinstall: " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        libcalamares.utils.debug(line)
        tail = (tail + [line])[-60:]
        for pat, frac in _PROGRESS:
            if pat.search(line):
                libcalamares.job.setprogress(frac)
    if proc.wait() != 0:
        return ("nixos-install failed", "<pre>" + "\n".join(tail) + "</pre>")

    libcalamares.globalstorage.insert("zedInstalled", True)
    libcalamares.job.setprogress(1.0)
    return None
