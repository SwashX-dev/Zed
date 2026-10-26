# Zed

Zed is a NixOS-derived Linux distribution that runs **s6** and **s6-rc**
instead of systemd, with a declarative system manager (`zedd` and the `zed`
command) designed for privacy, defensive security work and authorised
security research.

> **Status: development snapshot (0.1.0 alpha).** The init system and the
> system manager work and are tested in QEMU. There is no desktop, installer
> or release ISO yet. See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for an
> exact list of what is and is not implemented.

## What works today

* **s6 init on NixOS.** s6-svscan is PID 1; s6-rc starts services in
  dependency order with one supervised logger per daemon; NixOS modules,
  options, generations and rollbacks keep working. NixOS services that Zed
  cannot run yet are reported at evaluation time instead of being silently
  ignored.
* **`zed`, a transactional system manager.** Install and remove packages and
  tool collections, change system settings, roll back, and inspect services,
  generations and health. Every change is built from a staged copy of the
  configuration first and activated only after it builds; failed activations
  restore the previous system automatically.

```console
$ zed search wireshark
$ zed install wireshark          # asks for sudo, shows the plan, then applies it
$ zed install --collection forensics
$ zed config set firewall.allowedTCPPorts 22
$ zed rollback
$ zed generations
$ zed services
$ zed doctor
```

## Building and testing

You need Nix with flakes on x86_64 Linux. KVM makes VM tests much faster.

```console
nix build .#checks.x86_64-linux.vm-boot      # boot test: PID 1, services, firewall, switch
nix build .#checks.x86_64-linux.vm-zed       # zed install / remove / rollback in a VM
nix build .#checks.x86_64-linux.registry     # every managed setting and collection evaluates
nix build .#zed-suite                        # zedd and the zed CLI (runs the unit tests)
nix develop -c pytest                        # unit tests only
```

## Documentation

* [Architecture](docs/ARCHITECTURE.md): boot path, s6-rc services, zedd and the transaction engine
* [Limitations](docs/LIMITATIONS.md)
* [Security](SECURITY.md)

## Licence

MIT; see [LICENSE](LICENSE). Zed packages and configures upstream software
under its own licences.
