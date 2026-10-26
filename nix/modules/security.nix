# Secure defaults common to every edition.  Each setting is reversible from
# host.nix or Zed Blueprint; none of them collects or transmits data.
{ lib, ... }:

let
  # Stronger than upstream mkDefault values (priority 1000), weaker than a
  # plain assignment in host.nix (priority 100).
  zedDefault = lib.mkOverride 900;
in
{
  # Firewall on, nothing open.
  networking.firewall.enable = zedDefault true;
  networking.firewall.allowedTCPPorts = zedDefault [ ];
  networking.firewall.allowedUDPPorts = zedDefault [ ];

  # No remote login unless the user turns it on.
  services.openssh.enable = zedDefault false;

  # Administration through wheel only, always with a password.
  security.sudo.enable = zedDefault true;
  security.sudo.wheelNeedsPassword = zedDefault true;
  security.sudo.execWheelOnly = zedDefault true;

  security.protectKernelImage = zedDefault true;

  boot.tmp.cleanOnBoot = zedDefault true;

  boot.kernel.sysctl = {
    # Hide kernel pointers and the kernel log from unprivileged users.
    "kernel.kptr_restrict" = zedDefault 2;
    "kernel.dmesg_restrict" = zedDefault 1;
    # Restrict ptrace to direct descendants.
    "kernel.yama.ptrace_scope" = zedDefault 1;
    # eBPF is root-only and JIT-hardened.
    "kernel.unprivileged_bpf_disabled" = zedDefault 1;
    "net.core.bpf_jit_harden" = zedDefault 2;
    # Core dumps can contain secrets; do not write them.  mkForce because
    # NixOS' coredump module sets this at normal priority.
    "kernel.core_pattern" = lib.mkForce "|/bin/false";
    "fs.suid_dumpable" = zedDefault 0;
    # Network stack: ignore redirects, never send them, use SYN cookies.
    "net.ipv4.conf.all.accept_redirects" = zedDefault 0;
    "net.ipv4.conf.default.accept_redirects" = zedDefault 0;
    "net.ipv6.conf.all.accept_redirects" = zedDefault 0;
    "net.ipv6.conf.default.accept_redirects" = zedDefault 0;
    "net.ipv4.conf.all.send_redirects" = zedDefault 0;
    "net.ipv4.conf.default.send_redirects" = zedDefault 0;
    "net.ipv4.tcp_syncookies" = zedDefault 1;
    "net.ipv4.conf.all.rp_filter" = zedDefault 2;
  };
}
