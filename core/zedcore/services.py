"""s6-rc service queries and control for `zed services` and Sentinel.

Service metadata (descriptions, dependencies) comes from the manifest that
every Zed generation carries at zed/services.json; live state comes from
s6-rc and s6-svstat.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import names
from .errors import InvalidInput, NotFound, ZedError
from .paths import Paths
from .runner import Runner

LIVE = "/run/s6-rc"
SCAN = "/run/service"


class Services:
    def __init__(self, paths: Paths, runner: Runner | None = None):
        self.paths = paths
        self.runner = runner or Runner()

    def manifest(self) -> dict[str, dict]:
        try:
            return json.loads((self.paths.current_system / "zed" / "services.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _active(self) -> set[str]:
        res = self.runner.run(["s6-rc", "-l", LIVE, "-a", "list"], timeout=30)
        if not res.ok:
            raise ZedError("cannot query s6-rc", detail="\n".join(res.stderr_tail[-5:]))
        return set(res.stdout.split())

    def _svstat(self, name: str) -> dict:
        res = self.runner.run(["s6-svstat", "-o", "up,pid,updownfor,exitcode", f"{SCAN}/{name}"], timeout=10)
        if not res.ok:
            return {}
        fields = res.stdout.split()
        if len(fields) != 4:
            return {}
        up, pid, secs, code = fields
        return {
            "running": up == "true",
            "pid": int(pid) if pid.lstrip("-").isdigit() and int(pid) > 0 else None,
            "seconds": int(secs) if secs.isdigit() else None,
            "lastExit": int(code) if code.lstrip("-").isdigit() else None,
        }

    def list(self) -> list[dict]:
        manifest = self.manifest()
        active = self._active()
        out = []
        for name, meta in sorted(manifest.items()):
            if meta.get("type") == "bundle":
                continue
            entry = {
                "name": name,
                "description": meta.get("description", ""),
                "type": meta.get("type"),
                "up": name in active,
                "autoStart": meta.get("autoStart", True),
                "essential": meta.get("essential", False),
                "dependencies": meta.get("dependencies", []),
            }
            if meta.get("type") == "longrun":
                entry.update(self._svstat(name))
            out.append(entry)
        return out

    def _check(self, name: str) -> dict:
        names.service_name(name)
        meta = self.manifest().get(name)
        if meta is None:
            raise NotFound(f"no service named '{name}'", hint="run 'zed services' to list them")
        if meta.get("type") == "bundle":
            raise InvalidInput(f"'{name}' is a bundle, not a service")
        return meta

    def control(self, name: str, action: str) -> None:
        meta = self._check(name)
        if action == "start":
            argv = ["s6-rc", "-l", LIVE, "-u", "change", name]
        elif action == "stop":
            if meta.get("essential"):
                raise InvalidInput(f"'{name}' is essential and is only stopped at shutdown")
            argv = ["s6-rc", "-l", LIVE, "-d", "change", name]
        elif action == "restart":
            if meta.get("type") == "longrun":
                argv = ["s6-svc", "-r", f"{SCAN}/{name}"]
            else:
                self.control(name, "stop")
                argv = ["s6-rc", "-l", LIVE, "-u", "change", name]
        else:
            raise InvalidInput(f"unknown service action {action!r}")
        res = self.runner.run(argv, timeout=120)
        if not res.ok:
            raise ZedError(f"could not {action} {name}", detail="\n".join(res.stderr_tail[-10:]))

    def log(self, name: str, lines: int = 100) -> list[str]:
        self._check(name)
        path = self.paths.root / "var/log/zed" / name / "current"
        try:
            text = path.read_text(errors="replace")
        except FileNotFoundError:
            return []
        return text.splitlines()[-max(1, min(lines, 5000)) :]


def boot_status(paths: Paths) -> dict:
    try:
        lines = Path(paths.boot_status).read_text().split()
    except FileNotFoundError:
        return {"status": "starting", "failed": []}
    if not lines:
        return {"status": "unknown", "failed": []}
    return {"status": lines[0], "failed": lines[1:]}
