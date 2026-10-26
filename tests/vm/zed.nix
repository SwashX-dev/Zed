# zedd and the zed CLI on a real Zed host: install, remove and roll back a
# package through the transaction engine, with s6 supervising zedd.
#
# The VM gets a host flake at /etc/zed whose inputs are locked to exactly
# the Zed source and nixpkgs revision used here, so the generations it
# builds can be predicted (and prebuilt) outside the VM.  The test asserts
# that zed produces precisely those systems.
{
  self,
  zedLib,
  pkgs,
  lib,
}:

let
  host = import ./zed-host { inherit self zedLib pkgs lib; };
  inherit (host) hostFlake hostLock withHello withoutHello;
  # drvPath without its build closure: only compared as a string.
  predictedDrv = builtins.unsafeDiscardStringContext withHello.drvPath;
  drvList = pkgs.writeText "drv-list.jq" ''
    (.derivations // .) | to_entries[] | "VMDRV " + .key + " " + (.value.name // "")
  '';
in
{
  name = "zed-cli";

  nodes.machine =
    { lib, ... }:
    {
      imports = [ ./zed-host/host.nix ];
      virtualisation.additionalPaths = [
        withHello
        withoutHello
        hostFlake
        hostLock
      ];
      virtualisation.diskSize = 8192;
    };

  testScript = ''
    import json

    machine.start()
    machine.wait_for_file("/run/zed/boot-status", timeout=900)

    machine.succeed(
        "install -d -m 0755 /etc/zed /etc/zed/managed",
        "cp ${hostFlake} /etc/zed/flake.nix",
        "cp ${hostLock} /etc/zed/flake.lock",
        "cp ${./zed-host/host.nix} /etc/zed/host.nix",
        "cp ${./zed-host/hardware-configuration.nix} /etc/zed/hardware-configuration.nix",
        "chmod 0644 /etc/zed/*.nix /etc/zed/flake.lock",
    )

    def zed(args, user=None):
        cmd = f"zed {args}"
        if user:
            cmd = f"su - {user} -c '{cmd}'"
        return machine.succeed(cmd)

    def current():
        return machine.succeed("readlink -f /run/current-system").strip()

    with subtest("zedd runs under s6 and answers"):
        machine.wait_until_succeeds("test -S /run/zed/zedd.sock", timeout=300)
        machine.succeed("s6-svstat /run/service/zedd | grep -q '^up'")
        status = json.loads(zed("status --json"))
        assert status["live"] is False, status
        assert status["boot"]["status"] == "ok", status

    with subtest("unprivileged users can read but not change the system"):
        assert json.loads(zed("list --json", user="alice"))["packages"] == []
        out = machine.fail("su - alice -c 'zed --no-sudo install hello --yes' 2>&1")
        assert "administrator" in out, out
        machine.fail("su - alice -c 'zed --no-sudo services stop zed-chrony' 2>&1")

    with subtest("zed info describes a package without building it"):
        info = json.loads(zed("info hello --json"))
        assert info["version"] == "${pkgs.hello.version}", info
        assert info["installed"] is False and info["problem"] is None, info

    with subtest("invalid and unknown packages are refused before any build"):
        out = machine.fail("zed install 'hello;id' --yes 2>&1")
        assert "not a valid package name" in out, out
        out = machine.fail("zed install no-such-package-zed --yes 2>&1")
        assert "no package named" in out, out
        assert not machine.succeed("ls /run/zed/plans 2>/dev/null || true").strip()

    with subtest("the VM evaluates exactly the predicted system"):
        hello_state = json.loads("""${builtins.toJSON host.helloState}""")
        machine.succeed(
            "install -d /tmp/eval/managed && cp -r /etc/zed/. /tmp/eval/",
            # The JSON contains no single quotes, so it is safe inside '...'.
            f"printf '%s' '{json.dumps(hello_state)}' > /tmp/eval/managed/state.json",
        )
        actual = machine.succeed(
            "nix --extra-experimental-features 'nix-command flakes' eval --raw "
            "path:/tmp/eval#nixosConfigurations.zed.config.system.build.toplevel.drvPath"
        ).strip()
        if actual != "${predictedDrv}":
            # Print every derivation in the VM's graph; tests/vm/compare-drvs.sh
            # finds the first one that differs from the host's.
            graph = machine.succeed(
                f"nix --extra-experimental-features nix-command derivation show -r {actual}"
                " | jq -r -f ${drvList}"
            )
            print(graph)
        assert actual == "${predictedDrv}", f"VM evaluated {actual}"

    with subtest("dry run changes nothing"):
        before = current()
        out = zed("install hello --dry-run")
        assert "install package hello" in out, out
        assert current() == before
        machine.fail("test -e /etc/zed/managed/state.json")

    with subtest("zed install activates exactly the predicted generation"):
        out = zed("install hello --yes")
        assert "generation" in out, out
        assert current() == "${withHello}", (current(), "${withHello}")
        machine.succeed("hello | grep -q 'Hello, world!'")
        state = json.loads(machine.succeed("cat /etc/zed/managed/state.json"))
        assert state["packages"] == ["hello"], state
        assert json.loads(zed("list --json", user="alice"))["packages"] == ["hello"]

    with subtest("installing again is a no-op"):
        out = zed("install hello --yes")
        assert "already installed" in out, out
        assert current() == "${withHello}"

    with subtest("zed remove"):
        zed("remove hello --yes")
        assert current() == "${withoutHello}", current()
        machine.fail("command -v hello")

    with subtest("zed rollback restores the system and its managed state"):
        gens = json.loads(zed("generations --json"))["generations"]
        assert len(gens) >= 3, gens
        zed("rollback --yes")
        assert current() == "${withHello}", current()
        machine.succeed("hello")
        state = json.loads(machine.succeed("cat /etc/zed/managed/state.json"))
        assert state["packages"] == ["hello"], state

    with subtest("history records every change"):
        hist = json.loads(zed("history --json"))["history"]
        kinds = [h["kind"] for h in hist]
        assert kinds.count("change") == 2 and kinds.count("rollback") == 1, hist
        assert all(h["result"] == "ok" for h in hist), hist

    with subtest("zed services and doctor report real state"):
        services = {s["name"]: s for s in json.loads(zed("services --json"))["services"]}
        assert services["zedd"]["up"] and services["zedd"]["pid"], services["zedd"]
        pid = services["zed-chrony"]["pid"]
        zed("services restart zed-chrony")
        machine.wait_until_succeeds(f"test \"$(s6-svstat -p /run/service/zed-chrony)\" != {pid}")
        out = machine.succeed("zed doctor; echo rc=$?")
        assert "s6-svscan is PID 1" in out, out

    with subtest("zedd refuses unknown methods"):
        out = machine.succeed(
            "echo '{\"method\": \"exec\", \"params\": {\"argv\": [\"id\"]}}' | socat - UNIX-CONNECT:/run/zed/zedd.sock"
        )
        assert "unknown method" in out, out
  '';
}
