#!@bash@
# Lock / log out / suspend / restart / power off, from the panel or menu.
export PATH=@path@:/run/wrappers/bin:/run/current-system/sw/bin
choice=$(printf '%s\n' "  Lock" "  Log out" "  Suspend" "  Restart" "  Power off" |
    fuzzel --dmenu --prompt "POWER ❯ " --lines 5 --width 24) || exit 0
case "$choice" in
    *Lock) exec zed-lock ;;
    *"Log out")
        case "${XDG_CURRENT_DESKTOP:-}" in
            labwc) exec labwc --exit ;;
            niri) exec niri msg action quit --skip-confirmation ;;
            sway) exec swaymsg exit ;;
            Hyprland) exec hyprctl dispatch exit ;;
            *) echo "zed-power-menu: cannot log out of ${XDG_CURRENT_DESKTOP:-this session}" >&2; exit 1 ;;
        esac ;;
    *Suspend) zed-lock && exec sudo -n /run/current-system/sw/bin/zed-power suspend ;;
    *Restart) exec sudo -n /run/current-system/sw/bin/zed-power reboot ;;
    *"Power off") exec sudo -n /run/current-system/sw/bin/zed-power poweroff ;;
esac
