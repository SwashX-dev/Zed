#!@bash@
# zed-session-ready NAME
#
# Run by the compositor at startup.  Publishes the Wayland environment to
# D-Bus activation and to the session's s6 services, then starts the
# services that need a running compositor.
set -u
export PATH=@path@:/run/wrappers/bin:/run/current-system/sw/bin:$HOME/.nix-profile/bin

scan=$XDG_RUNTIME_DIR/zed-session
if [ ! -d "$scan" ]; then
    echo "zed-session-ready: not inside a zed-session" >&2
    exit 1
fi

vars="WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP NIRI_SOCKET SWAYSOCK"
for v in $vars; do
    if [ -n "${!v:-}" ]; then
        printf '%s' "${!v}" >"$scan/.env/$v"
    fi
done
# shellcheck disable=SC2086
dbus-update-activation-environment $vars 2>/dev/null || true

for d in @out@/share/zed/session-services/*/; do
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
s6-svscanctl -a "$scan"
