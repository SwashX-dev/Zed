#!@bash@
# Lock the screen with Zed's lock screen.
export PATH=@path@:/run/current-system/sw/bin
wall=$HOME/.config/zed/wallpaper
[ -e "$wall" ] || wall=@wallpaper@
exec swaylock -f -i "$wall" --scaling fill \
    --font Inter --font-size 22 --indicator-radius 96 --indicator-thickness 9 \
    --inside-color 121216e6 --ring-color c41e3aff --key-hl-color ff5a73ff --bs-hl-color 6d6d75ff \
    --line-color 00000000 --separator-color 00000000 --text-color e8e8ecff \
    --inside-ver-color 2a0a10e6 --ring-ver-color ff5a73ff --text-ver-color ffffffff \
    --inside-wrong-color 4a0f1be6 --ring-wrong-color ff2040ff --text-wrong-color ffffffff \
    --inside-clear-color 121216e6 --ring-clear-color 6d6d75ff --text-clear-color e8e8ecff \
    "$@"
