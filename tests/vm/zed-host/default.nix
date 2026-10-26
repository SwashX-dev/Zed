# The /etc/zed host flake used by tests/vm/zed.nix, and the generations it
# is expected to produce.  Separate so the prediction can be checked on the
# host as well (see docs/DEVELOPMENT.md).
{
  self,
  zedLib,
  pkgs,
  lib,
}:

rec {
  ourLock = lib.importJSON ../../../flake.lock;

  hostFlake = pkgs.writeText "flake.nix" ''
    {
      description = "Zed host (VM test)";
      inputs.zed.url = "path:${self.outPath}";
      inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
      inputs.zed.inputs.nixpkgs.follows = "nixpkgs";
      outputs = { zed, ... }: {
        nixosConfigurations.zed = zed.lib.mkHost { hostDir = ./.; };
      };
    }
  '';

  hostLock = pkgs.writeText "flake.lock" (
    builtins.toJSON {
      nodes = {
        root.inputs = {
          zed = "zed";
          nixpkgs = "nixpkgs";
        };
        inherit (ourLock.nodes) nixpkgs;
        zed = {
          inputs.nixpkgs = [ "nixpkgs" ];
          locked = {
            type = "path";
            path = self.outPath;
            inherit (self) narHash;
            lastModified = self.lastModified or 0;
          };
          original = {
            type = "path";
            path = self.outPath;
          };
        };
      };
      root = "root";
      version = 7;
    }
  );

  emptyState = {
    version = 1;
    packages = [ ];
    collections = [ ];
    settings = { };
  };
  helloState = emptyState // {
    packages = [ "hello" ];
  };

  predict =
    state:
    (zedLib.mkHost {
      hostDir = ./.;
      managedState = state;
    }).config.system.build.toplevel;

  withHello = predict helloState;
  withoutHello = predict emptyState;
}
