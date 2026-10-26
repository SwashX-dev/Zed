# Compatibility between NixOS modules (written for systemd) and Zed's s6
# init.
#
# 1. Session setup that logind would do: no pam_systemd; XDG_RUNTIME_DIR is
#    created by a pam_exec helper instead.
# 2. A compatibility report: every systemd unit that NixOS enables but that
#    Zed neither supervises nor knowingly replaces produces an evaluation
#    warning, so an unsupported service is never silently ignored.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkIf
    mkForce
    mkMerge
    types
    ;

  cfg = config.zed.init;

  rundir = pkgs.writeShellScript "zed-rundir" ''
    # pam_exec session helper: create /run/user/$UID for non-root users.
    [ "''${PAM_TYPE:-}" = open_session ] || exit 0
    uid=$(${pkgs.coreutils}/bin/id -u "$PAM_USER") || exit 0
    gid=$(${pkgs.coreutils}/bin/id -g "$PAM_USER") || exit 0
    [ "$uid" -ne 0 ] || exit 0
    dir=/run/user/$uid
    if [ ! -d "$dir" ]; then
      ${pkgs.coreutils}/bin/install -d -m 0755 /run/user
      ${pkgs.coreutils}/bin/install -d -m 0700 -o "$uid" -g "$gid" "$dir"
    fi
  '';

  rundirRule = {
    rules.session.zed-rundir = {
      order = 12500;
      control = "optional";
      modulePath = "${pkgs.pam}/lib/security/pam_exec.so";
      args = [
        "quiet"
        "${rundir}"
      ];
    };
  };

  # Units Zed replaces with its own s6-rc services or deliberately does not
  # need (their job is done by s6-linux-init, rc.init or a zed-* oneshot).
  replacedUnits = [
    "backdoor"
    "dbus"
    "getty@"
    "autovt@"
    "serial-getty@"
    "console-getty"
    "container-getty@"
    "nix-daemon"
    "nscd"
    "nftables"
    "firewall"
    "NetworkManager"
    "NetworkManager-wait-online"
    "NetworkManager-dispatcher"
    "chronyd"
    "seatd"
    "greetd"
    "polkit"
    "bluetooth"
    "upower"
    "acpid"
    "sshd"
    "sshd-keygen"
    "cpufreq"
    "tor"
    "wpa_supplicant"
    "zedd"
    "suid-sgid-wrappers"
    "mount-pstore"
    "network-local-commands"
    "network-setup"
    "resolvconf"
    "reload-systemd-vconsole-setup"
    "systemd-timesyncd"
    "systemd-oomd"
    "systemd-journald"
    "systemd-logind"
    "systemd-udevd"
    "systemd-random-seed"
    "systemd-tmpfiles-setup"
    "systemd-tmpfiles-clean"
    "systemd-sysctl"
    "systemd-modules-load"
    "systemd-vconsole-setup"
    "systemd-update-utmp"
    "systemd-user-sessions"
    "user@"
    "user-runtime-dir@"
    "linger-users"
    "post-resume"
    "pre-sleep"
    "save-hwclock"
    "audit"
    "post-boot"
    "register-nix-paths"
    "systemd-pstore"
    "fstrim"
    "nix-gc"
    "nix-optimise"
  ]
  ++ cfg.extraReplacedUnits;

  bootTargets = [
    "multi-user.target"
    "graphical.target"
    "sysinit.target"
    "basic.target"
    "sockets.target"
    "timers.target"
    "network.target"
    "network-online.target"
  ];

  unitBase = n: if lib.hasInfix "@" n then lib.head (lib.splitString "@" n) + "@" else n;

  untranslated = lib.filter (
    n:
    let
      u = config.systemd.services.${n};
    in
    u.enable && lib.any (t: lib.elem t bootTargets) (u.wantedBy ++ u.requiredBy) && !(lib.elem (unitBase n) replacedUnits)
  ) (lib.attrNames config.systemd.services);

  untranslatedTimers = lib.filter (n: config.systemd.timers.${n}.enable && !(lib.elem n replacedUnits)) (
    lib.attrNames config.systemd.timers
  );
in
{
  options.zed.init = {
    extraReplacedUnits = lib.mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "systemd unit names that a Zed module replaces; suppresses the compatibility warning.";
    };

    compatibilityReport = lib.mkOption {
      type = types.attrsOf (types.listOf types.str);
      readOnly = true;
      description = "systemd units enabled by NixOS modules that Zed does not run.";
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      zed.init.compatibilityReport = {
        services = untranslated;
        timers = untranslatedTimers;
      };

      # zedd refuses changes that add entries to this report.
      system.systemBuilderCommands = ''
        mkdir -p $out/zed
        ln -s ${pkgs.writeText "zed-compatibility.json" (builtins.toJSON config.zed.init.compatibilityReport)} $out/zed/compatibility.json
      '';

      warnings =
        lib.optional (untranslated != [ ]) ''
          Zed: these NixOS services are enabled but are not supervised by s6-rc and will not run:
            ${lib.concatStringsSep ", " untranslated}
          Add an s6-rc service under zed.init.services or disable them.  See docs/ARCHITECTURE.md.
        ''
        ++ lib.optional (untranslatedTimers != [ ]) ''
          Zed: these systemd timers have no s6 equivalent yet and will not run:
            ${lib.concatStringsSep ", " untranslatedTimers}
        '';

      # Wayland sessions and the greeter need XDG_RUNTIME_DIR; logind would
      # normally create it.
      security.pam.services.login = rundirRule // {
        startSession = mkForce false;
      };
      security.pam.services.su = rundirRule;

      environment.extraInit = ''
        if [ -z "''${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
          export XDG_RUNTIME_DIR="/run/user/$(id -u)"
        fi
      '';

      # Periodic garbage collection and store optimisation are timers
      # upstream; Zed runs them from its own scheduler (see periodic.nix).
      nix.gc.automatic = lib.mkDefault false;
      nix.optimise.automatic = lib.mkDefault false;
      systemd.oomd.enable = false;
      services.logrotate.enable = lib.mkDefault false;
    }

    (mkIf config.services.greetd.enable {
      security.pam.services.greetd = rundirRule // {
        startSession = mkForce false;
      };
    })

    (mkIf config.services.openssh.enable {
      security.pam.services.sshd = rundirRule // {
        startSession = mkForce false;
      };
    })
  ]);
}
