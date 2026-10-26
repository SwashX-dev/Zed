# Zed's identity outside the desktop: boot menu, boot splash, consoles.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf mkDefault mkOption types;
  cfg = config.zed.branding;
  plymouth = config.boot.plymouth.enable;

  # Shown on every text console above the login prompt.
  issue = ''
    [1;38;2;196;30;58m
       ███████╗███████╗██████╗
       ╚══███╔╝██╔════╝██╔══██╗
         ███╔╝ █████╗  ██║  ██║
        ███╔╝  ██╔══╝  ██║  ██║
       ███████╗███████╗██████╔╝
       ╚══════╝╚══════╝╚═════╝ [0m
    [2m   ${config.system.nixos.distroName} ${config.zed.version} · s6-supervised · \l[0m

  '';

  fastfetchLogo = pkgs.writeText "zed-fastfetch-logo" ''
    $1      ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
    $1      ▀▀▀▀▀▀▀▀▀▀▀▀█▀
    $1             ▄██▀$2       /
    $1           ▄██▀$2       /
    $1         ▄██▀$2     ═══╪══
    $1       ▄██▀$2      /
    $1     ▄██▀$2      /
    $1    ████████████████
    $1    ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
  '';
in
{
  options.zed.branding = {
    enable = lib.mkEnableOption "Zed's boot menu theme, boot splash and console styling" // {
      default = true;
    };
    bootSplash = mkOption {
      type = types.bool;
      default = config.zed.desktop.enable;
      defaultText = lib.literalExpression "config.zed.desktop.enable";
      description = "Show the graphical plymouth boot splash (also used to ask for disk passwords).";
    };
  };

  config = mkIf cfg.enable (
    lib.mkMerge [
      {
        boot.loader.grub = {
          theme = mkDefault "${pkgs.zed-branding}/share/grub/themes/zed";
          # The theme draws the background; NixOS sets a splash at mkDefault.
          splashImage = lib.mkOverride 900 null;
          gfxmodeEfi = mkDefault "1920x1080,auto";
          gfxmodeBios = mkDefault "1920x1080,auto";
        };

        # A crimson-led 16-colour palette for text consoles.
        console.colors = mkDefault [
          "0b0b0d"
          "c41e3a"
          "5fb88a"
          "e8a33d"
          "5d8fd6"
          "b36ad8"
          "4fb3b8"
          "c8c8ce"
          "3a3a42"
          "ff5a73"
          "7fd8a8"
          "ffc35c"
          "82adf0"
          "cf8ef0"
          "72d3d8"
          "ffffff"
        ];
        environment.etc.issue = lib.mkForce { text = issue; };

        environment.systemPackages = [
          pkgs.zed-branding
          pkgs.fastfetch
        ];
        environment.etc."xdg/fastfetch/config.jsonc".text = builtins.toJSON {
          logo = {
            source = "${fastfetchLogo}";
            type = "file";
            color = {
              "1" = "38;2;196;30;58";
              "2" = "38;2;200;200;206";
            };
          };
          display.color = {
            keys = "38;2;196;30;58";
            title = "38;2;255;90;115";
          };
          modules = [
            "title"
            "separator"
            "os"
            "kernel"
            "initsystem"
            "uptime"
            "packages"
            "shell"
            "display"
            "wm"
            "terminal"
            "cpu"
            "gpu"
            "memory"
            "disk"
            "battery"
          ];
        };
      }

      (mkIf cfg.bootSplash {
        boot.plymouth = {
          enable = true;
          theme = "zed";
          themePackages = [ pkgs.zed-branding ];
          font = "${pkgs.inter}/share/fonts/truetype/InterVariable.ttf";
        };
        boot.consoleLogLevel = mkDefault 3;
        boot.kernelParams = [
          "quiet"
          "splash"
          "udev.log_level=3"
        ];
      })

      (mkIf plymouth {
        # The steps that systemd units perform for plymouth upstream.
        zed.init.services =
          {
            zed-plymouth-rw = {
              type = "oneshot";
              description = "tell the boot splash that / is writable";
              dependencies = [ "zed-mounts" ];
              path = [ config.boot.plymouth.package ];
              up = "plymouth --ping && plymouth update-root-fs --read-write || true";
            };
            zed-plymouth-quit = {
              type = "oneshot";
              description = "end the boot splash before the login screen";
              dependencies = [
                "zed-sysinit"
                "zed-plymouth-rw"
              ];
              path = [ config.boot.plymouth.package ];
              up = "plymouth --ping && plymouth quit --retain-splash || true";
            };
          }
          // lib.genAttrs (map (t: "getty-${t}") config.zed.init.gettys) (_: {
            dependencies = [ "zed-plymouth-quit" ];
          })
          // lib.optionalAttrs config.services.greetd.enable {
            zed-greetd.dependencies = [ "zed-plymouth-quit" ];
          };
        zed.init.extraReplacedUnits = [
          "plymouth-quit"
          "plymouth-quit-wait"
          "plymouth-read-write"
          "plymouth-start"
          "plymouth-switch-root"
          "plymouth-halt"
          "plymouth-kexec"
          "plymouth-poweroff"
          "plymouth-reboot"
          # Disk passwords are asked by the scripted initrd through plymouth
          # directly; this agent only serves systemd's password queue.
          "systemd-ask-password-plymouth"
        ];
      })
    ]
  );
}
