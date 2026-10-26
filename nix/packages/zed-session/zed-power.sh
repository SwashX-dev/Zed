#!@bash@
# Root helper for the power menu.  Desktop users may run exactly
# `zed-power poweroff|reboot|suspend` through sudo without a password (see
# nix/modules/desktop); there is no logind to authorise these actions.
export PATH=@path@:/run/current-system/sw/bin
case "${1:-}" in
    poweroff) exec poweroff ;;
    reboot) exec reboot ;;
    suspend)
        if ! grep -qw mem /sys/power/state; then
            echo "zed-power: this machine does not support suspend to RAM" >&2
            exit 1
        fi
        echo mem >/sys/power/state ;;
    *) echo "usage: zed-power poweroff|reboot|suspend" >&2; exit 2 ;;
esac
