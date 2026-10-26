# Zed identity and lean defaults shared by every edition.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkOption types;
in
{
  options.zed = {
    version = mkOption {
      type = types.str;
      default = "0.1.0-alpha";
      readOnly = true;
      description = "Zed release version.";
    };

    channel = mkOption {
      type = types.enum [
        "stable"
        "testing"
      ];
      default = "stable";
      description = "Update channel followed by Zed Pulse.";
    };
  };

  config = {
    system.nixos.distroName = "Zed";
    system.nixos.distroId = "zed";
    system.nixos.extraOSReleaseArgs = {
      ID_LIKE = "nixos";
      HOME_URL = "https://github.com/SwashX-dev/Zed";
      BUG_REPORT_URL = "https://github.com/SwashX-dev/Zed/issues";
      DOCUMENTATION_URL = "https://github.com/SwashX-dev/Zed/tree/main/docs";
      ANSI_COLOR = "1;38;2;196;30;58";
      LOGO = "zed-logo";
      ZED_VERSION = config.zed.version;
      ZED_CHANNEL = config.zed.channel;
    };

    # Weaker than mkDefault so installer- and test-provided names win.
    networking.hostName = lib.mkOverride 1200 "zed";
    networking.networkmanager.enable = mkDefault true;
    services.chrony.enable = mkDefault true;

    # Flakes are how Zed systems are defined; channels are not used.
    nix.channel.enable = false;
    nix.settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      # Keep build logs and derivations available for `zed doctor`.
      keep-outputs = false;
      keep-derivations = false;
      auto-optimise-store = mkDefault true;
    };

    # No packages that the user did not choose.  NixOS installs perl, rsync
    # and strace by default; Zed editions add what they need explicitly.
    environment.defaultPackages = mkDefault [ ];
    documentation.nixos.enable = mkDefault false;
    documentation.doc.enable = mkDefault false;
    documentation.info.enable = mkDefault false;

    zed.periodic.jobs = {
      # Keep the 10 most recent system generations (always including the
      # running and the boot default), then collect unreferenced paths.
      nix-gc = {
        interval = 7 * 24 * 3600;
        command = ''
          nix-env --profile /nix/var/nix/profiles/system --delete-generations +10
          nix-store --gc
        '';
      };
    }
    // lib.optionalAttrs config.services.fstrim.enable {
      fstrim = {
        interval = 7 * 24 * 3600;
        command = "${pkgs.util-linux}/bin/fstrim --all --verbose --quiet-unsupported";
      };
    };

    services.fstrim.enable = mkDefault true;

    system.systemBuilderCommands = ''
      mkdir -p $out/zed
      echo ${config.zed.version} > $out/zed/version
    '';

    system.stateVersion = mkDefault "26.05";
  };
}
