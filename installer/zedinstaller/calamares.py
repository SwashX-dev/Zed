"""Glue between Calamares' global storage and the installer logic.

Kept free of `libcalamares` so it can be unit-tested: the Calamares job
modules (installer/calamares/modules/zed*/main.py) pass in a dict-like view
of global storage.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from . import gpu as gpumod
from .hostgen import Selections, ZedSource

SOURCE_FILE = Path("/etc/zed-installer/source.json")
MEDIUM_PACKAGES = Path("/etc/zed-installer/packages.json")

# Mount points that belong to the system itself.  Any other mount point the
# user assigns (an existing data partition, a Windows drive) is mounted with
# `nofail`, so a missing or failing data disk never stops the boot.
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/nix", "/var", "/tmp", "/usr"}


def _choices(gs, key: str) -> list[str]:
    v = gs.get(key)
    if not v:
        return []
    if isinstance(v, str):
        return [x for x in v.split(",") if x]
    return [str(x) for x in v]


def selections(gs) -> tuple[Selections, bool]:
    """Selections and whether the user asked for the proprietary NVIDIA driver."""
    extras = _choices(gs, "packagechooser_extras")
    profile = (_choices(gs, "packagechooser_profile") or ["desktop"])[0]
    locale = None
    lc = gs.get("localeConf") or {}
    if isinstance(lc, dict) and lc.get("LANG"):
        locale = lc["LANG"]
    tz = None
    if gs.get("locationRegion") and gs.get("locationZone"):
        tz = f"{gs.get('locationRegion')}/{gs.get('locationZone')}"
    firmware = gs.get("firmwareType") or "efi"
    boot = gs.get("bootLoader") or {}
    sel = Selections(
        username=gs.get("username") or "",
        fullname=gs.get("fullname") or "",
        hostname=gs.get("hostname") or "zed",
        timezone=tz,
        locale=locale,
        keyboard_layout=gs.get("keyboardLayout"),
        keyboard_variant=gs.get("keyboardVariant") or None,
        console_keymap=(gs.get("keyboardVConsoleKeymap") or "").strip() or None,
        profile=profile,
        sessions=_choices(gs, "packagechooser_desktops"),
        extra_collections=[e.split(":", 1)[1] for e in extras if e.startswith("collection:")],
        extra_packages=[e.split(":", 1)[1] for e in extras if e.startswith("pkg:")],
        allow_unfree="opt:unfree" in extras,
        firmware="bios" if firmware == "bios" else "efi",
        efi_mount="/boot",
        bios_device=boot.get("installPath") if isinstance(boot, dict) else None,
    )
    return sel, "opt:nvidia" in extras


def online(timeout: float = 4.0) -> bool:
    """Whether the binary cache is reachable."""
    try:
        with socket.create_connection(("cache.nixos.org", 443), timeout=timeout):
            return True
    except OSError:
        return False


def memory_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def gpu_plan(sel: Selections, want_nvidia: bool, sysfs: Path = Path("/sys")) -> gpumod.GpuPlan:
    return gpumod.plan(gpumod.detect(sysfs), allow_unfree=sel.allow_unfree, want_proprietary_nvidia=want_nvidia)


def source() -> ZedSource:
    return ZedSource.from_dict(json.loads(SOURCE_FILE.read_text()))


def medium_packages() -> set[str] | None:
    try:
        return set(json.loads(MEDIUM_PACKAGES.read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def data_mounts(partitions: list[dict]) -> list[dict]:
    """Partitions the user mounted outside the system tree, e.g. a Windows D: drive."""
    out = []
    for p in partitions or []:
        mp = p.get("mountPoint") or ""
        if not mp.startswith("/") or mp in SYSTEM_MOUNTS or p.get("fs") in ("linuxswap", "swap"):
            continue
        out.append(
            {
                "mountPoint": mp,
                "fs": (p.get("fs") or "").lower(),
                "label": p.get("partLabel") or p.get("fsLabel") or "",
                "device": p.get("device") or "",
            }
        )
    return out


def data_mounts_nix(mounts: list[dict], uid: int = 1000, gid: int = 100) -> str:
    """Option overrides for data partitions, appended to zed-hardware.nix."""
    from .hostgen import nix_string

    lines = []
    for m in mounts:
        mp = nix_string(m["mountPoint"])
        opts = ['"nofail"', '"x-gvfs-show"']
        if m["fs"] in ("ntfs", "ntfs3", "ntfs-3g"):
            lines.append(f'  fileSystems.{mp}.fsType = lib.mkForce "ntfs3";')
            opts += [f'"uid={uid}"', f'"gid={gid}"', '"windows_names"', '"iocharset=utf8"']
        elif m["fs"] in ("fat32", "fat16", "vfat", "exfat"):
            opts += [f'"uid={uid}"', f'"gid={gid}"', '"umask=022"']
        lines.append(f"  fileSystems.{mp}.options = [ {' '.join(opts)} ];")
    if not lines:
        return ""
    return (
        "\n# Data partitions chosen in the installer: mounted with nofail so that a\n"
        "# missing drive never blocks booting, and shown in the file manager.\n"
        "{ lib, ... }:\n{\n" + "\n".join(lines) + "\n}\n"
    )


def write_files(root: Path, files: dict[str, str]) -> None:
    base = root / "etc" / "zed"
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        p.write_text(text)
        os.chmod(p, 0o644)
