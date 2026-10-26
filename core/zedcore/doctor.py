"""`zed doctor`: health checks with actionable results.

Each check reports ok, warning, error, or unknown.  A check that cannot
determine the real state reports unknown rather than ok.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from . import generations
from .errors import ZedError
from .paths import Paths
from .registry import Registry
from .runner import Runner
from .services import LIVE, boot_status
from .state import load as load_state
import contextlib


@dataclass
class Check:
    id: str
    title: str
    status: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "title": self.title, "status": self.status, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


def run_checks(paths: Paths, registry: Registry, runner: Runner, *, live: bool) -> list[Check]:
    checks: list[Callable[[], Check]] = [
        lambda: _init(paths),
        lambda: _boot(paths),
        lambda: _s6_consistency(runner),
        lambda: _disk(paths),
        lambda: _reboot(paths),
    ]
    if not live:
        checks += [
            lambda: _host_flake(paths),
            lambda: _state(paths, registry),
            lambda: _generations(paths),
        ]
    out = []
    for c in checks:
        try:
            out.append(c())
        except Exception as e:  # noqa: BLE001 - a broken check must not hide the others
            out.append(Check("internal", "doctor", "unknown", f"check failed: {e}"))
    return out


def _init(paths: Paths) -> Check:
    try:
        comm = (paths.root / "proc/1/comm").read_text().strip()
    except OSError:
        return Check("init", "Init system", "unknown", "cannot read /proc/1/comm")
    if comm == "s6-svscan":
        return Check("init", "Init system", "ok", "s6-svscan is PID 1")
    return Check(
        "init",
        "Init system",
        "error",
        f"PID 1 is {comm}, not s6-svscan",
        hint="this system was not booted with Zed's s6 init",
    )


def _boot(paths: Paths) -> Check:
    st = boot_status(paths)
    if st["status"] == "ok":
        return Check("boot", "Boot services", "ok", "all boot services started")
    if st["status"] == "starting":
        return Check("boot", "Boot services", "warning", "the boot transition has not finished yet")
    if st["status"] == "degraded":
        failed = ", ".join(st["failed"]) or "unknown"
        return Check(
            "boot",
            "Boot services",
            "error",
            f"services that did not start: {failed}",
            hint="see 'zed services log NAME' and /run/uncaught-logs/current",
        )
    return Check("boot", "Boot services", "unknown", f"boot status is {st['status']!r}")


def _s6_consistency(runner: Runner) -> Check:
    try:
        res = runner.run(["s6-rc", "-l", LIVE, "diff"], timeout=30)
    except (OSError, ZedError) as e:
        return Check("s6", "Service supervisor", "unknown", str(e))
    if res.returncode == 0:
        return Check("s6", "Service supervisor", "ok", "s6-rc and s6 agree on every service")
    if res.returncode == 1:
        return Check(
            "s6",
            "Service supervisor",
            "warning",
            "s6-rc's record differs from the running services: " + " ".join(res.stdout.split()),
            hint="a service was started or stopped outside s6-rc; 'zed services' shows the state",
        )
    return Check("s6", "Service supervisor", "unknown", "s6-rc diff failed")


def _disk(paths: Paths) -> Check:
    target = paths.root / "nix" / "store"
    if not target.exists():
        target = paths.root
    usage = shutil.disk_usage(target)
    free_gib = usage.free / 2**30
    pct = 100 * usage.used / usage.total if usage.total else 0
    if free_gib < 2:
        return Check(
            "disk",
            "Free space for /nix",
            "error",
            f"{free_gib:.1f} GiB free ({pct:.0f}% used)",
            hint="updates and installs need space; run 'sudo zed gc'",
        )
    if free_gib < 8:
        return Check(
            "disk",
            "Free space for /nix",
            "warning",
            f"{free_gib:.1f} GiB free ({pct:.0f}% used)",
            hint="consider 'sudo zed gc' to delete old generations",
        )
    return Check("disk", "Free space for /nix", "ok", f"{free_gib:.1f} GiB free ({pct:.0f}% used)")


def _reboot(paths: Paths) -> Check:
    reasons = []
    with contextlib.suppress(FileNotFoundError):
        reasons = [line for line in paths.reboot_required.read_text().splitlines() if line.strip()]
    try:
        if os.readlink(paths.current_system / "kernel") != os.readlink(paths.booted_system / "kernel"):
            reasons.append("the running kernel is older than the active generation's")
    except OSError:
        pass
    if reasons:
        return Check(
            "reboot",
            "Pending reboot",
            "warning",
            "; ".join(dict.fromkeys(reasons)),
            hint="reboot to finish applying changes",
        )
    return Check("reboot", "Pending reboot", "ok", "no reboot needed")


def _host_flake(paths: Paths) -> Check:
    flake = paths.host_dir / "flake.nix"
    if not flake.is_file():
        return Check(
            "host",
            "Host configuration",
            "error",
            f"{flake} is missing",
            hint="restore /etc/zed from a backup or reinstall",
        )
    try:
        lock = json.loads(paths.lock_file.read_text())
        rev = lock["nodes"]["zed"]["locked"].get("rev", "?")
    except (OSError, KeyError, json.JSONDecodeError):
        return Check(
            "host",
            "Host configuration",
            "error",
            "flake.lock is missing or unreadable",
            hint="run 'sudo zed upgrade' to recreate it",
        )
    return Check("host", "Host configuration", "ok", f"Zed source pinned at {rev[:12]}")


def _state(paths: Paths, registry: Registry) -> Check:
    try:
        st = load_state(paths.state_file, registry)
    except ZedError as e:
        return Check(
            "state",
            "Managed configuration",
            "error",
            e.message,
            hint="'sudo zed rollback' restores the state recorded in an earlier generation",
        )
    return Check(
        "state",
        "Managed configuration",
        "ok",
        f"{len(st.packages)} packages, {len(st.collections)} collections, {len(st.settings)} settings",
    )


def _generations(paths: Paths) -> Check:
    gens = generations.list_generations(paths)
    if not gens:
        return Check("generations", "System generations", "unknown", "no system generations found")
    default = next((g for g in gens if g.default), None)
    current = next((g for g in gens if g.current), None)
    msg = f"{len(gens)} generations; boot default is {default.number if default else '?'}"
    if default and current and default.number != current.number:
        return Check(
            "generations",
            "System generations",
            "warning",
            msg + f", but generation {current.number} is running",
            hint="reboot, or run 'sudo zed rollback' to return to the running one",
        )
    if len(gens) < 2:
        return Check(
            "generations", "System generations", "ok", msg + " (no older generation to fall back to yet)"
        )
    return Check("generations", "System generations", "ok", msg)
