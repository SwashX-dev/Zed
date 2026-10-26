# Zed's init layer: s6-linux-init as PID 1, s6-svscan as the long-lived
# supervisor, and s6-rc for dependency-ordered service management.
#
# The NixOS module system is kept intact.  NixOS modules still generate
# /etc, users, PAM, udev rules, tmpfiles, sysctl and daemon configuration;
# this layer replaces only the parts that require systemd to be running:
# PID 1, service supervision, and switch-to-configuration.
#
# Boot path:
#   kernel -> scripted stage 1 (no systemd) -> stage-2-init.sh (activation)
#          -> zed-preinit (unpacks the s6-linux-init basedir into /run)
#          -> s6-linux-init -> s6-svscan (PID 1) -> rc.init -> s6-rc
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkOption
    mkEnableOption
    mkIf
    mkForce
    types
    attrNames
    filterAttrs
    mapAttrs'
    mapAttrsToList
    nameValuePair
    concatStringsSep
    concatMapStrings
    optionalString
    escapeShellArg
    ;

  cfg = config.zed.init;

  # Runtime location of the unpacked s6-linux-init basedir.
  basedir = "/run/zed/init";
  liveDir = "/run/s6-rc";
  scanDir = "/run/service";

  basePath = with pkgs; [
    coreutils
    findutils
    gnugrep
    gnused
    util-linux
    kmod
    iproute2
    procps
    s6
    s6-rc
    execline
  ];

  mkPath =
    extra: "${lib.makeBinPath (extra ++ basePath)}:/run/wrappers/bin:/run/current-system/sw/bin";

  envExports =
    env: concatStringsSep "\n" (mapAttrsToList (k: v: "export ${k}=${escapeShellArg v}") env);

  validName = n: builtins.match "[A-Za-z0-9][A-Za-z0-9_.@-]*" n != null;

  serviceModule =
    { name, ... }:
    {
      options = {
        type = mkOption {
          type = types.enum [
            "oneshot"
            "longrun"
            "bundle"
          ];
          default = "longrun";
          description = "s6-rc service type.";
        };
        description = mkOption {
          type = types.str;
          default = name;
          description = "Human-readable description shown by `zed services`.";
        };
        dependencies = mkOption {
          type = types.listOf types.str;
          default = [ ];
          description = "Services that must be up before this one starts.";
        };
        contents = mkOption {
          type = types.listOf types.str;
          default = [ ];
          description = "Members of a bundle.";
        };
        autoStart = mkOption {
          type = types.bool;
          default = true;
          description = "Whether the service belongs to the `default` bundle started at boot.";
        };
        essential = mkOption {
          type = types.bool;
          default = false;
          description = "Mark with flag-essential: only a full shutdown stops it.";
        };
        restartOnSwitch = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Restart the service during `switch-to-configuration switch` when
            its definition changed.  Disable for services whose restart would
            end the user's session (display managers, seat management).
          '';
        };
        fatal = mkOption {
          type = types.bool;
          default = false;
          description = ''
            For oneshots: propagate a failing `up` to s6-rc so dependants are
            not started.  Use for fail-closed services such as the firewall.
            Non-fatal oneshots log their failure and report success.
          '';
        };
        up = mkOption {
          type = types.lines;
          default = "";
          description = "Oneshot start script (bash, runs with `set -e`).";
        };
        down = mkOption {
          type = types.lines;
          default = "";
          description = "Oneshot stop script.";
        };
        preStart = mkOption {
          type = types.lines;
          default = "";
          description = "Longrun: commands run as root before dropping privileges.";
        };
        script = mkOption {
          type = types.lines;
          default = "";
          description = "Longrun: the supervised process.  Should `exec` the daemon in the foreground.";
        };
        finish = mkOption {
          type = types.lines;
          default = "";
          description = "Longrun: s6 finish script.  Its exit status is always forced to 0.";
        };
        path = mkOption {
          type = types.listOf types.package;
          default = [ ];
          description = "Packages added to PATH.";
        };
        environment = mkOption {
          type = types.attrsOf types.str;
          default = { };
          description = "Environment variables exported before the script runs.";
        };
        user = mkOption {
          type = types.nullOr types.str;
          default = null;
          description = "Longrun: account the supervised process runs as (via s6-setuidgid).";
        };
        notificationFd = mkOption {
          type = types.nullOr types.int;
          default = null;
          description = "Longrun: file descriptor on which the daemon signals readiness.";
        };
        timeoutUp = mkOption {
          type = types.int;
          default = 0;
          description = "Milliseconds before an `up` transition fails; 0 waits forever.";
        };
        timeoutDown = mkOption {
          type = types.int;
          default = 0;
          description = "Milliseconds before a `down` transition fails; 0 waits forever.";
        };
        downSignal = mkOption {
          type = types.nullOr types.str;
          default = null;
          example = "SIGINT";
          description = "Longrun: signal used to stop the process (default SIGTERM).";
        };
        log = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Longrun: give the service its own s6-log logger writing to
            /var/log/zed/<name>.  Each longrun has a dedicated logger so that
            changing one service's logging never forces restarts elsewhere.
          '';
        };
      };
    };

  userServices = cfg.services;

  # Every logged longrun gets "<name>-log", connected through an s6-rc pipeline.
  loggedNames = attrNames (filterAttrs (_: s: s.type == "longrun" && s.log) userServices);

  loggerScript =
    name:
    pkgs.writeScript "zed-s6-${name}-log-run" ''
      #!${pkgs.bash}/bin/bash
      exec 2>&1
      export PATH=${mkPath [ ]}
      dir=/var/log/zed/${name}
      install -d -m 0750 -o ${cfg.logUser} -g ${cfg.logUser} /var/log/zed
      install -d -m 0750 -o ${cfg.logUser} -g ${cfg.logUser} "$dir"
      exec s6-setuidgid ${cfg.logUser} s6-log -d3 -b -- n10 s1000000 T "$dir"
    '';

  oneshotScript =
    name: kind: svc: text:
    pkgs.writeScript "zed-s6-${name}-${kind}" ''
      #!${pkgs.bash}/bin/bash
      export PATH=${mkPath svc.path}
      ${envExports svc.environment}
      (
        set -e
        ${text}
      )
      rc=$?
      if [ "$rc" -ne 0 ]; then
        echo "zed-init: ${name} ${kind} failed with exit status $rc" >&2
        ${if svc.fatal && kind == "up" then "exit $rc" else "exit 0"}
      fi
      exit 0
    '';

  mainScript =
    name: svc:
    pkgs.writeScript "zed-s6-${name}-main" ''
      #!${pkgs.bash}/bin/bash
      ${svc.script}
    '';

  runScript =
    name: svc:
    pkgs.writeScript "zed-s6-${name}-run" ''
      #!${pkgs.bash}/bin/bash
      exec 2>&1
      export PATH=${mkPath svc.path}
      ${envExports svc.environment}
      ${svc.preStart}
      ${
        if svc.user != null then
          "exec ${pkgs.s6}/bin/s6-setuidgid ${svc.user} ${mainScript name svc}"
        else
          "exec ${mainScript name svc}"
      }
    '';

  finishScript =
    name: svc:
    pkgs.writeScript "zed-s6-${name}-finish" ''
      #!${pkgs.bash}/bin/bash
      export PATH=${mkPath svc.path}
      ( ${svc.finish} ) || true
      exit 0
    '';

  touchAll = dir: names: concatMapStrings (n: "touch ${dir}/${n}\n") names;

  mkServiceDir =
    name: svc:
    let
      d = "$out/${name}";
    in
    ''
      mkdir -p ${d}
      echo ${svc.type} > ${d}/type
      ${optionalString svc.essential "touch ${d}/flag-essential"}
    ''
    + (
      if svc.type == "bundle" then
        ''
          mkdir -p ${d}/contents.d
          ${touchAll "${d}/contents.d" svc.contents}
        ''
      else
        ''
          mkdir -p ${d}/dependencies.d
          ${touchAll "${d}/dependencies.d" svc.dependencies}
          echo ${toString svc.timeoutUp} > ${d}/timeout-up
          echo ${toString svc.timeoutDown} > ${d}/timeout-down
        ''
    )
    + optionalString (svc.type == "oneshot") (
      ''
        echo ${oneshotScript name "up" svc svc.up} > ${d}/up
      ''
      + optionalString (svc.down != "") ''
        echo ${oneshotScript name "down" svc svc.down} > ${d}/down
      ''
    )
    + optionalString (svc.type == "longrun") (
      ''
        cp ${runScript name svc} ${d}/run
        chmod 0755 ${d}/run
      ''
      + optionalString (svc.finish != "") ''
        cp ${finishScript name svc} ${d}/finish
        chmod 0755 ${d}/finish
      ''
      + optionalString (svc.notificationFd != null) ''
        echo ${toString svc.notificationFd} > ${d}/notification-fd
      ''
      + optionalString (svc.downSignal != null) ''
        echo ${svc.downSignal} > ${d}/down-signal
      ''
      + optionalString svc.log ''
        echo ${name}-log > ${d}/producer-for
        mkdir -p $out/${name}-log/dependencies.d
        echo longrun > $out/${name}-log/type
        echo ${name} > $out/${name}-log/consumer-for
        echo ${name}-pipeline > $out/${name}-log/pipeline-name
        echo 3 > $out/${name}-log/notification-fd
        cp ${loggerScript name} $out/${name}-log/run
        chmod 0755 $out/${name}-log/run
      ''
    );

  autoStartNames = attrNames (
    filterAttrs (n: s: s.autoStart && s.type != "bundle" && !(lib.elem n cfg.disabledServices)) userServices
  );

  # Read by `zed services` and Zed Sentinel.
  manifest = pkgs.writeText "zed-services.json" (
    builtins.toJSON (
      lib.mapAttrs (n: s: {
        inherit (s)
          type
          description
          dependencies
          contents
          essential
          ;
        autoStart = s.autoStart && !(lib.elem n cfg.disabledServices);
      }) userServices
    )
  );

  sourceDir = pkgs.runCommand "zed-s6-rc-source" { } ''
    mkdir -p $out
    ${concatStringsSep "\n" (mapAttrsToList mkServiceDir userServices)}
    mkdir -p $out/default/contents.d
    echo bundle > $out/default/type
    ${touchAll "$out/default/contents.d" (autoStartNames ++ map (n: "${n}-log") loggedNames)}
  '';

  noRestartNames = attrNames (filterAttrs (_: s: !s.restartOnSwitch) userServices);

  # The compiled database plus a per-service content hash.  The switch
  # script compares hashes between generations to decide what to restart,
  # because s6-rc-update does not restart services whose scripts changed.
  compiledDb = pkgs.runCommand "zed-s6-rc-db" { nativeBuildInputs = [ pkgs.s6-rc ]; } ''
    mkdir -p $out
    s6-rc-compile -v2 $out/compiled ${sourceDir}
    ln -s ${sourceDir} $out/source
    for d in ${sourceDir}/*/; do
      n=$(basename "$d")
      h=$(cd "$d" && find . -type f | LC_ALL=C sort | while read -r f; do
            printf '%s\n' "$f"; cat "$f"
          done | sha256sum | cut -d' ' -f1)
      printf '%s %s\n' "$n" "$h" >> $out/hashes
    done
    printf '%s\n' ${concatStringsSep " " noRestartNames} > $out/no-restart
  '';

  rcInit = pkgs.writeScript "rc.init" ''
    #!${pkgs.bash}/bin/bash
    # Run by s6-linux-init once s6-svscan is PID 1.  $1 is the runlevel.
    export PATH=${mkPath [ ]}
    rl="''${1:-default}"
    db=$(readlink -f /run/current-system/zed/s6-rc/compiled)
    if ! s6-rc-init -c "$db" -l ${liveDir} ${scanDir}; then
      echo "zed-init: s6-rc-init failed; only the recovery console on tty9 is available" >&2
      exit 1
    fi
    exec ${basedir}/scripts/runlevel "$rl"
  '';

  # Writes /run/zed/boot-status once the boot transition has finished:
  # "ok", or "degraded" followed by the services of the target bundle that
  # are not up.  `zed doctor` and the VM tests read it.
  runlevel = pkgs.writeScript "runlevel" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${mkPath [ ]}
    mkdir -p /run/zed
    rm -f /run/zed/boot-status
    if s6-rc -l ${liveDir} -v2 -up change "$1"; then
      echo ok > /run/zed/boot-status.tmp
    else
      echo "zed-init: some services failed to start; run 'zed doctor' or inspect /run/uncaught-logs" >&2
      {
        echo degraded
        comm -23 <(s6-rc -l ${liveDir} listall "$1" | sort) <(s6-rc -l ${liveDir} -a list | sort)
      } > /run/zed/boot-status.tmp
    fi
    mv /run/zed/boot-status.tmp /run/zed/boot-status
  '';

  rcShutdown = pkgs.writeScript "rc.shutdown" ''
    #!${pkgs.bash}/bin/bash
    # Stop every s6-rc service in reverse dependency order.  s6-linux-init
    # then kills leftover processes, unmounts filesystems, and halts.
    exec >/dev/console 2>&1
    export PATH=${mkPath [ ]}
    echo "zed-init: stopping services"
    s6-rc -l ${liveDir} -v2 -bDa change || true
    sync
  '';

  skel = pkgs.runCommand "zed-s6-linux-init-skel" { } ''
    mkdir -p $out
    cp ${rcInit} $out/rc.init
    cp ${rcShutdown} $out/rc.shutdown
    cp ${runlevel} $out/runlevel
    chmod 0755 $out/*
  '';

  initialPath = lib.makeBinPath (
    with pkgs;
    [
      s6
      s6-rc
      s6-linux-init.bin
      execline
      coreutils
      util-linux
    ]
  );

  # s6-linux-init-maker creates setgid directories and FIFOs, neither of
  # which the Nix store can contain.  Build it under fakeroot, keep it as a
  # tar archive, and unpack it into /run at boot (see zed-preinit below).
  initArchive =
    pkgs.runCommand "zed-s6-linux-init"
      {
        nativeBuildInputs = [
          pkgs.s6-linux-init.bin
          pkgs.fakeroot
          pkgs.gnutar
        ];
      }
      ''
        mkdir -p $out
        fakeroot -s state -- s6-linux-init-maker \
          -c ${basedir} \
          -p ${initialPath} \
          -m 0022 \
          -t 2 \
          -1 \
          -N \
          -q ${toString cfg.finalSleepMs} \
          -D default \
          -s /run/zed/kernel-env \
          ${optionalString (cfg.recoveryGetty != null) "-G ${escapeShellArg cfg.recoveryGetty}"} \
          -f ${skel} \
          basedir
        fakeroot -i state -- tar --numeric-owner -cpf $out/basedir.tar -C basedir .
      '';

  preinit = pkgs.writeScript "zed-preinit" ''
    #!${pkgs.bash}/bin/bash
    # exec'd by NixOS stage-2-init.sh in place of systemd, as PID 1.
    export PATH=${
      lib.makeBinPath [
        pkgs.coreutils
        pkgs.gnutar
      ]
    }
    mkdir -p ${basedir} /run/zed/kernel-env
    if ! tar --numeric-owner -xpf ${initArchive}/basedir.tar -C ${basedir}; then
      echo "zed-preinit: cannot unpack s6-linux-init basedir; starting emergency shell" >&2
      exec ${pkgs.bash}/bin/bash
    fi
    exec ${basedir}/bin/init "$@"
  '';

  # poweroff/reboot/halt/shutdown/telinit that talk to s6-linux-init.  They
  # take priority over systemd's versions, which remain installed but inert.
  initTools = lib.hiPrio (
    pkgs.runCommand "zed-init-tools" { } ''
      mkdir -p $out/bin
      for c in halt poweroff reboot shutdown telinit; do
        cat > $out/bin/$c <<EOF
      #!${pkgs.bash}/bin/bash
      if [ ! -x ${basedir}/bin/$c ]; then
        echo "$c: this system was not booted with Zed's s6 init" >&2
        exit 1
      fi
      exec ${basedir}/bin/$c "\$@"
      EOF
        chmod 0755 $out/bin/$c
      done
    ''
  );

  switchScript = pkgs.replaceVarsWith {
    src = ./switch-to-configuration.sh;
    isExecutable = true;
    replacements = {
      bash = "${pkgs.bash}/bin/bash";
      path = lib.makeBinPath (
        with pkgs;
        [
          coreutils
          gnugrep
          gnused
          util-linux
          s6
          s6-rc
        ]
      );
      liveDir = liveDir;
      installBootLoader = config.system.build.installBootLoader;
    };
  };
in
{
  options.zed.init = {
    enable = mkEnableOption "s6-linux-init and s6-rc as the init system" // {
      default = true;
    };

    services = mkOption {
      type = types.attrsOf (types.submodule serviceModule);
      default = { };
      description = "s6-rc services compiled into this generation's service database.";
    };

    logUser = mkOption {
      type = types.str;
      default = "zedlog";
      description = "Unprivileged account that owns per-service logs under /var/log/zed.";
    };

    recoveryGetty = mkOption {
      type = types.nullOr types.str;
      default = "${pkgs.util-linux}/bin/agetty --login-program ${pkgs.shadow}/bin/login -L 38400 tty9 linux";
      description = ''
        Login prompt started by s6-linux-init itself, before and independently
        of s6-rc, so the machine stays recoverable if service startup fails.
      '';
    };

    finalSleepMs = mkOption {
      type = types.int;
      default = 3000;
      description = "Grace period between SIGTERM and SIGKILL at shutdown.";
    };

    disabledServices = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = ''
        Services left out of the boot bundle.  They stay installed and can be
        started by hand (`zed services start NAME`).
      '';
    };

    protectedServices = mkOption {
      type = types.listOf types.str;
      readOnly = true;
      # Keep in sync with zedcore.registry.PROTECTED_SERVICES.
      default = [
        "zed-specialfs"
        "zed-modules"
        "zed-sysctl"
        "zed-udevd"
        "zed-coldplug"
        "zed-mounts"
        "zed-tmpfiles"
        "zed-wrappers"
        "zed-sysinit"
        "zed-dbus"
        "zed-nix-daemon"
        "zed-firewall"
        "zedd"
      ];
      description = "Services that cannot be disabled because the system needs them to boot or be managed.";
    };

    compiledDatabase = mkOption {
      type = types.package;
      readOnly = true;
      default = compiledDb;
      description = "The compiled s6-rc database of this generation.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.all validName (attrNames userServices);
        message = "zed.init.services: service names must match [A-Za-z0-9][A-Za-z0-9_.@-]*";
      }
      {
        assertion = !(userServices ? default);
        message = "zed.init.services: `default` is reserved for the boot bundle";
      }
      {
        assertion = lib.all (n: userServices ? ${n}) cfg.disabledServices;
        message = "zed.init.disabledServices names unknown services: ${
          concatStringsSep ", " (lib.filter (n: !(userServices ? ${n})) cfg.disabledServices)
        }";
      }
      {
        assertion = !lib.any (n: lib.elem n cfg.protectedServices) cfg.disabledServices;
        message = "zed.init.disabledServices cannot contain core services: ${
          concatStringsSep ", " (lib.filter (n: lib.elem n cfg.protectedServices) cfg.disabledServices)
        }";
      }
      {
        assertion = !config.boot.initrd.systemd.enable;
        message = "Zed's s6 init requires the scripted stage 1 (boot.initrd.systemd.enable = false)";
      }
    ];

    # Stage 1 without systemd.  Deprecated upstream and scheduled for removal
    # in NixOS 26.11; see docs/LIMITATIONS.md for Zed's migration plan.
    boot.initrd.systemd.enable = false;

    # stage-2-init.sh execs this instead of systemd.
    boot.systemdExecutable = "${preinit}";

    # NixOS's switch-to-configuration drives systemd over D-Bus; Zed ships
    # its own that drives s6-rc.
    system.switch.enable = false;
    system.systemBuilderCommands = ''
      mkdir -p $out/zed/s6-rc $out/bin
      ln -s ${compiledDb}/compiled $out/zed/s6-rc/compiled
      ln -s ${compiledDb}/hashes $out/zed/s6-rc/hashes
      ln -s ${compiledDb}/no-restart $out/zed/s6-rc/no-restart
      ln -s ${compiledDb}/source $out/zed/s6-rc/source
      echo ${pkgs.s6-rc.version} > $out/zed/s6-rc/version
      ln -s ${manifest} $out/zed/services.json
      sed "s|__TOPLEVEL__|$out|g" ${switchScript} > $out/bin/switch-to-configuration
      chmod 0755 $out/bin/switch-to-configuration
    '';

    environment.systemPackages = [
      initTools
      pkgs.s6
      pkgs.s6-rc
      pkgs.s6-linux-init
      pkgs.execline
    ];

    users.users.${cfg.logUser} = {
      isSystemUser = true;
      group = cfg.logUser;
      description = "s6-log service logger";
    };
    users.groups.${cfg.logUser} = { };

    # Without systemd as PID 1 these would never run; make that explicit so
    # their configuration does not suggest otherwise.
    services.timesyncd.enable = mkForce false;
    systemd.coredump.enable = false;
  };
}
