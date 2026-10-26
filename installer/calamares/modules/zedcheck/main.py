#!/usr/bin/env python3
"""Calamares job: validate the installer selections before partitioning.

Runs first in the exec sequence.  Returning an error here stops the
installation before Calamares touches any disk.
"""

import libcalamares
from zedinstaller import calamares as zc
from zedinstaller import compat


def pretty_name():
    return "Checking the installation choices"


class _GS:
    def get(self, key, default=None):
        gs = libcalamares.globalstorage
        return gs.value(key) if gs.contains(key) else default


def run():
    gs = _GS()
    sel, want_nvidia = zc.selections(gs)
    plan = zc.gpu_plan(sel, want_nvidia)
    is_online = zc.online()
    report = compat.check(
        sel,
        plan,
        online=is_online,
        memory_mib=zc.memory_mib(),
        packages_on_medium=zc.medium_packages(),
    )
    libcalamares.globalstorage.insert("zedGpuPlan", plan.to_dict())
    libcalamares.globalstorage.insert("zedOnline", is_online)
    for g in plan.gpus:
        libcalamares.utils.debug(f"zedcheck: GPU {g.describe()}")
    for w in report.warnings:
        libcalamares.utils.warning(f"zedcheck: {w}")
    if not report.ok:
        return (
            "The selected options cannot be installed",
            "<br/>".join(report.errors) + "<br/><br/>Nothing has been changed on your disks.",
        )
    return None
