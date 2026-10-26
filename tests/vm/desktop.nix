# A Zed desktop session on s6: greetd starts the session for a user, seatd
# provides the seat, the session's own s6-svscan supervises PipeWire and the
# panel, and the compositor renders.  Parameterised by session name.
{ session }:

{
  name = "zed-desktop-${session}";

  nodes.machine =
    { pkgs, ... }:
    {
      virtualisation = {
        memorySize = 3072;
        cores = 2;
        qemu.options = [
          "-vga none"
          "-device virtio-gpu-pci"
        ];
      };
      users.users.alice = {
        isNormalUser = true;
        initialPassword = "alice-test-password";
      };
      zed.desktop = {
        enable = true;
        sessions = [ session ];
        users = [ "alice" ];
        autologin = {
          user = "alice";
          inherit session;
        };
      };
      environment.systemPackages = [
        pkgs.jq
        pkgs.pulseaudio # pactl, to check the PulseAudio compatibility server
        pkgs.wlr-randr
      ];
    };

  enableOCR = true;

  testScript = ''
    import os
    import time

    machine.start()
    machine.wait_for_file("/run/zed/boot-status", timeout=900)

    def as_alice(cmd):
        return machine.succeed(f"su - alice -c 'export XDG_RUNTIME_DIR=/run/user/$(id -u alice); {cmd}'")

    with subtest("boot completes with the desktop services up"):
        status = machine.succeed("cat /run/zed/boot-status").strip()
        assert status == "ok", status
        for svc in ["zed-seatd", "zed-greetd", "zed-dbus", "zed-polkit", "zed-networkmanager"]:
            machine.succeed(f"s6-rc -a list | grep -qx {svc}")
        machine.fail("pgrep -x systemd")
        machine.fail("pgrep -x systemd-logind")

    with subtest("greetd starts the ${session} session as alice"):
        machine.wait_until_succeeds("pgrep -u alice -f 'zed-session ${session}'", timeout=300)
        machine.wait_until_succeeds("test -S /run/user/$(id -u alice)/wayland-1 || test -S /run/user/$(id -u alice)/wayland-0", timeout=300)

    rd = machine.succeed("echo /run/user/$(id -u alice)").strip()

    def stable(svc, seconds=10):
        """The session service is up and has stayed up (not crash-looping)."""
        machine.wait_until_succeeds(
            f"s6-svstat -o up,updownfor {rd}/zed-session/{svc} | awk '$1==\"true\" && $2>={seconds}{{ok=1}} END{{exit !ok}}'",
            timeout=300,
        )

    with subtest("the session's s6-svscan supervises user services"):
        for svc in ["pipewire", "wireplumber", "pipewire-pulse", "waybar", "mako", "wallpaper"]:
            stable(svc)
        print(machine.succeed("cat /home/alice/.local/state/zed/session.log || true"))

    with subtest("a crashed panel is restarted"):
        pid = machine.succeed(f"s6-svstat -p {rd}/zed-session/waybar").strip()
        machine.succeed(f"kill -9 {pid}")
        machine.wait_until_succeeds(
            f"test \"$(s6-svstat -p {rd}/zed-session/waybar)\" != {pid}", timeout=120
        )
        stable("waybar", seconds=5)

    with subtest("audio answers"):
        machine.wait_until_succeeds(
            "su - alice -c 'XDG_RUNTIME_DIR=/run/user/$(id -u) wpctl status' | grep -q Audio", timeout=180
        )
        machine.wait_until_succeeds(
            "su - alice -c 'XDG_RUNTIME_DIR=/run/user/$(id -u) pactl info' | grep -q 'Server Name: PulseAudio (on PipeWire'",
            timeout=180,
        )

    with subtest("the compositor renders"):
        disp = machine.succeed(f"ls {rd} | grep -m1 -E '^wayland-[0-9]+$'").strip()
        time.sleep(15)
        # QEMU's view of the screen, kept in the build directory so that it
        # survives a failed test (nix build --keep-failed).
        machine.screenshot(os.environ.get("TMPDIR", "/build") + "/desktop-${session}.png")
        machine.succeed(
            f"su - alice -c 'XDG_RUNTIME_DIR={rd} WAYLAND_DISPLAY={disp} grim /tmp/shot.png'"
        )
        size = int(machine.succeed("stat -c %s /tmp/shot.png").strip())
        print(machine.execute(f"su - alice -c 'XDG_RUNTIME_DIR={rd} WAYLAND_DISPLAY={disp} wlr-randr'")[1])
        print(machine.succeed("ps -u alice -o pid,args | cut -c1-160"))
        print(machine.succeed("tail -n 30 /home/alice/.local/state/zed/session-*.log || true"))
        assert size > 20000, f"Wayland screenshot is only {size} bytes"
        # The panel is drawn: OCR finds its clock (HH:MM) on QEMU's screen.
        machine.wait_for_text(r"[0-2][0-9]:[0-5][0-9]", timeout=120)
        machine.screenshot("desktop-${session}")

    with subtest("users can control the network without logind"):
        as_alice("nmcli general status")
        as_alice("nmcli networking off && nmcli networking on")

    with subtest("shutdown is clean"):
        machine.shutdown()
  '';
}
