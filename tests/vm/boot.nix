# Boots a minimal Zed system and verifies the init architecture.
{
  name = "zed-boot";

  nodes.machine = {
    users.users.alice = {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      initialPassword = "alice-test-password";
    };
  };

  testScript = ''
    machine.start()

    with subtest("PID 1 is s6-svscan, not systemd"):
        comm = machine.succeed("cat /proc/1/comm").strip()
        assert comm == "s6-svscan", f"PID 1 is {comm!r}"
        exe = machine.succeed("readlink /proc/1/exe").strip()
        assert "s6-" in exe and "systemd" not in exe, exe
        machine.fail("pgrep -x systemd")
        machine.fail("pgrep -x systemd-journald")
        machine.fail("pgrep -x systemd-logind")

    with subtest("boot transition completes without failures"):
        machine.wait_for_file("/run/zed/boot-status", timeout=900)
        status = machine.succeed("cat /run/zed/boot-status")
        if status.strip() != "ok":
            print(machine.execute("cat /run/uncaught-logs/current")[1])
        assert status.strip() == "ok", f"boot status: {status!r}"

    with subtest("core s6-rc services are up"):
        active = set(machine.succeed("s6-rc -a list").split())
        expected = {
            "zed-specialfs", "zed-udevd", "zed-coldplug", "zed-mounts",
            "zed-tmpfiles", "zed-wrappers", "zed-syslog", "zed-dbus",
            "zed-nscd", "zed-nix-daemon", "zed-firewall",
            "zed-networkmanager", "zed-chrony", "getty-tty1",
        }
        missing = expected - active
        assert not missing, f"services not up: {sorted(missing)}"

    with subtest("each longrun has its own logger"):
        machine.succeed("s6-rc -a list | grep -qx zed-dbus-log")
        machine.wait_until_succeeds("test -s /var/log/zed/zed-nix-daemon/current || test -d /var/log/zed/zed-nix-daemon")
        machine.succeed("stat -c %U /var/log/zed/zed-dbus | grep -qx zedlog")

    with subtest("firewall ruleset is loaded"):
        rules = machine.succeed("nft list ruleset")
        assert "nixos-fw" in rules, rules

    with subtest("setuid wrappers and accounts work"):
        machine.succeed("test -u /run/wrappers/bin/sudo")
        machine.succeed("su - alice -c id | grep -q wheel")
        machine.succeed("test -s /etc/machine-id")
        machine.succeed("test \"$(cat /proc/sys/kernel/hostname)\" = \"$(cat /etc/hostname)\"")

    with subtest("D-Bus and the Nix daemon answer"):
        machine.succeed("busctl --system list | grep -q org.freedesktop.NetworkManager")
        machine.succeed("nix-store --store daemon -q --hash /run/current-system")

    with subtest("os-release identifies Zed"):
        machine.succeed("grep -qx 'ID=zed' /etc/os-release")
        machine.succeed("grep -qx 'ID_LIKE=nixos' /etc/os-release")

    with subtest("switch-to-configuration is the s6 implementation"):
        out = machine.succeed("/run/current-system/bin/switch-to-configuration dry-activate")
        assert "no running services would be restarted" in out, out
        machine.succeed("/run/current-system/bin/switch-to-configuration test")
        machine.succeed("s6-rc -a list | grep -qx zed-dbus")

    with subtest("killed longruns are restarted by s6-supervise"):
        pid = machine.succeed("s6-svstat -p /run/service/zed-chrony").strip()
        machine.succeed(f"kill -9 {pid}")
        machine.wait_until_succeeds(f"test \"$(s6-svstat -p /run/service/zed-chrony)\" != {pid}")
        machine.wait_until_succeeds("s6-svstat /run/service/zed-chrony | grep -q '^up'")

    with subtest("clean shutdown"):
        machine.shutdown()
  '';
}
