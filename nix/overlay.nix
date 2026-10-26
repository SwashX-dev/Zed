final: prev: {
  zed-suite = final.callPackage ./packages/zed-suite.nix { };
  zed-branding = final.callPackage ./packages/zed-branding.nix { };
  zed-session = final.callPackage ./packages/zed-session { };
}
