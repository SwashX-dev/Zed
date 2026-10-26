# A minimal periodic-job runner replacing systemd timers for the few
# maintenance jobs Zed needs.  Jobs run one at a time; a job that was missed
# while the machine was off runs shortly after the next boot.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf mkOption types;

  cfg = config.zed.periodic;

  jobModule = {
    options = {
      interval = mkOption {
        type = types.ints.positive;
        description = "Minimum seconds between runs.";
      };
      command = mkOption {
        type = types.lines;
        description = "Shell commands to run (as root).";
      };
    };
  };

  jobScripts = lib.mapAttrs (
    name: job:
    pkgs.writeShellScript "zed-periodic-${name}" ''
      set -e
      export PATH=${lib.makeBinPath [ pkgs.coreutils config.nix.package ]}:/run/current-system/sw/bin
      ${job.command}
    ''
  ) cfg.jobs;

  runner = pkgs.writeShellScript "zed-periodic" ''
    export PATH=${lib.makeBinPath [ pkgs.coreutils ]}
    state=/var/lib/zed/periodic
    install -d -m 0700 "$state"
    sleep 120
    while true; do
      now=$(date +%s)
      ${lib.concatStrings (
        lib.mapAttrsToList (name: job: ''
          last=$(cat "$state/${name}" 2>/dev/null || echo 0)
          if [ $((now - last)) -ge ${toString job.interval} ]; then
            echo "zed-periodic: running ${name}"
            if ${jobScripts.${name}}; then
              echo "$now" > "$state/${name}"
            else
              echo "zed-periodic: ${name} failed (exit $?)"
            fi
          fi
        '') cfg.jobs
      )}
      sleep 900
    done
  '';
in
{
  options.zed.periodic.jobs = mkOption {
    type = types.attrsOf (types.submodule jobModule);
    default = { };
    description = "Periodic maintenance jobs run by the zed-periodic service.";
  };

  config = mkIf (config.zed.init.enable && cfg.jobs != { }) {
    zed.init.services.zed-periodic = {
      description = "periodic maintenance jobs";
      dependencies = [ "zed-sysinit" ];
      script = "exec ${runner}";
    };
  };
}
