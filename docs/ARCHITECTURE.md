# Zed architecture

Zed is NixOS with a different process supervisor. The NixOS module system,
`nixos-install`, generations, rollbacks and nearly all NixOS options work as
they do upstream. Zed replaces the parts of NixOS that require systemd to be
running as PID 1, and adds one management layer (zedd) that every Zed tool
goes through.

```
 zed CLI ─┐
 Arsenal ─┤  JSON over        ┌────────────── zedd (root, s6-supervised) ──────────────┐
Blueprint ┼─ /run/zed/zedd.sock ─► method table ─► transaction engine ─► nix build ─► switch-to-configuration
   Pulse ─┘                    └── authorisation: SO_PEERCRED uid / admin group ────────┘
                                                        │
                                       /etc/zed/managed/state.json (plain JSON)
                                                        │
                               nix/modules/managed.nix + settings registry → NixOS options
```

## Why not SixOS

[SixOS](https://codeberg.org/amjoseph/sixos) is the most complete s6-based
Nix operating system. It was evaluated as Zed's foundation and not adopted:

* SixOS shares no code with `nixpkgs/nixos` and replaces the NixOS module
  system with its own `infuse` combinator. NixOS options, `nixos-install`,
  `nixos-generate-config` and the NixOS Calamares integration would all be
  unavailable, while Zed's specification requires NixOS compatibility.
* SixOS is GPL-3.0-only; Zed is MIT. Zed contains no SixOS code.

Zed instead keeps NixOS intact and swaps only PID 1, supervision and
activation, in `nix/modules/init/`. SixOS remains a useful reference for
s6-rc service design.

## Boot path

```
firmware → boot loader (in the VM tests: QEMU's direct kernel boot)
  → kernel → scripted stage 1 (no systemd in the initrd)
  → NixOS stage-2-init.sh (mounts, activation script)
  → zed-preinit (unpacks the s6-linux-init basedir into /run/zed/init)
  → s6-linux-init → s6-svscan becomes PID 1 → rc.init → s6-rc-init
  → s6-rc brings up the `default` bundle → /run/zed/boot-status
```

* `boot.initrd.systemd.enable = false`: Zed uses NixOS' scripted stage 1.
  NixOS 26.05 deprecated it and plans to remove it in 26.11; see
  [LIMITATIONS.md](LIMITATIONS.md#scripted-initrd).
* `boot.systemdExecutable` points stage 2 at `zed-preinit` instead of systemd.
* s6-linux-init's basedir contains FIFOs and setgid directories, which the
  Nix store cannot hold, so it is built under fakeroot as a tar archive and
  unpacked into `/run` at every boot.
* A recovery login on **tty9** is started by s6-linux-init itself, before
  s6-rc, so the machine stays reachable if service startup fails.
* When the boot transition finishes, `/run/zed/boot-status` contains `ok`, or
  `degraded` followed by the services that did not come up. `zed doctor`
  reports it.

### systemd components that remain

Zed does not run systemd, systemd-journald, systemd-logind or systemd-udevd
as daemons from the systemd service manager. It does reuse these standalone
programs from the systemd package, because NixOS modules write their
configuration in systemd's file formats:

| Program | Used for | Run by |
|---|---|---|
| `systemd-udevd` | device manager (the udev daemon) | s6 longrun `zed-udevd` |
| `udevadm` | coldplug and settle | `zed-coldplug` |
| `systemd-tmpfiles` | `/etc/tmpfiles.d` | `zed-tmpfiles` |
| `systemd-sysctl` | `/etc/sysctl.d` | `zed-sysctl` |
| `systemd-modules-load` | `/etc/modules-load.d` | `zed-modules` |
| `systemd-vconsole-setup` | console keymap and font | `zed-console` |

None of them requires systemd as PID 1. The `vm-boot` test asserts that no
`systemd`, `systemd-journald` or `systemd-logind` process exists.

## Services: `zed.init.services`

Services are declared as Nix attribute sets and compiled with
`s6-rc-compile` into a database that is part of each system generation
(`/run/current-system/zed/s6-rc/compiled`).

* `type`: `longrun` (supervised daemon), `oneshot` (up/down scripts) or `bundle`.
* Every longrun gets its own `s6-log` logger writing to `/var/log/zed/<name>/`,
  connected through an s6-rc pipeline and owned by the unprivileged `zedlog`
  account.
* `fatal = true` oneshots stop dependants from starting when they fail. The
  firewall is fatal: if the nftables ruleset cannot load, NetworkManager and
  SSH do not start (fail closed).
* Where possible a service is *translated* from the upstream NixOS unit
  (`ExecStart`, environment, `StateDirectory`, ...), so NixOS options for that
  service keep working. Systemd sandboxing directives are **not** translated;
  services run with ordinary Unix privilege separation (see SECURITY.md).
* `zed.init.disabledServices` removes services from the boot bundle; core
  services are protected.
* Every generation carries `zed/services.json`, a manifest used by
  `zed services`.

### Compatibility report

`compat.nix` lists every systemd unit that a NixOS module enables for boot but
that Zed neither runs nor knowingly replaces. Such units would silently never
run, so:

* evaluation prints a warning naming them, and
* zedd refuses any change that adds a unit to that list
  (`zed/compatibility.json` in each generation).

### switch-to-configuration

NixOS' implementation drives systemd over D-Bus. Zed's
(`nix/modules/init/switch-to-configuration.sh`) runs the activation script,
installs the boot loader, then calls `s6-rc-update`. s6-rc-update does not
restart services whose scripts changed, so each generation records a hash of
every service directory and the switch script restarts exactly those that
changed. Services that would end the user's session (D-Bus, udev, seatd,
display managers) set `restartOnSwitch = false`.

s6-rc databases are only compatible within one s6-rc major series. If an
update moves to a different series, the switch does not touch the running
services, records the reason in `/run/zed/reboot-required` and the change
takes effect at the next boot.

## zedd and the transaction engine

`core/zedcore` is the only code that changes a Zed system.

### Host layout

```
/etc/zed/
├── flake.nix                  inputs: zed (pinned release), nixpkgs follows zed's
├── flake.lock
├── host.nix                   the user's own NixOS configuration; Zed never writes it
├── hardware-configuration.nix written by the installer
└── managed/state.json         written only by zedd
```

`zed.lib.mkHost` builds the system from these files. The managed state is
data, not code:

```json
{
  "version": 1,
  "packages": ["firefox"],
  "collections": ["forensics"],
  "settings": {"firewall.allowedTCPPorts": [22]}
}
```

`nix/modules/managed.nix` maps it onto NixOS options using the settings
registry `core/zedcore/data/settings.json`, which is shared by the Python
validator and the Nix module. A key that is not in the registry cannot be set
through zedd. The `registry` flake check sets every registry entry to its
example value in a real NixOS evaluation, so the registry cannot name an
option that does not exist or give it a value of the wrong type.

Settings from `state.json` are ordinary option definitions. If `host.nix`
sets the same non-mergeable option, the build fails with a conflict and zedd
reports it; Zed never overrides the user's own file silently.

### Transactions

1. **Plan.** zedd validates the request, applies it to a copy of the managed
   state, copies the host flake to `/run/zed/plans/<id>/flake`, writes the new
   state there and builds the system. Nothing on the running system changes.
   The plan reports the closure diff (`nix store diff-closures`), the services
   `switch-to-configuration dry-activate` would restart, and reboot
   requirements (kernel, initrd, s6-rc series).
2. **Commit.** zedd re-checks that `state.json` and `flake.lock` are unchanged
   since the plan (otherwise the plan is refused as stale), records the new
   generation, replaces `state.json` atomically (write, fsync, rename, fsync
   directory) and runs `switch-to-configuration switch`.
3. **Failure.** If activation fails, zedd restores the previous generation as
   boot default, restores `state.json` and `flake.lock`, and re-activates the
   previous system. The failure and the restore are recorded in
   `/var/lib/zed/history.jsonl`; the full log is in
   `/var/log/zed/transactions/<id>.log`.

Every generation stores the state it was built from in
`zed/managed-state.json`, so `zed rollback` restores both the system and the
matching state file.

Only one plan or commit runs at a time (an in-process lock plus `flock` on
`/run/zed/transaction.lock`). Read-only methods run concurrently.

### API and authorisation

zedd listens on `/run/zed/zedd.sock` (newline-delimited JSON, one request per
connection, progress events streamed before the final result). There is a
fixed method table and no method that runs arbitrary commands. All external
programs are run as argument vectors without a shell; package names reach Nix
only as validated attribute paths.

The caller's identity comes from the kernel (`SO_PEERCRED`):

* read methods (`info`, `search`, `list`, `generations`, `doctor`, ...): any
  local user;
* changing methods (`plan.*`, `services.control`, `gc`, ...): root, or
  members of `zed.daemon.adminGroup` if the administrator sets one (unset by
  default).

zedd never receives or checks passwords. The `zed` command re-runs itself
through `sudo` when the caller is not an administrator, so authentication is
sudo's.
