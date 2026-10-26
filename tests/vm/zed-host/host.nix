# host.nix of the machine in tests/vm/zed.nix.  The test VM is itself the
# "installed" Zed host, so its host configuration carries the test driver's
# instrumentation; otherwise switching generations would cut the driver off.
{
  lib,
  pkgs,
  modulesPath,
  ...
}:

{
  imports = [
    "${modulesPath}/testing/test-instrumentation.nix"
    "${modulesPath}/virtualisation/qemu-vm.nix"
  ];

  # The revision differs between the host's checkout and the copy inside
  # the VM; pin it so that both evaluate to the same derivations.
  system.configurationRevision = lib.mkForce "zed-vm-test";

  networking.hostName = "machine";
  virtualisation.memorySize = 4096;
  documentation.enable = false;
  # The VM has no network; never wait for a binary cache.
  nix.settings.substituters = lib.mkForce [ ];
  environment.systemPackages = [
    pkgs.jq
    pkgs.socat
  ];

  users.users.alice = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    initialPassword = "alice-test-password";
  };
}
