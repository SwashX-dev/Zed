# Known limitations

Zed is in early development (0.1.0 alpha). This file lists what is not
supported, not verified or known to be broken. It is updated with every
change that affects it.

## Status of the specification's components

| Component | Status |
|---|---|
| s6 / s6-rc init (PID 1, supervision, logging, switch) | Implemented; verified in the `vm-boot` QEMU test |
| zedd, transaction engine, `zed` CLI | Implemented; unit-tested and exercised in the `vm-zed` QEMU test |
| Settings registry (30 settings) and tool collections (8) | Implemented; every entry evaluated by the `registry` flake check |
| Privacy profiles (standard / private / custom) | Implemented as configuration; network effects not yet measured in a test |
| Graphical desktop, branding, live ISO, Calamares installer | Not implemented yet |
| Arsenal, Blueprint, Pulse, Veil, Capsules, Sentinel, Range, Identity, Ghost, Forge | Not implemented yet |
| Release channels, release ISOs | Not published yet |

## Init system

### Scripted initrd

Zed's boot path avoids systemd in the initrd by using NixOS' scripted stage 1
(`boot.initrd.systemd.enable = false`). NixOS 26.05 deprecated it and plans
to remove it in NixOS 26.11. Zed is pinned to 26.05. Before moving to 26.11,
Zed must either carry the scripted stage 1 itself or ship its own initrd; until
then Zed will not follow 26.11.

### Service hardening

systemd sandboxing directives in NixOS units (`ProtectSystem`, `PrivateTmp`,
`CapabilityBoundingSet`, `SystemCallFilter`, ...) are not translated. Services
under Zed run with plain Unix privilege separation, which is weaker than the
same service on stock NixOS.

### NixOS services without an s6 translation

A NixOS service that has no s6-rc translation will not run. Evaluation warns
about it and zedd refuses changes that add one. Supported services today:
D-Bus, udev, nscd, the Nix daemon, nftables firewall, NetworkManager, chrony,
seatd, greetd, polkit, BlueZ, UPower, acpid, OpenSSH and Tor. Notably **not**
supported yet: CUPS printing, Avahi, libvirtd, Docker/Podman services, zram,
systemd timers other than Zed's own maintenance jobs.

### Timers

Systemd timers are not run. Zed's own maintenance (garbage collection,
fstrim) runs from the `zed-periodic` service instead.

## Desktop environments

Investigated, not yet implemented:

* **GNOME** — GNOME 49 removed gnome-session's non-systemd code path, and
  downstream maintainers report GNOME cannot currently be started without
  systemd even with elogind. NixOS 26.05 ships a newer GNOME. GNOME is
  therefore expected to be unsupported on Zed unless gnome-session is
  patched; this will be confirmed in a VM test before any decision.
* **KDE Plasma** needs a logind implementation; non-systemd distributions use
  elogind with SDDM. Zed has no elogind integration yet.
* wlroots/smithay compositors (labwc, Sway, Niri, Hyprland) use libseat and
  should work with seatd, which Zed already supervises. Not yet tested.

## Management

* Plans live in zedd's memory; restarting zedd discards unconfirmed plans.
* `zed update`/`zed upgrade` need a GitHub release channel; no channel
  (`stable`/`testing` branch) has been published yet.
* `zed search` evaluates the whole package set on first use, which takes
  minutes and about 1–2 GB of memory; later searches use Nix's evaluation cache.

## Testing

* The development machine has no KVM, so VM tests run under QEMU's TCG
  emulation. No test has run on physical hardware.
* No hardware support (GPUs, Wi-Fi, audio, suspend) has been validated.
