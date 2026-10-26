#!@bash@
# zed-session NAME COMMAND [ARGS...]
#
# Starts a Zed desktop session as the logged-in user, doing what
# `systemd --user` does on other distributions:
#
#   1. a session D-Bus (dbus-run-session),
#   2. an s6-svscan in $XDG_RUNTIME_DIR/zed-session supervising per-user
#      services (PipeWire, WirePlumber, pipewire-pulse),
#   3. the compositor.  Once it is up it runs `zed-session-ready`, which adds
#      the services that need Wayland (panel, notifications, wallpaper).
#
# When the compositor exits, every per-user service is stopped.
set -u
export PATH=@path@:/run/wrappers/bin:/run/current-system/sw/bin:$HOME/.nix-profile/bin

name=${1:?usage: zed-session NAME COMMAND [ARGS...]}
shift
[ $# -gt 0 ] || { echo "zed-session: no compositor command" >&2; exit 2; }

if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "$XDG_RUNTIME_DIR" ]; then
    echo "zed-session: XDG_RUNTIME_DIR is missing; log in through the Zed login screen" >&2
    exit 1
fi

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    exec dbus-run-session -- "$0" "$name" "$@"
fi

export XDG_SESSION_TYPE=wayland
export XDG_SESSION_DESKTOP=$name
export XDG_CURRENT_DESKTOP=${ZED_CURRENT_DESKTOP:-$name}
export MOZ_ENABLE_WAYLAND=1 NIXOS_OZONE_WL=1 SDL_VIDEODRIVER=wayland
export QT_QPA_PLATFORM="wayland;xcb" _JAVA_AWT_WM_NONREPARENTING=1

# Without a GPU render node (some virtual machines), wlroots compositors must
# render in software.
if ! ls /dev/dri/renderD* >/dev/null 2>&1; then
    export WLR_RENDERER=pixman WLR_NO_HARDWARE_CURSORS=1 LIBGL_ALWAYS_SOFTWARE=1
fi

scan=$XDG_RUNTIME_DIR/zed-session
rm -rf "$scan"
mkdir -p "$scan/.env"
chmod 0700 "$scan"

install_services() {
    # $1: directory of service templates.  User copies win over Zed's, and
    # ~/.config/zed/disable/NAME turns a service off.
    local d n
    for d in "$1"/*/; do
        [ -d "$d" ] || continue
        n=$(basename "$d")
        [ -e "$HOME/.config/zed/disable/$n" ] && continue
        [ -e "$scan/$n" ] && continue
        if [ -d "$HOME/.config/zed/services/$n" ]; then
            cp -rL "$HOME/.config/zed/services/$n" "$scan/$n"
        else
            cp -rL "$d" "$scan/$n"
        fi
        chmod -R u+w "$scan/$n"
    done
}

install_services @out@/share/zed/user-services
mkdir -p "$HOME/.local/state/zed"
s6-svscan "$scan" >>"$HOME/.local/state/zed/session.log" 2>&1 &
svscan=$!

"$@"
rc=$?

s6-svscanctl -t "$scan" 2>/dev/null
wait "$svscan" 2>/dev/null
rm -rf "$scan"
exit "$rc"
