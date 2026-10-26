"""The Nix operations zedd performs, each as one fixed command line.

User-supplied values reach these commands only as validated nixpkgs
attribute paths (see names.py) inside a flake attribute path, or as paths
that zedd created itself.  Nix expressions passed with --apply are
constants defined in this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from . import names
from .errors import BuildFailed, NotFound, ZedError
from .runner import CancelToken, LineCallback, Runner

# Every Zed host flake exposes its system under this name, whatever the
# machine's host name is.
HOST_ATTR = "nixosConfigurations.zed"
SYSTEM = "x86_64-linux"

# Summarises a package for `zed info` and Arsenal without building it.
# Every field is guarded so that broken or unfree packages still describe
# themselves instead of failing evaluation.
_PKG_INFO_FN = r"""
p:
let
  m = p.meta or { };
  try = v: d: let r = builtins.tryEval v; in if r.success then r.value else d;
  lic = l: if builtins.isList l then map lic l
    else if builtins.isAttrs l then { spdx = l.spdxId or null; name = l.fullName or l.shortName or null; free = l.free or true; }
    else { spdx = null; name = toString l; free = true; };
  licenses = let l = try (m.license or [ ]) [ ]; in if builtins.isList l then map lic l else [ (lic l) ];
in {
  isDerivation = (p.type or null) == "derivation";
  name = try (p.name or null) null;
  pname = try (p.pname or null) null;
  version = try (p.version or null) null;
  description = try (m.description or null) null;
  longDescription = try (m.longDescription or null) null;
  homepage = try (let h = m.homepage or null; in if builtins.isList h then builtins.head h else h) null;
  licenses = licenses;
  unfree = builtins.any (l: !l.free) licenses;
  broken = try (m.broken or false) false;
  insecure = try ((m.knownVulnerabilities or [ ]) != [ ]) false;
  knownVulnerabilities = try (m.knownVulnerabilities or [ ]) [ ];
  available = try (m.available or true) false;
  platforms = try (builtins.elem "x86_64-linux" (map (x: if builtins.isString x then x else x.system or "") (m.platforms or [ "x86_64-linux" ]))) true;
  mainProgram = try (m.mainProgram or null) null;
  position = try (m.position or null) null;
}
"""


@dataclass
class PackageInfo:
    attr: str
    data: dict

    @property
    def installable_problem(self) -> str | None:
        d = self.data
        if not d.get("isDerivation"):
            return f"'{self.attr}' is a package set or function, not an installable package"
        if not d.get("platforms", True):
            return f"'{self.attr}' does not support {SYSTEM}"
        if d.get("broken"):
            return f"'{self.attr}' is marked broken in this Zed release's package set"
        if d.get("insecure"):
            vulns = "; ".join(d.get("knownVulnerabilities") or [])
            return f"'{self.attr}' has known vulnerabilities and is blocked by nixpkgs: {vulns}"
        return None


class NixOps:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    # --- evaluation ----------------------------------------------------

    def package_info(self, host_dir: Path, attr: str) -> PackageInfo:
        names.package_attr(attr)
        res = self.runner.run(
            [
                "nix",
                "eval",
                "--json",
                f"path:{host_dir}#{HOST_ATTR}.pkgs.{attr}",
                "--apply",
                _PKG_INFO_FN,
            ],
            timeout=600,
        )
        if not res.ok:
            text = "\n".join(res.stderr_tail)
            if "does not provide attribute" in text or "attribute '" in text and "missing" in text:
                raise NotFound(
                    f"no package named '{attr}' in this Zed release",
                    hint=f"try 'zed search {attr.split('.')[-1]}'",
                )
            raise ZedError(f"could not evaluate package '{attr}'", detail=_tail(res.stderr_tail))
        return PackageInfo(attr, json.loads(res.stdout))

    def nixpkgs_path(self, host_dir: Path) -> str:
        res = self.runner.run(["nix", "eval", "--raw", f"path:{host_dir}#{HOST_ATTR}.pkgs.path"], timeout=600)
        if not res.ok:
            raise ZedError("could not locate the system's package set", detail=_tail(res.stderr_tail))
        return res.stdout.strip()

    def search(self, nixpkgs: str, terms: list[str], *, on_line: LineCallback | None = None) -> dict:
        # nix search takes regular expressions; terms are escaped so they
        # match literally.
        regexes = [re.escape(t) for t in terms] or ["^"]
        res = self.runner.run(
            ["nix", "search", "--json", f"path:{nixpkgs}#legacyPackages.{SYSTEM}", *regexes],
            on_line=on_line,
            timeout=1800,
        )
        if not res.ok:
            raise ZedError("package search failed", detail=_tail(res.stderr_tail))
        return json.loads(res.stdout or "{}")

    # --- building ------------------------------------------------------

    def build_system(
        self,
        flake_dir: Path,
        out_link: Path,
        *,
        on_line: LineCallback | None,
        log: IO[str] | None,
        cancel: CancelToken | None,
    ) -> Path:
        res = self.runner.run(
            [
                "nix",
                "build",
                "--print-out-paths",
                "--out-link",
                str(out_link),
                "-L",
                f"path:{flake_dir}#{HOST_ATTR}.config.system.build.toplevel",
            ],
            on_line=on_line,
            log=log,
            cancel=cancel,
        )
        if not res.ok:
            raise BuildFailed(
                "building the new system failed; nothing was changed",
                detail=_tail(res.stderr_tail),
                hint=_build_hint(res.stderr_tail),
            )
        return Path(res.stdout.strip().splitlines()[-1])

    def update_input(
        self, flake_dir: Path, input_name: str, *, log: IO[str] | None, on_line: LineCallback | None
    ) -> None:
        res = self.runner.run(
            ["nix", "flake", "update", input_name, "--flake", f"path:{flake_dir}"],
            log=log,
            on_line=on_line,
            timeout=1800,
        )
        if not res.ok:
            raise ZedError(
                "could not fetch the latest Zed release",
                detail=_tail(res.stderr_tail),
                hint="check the network connection; nothing was changed",
            )

    def remote_revision(self, flake_ref: str) -> dict:
        res = self.runner.run(["nix", "flake", "metadata", "--json", "--refresh", flake_ref], timeout=300)
        if not res.ok:
            raise ZedError(
                "could not reach the Zed release channel",
                detail=_tail(res.stderr_tail),
                hint="check the network connection",
            )
        return json.loads(res.stdout)

    def diff_closures(self, old: Path, new: Path) -> list[str]:
        res = self.runner.run(["nix", "store", "diff-closures", str(old), str(new)], timeout=600)
        if not res.ok:
            return []
        return [_strip_ansi(line) for line in res.stdout.splitlines() if line.strip()]

    # --- activation ----------------------------------------------------

    def set_profile(self, profile: Path, toplevel: Path, *, log: IO[str] | None) -> None:
        res = self.runner.run(["nix-env", "--profile", str(profile), "--set", str(toplevel)], log=log)
        if not res.ok:
            raise ZedError("could not record the new system generation", detail=_tail(res.stderr_tail))

    def switch_generation(self, profile: Path, number: int, *, log: IO[str] | None) -> None:
        res = self.runner.run(
            ["nix-env", "--profile", str(profile), "--switch-generation", str(int(number))], log=log
        )
        if not res.ok:
            raise ZedError(f"could not select generation {number}", detail=_tail(res.stderr_tail))

    def rollback_profile(self, profile: Path, *, log: IO[str] | None) -> None:
        res = self.runner.run(["nix-env", "--profile", str(profile), "--rollback"], log=log)
        if not res.ok:
            raise ZedError("could not roll back the system profile", detail=_tail(res.stderr_tail))

    def switch(self, toplevel: Path, action: str, *, on_line: LineCallback | None, log: IO[str] | None):
        if action not in ("switch", "boot", "test", "dry-activate"):
            raise ValueError(action)
        return self.runner.run(
            [str(toplevel / "bin" / "switch-to-configuration"), action],
            on_line=on_line,
            log=log,
        )


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _tail(lines: list[str], n: int = 25) -> str:
    return "\n".join(_strip_ansi(line) for line in lines[-n:])


def _build_hint(lines: list[str]) -> str | None:
    text = "\n".join(lines)
    if "has an unfree license" in text:
        return "this package is proprietary; allow it with 'zed config set nix.allowUnfree true'"
    if "No space left on device" in text:
        return "the disk is full; free space with 'zed gc' and retry"
    if "unable to download" in text or "Could not resolve host" in text:
        return "a download failed; check the network connection and retry"
    if "conflicting definition values" in text:
        return "host.nix sets the same option; change it there or remove it from host.nix"
    return None
