# A Zed system for `nixos-rebuild build-vm`-style testing in QEMU.
{ modulesPath, ... }:

{
  imports = [ "${modulesPath}/virtualisation/qemu-vm.nix" ];

  virtualisation = {
    memorySize = 1536;
    cores = 2;
    graphics = false;
  };

  users.users.zed = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    # Development VM only: a known password so the console can be used.
    initialPassword = "zed";
  };
}
