# The Zed desktop: Wayland sessions on seatd, greetd login, PipeWire audio,
# all under s6 (system services) and a per-session s6-svscan (user
# services).  No logind: seat access comes from seatd, and the actions that
# logind would authorise (network control, mounting removable media, power)
# are granted to desktop users by explicit polkit and sudo rules.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkIf
    mkOption
    mkDefault
    types
    ;

  cfg = config.zed.desktop;
  sessionDefs = import ./sessions.nix { inherit pkgs lib; };
  enabled = lib.filterAttrs (n: _: lib.elem n cfg.sessions) sessionDefs;

  # Only Zed's own session entries are offered: upstream ones (niri-session,
  # ...) start their services through systemd --user.
  sessionsDir = pkgs.runCommand "zed-wayland-sessions" { } (
    ''
      mkdir -p $out/share/wayland-sessions
    ''
    + lib.concatStrings (
      lib.mapAttrsToList (n: s: ''
        cat > $out/share/wayland-sessions/zed-${n}.desktop <<EOF
        [Desktop Entry]
        Name=${s.title}
        Comment=${s.description}
        Exec=${pkgs.zed-session}/bin/zed-session ${n} ${s.command}
        Type=Application
        DesktopNames=${s.desktopName}
        EOF
      '') enabled
    )
  );

  labwcTheme = pkgs.runCommand "zed-labwc-theme" { } ''
    mkdir -p $out/share/themes/Zed/labwc
    cp ${./files/labwc/themerc} $out/share/themes/Zed/labwc/themerc
  '';

  # GTK 3 theme: adw-gtk3-dark with Zed's crimson accent.
  gtkTheme = pkgs.runCommand "zed-gtk-theme" { } ''
    mkdir -p $out/share/themes
    cp -r ${pkgs.adw-gtk3}/share/themes/adw-gtk3-dark $out/share/themes/Zed
    chmod -R u+w $out/share/themes/Zed
    sed -i 's/^Name=.*/Name=Zed/' $out/share/themes/Zed/index.theme 2>/dev/null || true
    for f in $out/share/themes/Zed/gtk-3.0/gtk.css $out/share/themes/Zed/gtk-3.0/gtk-dark.css; do
      [ -f "$f" ] || continue
      cat >> "$f" <<'EOF'

    /* Zed accent */
    @define-color accent_color #ff5a73;
    @define-color accent_bg_color #c41e3a;
    @define-color accent_fg_color #ffffff;
    @define-color theme_selected_bg_color #c41e3a;
    @define-color theme_selected_fg_color #ffffff;
    @define-color window_bg_color #0f0f12;
    @define-color view_bg_color #0b0b0d;
    @define-color headerbar_bg_color #121216;
    @define-color headerbar_border_color #c41e3a;
    EOF
    done
  '';

  greeting = "Z E D  ·  ${config.system.nixos.distroName} ${config.zed.version}  ·  s6-supervised";
in
{
  imports = [ ./panel.nix ];

  options.zed.desktop = {
    enable = lib.mkEnableOption "the Zed desktop";

    sessions = mkOption {
      type = types.listOf (types.enum (lib.attrNames sessionDefs));
      default = [ "labwc" ];
      description = ''
        Desktop sessions offered on the login screen.  See
        docs/DESKTOPS.md for what each has been verified to do on Zed.
      '';
    };

    users = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = ''
        Accounts that use the desktop.  They join the groups that give access
        to the seat, audio, video, input and network control (the installer
        fills this in).
      '';
    };

    autologin = mkOption {
      type = types.nullOr (
        types.submodule {
          options = {
            user = mkOption { type = types.str; };
            session = mkOption { type = types.enum (lib.attrNames sessionDefs); };
          };
        }
      );
      default = null;
      description = "Start this session for this user without a login prompt (live image).";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.sessions != [ ];
        message = "zed.desktop.sessions must list at least one session";
      }
    ];

    # --- seat, login --------------------------------------------------
    services.seatd.enable = true;
    services.greetd = {
      enable = true;
      settings = {
        default_session = {
          user = "greeter";
          command = lib.concatStringsSep " " [
            "${pkgs.tuigreet}/bin/tuigreet"
            "--time"
            "--remember"
            "--remember-user-session"
            "--asterisks"
            "--sessions ${sessionsDir}/share/wayland-sessions"
            "--greeting '${greeting}'"
            "--theme 'border=red;text=white;prompt=red;time=gray;action=gray;button=red;container=black;input=white'"
            "--power-shutdown 'sudo -n /run/current-system/sw/bin/zed-power poweroff'"
            "--power-reboot 'sudo -n /run/current-system/sw/bin/zed-power reboot'"
          ];
        };
      }
      // lib.optionalAttrs (cfg.autologin != null) {
        initial_session = {
          inherit (cfg.autologin) user;
          command = "${pkgs.zed-session}/bin/zed-session ${cfg.autologin.session} ${sessionDefs.${cfg.autologin.session}.command}";
        };
      };
    };
    users.users =
      lib.genAttrs cfg.users (_: {
        extraGroups = [
          "seat"
          "video"
          "audio"
          "input"
          "render"
          "networkmanager"
          "storage"
        ];
      })
      // {
        greeter.extraGroups = [ "seat" ];
      };
    users.groups.storage = { };

    # --- audio --------------------------------------------------------
    # PipeWire's daemons run per session under the session's s6-svscan
    # (zed-session); this provides their configuration.
    services.pipewire = {
      enable = true;
      alsa.enable = true;
      pulse.enable = true;
      wireplumber.enable = true;
    };
    # Started on demand over D-Bus (no systemd needed); lets PipeWire use
    # realtime scheduling.
    security.rtkit.enable = true;
    services.pulseaudio.enable = false;

    # --- graphics, portals, devices -------------------------------------
    hardware.graphics.enable = true;
    xdg.portal = {
      enable = true;
      wlr.enable = true;
      extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
      config.common.default = [
        "wlr"
        "gtk"
      ];
    };
    services.udisks2.enable = true;
    services.upower.enable = mkDefault true;
    services.gvfs.enable = mkDefault true;
    programs.dconf.enable = true;
    security.pam.services.swaylock = { };

    # --- authorisation without logind ----------------------------------
    security.polkit.enable = true;
    security.polkit.extraConfig = ''
      // Zed has no logind, so polkit cannot tell "active local session"
      // users apart.  Grant desktop users what an active session gets on
      // other distributions, by group membership.
      polkit.addRule(function (action, subject) {
        if (subject.isInGroup("networkmanager") &&
            action.id.indexOf("org.freedesktop.NetworkManager.") === 0) {
          return polkit.Result.YES;
        }
        var removable = [
          "org.freedesktop.udisks2.filesystem-mount",
          "org.freedesktop.udisks2.encrypted-unlock",
          "org.freedesktop.udisks2.eject-media",
          "org.freedesktop.udisks2.power-off-drive"
        ];
        if (subject.isInGroup("storage") && removable.indexOf(action.id) >= 0) {
          return polkit.Result.YES;
        }
      });
    '';
    security.sudo.extraRules = [
      {
        groups = [ "seat" ];
        commands =
          map
            (a: {
              command = "/run/current-system/sw/bin/zed-power ${a}";
              options = [ "NOPASSWD" ];
            })
            [
              "poweroff"
              "reboot"
              "suspend"
            ];
      }
    ];

    # --- look and feel -------------------------------------------------
    fonts = {
      packages = with pkgs; [
        inter
        jetbrains-mono
        nerd-fonts.symbols-only
        noto-fonts
        noto-fonts-color-emoji
      ];
      fontconfig.defaultFonts = {
        sansSerif = [ "Inter" ];
        monospace = [ "JetBrains Mono" ];
        emoji = [ "Noto Color Emoji" ];
      };
    };

    programs.dconf.profiles.user.databases = [
      {
        settings."org/gnome/desktop/interface" = {
          color-scheme = "prefer-dark";
          accent-color = "red";
          gtk-theme = "Zed";
          icon-theme = "Papirus-Dark";
          cursor-theme = "Bibata-Modern-Classic";
          font-name = "Inter 11";
          monospace-font-name = "JetBrains Mono 11";
        };
      }
    ];

    environment.etc = {
      "xdg/gtk-3.0/settings.ini".text = ''
        [Settings]
        gtk-theme-name=Zed
        gtk-icon-theme-name=Papirus-Dark
        gtk-cursor-theme-name=Bibata-Modern-Classic
        gtk-font-name=Inter 11
        gtk-application-prefer-dark-theme=1
      '';
      "xdg/foot/foot.ini".source = ./files/foot.ini;
      "xdg/fuzzel/fuzzel.ini".source = ./files/fuzzel.ini;
      "xdg/mako/config".source = ./files/mako.conf;
    };

    environment.sessionVariables = {
      XCURSOR_THEME = "Bibata-Modern-Classic";
      XCURSOR_SIZE = "24";
    };

    qt = {
      enable = true;
      platformTheme = "gtk2";
      style = "gtk2";
    };

    environment.pathsToLink = [
      "/share/themes"
      "/share/backgrounds"
      "/share/wayland-sessions"
      "/share/zed"
    ];

    environment.systemPackages =
      (with pkgs; [
        zed-session
        zed-branding
        labwcTheme
        gtkTheme
        sessionsDir
        papirus-icon-theme
        bibata-cursors
        adwaita-icon-theme
        # session components
        waybar
        fuzzel
        foot
        mako
        swaybg
        swaylock
        networkmanagerapplet
        pavucontrol
        wl-clipboard
        grim
        slurp
        brightnessctl
        libnotify
        thunar
        xarchiver
        dbus
      ])
      ++ lib.concatMap (s: s.packages) (lib.attrValues enabled);

    # --- per-session settings -------------------------------------------
    environment.etc."xdg/labwc/rc.xml" = mkIf (enabled ? labwc) { source = ./files/labwc/rc.xml; };
    environment.etc."xdg/labwc/menu.xml" = mkIf (enabled ? labwc) { source = ./files/labwc/menu.xml; };
    environment.etc."xdg/labwc/autostart" = mkIf (enabled ? labwc) { source = ./files/labwc/autostart; };
    environment.etc."niri/config.kdl" = mkIf (enabled ? niri) { source = ./files/niri.kdl; };
    environment.etc."sway/zed.conf" = mkIf (enabled ? sway) { source = ./files/sway.conf; };
  };
}
