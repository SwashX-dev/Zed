# zedd, the Zed management daemon, and the `zed` command.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf mkOption types;
  cfg = config.zed.daemon;
in
{
  options.zed.daemon = {
    enable = lib.mkEnableOption "zedd, the Zed system management daemon" // {
      default = true;
    };

    package = mkOption {
      type = types.package;
      default = pkgs.zed-suite;
      defaultText = lib.literalExpression "pkgs.zed-suite";
      description = "Package providing zedd and the zed command.";
    };

    adminGroup = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "wheel";
      description = ''
        Members of this group may change the system through zedd without
        sudo.  When null (the default), only root may; the zed command then
        re-runs itself through sudo, which asks for the user's password.
      '';
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];

    zed.init.services.zedd = {
      description = "Zed management daemon";
      dependencies = [
        "zed-sysinit"
        "zed-nix-daemon"
      ];
      # zedd activates new generations itself; restarting it in the middle
      # of that would interrupt the transaction.  It re-executes after a
      # commit that changed its own package.
      restartOnSwitch = false;
      notificationFd = 3;
      environment.ZED_CHILD_PATH = "/run/current-system/sw/bin:/run/wrappers/bin";
      preStart = ''
        install -d -m 0755 /run/zed
        install -d -m 0750 /var/lib/zed /var/log/zed/transactions
        install -d -m 0755 /var/cache/zed
      '';
      script = lib.escapeShellArgs (
        [
          "exec"
          "${cfg.package}/bin/zedd"
          "--notify-fd"
          "3"
        ]
        ++ lib.optionals (cfg.adminGroup != null) [
          "--admin-group"
          cfg.adminGroup
        ]
      );
    };
  };
}
