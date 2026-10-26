# The Zed panel (waybar).  Launchers are an option so that the live image
# and Zed applications can add buttons; users override the whole panel by
# copying /etc/xdg/waybar/config.jsonc to ~/.config/waybar/ (deleting the
# copy restores Zed's panel).
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkOption types;
  cfg = config.zed.desktop.panel;

  launcherModule = l: {
    name = "custom/${l.id}";
    value = {
      format = l.label;
      tooltip-format = l.tooltip;
      on-click = l.command;
    };
  };

  panel = {
    layer = "top";
    position = "top";
    height = 34;
    spacing = 6;
    modules-left = [ "custom/zed" ] ++ map (l: "custom/${l.id}") cfg.launchers ++ [ "wlr/taskbar" ];
    modules-center = [ "clock" ];
    modules-right = [
      "tray"
      "pulseaudio"
      "network"
      "battery"
      "custom/power"
    ];
    "custom/zed" = {
      format = "";
      tooltip-format = "Applications";
      on-click = "fuzzel";
    };
    "wlr/taskbar" = {
      format = "{icon}";
      icon-size = 20;
      on-click = "activate";
      on-click-middle = "close";
    };
    clock = {
      format = "{:%a %d %b  %H:%M}";
      tooltip-format = "<tt>{calendar}</tt>";
    };
    pulseaudio = {
      format = "{icon} {volume}%";
      format-muted = "󰝟 muted";
      format-icons.default = [
        "󰕿"
        "󰖀"
        "󰕾"
      ];
      on-click = "pavucontrol";
    };
    network = {
      format-wifi = "󰖩 {essid}";
      format-ethernet = "󰈀 wired";
      format-disconnected = "󰖪 offline";
      tooltip-format = "{ifname} {ipaddr}";
      on-click = "nm-connection-editor";
    };
    battery = {
      format = "{icon} {capacity}%";
      format-icons = [
        "󰁺"
        "󰁼"
        "󰁾"
        "󰂀"
        "󰁹"
      ];
      states = {
        warning = 25;
        critical = 10;
      };
    };
    "custom/power" = {
      format = "⏻";
      tooltip-format = "Lock, log out, suspend, restart or power off";
      on-click = "zed-power-menu";
    };
    tray.spacing = 8;
  }
  // lib.listToAttrs (map launcherModule cfg.launchers);

  launcherCss = lib.concatMapStrings (l: ''
    #custom-${l.id} { padding: 0 10px; ${lib.optionalString l.highlight "background: #c41e3a; color: #ffffff; font-weight: 700;"} }
    #custom-${l.id}:hover { background: #26262c; }
  '') cfg.launchers;
in
{
  options.zed.desktop.panel.launchers = mkOption {
    type = types.listOf (
      types.submodule {
        options = {
          id = mkOption { type = types.strMatching "[a-z0-9-]+"; };
          label = mkOption { type = types.str; };
          tooltip = mkOption { type = types.str; };
          command = mkOption { type = types.str; };
          highlight = mkOption {
            type = types.bool;
            default = false;
          };
        };
      }
    );
    default = [ ];
    description = "Buttons on the Zed panel, left of the window list.";
  };

  config = lib.mkIf config.zed.desktop.enable {
    environment.etc."xdg/waybar/config.jsonc".text = builtins.toJSON panel;
    environment.etc."xdg/waybar/style.css".text = builtins.readFile ./files/waybar.css + launcherCss;
  };
}
