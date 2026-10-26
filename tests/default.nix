{
  self,
  pkgs,
  lib,
  zedLib,
}:

let
  # NixOS VM tests against Zed modules.  These need KVM for reasonable speed;
  # without it QEMU falls back to TCG emulation.
  zedTest =
    path:
    pkgs.testers.runNixOSTest {
      imports = [ path ];
      node.specialArgs = {
        zedFlake = self;
        zedManagedState = { };
        inherit zedLib;
      };
      node.pkgsReadOnly = false;
      defaults = {
        imports = [ self.nixosModules.default ];
        nixpkgs.overlays = [ self.overlays.default ];
      };
    };

  registry = lib.importJSON ../core/zedcore/data/settings.json;
  catalog = lib.importJSON ../core/zedcore/data/collections.json;

  # A system with every managed setting set to its example value, every
  # collection installed and an extra package.  Evaluating it proves that
  # each registry entry names a real NixOS option of the right type and that
  # every catalog package exists in the pinned package set.
  everything = zedLib.mkZedSystem {
    managedState = {
      version = 1;
      packages = [ "hello" ];
      collections = map (c: c.id) catalog.collections;
      settings = lib.listToAttrs (map (s: lib.nameValuePair s.key s.example) registry.settings);
    };
    modules = [
      {
        boot.loader.grub.device = "nodev";
        fileSystems."/" = {
          device = "/dev/disk/by-label/zed";
          fsType = "ext4";
        };
      }
    ];
  };

  evalCheck =
    name: sys: expect:
    let
      cfg = sys.config;
      failed = lib.filter (a: !a.assertion) cfg.assertions;
      drv = builtins.unsafeDiscardStringContext cfg.system.build.toplevel.drvPath;
      problems = lib.filter (x: !x.ok) (expect cfg);
    in
    assert lib.assertMsg (failed == [ ]) (lib.concatMapStringsSep "\n" (a: a.message) failed);
    assert lib.assertMsg (problems == [ ]) (lib.concatMapStringsSep "\n" (x: x.msg) problems);
    pkgs.runCommand "zed-check-${name}" { } ''
      echo ${drv} > $out
    '';
in
{
  vm-boot = zedTest ./vm/boot.nix;
  vm-zed = zedTest (import ./vm/zed.nix { inherit self zedLib pkgs lib; });
  vm-desktop-labwc = zedTest (import ./vm/desktop.nix { session = "labwc"; });
  vm-desktop-niri = zedTest (import ./vm/desktop.nix { session = "niri"; });
  vm-desktop-sway = zedTest (import ./vm/desktop.nix { session = "sway"; });

  registry = evalCheck "registry" everything (cfg: [
    {
      ok = cfg.networking.hostName == "zedtest";
      msg = "managed hostName was not applied";
    }
    {
      ok = lib.elem 443 cfg.networking.firewall.allowedTCPPorts;
      msg = "managed firewall ports were not applied";
    }
    {
      ok = cfg.boot.kernelPackages.kernel.version == pkgs.linuxPackages_6_12.kernel.version;
      msg = "kernel.package = lts-6.12 did not select linux 6.12";
    }
    {
      ok = lib.elem "zed-chrony" cfg.zed.init.disabledServices;
      msg = "services.disabled was not applied";
    }
    {
      ok = cfg.networking.networkmanager.wifi.macAddress == "preserve";
      msg = "an explicit setting must win over the privacy profile's default";
    }
    {
      ok = lib.any (p: (p.pname or "") == "nmap") cfg.environment.systemPackages;
      msg = "collection packages were not installed";
    }
  ]);

  python = pkgs.zed-suite;
}
