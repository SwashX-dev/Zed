# Maps the zedd-managed state (/etc/zed/managed/state.json) onto NixOS
# options.
#
# The state is plain JSON written only by zedd's transaction engine.  This
# module never evaluates code from it: package names are looked up as
# attribute paths in pkgs, collection ids in Zed's catalog, and settings
# through the registry shared with zedcore (core/zedcore/data/settings.json),
# which names the exact NixOS option each key may set.
#
# The state arrives as the `zedManagedState` module argument rather than an
# option: which options it defines must be known before `config` exists.
{
  config,
  lib,
  pkgs,
  zedManagedState ? { },
  ...
}:

let
  inherit (lib)
    mkIf
    mkMerge
    filterAttrs
    attrNames
    concatStringsSep
    ;

  state = zedManagedState;
  registry = lib.importJSON ../../core/zedcore/data/settings.json;
  catalog = lib.importJSON ../../core/zedcore/data/collections.json;

  byKey = lib.listToAttrs (map (s: lib.nameValuePair s.key s) registry.settings);
  collectionsById = lib.listToAttrs (map (c: lib.nameValuePair c.id c) catalog.collections);

  settings = state.settings or { };
  packageNames = state.packages or [ ];
  collectionIds = state.collections or [ ];

  unknownSettings = lib.filter (k: !(byKey ? ${k})) (attrNames settings);
  unknownCollections = lib.filter (c: !(collectionsById ? ${c})) collectionIds;

  validAttr = p: builtins.match "([A-Za-z_][A-Za-z0-9_-]{0,63})([.][A-Za-z_][A-Za-z0-9_-]{0,63}){0,3}" p != null;

  lookup =
    p:
    let
      path = lib.splitString "." p;
    in
    if !(validAttr p) then
      throw "zed: invalid package name '${p}' in managed state"
    else
      lib.attrByPath path (throw "zed: package '${p}' does not exist in this Zed release's package set") pkgs;

  collectionPackages = lib.concatMap (c: collectionsById.${c}.packages) (
    lib.filter (c: collectionsById ? ${c}) collectionIds
  );

  optionSettings = filterAttrs (k: _: byKey ? ${k} && byKey.${k}.kind == "option") settings;

  kernels = {
    latest = pkgs.linuxPackages_latest;
    "lts-6.12" = pkgs.linuxPackages_6_12;
  };
  kernelChoice = settings."kernel.package" or "default";
in
{
  config = mkMerge (
    [
      {
        assertions = [
          {
            assertion = unknownSettings == [ ];
            message = "zed: unknown settings in managed state: ${concatStringsSep ", " unknownSettings}";
          }
          {
            assertion = unknownCollections == [ ];
            message = "zed: unknown collections in managed state: ${concatStringsSep ", " unknownCollections}";
          }
          {
            assertion = kernelChoice == "default" || kernels ? ${kernelChoice};
            message = "zed: unknown kernel choice '${kernelChoice}'";
          }
        ];

        environment.systemPackages = map lookup (lib.unique (packageNames ++ collectionPackages));

        # Every generation records the state it was built from, so a
        # rollback can restore the matching state file.
        system.systemBuilderCommands = ''
          mkdir -p $out/zed
          ln -s ${pkgs.writeText "zed-managed-state.json" (builtins.toJSON state)} $out/zed/managed-state.json
        '';
      }

      (mkIf (kernels ? ${kernelChoice}) {
        boot.kernelPackages = kernels.${kernelChoice};
      })

      (mkIf (settings ? "services.disabled") {
        zed.init.disabledServices = settings."services.disabled";
      })
    ]
    ++ lib.mapAttrsToList (k: v: lib.setAttrByPath byKey.${k}.option v) optionSettings
  );
}
