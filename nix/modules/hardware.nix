# Graphics drivers chosen by the installer (zed-hardware.nix records the
# detected devices) and switchable afterwards through the managed setting
# `hardware.nvidiaDriver`.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf mkMerge mkOption types;
  cfg = config.zed.hardware.gpu;
  proprietary = lib.elem cfg.nvidia.driver [
    "proprietary"
    "proprietary-open"
  ];
in
{
  options.zed.hardware.gpu = {
    vendors = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "GPU vendors detected at installation (informational; shown by Sentinel).";
    };
    vaapi = mkOption {
      type = types.listOf (
        types.enum [
          "intel-media-driver"
          "intel-vaapi-driver"
        ]
      );
      default = [ ];
      description = "VA-API video decoding drivers to install.";
    };
    nvidia = {
      driver = mkOption {
        type = types.enum [
          "none"
          "nouveau"
          "proprietary"
          "proprietary-open"
        ];
        default = "none";
        description = ''
          nouveau: the free driver (works offline).  proprietary: NVIDIA's
          driver with its closed kernel module (Maxwell to Volta).
          proprietary-open: NVIDIA's driver with its open kernel module
          (Turing and newer).  The proprietary variants need
          nixpkgs.config.allowUnfree.
        '';
      };
      prime = {
        offload = mkOption {
          type = types.bool;
          default = false;
        };
        nvidiaBusId = mkOption {
          type = types.str;
          default = "";
        };
        intelBusId = mkOption {
          type = types.str;
          default = "";
        };
        amdgpuBusId = mkOption {
          type = types.str;
          default = "";
        };
      };
    };
  };

  config = mkMerge [
    (mkIf (cfg.vaapi != [ ]) {
      hardware.graphics.extraPackages = map (n: pkgs.${n}) cfg.vaapi;
    })

    (mkIf proprietary {
      assertions = [
        {
          assertion = config.nixpkgs.config.allowUnfree or false;
          message = "hardware.nvidiaDriver = ${cfg.nvidia.driver} needs nix.allowUnfree = true";
        }
      ];
      services.xserver.videoDrivers = [ "nvidia" ];
      hardware.nvidia = {
        open = cfg.nvidia.driver == "proprietary-open";
        modesetting.enable = true;
        package = config.boot.kernelPackages.nvidiaPackages.stable;
        prime = mkIf cfg.nvidia.prime.offload {
          offload.enable = true;
          offload.enableOffloadCmd = true;
          inherit (cfg.nvidia.prime) nvidiaBusId intelBusId amdgpuBusId;
        };
      };
      # wlroots compositors and NVIDIA's driver.
      environment.sessionVariables.WLR_NO_HARDWARE_CURSORS = "1";
    })

    (mkIf (cfg.nvidia.driver == "nouveau") {
      boot.initrd.kernelModules = [ "nouveau" ];
    })
  ];
}
