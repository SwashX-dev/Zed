#!@bash@
# switch-to-configuration for Zed's s6-rc init.
#
# Installed into every generation as bin/switch-to-configuration, so
# nixos-rebuild, nixos-install and zedd all use it unchanged.  It replaces
# the NixOS implementation, which drives systemd over D-Bus.
set -euo pipefail
export PATH=@path@

toplevel=__TOPLEVEL__
liveDir=@liveDir@
action="${1:-}"

usage() {
    cat >&2 <<EOF
Usage: $0 [switch|boot|test|dry-activate]

switch:       make this configuration the boot default and activate it now
boot:         make this configuration the boot default
test:         activate this configuration without making it the boot default
dry-activate: show which s6-rc services would be restarted
EOF
    exit 1
}

case "$action" in
    switch | boot | test | dry-activate) ;;
    *) usage ;;
esac

if [ ! -f /etc/NIXOS ] && [ "${NIXOS_INSTALL_BOOTLOADER:-0}" != 1 ]; then
    echo "switch-to-configuration: /etc/NIXOS is missing; refusing to modify a system that is not NixOS or Zed" >&2
    exit 100
fi

# Print "name restart" for every atomic service present in both the running
# and the new generation whose definition hash differs.  s6-rc-update reads
# this as a conversion file.
plan_restarts() {
    local old_hashes="$1"
    local new_hashes="$toplevel/zed/s6-rc/hashes"
    local no_restart="$toplevel/zed/s6-rc/no-restart"
    local n h t
    [ -f "$old_hashes" ] || return 0

    declare -A old=()
    while read -r n h; do
        old[$n]=$h
    done <"$old_hashes"

    declare -A skip=()
    while read -r n; do
        [ -n "$n" ] && skip[$n]=1 && skip[$n-log]=1
    done <"$no_restart"

    while read -r n h; do
        [ -n "${old[$n]:-}" ] || continue
        [ "${old[$n]}" != "$h" ] || continue
        [ -z "${skip[$n]:-}" ] || continue
        t=$(cat "$toplevel/zed/s6-rc/source/$n/type")
        [ "$t" != bundle ] || continue
        printf '%s restart\n' "$n"
    done <"$new_hashes"
}

old_system=$(readlink -f /run/current-system 2>/dev/null || true)
old_hashes="${old_system:+$old_system/zed/s6-rc/hashes}"

# s6-rc databases are only compatible within one major series (the first
# two version components).  A live s6-rc-update across series is refused;
# the new services take over at the next boot instead.
series() { cut -d. -f1-2 "$1" 2>/dev/null || echo unknown; }
live_update=1
if [ -n "$old_system" ] && [ -e "$liveDir" ]; then
    old_series=$(series "$old_system/zed/s6-rc/version")
    new_series=$(series "$toplevel/zed/s6-rc/version")
    if [ "$old_series" != "$new_series" ]; then
        live_update=0
    fi
fi

reboot_required() {
    mkdir -p /run/zed
    printf '%s\n' "$1" >>/run/zed/reboot-required
    echo "switch-to-configuration: $1; reboot to complete the change" >&2
}

if [ "$action" = dry-activate ]; then
    echo "would activate $toplevel"
    if [ "$live_update" = 0 ]; then
        echo "s6-rc changes from $old_series to $new_series: services would switch at the next boot"
        exit 0
    fi
    plan=$(plan_restarts "${old_hashes:-/nonexistent}")
    if [ -n "$plan" ]; then
        echo "would restart:"
        printf '%s\n' "$plan" | sed 's/ restart$//; s/^/  /'
    else
        echo "no running services would be restarted"
    fi
    exit 0
fi

mkdir -p /run/nixos
exec {lockfd}>/run/nixos/switch-to-configuration.lock
if ! flock -n "$lockfd"; then
    echo "switch-to-configuration: another switch is already in progress" >&2
    exit 1
fi

status=0

if [ "$action" = switch ] || [ "$action" = boot ]; then
    if ! @installBootLoader@ "$toplevel"; then
        echo "switch-to-configuration: installing the boot loader failed" >&2
        exit 2
    fi
fi

if [ "$action" = switch ] || [ "$action" = test ]; then
    conv=$(mktemp /run/nixos/s6-rc-conv.XXXXXX)
    trap 'rm -f "$conv"' EXIT
    plan_restarts "${old_hashes:-/nonexistent}" >"$conv"

    if ! "$toplevel/activate"; then
        echo "switch-to-configuration: activation script reported errors" >&2
        status=3
    fi

    if [ -e "$liveDir" ] && [ "$live_update" = 0 ]; then
        reboot_required "s6-rc changes from $old_series to $new_series"
    elif [ -e "$liveDir" ]; then
        if [ -s "$conv" ]; then
            echo "restarting changed services:"
            sed 's/ restart$//; s/^/  /' "$conv"
        fi
        db=$(readlink -f "$toplevel/zed/s6-rc/compiled")
        if ! s6-rc-update -l "$liveDir" -f "$conv" "$db"; then
            echo "switch-to-configuration: s6-rc-update failed" >&2
            status=4
        elif ! s6-rc -l "$liveDir" -u change default; then
            echo "switch-to-configuration: some services failed to start" >&2
            status=4
        fi
    else
        echo "switch-to-configuration: s6-rc is not running; services take effect at next boot" >&2
    fi
fi

exit "$status"
