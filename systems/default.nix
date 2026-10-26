{ zedLib }:

{
  # Development VM: the smallest Zed system, used for boot tests.
  vm-minimal = zedLib.mkZedSystem {
    modules = [ ./vm/minimal.nix ];
  };
}
