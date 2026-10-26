# Early-boot services: everything systemd's PID 1 normally does implicitly
# between stage 2 and multi-user.target.
#
# Where NixOS emits configuration in systemd's file formats (modules-load.d,
# sysctl.d, tmpfiles.d, vconsole.conf, udev rules), Zed runs systemd's
# standalone helper binaries against those files.  They do not require
# systemd to be PID 1, and reusing them keeps every NixOS option that writes
# those files working.  See docs/ARCHITECTURE.md, "systemd components".
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf optionalAttrs;

  cfg = config.zed.init;
  systemd = config.systemd.package;

  ttys =
    lib.filter (t: !(config.services.greetd.enable && t == "tty${toString config.services.greetd.settings.terminal.vt}"))
      cfg.gettys;

  gettyArgs = lib.escapeShellArgs (
    [
      "--login-program"
      config.services.getty.loginProgram
    ]
    ++ lib.optionals (config.services.getty.autologinUser != null) [
      "--autologin"
      config.services.getty.autologinUser
    ]
  );

  mkGetty = tty: {
    name = "getty-${tty}";
    value = {
      description = "login prompt on ${tty}";
      dependencies = [
        "zed-sysinit"
        "zed-wrappers"
      ];
      log = false;
      script = ''
        exec ${pkgs.util-linux}/bin/agetty ${gettyArgs} --noclear --keep-baud ${tty} 115200,38400,9600 linux
      '';
    };
  };

  backdoor = config.testing.backdoor or false;
in
{
  options.zed.init.gettys = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [
      "tty1"
      "tty2"
      "tty3"
      "tty4"
      "tty5"
      "tty6"
    ];
    description = "Virtual terminals with a login prompt.  The greetd VT is skipped automatically.";
  };

  config = mkIf cfg.enable {
    zed.init.services = {
      zed-specialfs = {
        type = "oneshot";
        description = "kernel pseudo-filesystems not mounted by stage 1";
        up = ''
          mnt() {
            mountpoint -q "$2" && return 0
            [ -d "$2" ] || mkdir -p "$2" 2>/dev/null || return 0
            mount -t "$1" -o "$3" "$1" "$2" || echo "zed-specialfs: could not mount $2" >&2
          }
          if [ -d /sys/firmware/efi ]; then
            mnt efivarfs /sys/firmware/efi/efivars nosuid,nodev,noexec
          fi
          mnt cgroup2 /sys/fs/cgroup nosuid,nodev,noexec,nsdelegate
          mnt securityfs /sys/kernel/security nosuid,nodev,noexec
          mnt pstore /sys/fs/pstore nosuid,nodev,noexec
          mnt configfs /sys/kernel/config nosuid,nodev,noexec
          mnt mqueue /dev/mqueue nosuid,nodev,noexec
        '';
      };

      zed-modules = {
        type = "oneshot";
        description = "load kernel modules from /etc/modules-load.d";
        dependencies = [ "zed-specialfs" ];
        up = "${systemd}/lib/systemd/systemd-modules-load";
      };

      zed-sysctl = {
        type = "oneshot";
        description = "apply kernel parameters from /etc/sysctl.d";
        dependencies = [ "zed-modules" ];
        up = "${systemd}/lib/systemd/systemd-sysctl";
      };

      zed-hostname = {
        type = "oneshot";
        description = "set the hostname from /etc/hostname";
        up = ''
          name=$(cat /etc/hostname 2>/dev/null || true)
          printf '%s' "''${name:-zed}" > /proc/sys/kernel/hostname
        '';
      };

      zed-loopback = {
        type = "oneshot";
        description = "bring up the loopback interface";
        up = "ip link set lo up";
      };

      zed-udevd = {
        description = "device manager (systemd-udevd, standalone)";
        dependencies = [ "zed-specialfs" ];
        restartOnSwitch = false;
        script = "exec ${systemd}/lib/systemd/systemd-udevd";
      };

      zed-coldplug = {
        type = "oneshot";
        description = "replay kernel device events and wait for udev";
        dependencies = [
          "zed-udevd"
          "zed-modules"
        ];
        path = [ systemd ];
        up = ''
          for _ in $(seq 100); do
            udevadm control --ping 2>/dev/null && break
            sleep 0.1
          done
          udevadm trigger --type=subsystems --action=add
          udevadm trigger --type=devices --action=add
          udevadm settle --timeout=120 || echo "zed-coldplug: udev did not settle within 120s" >&2
        '';
      };

      zed-console = {
        type = "oneshot";
        description = "console keymap and font from /etc/vconsole.conf";
        dependencies = [ "zed-coldplug" ];
        up = "${systemd}/lib/systemd/systemd-vconsole-setup";
      };

      zed-mounts = {
        type = "oneshot";
        description = "mount remaining filesystems and swap from /etc/fstab";
        dependencies = [ "zed-coldplug" ];
        up = ''
          mount -a -O no_netdev
          swapon -a || true
        '';
      };

      zed-tmpfiles = {
        type = "oneshot";
        description = "create runtime files and directories from /etc/tmpfiles.d";
        dependencies = [ "zed-mounts" ];
        up = "${systemd}/bin/systemd-tmpfiles --create --remove --boot --exclude-prefix=/dev";
      };

      zed-machine-id = {
        type = "oneshot";
        description = "ensure /etc/machine-id exists";
        dependencies = [ "zed-mounts" ];
        up = ''
          if ! grep -qE '^[0-9a-f]{32}$' /etc/machine-id 2>/dev/null; then
            id=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
            printf '%s\n' "$id" > /etc/machine-id.tmp
            chmod 0444 /etc/machine-id.tmp
            mv /etc/machine-id.tmp /etc/machine-id
          fi
        '';
      };

      zed-random-seed = {
        type = "oneshot";
        description = "carry kernel entropy across reboots";
        dependencies = [ "zed-mounts" ];
        up = ''
          seed=/var/lib/zed/random-seed
          install -d -m 0700 /var/lib/zed
          if [ -s "$seed" ]; then
            cat "$seed" > /dev/urandom
          fi
          umask 077
          head -c 512 /dev/urandom > "$seed"
        '';
        down = ''
          umask 077
          head -c 512 /dev/urandom > /var/lib/zed/random-seed
        '';
      };

      zed-wrappers = {
        type = "oneshot";
        description = "setuid/setgid wrappers in /run/wrappers";
        dependencies = [ "zed-specialfs" ];
        fatal = true;
        path = [ pkgs.coreutils ];
        up = ''
          if ! mountpoint -q /run/wrappers; then
            mkdir -p /run/wrappers
            mount -t tmpfs -o nodev,mode=755,size=${config.security.wrapperDirSize} tmpfs /run/wrappers
          fi
          ${config.systemd.services.suid-sgid-wrappers.script}
        '';
      };

      zed-syslog = {
        description = "syslog(3) collector on /dev/log";
        dependencies = [ "zed-specialfs" ];
        notificationFd = 3;
        script = "exec s6-socklog -d3 -x /dev/log";
      };

      zed-sysinit = {
        type = "bundle";
        description = "system initialisation complete";
        contents = [
          "zed-specialfs"
          "zed-modules"
          "zed-sysctl"
          "zed-hostname"
          "zed-loopback"
          "zed-udevd"
          "zed-coldplug"
          "zed-console"
          "zed-mounts"
          "zed-tmpfiles"
          "zed-machine-id"
          "zed-random-seed"
          "zed-syslog"
        ];
      };
    }
    // lib.listToAttrs (map mkGetty ttys)
    // optionalAttrs backdoor {
      # Root shell on the virtio console for NixOS' VM test driver.  Only
      # present when the test-instrumentation module is imported.
      zed-test-backdoor = {
        description = "VM test driver backdoor";
        dependencies = [
          "zed-sysinit"
          "zed-wrappers"
        ];
        log = false;
        script = ''
          export USER=root HOME=/root DISPLAY=:0.0
          for _ in $(seq 600); do
            [ -c /dev/hvc0 ] && [ -c /dev/ttyS0 ] && break
            sleep 0.1
          done
          if [ -e /etc/profile ]; then
            set +o nounset
            source /etc/profile
          fi
          export PAGER=
          cd /tmp
          exec < /dev/hvc0 > /dev/hvc0
          while ! exec 2> /dev/ttyS0; do sleep 0.1; done
          echo "connecting to host..." >&2
          stty -F /dev/hvc0 raw -echo
          echo "Spawning backdoor root shell..."
          PS1="" exec ${pkgs.bashNonInteractive}/bin/bash --norc /dev/hvc0
        '';
      };
    };
  };
}
