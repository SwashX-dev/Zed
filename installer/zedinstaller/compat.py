"""Installer compatibility rules.

Run by the `zedcheck` job before partitioning, so an unsupported
combination stops the installation before any disk is touched.  Errors stop
the installer; warnings are shown in the log and the summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .gpu import GpuPlan
from .hostgen import Selections, load_profiles

SESSIONS = {"labwc", "niri", "sway"}
# Sessions that need GPU acceleration to be usable (no software fallback).
NEEDS_GPU = {"niri"}
VIRTUAL = {"qemu", "virtio", "vmware", "virtualbox", "hyperv"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check(
    sel: Selections,
    gpu: GpuPlan,
    *,
    online: bool,
    memory_mib: int,
    packages_on_medium: set[str] | None = None,
) -> Report:
    r = Report()
    profiles = load_profiles()
    prof = profiles.get(sel.profile)
    if prof is None:
        r.errors.append(f"Unknown installation profile '{sel.profile}'.")
        return r

    unknown = set(sel.sessions) - SESSIONS
    if unknown:
        r.errors.append(f"Unsupported desktop session(s): {', '.join(sorted(unknown))}.")
    if prof["desktop"] and not sel.sessions:
        r.errors.append(f"{prof['title']} includes a desktop: choose at least one desktop session.")
    if not prof["desktop"] and sel.sessions:
        r.warnings.append(
            f"{prof['title']} has no graphical desktop; the selected sessions are not installed."
        )

    vendors = set(gpu.vendors)
    if prof["desktop"] and vendors and vendors <= VIRTUAL:
        for s in sorted(set(sel.sessions) & NEEDS_GPU):
            r.warnings.append(
                f"{s} needs GPU acceleration; in this virtual machine it uses slow software rendering."
            )

    if gpu.needs_network and not online:
        r.errors.append(
            "The proprietary NVIDIA driver must be downloaded, but there is no network connection. "
            "Connect to a network, or go back and choose the free nouveau driver."
        )

    if packages_on_medium is not None and not online:
        wanted = set(prof["state"]["packages"]) | set(sel.extra_packages)
        missing = sorted(wanted - packages_on_medium)
        if missing:
            r.errors.append(
                "These packages are not on the installation medium and need a network connection: "
                + ", ".join(missing)
            )

    if sel.profile == "complete" and memory_mib < 4096:
        r.warnings.append("Zed Complete is large; with less than 4 GiB of memory some tools will be slow.")
    if prof["desktop"] and memory_mib < 2048:
        r.warnings.append("The desktop needs about 2 GiB of memory to be comfortable.")

    for note in gpu.notes:
        r.warnings.append(note)
    return r
