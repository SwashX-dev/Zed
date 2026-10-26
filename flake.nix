{
  description = "Zed: a NixOS-derived Linux distribution supervised by s6 and s6-rc";

  inputs = {
    # Zed releases pin one tested nixpkgs revision through flake.lock.
    # Installed systems never follow this branch directly; Zed Pulse moves
    # them between tested Zed release snapshots instead.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      inherit (nixpkgs) lib;

      pkgs = import nixpkgs {
        inherit system;
        overlays = [ self.overlays.default ];
      };

      zedLib = import ./nix/lib { inherit self nixpkgs; };
    in
    {
      overlays.default = import ./nix/overlay.nix;

      nixosModules.default = import ./nix/modules;

      lib = zedLib;

      packages.${system} = {
        default = pkgs.zed-suite;
        inherit (pkgs) zed-suite zed-branding;
      }
      // lib.mapAttrs' (
        name: cfg: lib.nameValuePair "iso-${lib.removePrefix "iso-" name}" cfg.config.system.build.isoImage
      ) (lib.filterAttrs (name: _: lib.hasPrefix "iso-" name) self.nixosConfigurations);

      nixosConfigurations = import ./systems { inherit zedLib; };

      checks.${system} = import ./tests {
        inherit self pkgs lib zedLib;
      };

      formatter.${system} = pkgs.nixfmt;

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.nixfmt
          pkgs.python3
          pkgs.python3Packages.pytest
          pkgs.ruff
          pkgs.s6-rc
        ];
      };
    };
}
