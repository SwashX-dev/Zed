"""The configuration transaction engine.

Every change to a Zed system, from the CLI or any desktop application,
goes through two steps:

plan    The requested change is applied to a staged copy of the host flake
        (/run/zed/plans/<id>/flake) and the resulting system is evaluated and
        built.  Nothing on the running system changes.  The plan reports the
        closure difference, services to restart and whether a reboot is
        needed.  Cancelling simply discards the staged copy.

commit  Re-checks that the managed state and flake.lock have not changed
        since the plan was made, records the new generation, replaces the
        state file atomically and activates.  If activation fails, the
        previous generation, state file and lock file are restored and
        re-activated.

Only one plan or commit runs at a time (an in-process lock plus flock on
/run/zed/transaction.lock).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from . import generations, history
from . import names as namecheck
from . import state as statemod
from .catalog import Catalog
from .errors import (
    ActivationFailed,
    Busy,
    Conflict,
    InvalidInput,
    NotFound,
    PermissionDenied,
    Unsupported,
    ZedError,
)
from .nixops import NixOps
from .paths import Paths
from .registry import Registry
from .runner import CancelToken
from .state import ManagedState

Progress = Callable[[dict], None]

PLAN_TTL_SECONDS = 3600


def _noop_progress(_: dict) -> None:
    pass


@dataclass
class Changes:
    install: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    add_collections: list[str] = field(default_factory=list)
    remove_collections: list[str] = field(default_factory=list)
    set: dict[str, Any] = field(default_factory=dict)
    reset: list[str] = field(default_factory=list)
    upgrade: bool = False
    # Rebuild from the current /etc/zed files (after editing host.nix).
    rebuild: bool = False
    # A complete replacement state (Zed Forge import); validated like any
    # state read from disk.
    replace_state: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Changes:
        if not isinstance(d, dict):
            raise InvalidInput("changes must be an object")
        allowed = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - allowed
        if unknown:
            raise InvalidInput(f"unknown change fields: {', '.join(sorted(unknown))}")
        c = cls(**{k: v for k, v in d.items() if k in allowed})
        for name in ("install", "remove", "add_collections", "remove_collections", "reset"):
            v = getattr(c, name)
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise InvalidInput(f"{name} must be a list of strings")
            if len(v) > 256:
                raise InvalidInput(f"{name}: too many entries")
        if not isinstance(c.set, dict):
            raise InvalidInput("set must be an object")
        if not isinstance(c.upgrade, bool) or not isinstance(c.rebuild, bool):
            raise InvalidInput("upgrade and rebuild must be true or false")
        return c

    def empty(self) -> bool:
        return not (
            self.install
            or self.remove
            or self.add_collections
            or self.remove_collections
            or self.set
            or self.reset
            or self.upgrade
            or self.rebuild
            or self.replace_state is not None
        )


@dataclass
class Plan:
    id: str
    kind: str  # "change" | "rollback"
    uid: int
    created: float
    summary: list[str]
    base_digest: str
    lock_digest: str
    new_state: ManagedState | None
    new_lock: str | None = None
    toplevel: Path | None = None
    target_generation: int | None = None
    closure_diff: list[str] = field(default_factory=list)
    restarts: list[str] = field(default_factory=list)
    reboot_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    noop: bool = False
    log_path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "noop": self.noop,
            "toplevel": str(self.toplevel) if self.toplevel else None,
            "targetGeneration": self.target_generation,
            "closureDiff": self.closure_diff,
            "restarts": self.restarts,
            "rebootReasons": self.reboot_reasons,
            "warnings": self.warnings,
            "state": self.new_state.to_dict() if self.new_state else None,
            "expiresIn": max(0, int(self.created + PLAN_TTL_SECONDS - time.time())),
            "log": str(self.log_path) if self.log_path else None,
        }


def _digest_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"


def _host_digest(host_dir: Path) -> str:
    """Fingerprint of the host flake outside managed/: host.nix, flake.lock, ...

    A plan is refused at commit if the user edited these files meanwhile.
    """
    h = hashlib.sha256()
    if not host_dir.is_dir():
        return "absent"
    for f in sorted(host_dir.rglob("*")):
        rel = f.relative_to(host_dir)
        if rel.parts[0] in ("managed", ".git") or not f.is_file():
            continue
        h.update(str(rel).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def _read_or_none(p: Path) -> str | None:
    try:
        return p.read_text()
    except FileNotFoundError:
        return None


class Engine:
    def __init__(
        self,
        paths: Paths,
        registry: Registry,
        catalog: Catalog,
        nix: NixOps,
    ):
        self.paths = paths
        self.registry = registry
        self.catalog = catalog
        self.nix = nix
        self._lock = threading.Lock()
        self._plans: dict[str, Plan] = {}
        self.current_job: str | None = None

    # --- preconditions -------------------------------------------------

    def is_live(self) -> bool:
        return self.paths.live_marker.exists()

    def require_host(self) -> None:
        if self.is_live():
            raise Unsupported(
                "this is the Zed live environment; system changes are not possible here",
                hint="install Zed first, or run a program temporarily with 'nix shell nixpkgs#NAME'",
            )
        if not (self.paths.host_dir / "flake.nix").is_file():
            raise Unsupported(
                f"{self.paths.host_dir} does not contain a Zed host flake",
                hint="this system was not installed by the Zed installer; see docs/INSTALLATION.md",
            )

    @contextlib.contextmanager
    def exclusive(self, job: str) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise Busy(f"another change is in progress ({self.current_job})")
        fd = None
        try:
            self.paths.run_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.paths.run_dir / "transaction.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy("another program is changing the system configuration") from None
            self.current_job = job
            yield
        finally:
            self.current_job = None
            if fd is not None:
                os.close(fd)
            self._lock.release()

    def current_state(self) -> ManagedState:
        return statemod.load(self.paths.state_file, self.registry, zoneinfo_dir=self._zoneinfo())

    def _zoneinfo(self) -> Path:
        return self.paths.root / "etc" / "zoneinfo"

    # --- planning ------------------------------------------------------

    def _new_plan(self, kind: str, uid: int) -> Plan:
        pid = secrets.token_hex(8)
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        return Plan(
            id=pid,
            kind=kind,
            uid=uid,
            created=time.time(),
            summary=[],
            base_digest=self.current_state().digest(),
            lock_digest=_host_digest(self.paths.host_dir),
            new_state=None,
            log_path=self.paths.logs_dir / f"{pid}.log",
        )

    def _apply(self, state: ManagedState, c: Changes, plan: Plan) -> ManagedState:
        new = state.copy()
        if c.replace_state is not None:
            new = ManagedState.from_dict(c.replace_state, self.registry, zoneinfo_dir=self._zoneinfo())
            plan.summary.append("replace the managed configuration with an imported profile")

        install = [namecheck.package_attr(a) for a in c.install]
        remove = [namecheck.package_attr(a) for a in c.remove]
        if set(install) & set(remove):
            raise InvalidInput("the same package cannot be installed and removed at once")

        for attr in remove:
            if attr not in new.packages:
                owners = [
                    col.id for col in self.catalog if col.id in new.collections and attr in col.packages
                ]
                if owners:
                    raise InvalidInput(
                        f"'{attr}' is part of the installed collection(s) {', '.join(owners)}",
                        hint=f"remove the collection with 'zed remove --collection {owners[0]}'",
                    )
                raise NotFound(
                    f"'{attr}' is not installed by Zed",
                    hint="packages listed in /etc/zed/host.nix are managed there, not by zed",
                )
        for attr in new.remove_packages(remove):
            plan.summary.append(f"remove package {attr}")

        for cid in c.remove_collections:
            self.catalog.get(cid)
            if cid not in new.collections:
                raise NotFound(f"collection '{cid}' is not installed")
        for cid in new.remove_collections(c.remove_collections):
            plan.summary.append(f"remove collection {cid}")

        for cid in c.add_collections:
            col = self.catalog.get(cid)
            if cid in new.collections:
                plan.warnings.append(f"collection '{cid}' is already installed")
            else:
                plan.summary.append(f"install collection {cid} ({len(col.packages)} packages)")
        new.add_collections(c.add_collections)

        for attr in install:
            if attr in new.packages:
                plan.warnings.append(f"'{attr}' is already installed")
                continue
            info = self.nix.package_info(self.paths.host_dir, attr)
            problem = info.installable_problem
            if problem:
                raise InvalidInput(problem)
            if info.data.get("unfree"):
                lic = ", ".join(lic_.get("name") or "proprietary" for lic_ in info.data.get("licenses", []))
                plan.warnings.append(f"'{attr}' has a proprietary licence ({lic})")
            version = info.data.get("version") or "?"
            plan.summary.append(f"install package {attr} {version}")
        new.add_packages(install)

        for key, value in c.set.items():
            v = self.registry.validate(key, value, zoneinfo_dir=self._zoneinfo())
            s = self.registry.get(key)
            if new.settings.get(key) != v:
                plan.summary.append(f"set {key} = {json.dumps(v)}")
                if s.danger:
                    plan.warnings.append(s.danger)
                if s.reboot:
                    plan.reboot_reasons.append(f"{key} takes effect after a reboot")
            new.set_setting(key, v)
        for key in c.reset:
            self.registry.get(key)
            if new.reset_setting(key):
                plan.summary.append(f"reset {key} to its default")
        return new

    def plan_change(
        self,
        changes: Changes,
        uid: int,
        progress: Progress = _noop_progress,
        cancel: CancelToken | None = None,
    ) -> Plan:
        self.require_host()
        if changes.empty():
            raise InvalidInput("no changes requested")
        with self.exclusive("planning a change"):
            plan = self._new_plan("change", uid)
            progress({"type": "step", "step": "validate", "message": "checking the requested change"})
            base = self.current_state()
            new = self._apply(base, changes, plan)
            plan.new_state = new

            if new.digest() == base.digest() and not changes.upgrade and not changes.rebuild:
                plan.noop = True
                plan.summary = plan.summary or ["nothing to change"]
                return plan

            staged = self._stage(plan, new)
            log = open(plan.log_path, "a")  # noqa: SIM115 - closed in finally
            try:
                if changes.upgrade:
                    progress({"type": "step", "step": "fetch", "message": "fetching the latest Zed release"})
                    self.nix.update_input(staged, "zed", log=log, on_line=_log_event(progress))
                    new_lock = (staged / "flake.lock").read_text()
                    if new_lock != _read_or_none(self.paths.lock_file):
                        plan.new_lock = new_lock
                        plan.summary.append("upgrade to the latest Zed release on this channel")
                    elif new.digest() == base.digest():
                        plan.noop = True
                        plan.summary = ["Zed is already up to date"]
                        self._discard_files(plan)
                        return plan

                progress(
                    {"type": "step", "step": "build", "message": "evaluating and building the new system"}
                )
                plan.toplevel = self.nix.build_system(
                    staged,
                    self._plan_dir(plan) / "result",
                    on_line=_log_event(progress),
                    log=log,
                    cancel=cancel,
                )
                if changes.rebuild and not changes.upgrade and new.digest() == base.digest():
                    current = (
                        self.paths.current_system.resolve() if self.paths.current_system.exists() else None
                    )
                    if current == plan.toplevel.resolve():
                        plan.noop = True
                        plan.summary = ["the running system already matches /etc/zed"]
                        self._discard_files(plan)
                        return plan
                    plan.summary.append("rebuild from /etc/zed (host.nix and hardware files)")
                self._check_compatibility(plan)
                progress({"type": "step", "step": "diff", "message": "comparing with the running system"})
                self._describe(plan, log)
            except BaseException:
                self._discard_files(plan)
                raise
            finally:
                log.close()
            self._plans[plan.id] = plan
            return plan

    def plan_rollback(self, generation: int | None, uid: int, progress: Progress = _noop_progress) -> Plan:
        self.require_host()
        with self.exclusive("planning a rollback"):
            plan = self._new_plan("rollback", uid)
            gens = generations.list_generations(self.paths)
            default = next((g for g in gens if g.default), None)
            if generation is None:
                if default is None:
                    raise NotFound("cannot determine the current boot generation")
                older = [g for g in gens if g.number < default.number]
                if not older:
                    raise NotFound("there is no earlier generation to roll back to")
                target = older[-1]
            else:
                target = next((g for g in gens if g.number == int(generation)), None)
                if target is None:
                    raise NotFound(f"generation {generation} does not exist (see 'zed generations')")
                if default is not None and target.number == default.number and target.current:
                    plan.noop = True
                    plan.summary = [f"generation {target.number} is already active"]
                    return plan
            plan.target_generation = target.number
            plan.toplevel = target.path
            plan.summary.append(f"switch to generation {target.number} from {target.created:%Y-%m-%d %H:%M}")
            state = target.managed_state()
            if state is None:
                plan.warnings.append(
                    "this generation has no recorded managed state; "
                    "/etc/zed/managed/state.json is kept as it is"
                )
                plan.new_state = None
            else:
                plan.new_state = ManagedState.from_dict(state, self.registry, zoneinfo_dir=self._zoneinfo())
            with open(plan.log_path, "a") as log:
                self._describe(plan, log)
            self._plans[plan.id] = plan
            return plan

    # --- committing ----------------------------------------------------

    def get_plan(self, plan_id: str, uid: int) -> Plan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise NotFound("no such plan; it may have expired or zedd was restarted")
        if uid != 0 and uid != plan.uid:
            raise PermissionDenied("this plan belongs to another user")
        if time.time() > plan.created + PLAN_TTL_SECONDS:
            self.discard(plan_id, uid)
            raise Conflict("the plan has expired; create it again")
        return plan

    def discard(self, plan_id: str, uid: int) -> None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return
        if uid != 0 and uid != plan.uid:
            raise PermissionDenied("this plan belongs to another user")
        self._plans.pop(plan_id, None)
        self._discard_files(plan)

    def commit(self, plan_id: str, uid: int, progress: Progress = _noop_progress) -> dict:
        self.require_host()
        with self.exclusive("applying a change"):
            plan = self.get_plan(plan_id, uid)
            if plan.noop:
                self.discard(plan_id, uid)
                return {"result": "unchanged"}
            if plan.base_digest != self.current_state().digest() or plan.lock_digest != _host_digest(
                self.paths.host_dir
            ):
                self.discard(plan_id, uid)
                raise Conflict(
                    "the system configuration changed after this plan was made",
                    hint="review the change again; nothing was applied",
                )
            if plan.toplevel is None:
                raise ZedError("internal error: plan has no system to activate")
            try:
                return self._activate(plan, uid, progress)
            finally:
                self._plans.pop(plan.id, None)
                self._discard_files(plan)

    def _activate(self, plan: Plan, uid: int, progress: Progress) -> dict:
        profile = self.paths.system_profile
        prev = generations.default_generation(self.paths)
        old_state = _read_or_none(self.paths.state_file)
        old_lock = _read_or_none(self.paths.lock_file)
        record = {"id": plan.id, "kind": plan.kind, "uid": uid, "summary": plan.summary}
        if prev:
            record["from"] = prev.number

        with open(plan.log_path, "a") as log:

            def restore_files() -> None:
                if plan.new_state is not None:
                    if old_state is None:
                        self.paths.state_file.unlink(missing_ok=True)
                    else:
                        statemod.write_atomic(self.paths.state_file, old_state)
                if plan.new_lock is not None and old_lock is not None:
                    statemod.write_atomic(self.paths.lock_file, old_lock)

            progress({"type": "step", "step": "activate", "message": "activating the new system"})
            try:
                if plan.kind == "rollback":
                    self.nix.switch_generation(profile, plan.target_generation, log=log)
                else:
                    self.nix.set_profile(profile, plan.toplevel, log=log)
                if plan.new_state is not None:
                    statemod.write_atomic(self.paths.state_file, plan.new_state.to_json())
                if plan.new_lock is not None:
                    statemod.write_atomic(self.paths.lock_file, plan.new_lock)
            except BaseException as e:
                restore_files()
                self._restore_profile(plan, prev, log)
                history.append(self.paths.history_file, {**record, "result": "failed", "error": str(e)})
                raise

            res = self.nix.switch(plan.toplevel, "switch", on_line=_log_event(progress), log=log)
            new = generations.default_generation(self.paths)
            if res.ok:
                record.update(result="ok", to=new.number if new else None)
                history.append(self.paths.history_file, record)
                return {
                    "result": "applied",
                    "generation": new.number if new else None,
                    "rebootReasons": plan.reboot_reasons + self._pending_reboot(),
                }

            # Activation failed: put the previous generation back.
            progress(
                {
                    "type": "step",
                    "step": "rollback",
                    "message": "activation failed; restoring the previous system",
                }
            )
            restore_files()
            restored = self._restore_profile(plan, prev, log)
            if restored and prev is not None:
                back = self.nix.switch(prev.path, "switch", on_line=_log_event(progress), log=log)
                restored = back.ok
            record.update(
                result="rolled-back" if restored else "failed", failedGeneration=new.number if new else None
            )
            history.append(self.paths.history_file, record)
            raise ActivationFailed(
                "the new system could not be activated; "
                + (
                    "the previous system was restored"
                    if restored
                    else "restoring the previous system also failed"
                ),
                detail="\n".join(res.stderr_tail[-25:]),
                hint=f"full log: {plan.log_path}"
                + ("" if restored else "; boot an earlier generation from the boot menu"),
            )

    def _restore_profile(self, plan: Plan, prev, log: IO[str]) -> bool:
        if prev is None:
            return False
        try:
            current = generations.default_generation(self.paths)
            if current is None or current.number != prev.number:
                self.nix.switch_generation(self.paths.system_profile, prev.number, log=log)
            return True
        except ZedError as e:
            log.write(f"restoring generation {prev.number} failed: {e}\n")
            return False

    # --- helpers -------------------------------------------------------

    def _plan_dir(self, plan: Plan) -> Path:
        return self.paths.plans_dir / plan.id

    def _stage(self, plan: Plan, new: ManagedState) -> Path:
        d = self._plan_dir(plan)
        self.paths.plans_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.paths.plans_dir, 0o700)
        staged = d / "flake"
        shutil.copytree(
            self.paths.host_dir,
            staged,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "result", "result-*"),
        )
        statemod.write_atomic(staged / "managed" / "state.json", new.to_json())
        return staged

    def _discard_files(self, plan: Plan) -> None:
        shutil.rmtree(self._plan_dir(plan), ignore_errors=True)

    def _check_compatibility(self, plan: Plan) -> None:
        def report(top: Path) -> set[str]:
            try:
                data = json.loads((top / "zed" / "compatibility.json").read_text())
            except (OSError, json.JSONDecodeError):
                return set()
            return set(data.get("services", [])) | {f"{t} (timer)" for t in data.get("timers", [])}

        if plan.toplevel is None:
            raise ZedError("internal error: plan has no system to activate")
        new = report(plan.toplevel) - report(self.paths.current_system)
        if new:
            raise Unsupported(
                "the change enables services that Zed's s6 init does not run yet: " + ", ".join(sorted(new)),
                hint="nothing was changed; see docs/ARCHITECTURE.md for supported services",
            )

    def _describe(self, plan: Plan, log: IO[str]) -> None:
        if plan.toplevel is None:
            raise ZedError("internal error: plan has no system to activate")
        current = self.paths.current_system
        if current.exists():
            plan.closure_diff = self.nix.diff_closures(current, plan.toplevel)
            dry = self.nix.switch(plan.toplevel, "dry-activate", on_line=None, log=log)
            if dry.ok:
                lines = dry.stderr_tail + dry.stdout.splitlines()
                plan.restarts = [line.strip() for line in lines if line.startswith("  ")]
                plan.reboot_reasons += [line for line in lines if "next boot" in line]
        booted = self.paths.booted_system
        for rel, what in (
            ("kernel", "the kernel"),
            ("initrd", "the initial ramdisk"),
            ("kernel-modules", None),
        ):
            if what is None:
                continue
            try:
                if os.readlink(booted / rel) != os.readlink(plan.toplevel / rel):
                    plan.reboot_reasons.append(f"{what} changes; reboot to use it")
            except OSError:
                continue
        plan.reboot_reasons = list(dict.fromkeys(plan.reboot_reasons))

    def _pending_reboot(self) -> list[str]:
        text = _read_or_none(self.paths.reboot_required)
        return [line for line in (text or "").splitlines() if line.strip()]

    def cleanup_stale(self) -> None:
        """Remove staged plans left over from a previous zedd run."""
        shutil.rmtree(self.paths.plans_dir, ignore_errors=True)


def _log_event(progress: Progress) -> Callable[[str], None]:
    return lambda line: progress({"type": "log", "line": line})
