# The Zed installer: Calamares configuration, branding, Zed's job modules
# and the `zed-install` launcher.
{
  lib,
  runCommand,
  bash,
  sudo,
  resvg,
  inter,
  glibcLocales,
  calamares-nixos,
  python3,
  zed-suite,
  zed-branding,
}:

let
  src = lib.fileset.toSource {
    root = ../../installer/calamares;
    fileset = ../../installer/calamares;
  };
  version = "0.1.0-alpha";
  editions = {
    minimal = {
      title = "Minimal";
      tagline = "Text console. s6, networking, zed.";
    };
    desktop = {
      title = "Desktop";
      tagline = "The Zed desktop, nothing more.";
    };
    privacy = {
      title = "Privacy";
      tagline = "Private network profile, Tor Browser.";
    };
    security = {
      title = "Security Essentials";
      tagline = "Six security tool collections.";
    };
    complete = {
      title = "Complete";
      tagline = "Every Zed collection and privacy tool.";
    };
  };
in
runCommand "zed-installer-${version}"
  {
    nativeBuildInputs = [ resvg ];
    meta = {
      description = "Zed graphical installer (Calamares configuration and modules)";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
      mainProgram = "zed-install";
    };
  }
  ''
    fontdir=$(dirname "$(find -L ${inter}/share/fonts -name 'Inter*.ttf' | head -1)")
    render() { resvg --skip-system-fonts --use-fonts-dir "$fontdir" "$@"; }

    cfg=$out/etc/calamares
    mkdir -p $cfg/modules $cfg/branding $out/lib/calamares/modules $out/bin $out/share/applications

    substitute ${src}/config/settings.conf $cfg/settings.conf \
      --subst-var-by zedmodules $out/lib/calamares/modules
    for f in ${src}/config/modules/*.conf; do
      substitute "$f" $cfg/modules/$(basename "$f") \
        --subst-var-by localeGenPath ${glibcLocales}/share/i18n/SUPPORTED
    done
    cp -r ${src}/modules/zedcheck ${src}/modules/zedinstall $out/lib/calamares/modules/

    b=$cfg/branding/zed
    mkdir -p $b/editions $b/desktops
    for f in ${src}/branding/zed/*; do
      [ -f "$f" ] || continue
      substitute "$f" $b/$(basename "$f") --subst-var-by version ${version}
    done
    if [ -d ${src}/branding/zed/desktops ]; then
      cp ${src}/branding/zed/desktops/*.png $b/desktops/ 2>/dev/null || true
    fi
    render -w 256 -h 256 ${zed-branding}/share/icons/hicolor/scalable/apps/zed-logo.svg $b/zed-logo.png
    render -w 960 ${zed-branding}/share/backgrounds/zed/zed-katana.svg $b/welcome.png
    ${lib.concatStrings (
      lib.mapAttrsToList (id: e: ''
        sed -e "s|@TITLE@|${e.title}|" -e "s|@TAGLINE@|${e.tagline}|" \
          ${src}/branding/edition-card.svg > card.svg
        render card.svg $b/editions/${id}.png
      '') editions
    )}

    cat > $out/bin/zed-install <<EOF
    #!${bash}/bin/bash
    # Start the Zed installer as root inside the current Wayland session.
    exec /run/wrappers/bin/sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,XDG_SESSION_TYPE \\
      env QT_QPA_PLATFORM=wayland \\
          PYTHONPATH=${zed-suite}/${python3.sitePackages} \\
          PATH=/run/current-system/sw/bin:/run/wrappers/bin \\
      ${calamares-nixos}/bin/calamares -c $cfg "\$@"
    EOF
    chmod 0755 $out/bin/zed-install

    cat > $out/share/applications/zed-install.desktop <<EOF
    [Desktop Entry]
    Type=Application
    Name=Install Zed
    Comment=Install Zed on this computer
    Exec=zed-install
    Icon=zed-logo
    Categories=System;
    EOF
  ''
