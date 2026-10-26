"""zed: the Zed command-line system manager.

zed is a client of zedd and uses exactly the same operations as Zed Arsenal,
Blueprint and Pulse, so terminal and graphical changes always agree.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any

from . import __version__
from .client import Client, DaemonUnavailable
from .errors import PermissionDenied, ZedError

# --- output helpers ------------------------------------------------------

_TTY = sys.stdout.isatty()
_COLOR = _TTY and os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"


def _c(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if _COLOR else s


def bold(s: str) -> str:
    return _c("1", s)


def dim(s: str) -> str:
    return _c("2", s)


def red(s: str) -> str:
    return _c("1;38;2;196;30;58", s)


def green(s: str) -> str:
    return _c("32", s)


def yellow(s: str) -> str:
    return _c("33", s)


class Progress:
    """Shows zedd progress events: steps always, build output with --verbose."""

    def __init__(self, verbose: bool):
        self.verbose = verbose
        self._status_shown = False

    def __call__(self, ev: dict) -> None:
        if ev.get("type") == "step":
            self._clear()
            print(f"{red('==>')} {bold(ev.get('message', ''))}", file=sys.stderr)
        elif ev.get("type") == "log":
            line = ev.get("line", "")
            if self.verbose:
                print(dim(line), file=sys.stderr)
            elif sys.stderr.isatty():
                width = shutil.get_terminal_size((80, 20)).columns - 1
                sys.stderr.write("\r\x1b[2K" + dim(line[:width]))
                sys.stderr.flush()
                self._status_shown = True

    def _clear(self) -> None:
        if self._status_shown:
            sys.stderr.write("\r\x1b[2K")
            sys.stderr.flush()
            self._status_shown = False

    def done(self) -> None:
        self._clear()


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("zed: refusing to change the system without confirmation; pass --yes", file=sys.stderr)
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def print_plan(plan: dict) -> None:
    print(bold("Planned changes:"))
    for line in plan.get("summary", []):
        print(f"  {line}")
    for w in plan.get("warnings", []):
        print(f"  {yellow('warning:')} {w}")
    diff = plan.get("closureDiff") or []
    if diff:
        print(bold("Package changes in the system closure:"))
        shown = diff[:40]
        for line in shown:
            print(f"  {line}")
        if len(diff) > len(shown):
            print(dim(f"  ... and {len(diff) - len(shown)} more"))
    if plan.get("restarts"):
        print(bold("Services that will restart:"))
        for s in plan["restarts"]:
            print(f"  {s}")
    for r in plan.get("rebootReasons", []):
        print(f"  {yellow('reboot:')} {r}")


# --- privilege handling ----------------------------------------------------


def ensure_admin(client: Client, args: argparse.Namespace) -> None:
    """Re-run this command through sudo when zedd will not accept it from us."""
    if os.geteuid() == 0:
        return
    info = client.call("ping")
    if info.get("admin"):
        return
    if args.no_sudo or not shutil.which("sudo"):
        raise PermissionDenied(
            "changing the system requires administrator rights", hint="run the command with sudo"
        )
    exe = shutil.which(sys.argv[0]) or sys.argv[0]
    print(dim("zed: administrator rights needed; asking sudo"), file=sys.stderr)
    sys.stdout.flush()
    os.execvp("sudo", ["sudo", "--", exe, *sys.argv[1:]])  # noqa: S606 - replaces this process with sudo; argv is our own


def run_plan(client: Client, args: argparse.Namespace, method: str, params: dict, verb: str) -> int:
    ensure_admin(client, args)
    progress = Progress(args.verbose)
    try:
        plan = client.call(method, params, on_event=progress)
    finally:
        progress.done()
    if args.json and (plan.get("noop") or args.dry_run):
        print(json.dumps(plan, indent=2))
        return 0
    if plan.get("noop"):
        for line in plan.get("summary", []):
            print(line)
        for w in plan.get("warnings", []):
            print(f"{yellow('warning:')} {w}")
        return 0
    print_plan(plan)
    if args.dry_run:
        client.call("plan.discard", {"id": plan["id"]})
        print(dim("dry run: nothing was changed"))
        return 0
    if not confirm(f"{verb}?", args.yes):
        client.call("plan.discard", {"id": plan["id"]})
        print("cancelled; nothing was changed")
        return 1
    progress = Progress(args.verbose)
    try:
        result = client.call("plan.commit", {"id": plan["id"]}, on_event=progress)
    finally:
        progress.done()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if result.get("result") == "unchanged":
        print("nothing changed")
        return 0
    print(green("done") + f": generation {result.get('generation')} is active")
    for r in result.get("rebootReasons", []):
        print(f"{yellow('reboot:')} {r}")
    return 0


# --- commands ------------------------------------------------------------


def cmd_install(client: Client, args) -> int:
    if not args.packages and not args.collection:
        raise ZedError("name at least one package or --collection")
    changes = {"install": args.packages, "add_collections": args.collection or []}
    return run_plan(client, args, "plan.create", {"changes": changes}, "Install")


def cmd_remove(client: Client, args) -> int:
    if not args.packages and not args.collection:
        raise ZedError("name at least one package or --collection")
    changes = {"remove": args.packages, "remove_collections": args.collection or []}
    return run_plan(client, args, "plan.create", {"changes": changes}, "Remove")


def cmd_upgrade(client: Client, args) -> int:
    return run_plan(client, args, "plan.create", {"changes": {"upgrade": True}}, "Upgrade")


def cmd_apply(client: Client, args) -> int:
    return run_plan(client, args, "plan.create", {"changes": {"rebuild": True}}, "Apply")


def cmd_rollback(client: Client, args) -> int:
    return run_plan(client, args, "plan.rollback", {"generation": args.generation}, "Roll back")


def cmd_search(client: Client, args) -> int:
    progress = Progress(args.verbose)
    try:
        res = client.call("search", {"terms": args.terms, "limit": args.limit}, on_event=progress)
    finally:
        progress.done()
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    if not res["results"]:
        print("no packages found")
        return 1
    for r in res["results"]:
        mark = green(" [installed]") if r["installed"] else ""
        print(f"{bold(r['attr'])} {dim(r.get('version') or '')}{mark}")
        if r.get("description"):
            print(f"  {r['description']}")
    if res["total"] > len(res["results"]):
        print(dim(f"{res['total'] - len(res['results'])} more matches; refine the search or use --limit"))
    return 0


def cmd_info(client: Client, args) -> int:
    res = client.call("info", {"package": args.package})
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"{bold(res['attr'])} {res.get('version') or ''}")
    if res.get("description"):
        print(f"  {res['description']}")
    if res.get("homepage"):
        print(f"  homepage: {res['homepage']}")
    lic = ", ".join((lic_.get("spdx") or lic_.get("name") or "?") for lic_ in res.get("licenses", []))
    print(f"  licence:  {lic or 'unknown'}" + (yellow(" (proprietary)") if res.get("unfree") else ""))
    if res.get("mainProgram"):
        print(f"  command:  {res['mainProgram']}")
    print(f"  installed: {'yes (' + ', '.join(res['installedBy']) + ')' if res['installed'] else 'no'}")
    if res.get("problem"):
        print(f"  {red('cannot install:')} {res['problem']}")
    return 0


def cmd_list(client: Client, args) -> int:
    res = client.call("list")
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    print(bold("Packages installed with zed:"))
    for p in res["packages"] or []:
        print(f"  {p}")
    if not res["packages"]:
        print(dim("  none"))
    print(bold("Collections:"))
    for c in res["collections"] or []:
        print(f"  {c['id']}  {dim(c['title'] + ', ' + str(len(c['packages'])) + ' packages')}")
    if not res["collections"]:
        print(dim("  none"))
    print(dim("Packages declared in /etc/zed/host.nix are not listed here."))
    return 0


def cmd_collections(client: Client, args) -> int:
    res = client.call("collections")
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    for c in res["collections"]:
        mark = green(" [installed]") if c["installed"] else ""
        print(f"{bold(c['id'])}{mark}  {c['title']}")
        print(f"  {c['description']}")
        print(dim(f"  {' '.join(c['packages'])}"))
    return 0


def cmd_update(client: Client, args) -> int:
    progress = Progress(args.verbose)
    try:
        res = client.call("update.check", on_event=progress)
    finally:
        progress.done()
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    cur = (res.get("current") or "?")[:12]
    if res["updateAvailable"]:
        print(f"An update is available on the {bold(res['channel'])} channel: {cur} -> {res['latest'][:12]}")
        print("Run 'zed upgrade' to review and apply it.")
    else:
        print(f"Zed is up to date on the {bold(res['channel'])} channel ({cur}).")
    return 0


def cmd_generations(client: Client, args) -> int:
    res = client.call("generations")
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    for g in res["generations"]:
        flags = [
            f
            for f, on in (("current", g["current"]), ("boot default", g["default"]), ("booted", g["booted"]))
            if on
        ]
        tag = f" {green('(' + ', '.join(flags) + ')')}" if flags else ""
        pk = g.get("packages")
        extra = f", {len(pk)} zed packages" if pk is not None else ""
        print(
            f"{bold(str(g['number']).rjust(4))}  {g['created'][:16].replace('T', ' ')}  "
            f"{g.get('zedVersion') or 'zed ?'}  kernel {g.get('kernel') or '?'}{extra}{tag}"
        )
    return 0


def cmd_history(client: Client, args) -> int:
    res = client.call("history", {"limit": args.limit})
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    for h in res["history"]:
        res_s = h.get("result", "?")
        colour = green if res_s == "ok" else red
        gens = f" {h.get('from', '?')} -> {h.get('to', '?')}" if "to" in h else ""
        print(
            f"{h['time'][:19].replace('T', ' ')}  {colour(res_s):<10} {h.get('kind')}{gens}: {'; '.join(h.get('summary', []))}"  # noqa: E501
        )
    return 0


def cmd_doctor(client: Client, args) -> int:
    res = client.call("doctor")
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    worst = 0
    rank = {"ok": 0, "unknown": 1, "warning": 1, "error": 2}
    for c in res["checks"]:
        st = c["status"]
        worst = max(worst, rank.get(st, 1))
        sym = {
            "ok": green("ok     "),
            "warning": yellow("warning"),
            "error": red("error  "),
            "unknown": dim("unknown"),
        }[st]
        print(f"{sym}  {bold(c['title'])}: {c['message']}")
        if c.get("hint"):
            print(f"         {dim(c['hint'])}")
    return worst


def cmd_status(client: Client, args) -> int:
    res = client.call("status")
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"zed {res['version']}{' (live environment)' if res['live'] else ''}")
    print(
        f"boot: {res['boot']['status']}"
        + (f" ({', '.join(res['boot']['failed'])} failed)" if res["boot"]["failed"] else "")
    )
    print(f"generation: {res['generation']}")
    if res.get("busy"):
        print(f"busy: {res['busy']}")
    for r in res.get("rebootRequired", []):
        print(f"{yellow('reboot:')} {r}")
    return 0


def cmd_services(client: Client, args) -> int:
    action = args.action or "list"
    if action == "list":
        res = client.call("services.list")
        if args.json:
            print(json.dumps(res, indent=2))
            return 0
        for s in res["services"]:
            if s["name"].endswith("-log") and not args.all:
                continue
            state = green("up  ") if s["up"] else (dim("down") if not s["autoStart"] else red("down"))
            pid = f" pid {s['pid']}" if s.get("pid") else ""
            print(f"{state}  {bold(s['name']):<34} {dim(s['description'])}{dim(pid)}")
        return 0
    if not args.name:
        raise ZedError(f"'zed services {action}' needs a service name")
    if action == "log":
        ensure_admin(client, args)
        res = client.call("services.log", {"name": args.name, "lines": args.lines})
        print("\n".join(res["lines"]))
        return 0
    ensure_admin(client, args)
    client.call("services.control", {"name": args.name, "action": action})
    print(f"{action}: {args.name} {green('ok')}")
    return 0


def _parse_value(setting: dict, raw: str) -> Any:
    t = setting["type"]
    if t == "bool":
        low = raw.lower()
        if low in ("true", "on", "yes", "1"):
            return True
        if low in ("false", "off", "no", "0"):
            return False
        raise ZedError(f"{setting['key']} expects true or false")
    if t == "int":
        try:
            return int(raw)
        except ValueError:
            raise ZedError(f"{setting['key']} expects a whole number") from None
    if t == "list":
        if raw.strip().startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise ZedError(f"invalid list: {e}") from None
        items = [x.strip() for x in raw.split(",") if x.strip()]
        if setting.get("items", {}).get("type") == "int":
            try:
                return [int(x) for x in items]
            except ValueError:
                raise ZedError(f"{setting['key']} expects numbers") from None
        return items
    if t == "enum" and setting.get("nullable") and raw in ("none", "null", "unset"):
        return None
    return raw


def cmd_config(client: Client, args) -> int:
    action = args.action or "list"
    schema = client.call("config.schema")
    by_key = {s["key"]: s for s in schema["settings"]}
    if action in ("list", "schema"):
        current = client.call("config.get")["settings"]
        if args.json:
            print(json.dumps({"schema": schema, "settings": current}, indent=2))
            return 0
        titles = dict((s["id"], s["title"]) for s in schema["sections"])
        section = None
        for s in schema["settings"]:
            if s["section"] != section:
                section = s["section"]
                print(bold(titles[section]))
            val = json.dumps(current[s["key"]]) if s["key"] in current else dim("(default)")
            print(f"  {s['key']:<40} {val}")
            if args.verbose:
                print(dim(f"      {s['explain']}"))
        return 0
    if not args.key:
        raise ZedError(f"'zed config {action}' needs a setting name")
    setting = by_key.get(args.key)
    if setting is None:
        raise ZedError(f"unknown setting {args.key!r}; 'zed config list' shows them all")
    if action in ("get", "explain"):
        cur = client.call("config.get", {"key": args.key})
        print(f"{bold(setting['title'])} ({args.key})")
        print(f"  value: {json.dumps(cur['value']) if cur['managed'] else 'default (not set by zed)'}")
        if setting.get("option"):
            print(f"  NixOS option: {setting['option']}")
        if setting.get("choices"):
            print(f"  choices: {', '.join(setting['choices'])}")
        print(f"  {setting['explain']}")
        if setting.get("danger"):
            print(f"  {yellow('caution:')} {setting['danger']}")
        return 0
    if action == "set":
        if args.value is None:
            raise ZedError("'zed config set' needs a value")
        value = _parse_value(setting, args.value)
        return run_plan(client, args, "plan.create", {"changes": {"set": {args.key: value}}}, "Apply")
    if action == "reset":
        return run_plan(client, args, "plan.create", {"changes": {"reset": [args.key]}}, "Apply")
    raise ZedError(f"unknown config action {action!r}")


def cmd_gc(client: Client, args) -> int:
    ensure_admin(client, args)
    if not confirm(
        f"Delete all but the newest {args.keep} system generations and collect garbage?", args.yes
    ):
        return 1
    progress = Progress(args.verbose)
    try:
        res = client.call("gc", {"keep": args.keep}, on_event=progress)
    finally:
        progress.done()
    print(green("done") + (f": {res['freed']}" if res.get("freed") else ""))
    return 0


# --- argument parsing ----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-v", "--verbose", action="store_true", help="show build output and details")
    common.add_argument("--no-sudo", action="store_true", help="never re-run through sudo")

    change = argparse.ArgumentParser(add_help=False)
    change.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    change.add_argument("-n", "--dry-run", action="store_true", help="show the plan without applying it")

    p = argparse.ArgumentParser(
        prog="zed",
        description="Manage a Zed system: packages, settings, services, updates and rollbacks.",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=f"zed {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, func, help_, parents=(common,), **kw):
        sp = sub.add_parser(name, help=help_, description=help_, parents=list(parents), **kw)
        sp.set_defaults(func=func)
        return sp

    sp = add("install", cmd_install, "install packages or tool collections", (common, change))
    sp.add_argument("packages", nargs="*", metavar="PACKAGE", help="nixpkgs attribute, e.g. firefox")
    sp.add_argument("-c", "--collection", action="append", metavar="ID", help="install a tool collection")
    sp = add("remove", cmd_remove, "remove packages or tool collections", (common, change))
    sp.add_argument("packages", nargs="*", metavar="PACKAGE")
    sp.add_argument("-c", "--collection", action="append", metavar="ID")
    sp = add("search", cmd_search, "search the package set")
    sp.add_argument("terms", nargs="+", metavar="TERM")
    sp.add_argument("--limit", type=int, default=30)
    sp = add("info", cmd_info, "show details of a package")
    sp.add_argument("package")
    add("list", cmd_list, "list packages and collections installed with zed")
    add("collections", cmd_collections, "list the security tool collections")
    add("update", cmd_update, "check whether a Zed update is available")
    add("upgrade", cmd_upgrade, "review and apply the latest Zed release", (common, change))
    add("apply", cmd_apply, "rebuild after editing /etc/zed/host.nix", (common, change))
    sp = add("rollback", cmd_rollback, "return to an earlier system generation", (common, change))
    sp.add_argument("generation", nargs="?", type=int, help="generation number (default: the previous one)")
    add("generations", cmd_generations, "list system generations")
    sp = add("history", cmd_history, "show the change history")
    sp.add_argument("--limit", type=int, default=30)
    add("doctor", cmd_doctor, "check the system's health")
    add("status", cmd_status, "show a short status summary")
    sp = add("services", cmd_services, "list or control s6 services", (common, change))
    sp.add_argument("action", nargs="?", choices=["list", "start", "stop", "restart", "log"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--all", action="store_true", help="include per-service loggers")
    sp.add_argument("--lines", type=int, default=100)
    sp = add("config", cmd_config, "show or change system settings", (common, change))
    sp.add_argument("action", nargs="?", choices=["list", "get", "explain", "set", "reset", "schema"])
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    sp = add("gc", cmd_gc, "delete old generations and free disk space", (common, change))
    sp.add_argument("--keep", type=int, default=5)
    sub.add_parser("help", help="show this help")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help()
        return 0
    client = Client()
    try:
        return args.func(client, args) or 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except DaemonUnavailable as e:
        print(f"{red('zed:')} {e.message}", file=sys.stderr)
        if e.hint:
            print(f"     {e.hint}", file=sys.stderr)
        return 3
    except ZedError as e:
        print(f"{red('zed:')} {e.message}", file=sys.stderr)
        if e.detail:
            print(dim(e.detail), file=sys.stderr)
        if e.hint:
            print(f"     {e.hint}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
