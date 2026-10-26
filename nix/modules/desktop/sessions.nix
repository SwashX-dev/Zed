# Desktop sessions Zed can offer.  `status` is what Zed's VM tests have
# verified, and is shown by the installer and `zed desktop list`:
#
#   verified      logs in, renders, runs the panel, audio and network applet
#                 in the desktop VM test
#   experimental  builds and is wired up, but not (yet) verified by a test
#
# Desktops that cannot run without systemd are listed in docs/DESKTOPS.md
# instead of here.
{ pkgs, lib }:

{
  labwc = {
    title = "Zed · labwc";
    desktopName = "labwc";
    description = "Stacking Wayland desktop with Zed's panel. Lightest and most tested.";
    command = "labwc";
    packages = [ pkgs.labwc ];
    status = "experimental";
    memory = "≈ 250 MB idle";
    kind = "Stacking (floating windows)";
  };

  niri = {
    title = "Zed · niri";
    desktopName = "niri";
    description = "Scrollable-tiling Wayland compositor: windows in an endless horizontal strip.";
    command = "niri -c /etc/niri/config.kdl";
    packages = [
      pkgs.niri
      pkgs.xwayland-satellite
    ];
    status = "experimental";
    memory = "≈ 300 MB idle";
    kind = "Scrollable tiling";
  };

  sway = {
    title = "Zed · Sway";
    desktopName = "sway";
    description = "i3-compatible tiling Wayland compositor.";
    command = "sway -c /etc/sway/zed.conf";
    packages = [ pkgs.sway ];
    status = "experimental";
    memory = "≈ 250 MB idle";
    kind = "Tiling (i3-style)";
  };
}
