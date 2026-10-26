# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/SwashX-dev/Zed/security/advisories/new)
for this repository, not in public issues. Include the Zed version
(`zed status`), what you did and what happened.

## Supported versions

Zed has no stable release yet. Only the latest commit of the development
branch receives fixes.

## Threat model and defaults

Zed aims to keep a single-user or small multi-user machine safe from network
attackers and from unprivileged local programs, and to make every
administrative change explicit and reversible.

Defaults on every edition:

* nftables firewall enabled with no open ports, loaded fail-closed;
* no SSH server, no listening network services;
* sudo for the `wheel` group, always with a password;
* no default passwords, embedded credentials or hidden accounts;
* no telemetry, analytics or automatic upload of logs;
* kernel hardening sysctls (restricted kernel pointers and dmesg, Yama
  ptrace scope 1, unprivileged eBPF disabled, no core dumps).

### Privileged management (zedd)

* zedd is the only component that changes system configuration. It exposes a
  fixed set of methods; none runs arbitrary commands.
* Callers are identified by the kernel (`SO_PEERCRED`). Changing methods
  require root, or membership of an administrator group that is unset by
  default. zedd never handles passwords; the `zed` command elevates through
  sudo.
* Package names and settings are validated against strict patterns and the
  settings registry before use, and are passed to Nix as data, never as code
  or shell strings.
* Changes are built before activation and recorded in a history log;
  activation failures roll back automatically.

### Known weaknesses

* Services do not get systemd's sandboxing (see
  [docs/LIMITATIONS.md](docs/LIMITATIONS.md#service-hardening)).
* The privacy profiles change MAC address, DHCP and IPv6 behaviour. They do
  not anonymise traffic. Use Tor Browser or a Whonix-style setup for that.
* Security tools in Zed's collections are for systems you own or are
  authorised to test.
