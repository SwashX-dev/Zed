# The Zed desktop session: session launcher, per-user s6 service templates,
# lock screen and power menu.
{
  lib,
  runCommand,
  bash,
  coreutils,
  gnugrep,
  dbus,
  s6,
  s6-portable-utils,
  fuzzel,
  swaylock,
  zed-branding,
}:

let
  wallpaper = "${zed-branding}/share/backgrounds/zed/zed-katana.png";
  path = lib.makeBinPath [
    coreutils
    gnugrep
    dbus
    s6
    s6-portable-utils
    fuzzel
    swaylock
  ];

  # name -> command; run under the session's s6-svscan.
  userServices = {
    pipewire = "pipewire";
    wireplumber = "wireplumber";
    pipewire-pulse = "pipewire-pulse";
  };

  # Started once the compositor runs, with its Wayland environment.
  sessionServices = {
    waybar = "waybar";
    mako = ''mako -c "$(test -f "$HOME/.config/mako/config" && echo "$HOME/.config/mako/config" || echo /etc/xdg/mako/config)"'';
    wallpaper = ''swaybg -m fill -i "$(test -e "$HOME/.config/zed/wallpaper" && echo "$HOME/.config/zed/wallpaper" || echo ${wallpaper})"'';
    nm-applet = "nm-applet --indicator";
  };

  # Each service logs to ~/.local/state/zed/session-NAME.log, truncated at
  # every start so it never grows without bound.
  runScript = name: withEnv: cmd: ''
    #!${bash}/bin/bash
    mkdir -p "$HOME/.local/state/zed"
    exec >"$HOME/.local/state/zed/session-${name}.log" 2>&1
    ${lib.optionalString withEnv "exec s6-envdir -I ../.env ${bash}/bin/bash -c ${lib.escapeShellArg "exec ${cmd}"}"}
    ${lib.optionalString (!withEnv) "exec ${cmd}"}
  '';

  serviceDirs =
    dir: withEnv: services:
    lib.concatStrings (
      lib.mapAttrsToList (n: cmd: ''
        mkdir -p $out/share/zed/${dir}/${n}
        cat > $out/share/zed/${dir}/${n}/run <<'ZEDEOF'
        ${runScript n withEnv cmd}
        ZEDEOF
        chmod 0755 $out/share/zed/${dir}/${n}/run
      '') services
    );
in
runCommand "zed-session-0.1.0"
  {
    meta = {
      description = "Zed desktop session launcher and per-user services";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
  }
  ''
    mkdir -p $out/bin
    for s in zed-session zed-session-ready zed-lock zed-power-menu zed-power; do
      substitute ${./.}/$s.sh $out/bin/$s \
        --subst-var-by bash ${bash}/bin/bash \
        --subst-var-by path ${path} \
        --subst-var-by wallpaper ${wallpaper} \
        --subst-var out
      chmod 0755 $out/bin/$s
    done
    ${serviceDirs "user-services" false userServices}
    ${serviceDirs "session-services" true sessionServices}
  ''
