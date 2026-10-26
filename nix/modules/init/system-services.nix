# Long-running system daemons under s6 supervision.
#
# Each entry is reviewed by hand.  Where possible the daemon command line,
# environment and generated configuration files come from the upstream
# NixOS module, so NixOS options (services.chrony.servers,
# networking.networkmanager.*, services.greetd.settings, ...) keep working.
#
# Not translated: systemd sandboxing directives (ProtectSystem, PrivateTmp,
# capability bounding, ...).  Services run with plain Unix privilege
# separation only.  docs/SECURITY.md documents this difference.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkIf
    mkMerge
    mkForce
    optionalAttrs
    ;

  cfg = config.zed.init;
  units = config.systemd.services;

  # systemd allows a list of ExecStart= lines where "" resets the list, and
  # prefixes such as "-", "!", "@", "+", ":".  Take the effective last line.
  execStartOf =
    unit:
    let
      raw = units.${unit}.serviceConfig.ExecStart;
      lines = lib.filter (l: l != "") (map toString (lib.toList raw));
      last = lib.last lines;
    in
    lib.removePrefix "!" (lib.removePrefix "-" (lib.removePrefix "+" (lib.removePrefix ":" last)));

  # Every command line of a key, in order, with systemd's prefixes removed.
  execLines =
    unit: key:
    let
      raw = units.${unit}.serviceConfig.${key} or [ ];
      strip = l: lib.removePrefix "!" (lib.removePrefix "-" (lib.removePrefix "+" (lib.removePrefix ":" l)));
    in
    map strip (lib.filter (l: l != "") (map toString (lib.toList raw)));

  # A Type=oneshot unit as one script: ExecStartPre, every ExecStart line,
  # then ExecStartPost, stopping at the first failure as systemd does.
  oneshotScriptOf =
    unit:
    lib.concatStringsSep "\n" (
      execLines unit "ExecStartPre" ++ execLines unit "ExecStart" ++ execLines unit "ExecStartPost"
    );

  envOf = unit: removeAttrs (units.${unit}.environment or { }) [ "PATH" ];

  # Create systemd-style managed directories owned by the service account.
  managedDirs =
    unit: user:
    let
      sc = units.${unit}.serviceConfig;
      dirs =
        base: key:
        map (d: "${base}/${d}") (lib.concatMap (lib.splitString " ") (lib.toList (sc.${key} or [ ])));
      all =
        dirs "/run" "RuntimeDirectory"
        ++ dirs "/var/lib" "StateDirectory"
        ++ dirs "/var/cache" "CacheDirectory"
        ++ dirs "/var/log" "LogsDirectory";
      owner = if user == null then "" else "-o ${user}";
    in
    lib.concatMapStrings (d: "install -d -m 0755 ${owner} ${d}\n") all;

  translate =
    unit: extra:
    let
      user = units.${unit}.serviceConfig.User or null;
    in
    {
      description = units.${unit}.description or unit;
      path = lib.filter lib.isDerivation (units.${unit}.path or [ ]);
      environment = envOf unit;
      inherit user;
      preStart = managedDirs unit user;
      script = "exec ${execStartOf unit}";
    }
    // extra;

  dbusCfg = config.services.dbus;
in
{
  config = mkIf cfg.enable (mkMerge [
    {
      # dbus-broker requires systemd as its launcher; Zed uses the reference
      # dbus-daemon with its setuid activation helper.
      services.dbus.implementation = mkForce "dbus";

      # nftables is Zed's only supported firewall backend.
      networking.nftables.enable = lib.mkDefault true;

      # ModemManager is pulled in by NetworkManager upstream; it is optional
      # hardware support, not a default service.
      networking.modemmanager.enable = lib.mkOverride 900 false;
    }

    (mkIf dbusCfg.enable {
      zed.init.services.zed-dbus = {
        description = "D-Bus system message bus";
        dependencies = [
          "zed-sysinit"
          "zed-wrappers"
        ];
        restartOnSwitch = false;
        essential = true;
        notificationFd = 3;
        preStart = "install -d -m 0755 /run/dbus";
        script = "exec ${dbusCfg.dbusPackage}/bin/dbus-daemon --system --nofork --nopidfile --nosyslog --print-address=3";
      };

      # dbus-daemon is never restarted; reload its policy when it changes.
      # The config directory path is part of the script, so this oneshot's
      # hash changes exactly when the D-Bus configuration does.
      zed.init.services.zed-dbus-reload = {
        type = "oneshot";
        description = "reload D-Bus policy (${config.environment.etc."dbus-1".source})";
        dependencies = [ "zed-dbus" ];
        up = ''
          ${dbusCfg.dbusPackage}/bin/dbus-send --system --type=method_call \
            --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig
        '';
      };
    })

    (mkIf config.services.nscd.enable {
      zed.init.services.zed-nscd = translate "nscd" {
        description = "name service cache (nsncd)";
        dependencies = [ "zed-sysinit" ];
        environment = envOf "nscd" // {
          NSNCD_HANDOFF_TIMEOUT = "10";
        };
      };
    })

    (mkIf (units ? register-nix-paths && units.register-nix-paths.enable) {
      # VM images only: load the Nix database shipped with the image.
      zed.init.services.zed-register-nix-paths = {
        type = "oneshot";
        description = "register store paths of the VM image";
        dependencies = [ "zed-mounts" ];
        restartOnSwitch = false;
        up = units.register-nix-paths.script;
      };
      zed.init.services.zed-nix-daemon.dependencies = [ "zed-register-nix-paths" ];
    })

    (mkIf (units ? post-boot && units.post-boot.enable) {
      zed.init.services.zed-post-boot = {
        type = "oneshot";
        description = "power management commands at boot";
        dependencies = [ "zed-sysinit" ];
        up = units.post-boot.script;
      };
    })

    (mkIf config.nix.enable {
      zed.init.services.zed-nix-daemon = {
        description = "Nix build daemon";
        dependencies = [ "zed-sysinit" ];
        path = [
          config.nix.package
          config.programs.ssh.package
        ];
        environment = envOf "nix-daemon";
        preStart = "install -d -m 0755 /nix/var/nix/daemon-socket";
        script = "exec ${config.nix.package.out}/bin/nix-daemon --daemon";
      };
    })

    (mkIf config.networking.nftables.enable {
      # Fail closed: if the ruleset does not load, networking does not start.
      # `down` is deliberately empty so a restart re-applies the ruleset
      # atomically instead of opening a window with no firewall.
      zed.init.services.zed-firewall = {
        type = "oneshot";
        description = "nftables firewall ruleset";
        dependencies = [ "zed-sysinit" ];
        fatal = true;
        up = ''
          ${managedDirs "nftables" null}
          ${oneshotScriptOf "nftables"}
        '';
      };
    })

    (mkIf config.networking.networkmanager.enable {
      zed.init.services.zed-networkmanager = translate "NetworkManager" {
        description = "network connection manager";
        dependencies = [
          "zed-dbus"
          "zed-coldplug"
        ]
        ++ lib.optional config.networking.nftables.enable "zed-firewall";
        preStart = ''
          install -d -m 0755 /run/NetworkManager /var/lib/NetworkManager
          install -d -m 0700 /etc/NetworkManager/system-connections
        '';
        script = "exec ${config.networking.networkmanager.package}/bin/NetworkManager --no-daemon";
      };
    })

    (mkIf (units ? wpa_supplicant && units.wpa_supplicant.enable) {
      # Upstream runs it as the wpa_supplicant user with two ambient
      # capabilities; root-only setup steps ("+" prefixed) run in preStart.
      zed.init.services.zed-wpa-supplicant = {
        description = "Wi-Fi authentication (wpa_supplicant)";
        dependencies = [
          "zed-dbus"
          "zed-coldplug"
        ];
        path = [ pkgs.util-linux ];
        preStart = ''
          ${managedDirs "wpa_supplicant" "wpa_supplicant"}
          ${lib.concatStringsSep "\n" (execLines "wpa_supplicant" "ExecStartPre")}
        '';
        script = ''
          exec setpriv --reuid=wpa_supplicant --regid=wpa_supplicant --init-groups \
            --inh-caps=+net_admin,+net_raw --ambient-caps=+net_admin,+net_raw \
            ${execStartOf "wpa_supplicant"}
        '';
      };
      zed.init.services.zed-networkmanager.dependencies = lib.mkIf config.networking.networkmanager.enable [
        "zed-wpa-supplicant"
      ];
    })

    (mkIf config.services.chrony.enable {
      zed.init.services.zed-chrony = translate "chronyd" {
        description = "network time synchronisation (chrony)";
        dependencies = [ "zed-sysinit" ];
        # chronyd drops to the chrony user itself after binding.
        user = null;
        preStart = ''
          install -d -m 0750 -o chrony -g chrony /var/lib/chrony
          install -d -m 0750 -o chrony -g chrony /run/chrony
        '';
      };
    })

    (mkIf config.services.seatd.enable {
      zed.init.services.zed-seatd = {
        description = "seat management (seatd)";
        dependencies = [ "zed-coldplug" ];
        restartOnSwitch = false;
        notificationFd = 3;
        script = with config.services.seatd; "exec ${pkgs.seatd.bin}/bin/seatd -n 3 -u ${user} -g ${group} -l ${logLevel}";
      };
    })

    (mkIf config.services.greetd.enable {
      zed.init.services.zed-greetd = translate "greetd" {
        description = "login manager (greetd)";
        dependencies = [
          "zed-sysinit"
          "zed-wrappers"
          "zed-dbus"
        ]
        ++ lib.optional config.services.seatd.enable "zed-seatd";
        user = null;
        restartOnSwitch = false;
      };
    })

    (mkIf config.security.polkit.enable {
      zed.init.services.zed-polkit = {
        description = "PolicyKit authorization manager";
        dependencies = [ "zed-dbus" ];
        user = "polkituser";
        script = "exec ${execStartOf "polkit"}";
      };
    })

    (mkIf config.hardware.bluetooth.enable {
      zed.init.services.zed-bluetooth = {
        description = "Bluetooth daemon";
        dependencies = [
          "zed-dbus"
          "zed-coldplug"
        ];
        preStart = "install -d -m 0700 /var/lib/bluetooth";
        script = "exec ${config.hardware.bluetooth.package}/libexec/bluetooth/bluetoothd -n -f /etc/bluetooth/main.conf";
      };
    })

    (mkIf config.services.upower.enable {
      zed.init.services.zed-upower = {
        description = "power and battery information";
        dependencies = [ "zed-dbus" ];
        preStart = "install -d -m 0755 /var/lib/upower";
        script = "exec ${config.services.upower.package}/libexec/upowerd";
      };
    })

    (mkIf config.services.acpid.enable {
      zed.init.services.zed-acpid = translate "acpid" {
        description = "ACPI event daemon";
        dependencies = [ "zed-coldplug" ];
      };
    })

    (mkIf (units ? cpufreq && units.cpufreq.enable) {
      # cpupower exits 237 where there is no cpufreq driver (most VMs); the
      # oneshot is non-fatal, so that only logs a message.
      zed.init.services.zed-cpufreq = {
        type = "oneshot";
        description = "CPU frequency governor";
        dependencies = [ "zed-modules" ];
        up = oneshotScriptOf "cpufreq";
      };
    })

    (mkIf config.services.openssh.enable {
      zed.init.services.zed-sshd-keygen = {
        type = "oneshot";
        description = "generate SSH host keys if missing";
        dependencies = [ "zed-mounts" ];
        path = [ config.services.openssh.package ];
        up = units.sshd-keygen.script;
      };
      zed.init.services.zed-sshd = translate "sshd" {
        description = "OpenSSH server";
        dependencies = [
          "zed-sysinit"
          "zed-sshd-keygen"
        ]
        ++ lib.optional config.networking.nftables.enable "zed-firewall";
        user = null;
        preStart = ''
          install -d -m 0755 /var/empty /run/sshd
          ${units.sshd.preStart or ""}
        '';
        script = "exec ${config.services.openssh.package}/bin/sshd -D -f /etc/ssh/sshd_config";
      };
    })

    (mkIf config.services.tor.enable {
      zed.init.services.zed-tor = translate "tor" {
        description = "Tor onion router";
        dependencies = [ "zed-sysinit" ] ++ lib.optional config.networking.networkmanager.enable "zed-networkmanager";
      };
    })
  ]);
}
