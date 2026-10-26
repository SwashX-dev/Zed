"""zedd: the Zed management daemon.

zedd runs as root under s6 supervision and is the only component that
changes system configuration.  It exposes a fixed set of methods on
/run/zed/zedd.sock; there is no method that runs arbitrary commands.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socketserver
import sys
import threading
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import __version__, doctor, generations, history, names
from . import state as statemod
from .auth import Peer, Policy, peer_of
from .catalog import Catalog
from .errors import InvalidInput, NotFound, Unsupported, ZedError
from .nixops import NixOps
from .paths import Paths, default_paths
from .protocol import ProtocolError, encode, read_message
from .registry import Registry
from .runner import CancelToken, Runner
from .services import Services, boot_status
from .transactions import Changes, Engine
import contextlib

log = logging.getLogger("zedd")


@dataclass
class Call:
    peer: Peer
    params: dict
    emit: Callable[[dict], None]
    cancel: CancelToken


Handler = Callable[[Call], Any]


class Zedd:
    def __init__(self, paths: Paths, policy: Policy, runner: Runner | None = None):
        self.paths = paths
        self.policy = policy
        self.runner = runner or Runner()
        self.registry = Registry.load()
        self.catalog = Catalog.load()
        self.nix = NixOps(self.runner)
        self.engine = Engine(paths, self.registry, self.catalog, self.nix)
        self.services = Services(paths, self.runner)
        self._eval_slots = threading.BoundedSemaphore(2)
        self._nixpkgs_cache: tuple[str, str] | None = None
        # name -> (handler, admin required)
        self.methods: dict[str, tuple[Handler, bool]] = {
            "ping": (self.ping, False),
            "status": (self.status, False),
            "info": (self.info, False),
            "search": (self.search, False),
            "list": (self.list_installed, False),
            "collections": (self.collections, False),
            "generations": (self.generations, False),
            "history": (self.history, False),
            "config.schema": (self.config_schema, False),
            "config.get": (self.config_get, False),
            "services.list": (self.services_list, False),
            "doctor": (self.doctor, False),
            "update.check": (self.update_check, False),
            "plan.get": (self.plan_get, False),
            "plan.create": (self.plan_create, True),
            "plan.rollback": (self.plan_rollback, True),
            "plan.commit": (self.plan_commit, True),
            "plan.discard": (self.plan_discard, True),
            "services.control": (self.services_control, True),
            "services.log": (self.services_log, True),
            "gc": (self.gc, True),
        }

    def outdated(self) -> bool:
        """Whether the active generation provides a different zedd than this one."""
        try:
            mine = Path(sys.argv[0]).resolve(strict=True)
            active = (self.paths.current_system / "sw" / "bin" / "zedd").resolve(strict=True)
        except OSError:
            return False
        return mine != active

    # --- dispatch ------------------------------------------------------

    def dispatch(self, method: str, call: Call) -> Any:
        entry = self.methods.get(method)
        if entry is None:
            raise NotFound(f"unknown method {method!r}")
        handler, admin = entry
        if admin:
            self.policy.require_admin(call.peer, method)
        return handler(call)

    # --- read methods --------------------------------------------------

    def ping(self, call: Call) -> dict:
        return {"version": __version__, "admin": self.policy.is_admin(call.peer)}

    def status(self, call: Call) -> dict:
        gen = generations.default_generation(self.paths)
        return {
            "version": __version__,
            "live": self.engine.is_live(),
            "busy": self.engine.current_job,
            "boot": boot_status(self.paths),
            "generation": gen.number if gen else None,
            "rebootRequired": self.engine._pending_reboot(),
            "admin": self.policy.is_admin(call.peer),
        }

    def _state(self) -> statemod.ManagedState:
        if self.engine.is_live() or not self.paths.host_dir.exists():
            return statemod.ManagedState()
        return self.engine.current_state()

    def info(self, call: Call) -> dict:
        attr = names.package_attr(call.params.get("package", ""))
        self.engine.require_host()
        with self._eval_slots:
            pkg = self.nix.package_info(self.paths.host_dir, attr)
        st = self._state()
        in_cols = [c.id for c in self.catalog if c.id in st.collections and attr in c.packages]
        return {
            "attr": attr,
            **pkg.data,
            "problem": pkg.installable_problem,
            "installed": attr in st.packages or bool(in_cols),
            "installedBy": (["zed"] if attr in st.packages else []) + [f"collection:{c}" for c in in_cols],
        }

    def _nixpkgs(self) -> str:
        key = (self.paths.lock_file.read_bytes() if self.paths.lock_file.exists() else b"").hex()
        if self._nixpkgs_cache and self._nixpkgs_cache[0] == key:
            return self._nixpkgs_cache[1]
        path = self.nix.nixpkgs_path(self.paths.host_dir)
        self._nixpkgs_cache = (key, path)
        return path

    def search(self, call: Call) -> dict:
        terms = call.params.get("terms", [])
        if not isinstance(terms, list) or not all(isinstance(t, str) and 0 < len(t) <= 64 for t in terms):
            raise InvalidInput("search terms must be 1-64 characters each")
        if not terms:
            raise InvalidInput("give at least one search term")
        self.engine.require_host()
        limit = min(int(call.params.get("limit", 50)), 500)
        call.emit(
            {
                "type": "step",
                "step": "search",
                "message": "searching the package set (the first search builds a cache)",
            }
        )
        with self._eval_slots:
            raw = self.nix.search(self._nixpkgs(), terms)
        st = self._state()
        installed = set(st.packages) | self.catalog.packages_of(st.collections)
        results = []
        for key, v in raw.items():
            attr = key.split(".", 2)[-1] if key.startswith("legacyPackages.") else key
            results.append(
                {
                    "attr": attr,
                    "pname": v.get("pname"),
                    "version": v.get("version"),
                    "description": v.get("description"),
                    "installed": attr in installed,
                }
            )
        # Exact and prefix matches first.
        t0 = terms[0].lower()
        results.sort(key=lambda r: (r["attr"].lower() != t0, not r["attr"].lower().startswith(t0), r["attr"]))
        return {"total": len(results), "results": results[:limit]}

    def list_installed(self, call: Call) -> dict:
        st = self._state()
        return {
            "packages": st.packages,
            "collections": [self.catalog.get(c).to_dict() for c in st.collections if _known(self.catalog, c)],
            "settings": st.settings,
        }

    def collections(self, call: Call) -> dict:
        st = self._state()
        return {"collections": [{**c.to_dict(), "installed": c.id in st.collections} for c in self.catalog]}

    def generations(self, call: Call) -> dict:
        return {"generations": [g.to_dict() for g in generations.list_generations(self.paths)]}

    def history(self, call: Call) -> dict:
        limit = min(int(call.params.get("limit", 50)), 1000)
        return {"history": history.read(self.paths.history_file, limit)}

    def config_schema(self, call: Call) -> dict:
        return {
            "sections": [{"id": i, "title": t} for i, t in self.registry.sections],
            "settings": [s.to_dict() for s in self.registry],
        }

    def config_get(self, call: Call) -> dict:
        st = self._state()
        key = call.params.get("key")
        if key is not None:
            self.registry.get(key)
            return {"key": key, "managed": key in st.settings, "value": st.settings.get(key)}
        return {"settings": st.settings}

    def services_list(self, call: Call) -> dict:
        return {"services": self.services.list()}

    def doctor(self, call: Call) -> dict:
        checks = doctor.run_checks(self.paths, self.registry, self.runner, live=self.engine.is_live())
        return {"checks": [c.to_dict() for c in checks]}

    def update_check(self, call: Call) -> dict:
        self.engine.require_host()
        try:
            lock = json.loads(self.paths.lock_file.read_text())
            node = lock["nodes"]["zed"]
        except (OSError, KeyError, json.JSONDecodeError) as e:
            raise ZedError("cannot read /etc/zed/flake.lock", detail=str(e)) from e
        orig = node.get("original", {})
        if orig.get("type") != "github":
            raise Unsupported(
                "this system's Zed source is not a GitHub release channel",
                hint="offline installs pin the installer's release; docs/UPDATING.md explains channels",
            )
        ref = orig.get("ref", "stable")
        flake_ref = f"github:{orig['owner']}/{orig['repo']}/{ref}"
        call.emit({"type": "step", "step": "fetch", "message": f"checking {flake_ref}"})
        meta = self.nix.remote_revision(flake_ref)
        current = node.get("locked", {}).get("rev")
        latest = meta.get("revision")
        return {
            "channel": ref,
            "current": current,
            "latest": latest,
            "latestDate": meta.get("lastModified"),
            "updateAvailable": bool(latest and latest != current),
        }

    def plan_get(self, call: Call) -> dict:
        return self.engine.get_plan(str(call.params.get("id", "")), call.peer.uid).to_dict()

    # --- admin methods -------------------------------------------------

    def plan_create(self, call: Call) -> dict:
        changes = Changes.from_dict(call.params.get("changes", {}))
        plan = self.engine.plan_change(changes, call.peer.uid, call.emit, call.cancel)
        return plan.to_dict()

    def plan_rollback(self, call: Call) -> dict:
        gen = call.params.get("generation")
        if gen is not None and (isinstance(gen, bool) or not isinstance(gen, int)):
            raise InvalidInput("generation must be a number")
        return self.engine.plan_rollback(gen, call.peer.uid, call.emit).to_dict()

    def plan_commit(self, call: Call) -> dict:
        # Activation is never cancelled, even if the client goes away.
        return self.engine.commit(str(call.params.get("id", "")), call.peer.uid, call.emit)

    def plan_discard(self, call: Call) -> dict:
        self.engine.discard(str(call.params.get("id", "")), call.peer.uid)
        return {"discarded": True}

    def services_control(self, call: Call) -> dict:
        name = call.params.get("name", "")
        action = call.params.get("action", "")
        self.services.control(name, action)
        history.append(
            self.paths.history_file,
            {"kind": "service", "uid": call.peer.uid, "summary": [f"{action} {name}"], "result": "ok"},
        )
        return {"name": name, "action": action}

    def services_log(self, call: Call) -> dict:
        return {"lines": self.services.log(call.params.get("name", ""), int(call.params.get("lines", 100)))}

    def gc(self, call: Call) -> dict:
        self.engine.require_host()
        keep = int(call.params.get("keep", 5))
        if not 2 <= keep <= 100:
            raise InvalidInput("keep must be between 2 and 100")
        with self.engine.exclusive("collecting garbage"):
            emit_line = lambda line: call.emit({"type": "log", "line": line})  # noqa: E731
            r1 = self.runner.run(
                ["nix-env", "--profile", str(self.paths.system_profile), "--delete-generations", f"+{keep}"],
                on_line=emit_line,
            )
            if not r1.ok:
                raise ZedError("could not delete old generations", detail="\n".join(r1.stderr_tail[-10:]))
            r2 = self.runner.run(["nix-store", "--gc"], on_line=emit_line, cancel=call.cancel)
            if not r2.ok:
                raise ZedError("garbage collection failed", detail="\n".join(r2.stderr_tail[-10:]))
        freed = next(
            (line for line in reversed(r2.stderr_tail + r2.stdout.splitlines()) if "freed" in line), ""
        )
        history.append(
            self.paths.history_file,
            {
                "kind": "gc",
                "uid": call.peer.uid,
                "summary": [f"kept {keep} generations", freed],
                "result": "ok",
            },
        )
        return {"kept": keep, "freed": freed}


def _known(catalog: Catalog, cid: str) -> bool:
    try:
        catalog.get(cid)
        return True
    except ZedError:
        return False


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        zedd = self.server.zedd
        cancel = CancelToken()
        try:
            peer = peer_of(self.request)
        except OSError:
            return
        wlock = threading.Lock()

        def send(obj: dict) -> None:
            with wlock:
                try:
                    self.wfile.write(encode(obj))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # Client went away: stop any build it started.
                    cancel.cancel()

        try:
            msg = read_message(self.rfile)
        except ProtocolError as e:
            send({"error": {"code": "invalid_input", "message": str(e)}})
            return
        if msg is None:
            return
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            send({"error": {"code": "invalid_input", "message": "request needs a method and object params"}})
            return
        log.info("%s(uid=%d pid=%d) %s", peer.user, peer.uid, peer.pid, method)
        call = Call(peer=peer, params=params, emit=lambda ev: send({"event": ev}), cancel=cancel)
        try:
            result = zedd.dispatch(method, call)
            send({"result": result})
            if method == "plan.commit" and zedd.outdated():
                # The new generation ships a different zedd: exit so that
                # s6-supervise starts the new one.
                log.info("zedd was updated; restarting")
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except ZedError as e:
            log.info("%s failed: %s", method, e.message)
            send({"error": e.to_dict()})
        except Exception as e:  # noqa: BLE001 - report, never crash the daemon
            log.exception("internal error in %s", method)
            send({"error": {"code": "internal", "message": f"internal error: {e}"}})


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, zedd: Zedd):
        self.zedd = zedd
        super().__init__(path, _Handler)


def serve(paths: Paths, policy: Policy, notify_fd: int | None = None) -> None:
    zedd = Zedd(paths, policy)
    zedd.engine.cleanup_stale()
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.run_dir, 0o755)  # noqa: S103 - world-readable so clients can reach the socket
    sock = paths.socket
    with contextlib.suppress(FileNotFoundError):
        sock.unlink()
    server = _Server(str(sock), zedd)
    # Any local user may connect; each method checks authorisation itself.
    os.chmod(sock, 0o666)  # noqa: S103 - every method authorises the caller itself

    def stop(signum, frame):  # noqa: ARG001
        def _shutdown() -> None:
            # Never stop in the middle of an activation.
            with zedd.engine._lock:
                server.shutdown()

        threading.Thread(target=_shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if notify_fd is not None:
        os.write(notify_fd, b"\n")
        os.close(notify_fd)
    log.info("zedd %s listening on %s", __version__, sock)
    server.serve_forever()
    server.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zedd", description="Zed management daemon")
    p.add_argument("--admin-group", default=None, help="group whose members may change the system")
    p.add_argument("--notify-fd", type=int, default=None, help="s6 readiness notification fd")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    if os.geteuid() != 0 and "ZED_ROOT" not in os.environ:
        print("zedd must run as root", file=sys.stderr)
        return 1
    serve(default_paths(), Policy(args.admin_group), args.notify_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
