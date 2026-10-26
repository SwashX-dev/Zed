# Zed's visual identity, rendered from the SVG sources in branding/ at build
# time: icons, wallpapers, the GRUB theme and the plymouth theme.
{
  lib,
  runCommand,
  resvg,
  grub2,
  inter,
  dejavu_fonts,
}:

let
  src = lib.fileset.toSource {
    root = ../../branding;
    fileset = ../../branding;
  };
in
runCommand "zed-branding-0.1.0"
  {
    nativeBuildInputs = [
      resvg
      grub2
    ];
    meta = {
      description = "Zed logos, wallpapers, GRUB and plymouth themes";
      license = lib.licenses.mit;
    };
  }
  ''
    fontdir=$(dirname "$(find -L ${inter}/share/fonts -name 'Inter*.ttf' | head -1)")
    render() { resvg --skip-system-fonts --use-fonts-dir "$fontdir" "$@"; }

    # Icons and logos.
    for size in 16 22 24 32 48 64 128 256 512; do
      d=$out/share/icons/hicolor/''${size}x''${size}/apps
      mkdir -p $d
      render -w $size -h $size ${src}/logos/zed-logo.svg $d/zed-logo.png
    done
    mkdir -p $out/share/icons/hicolor/scalable/apps $out/share/pixmaps
    cp ${src}/logos/zed-logo.svg $out/share/icons/hicolor/scalable/apps/zed-logo.svg
    render -w 256 -h 256 ${src}/logos/zed-logo.svg $out/share/pixmaps/zed-logo.png

    # Wallpapers.
    w=$out/share/backgrounds/zed
    mkdir -p $w
    cp ${src}/wallpapers/zed-katana.svg $w/
    render -w 3840 -h 2160 ${src}/wallpapers/zed-katana.svg $w/zed-katana.png
    render -w 1920 -h 1080 ${src}/wallpapers/zed-katana.svg $w/zed-katana-1080p.png

    # GRUB theme.
    g=$out/share/grub/themes/zed
    mkdir -p $g
    cp ${src}/grub/theme.txt $g/
    cp $w/zed-katana-1080p.png $g/background.png
    render -w 160 -h 160 ${src}/logos/zed-logo.svg $g/logo.png
    for p in select_c select_w select_e scrollbar_thumb_c; do
      render ${src}/parts/$p.svg $g/$p.png
    done
    # grub-mkfont names each font "<family> <style> <size>"; theme.txt
    # refers to those names.  It can exit 0 on errors, so check its output.
    inter=$(find -L ${inter}/share/fonts -name 'Inter*.ttf' ! -name '*Italic*' | sort | head -1)
    test -n "$inter"
    mkfont() {
      local out=$1
      shift
      grub-mkfont -o "$out" "$@"
      test -s "$out" || { echo "grub-mkfont failed for $out"; exit 1; }
    }
    mkfont $g/inter-16.pf2 -s 16 -n Inter "$inter"
    mkfont $g/inter-20.pf2 -s 20 -n Inter "$inter"
    mkfont $g/inter-bold-20.pf2 -s 20 -b -n Inter "$inter"
    mkfont $g/inter-bold-48.pf2 -s 48 -b -n Inter "$inter"
    mkfont $g/dejavu-mono-16.pf2 -s 16 -n "DejaVu Sans Mono" \
      ${dejavu_fonts}/share/fonts/truetype/DejaVuSansMono.ttf

    # Plymouth theme.
    p=$out/share/plymouth/themes/zed
    mkdir -p $p
    cp ${src}/plymouth/zed.script $p/
    substitute ${src}/plymouth/zed.plymouth $p/zed.plymouth --subst-var out
    render -w 360 -h 360 ${src}/logos/zed-logo.svg $p/logo.png
    for part in wordmark track fill prompt; do
      render ${src}/parts/$part.svg $p/$part.png
    done
  ''
