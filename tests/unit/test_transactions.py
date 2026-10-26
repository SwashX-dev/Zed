import json
import threading

import pytest

from zedcore import generations, history
from zedcore.errors import (
    ActivationFailed,
    Busy,
    BuildFailed,
    Conflict,
    InvalidInput,
    NotFound,
    PermissionDenied,
    Unsupported,
)
from zedcore.transactions import Changes


def state_on_disk(paths):
    return json.loads(paths.state_file.read_text())


def test_install_plan_changes_nothing_until_commit(engine, paths, nix):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    assert "install package firefox 158.0.1" in plan.summary
    assert not paths.state_file.exists()
    assert generations.default_generation(paths).number == 1
    # The build saw the staged state, not the live one.
    assert nix.builds[0]["state"]["packages"] == ["firefox"]
    assert "/plans/" in nix.builds[0]["flake"]
    assert plan.restarts == ["zed-dbus"]

    result = engine.commit(plan.id, uid=0)
    assert result["result"] == "applied"
    assert result["generation"] == 2
    assert state_on_disk(paths)["packages"] == ["firefox"]
    assert generations.default_generation(paths).number == 2
    assert generations.default_generation(paths).current
    # Staged files are cleaned up after commit.
    assert not (paths.plans_dir / plan.id).exists()
    h = history.read(paths.history_file)
    assert h[-1]["result"] == "ok" and h[-1]["to"] == 2


def test_remove_and_rollback_restore_state(engine, paths):
    engine.commit(engine.plan_change(Changes(install=["firefox", "hello"]), uid=0).id, uid=0)
    engine.commit(engine.plan_change(Changes(remove=["hello"]), uid=0).id, uid=0)
    assert state_on_disk(paths)["packages"] == ["firefox"]

    plan = engine.plan_rollback(None, uid=0)
    assert plan.target_generation == 2
    engine.commit(plan.id, uid=0)
    assert state_on_disk(paths)["packages"] == ["firefox", "hello"]
    assert generations.default_generation(paths).number == 2

    # Rolling back to generation 1 restores the empty state.
    engine.commit(engine.plan_rollback(1, uid=0).id, uid=0)
    assert state_on_disk(paths)["packages"] == []


def test_failed_activation_restores_previous_system(engine, paths, nix):
    engine.commit(engine.plan_change(Changes(install=["hello"]), uid=0).id, uid=0)
    before_state = paths.state_file.read_text()
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    nix.fail_switch_for.add(str(plan.toplevel))
    with pytest.raises(ActivationFailed, match="previous system was restored"):
        engine.commit(plan.id, uid=0)
    assert paths.state_file.read_text() == before_state
    default = generations.default_generation(paths)
    assert default.number == 2 and default.current
    # The previous generation was re-activated after the failure.
    assert nix.switch_calls[-1] == (str(default.path), "switch")
    assert history.read(paths.history_file)[-1]["result"] == "rolled-back"


def test_failed_build_changes_nothing(engine, paths, nix):
    nix.fail_build = True
    with pytest.raises(BuildFailed):
        engine.plan_change(Changes(install=["firefox"]), uid=0)
    assert not paths.state_file.exists()
    assert not any(paths.plans_dir.iterdir()) if paths.plans_dir.exists() else True


def test_invalid_requests(engine):
    with pytest.raises(NotFound):
        engine.plan_change(Changes(install=["nosuchpackage"]), uid=0)
    with pytest.raises(InvalidInput, match="broken"):
        engine.plan_change(Changes(install=["brokenpkg"]), uid=0)
    with pytest.raises(InvalidInput, match="not an installable package"):
        engine.plan_change(Changes(install=["python3Packages"]), uid=0)
    with pytest.raises(InvalidInput):
        engine.plan_change(Changes(install=["a;b"]), uid=0)
    with pytest.raises(NotFound, match="not installed"):
        engine.plan_change(Changes(remove=["firefox"]), uid=0)
    with pytest.raises(InvalidInput):
        engine.plan_change(Changes(), uid=0)
    with pytest.raises(InvalidInput):
        Changes.from_dict({"install": "firefox"})
    with pytest.raises(InvalidInput):
        Changes.from_dict({"exec": ["rm"]})


def test_unfree_package_is_flagged(engine):
    plan = engine.plan_change(Changes(install=["unfreepkg"]), uid=0)
    assert any("proprietary" in w for w in plan.warnings)


def test_collection_membership_blocks_single_removal(engine):
    engine.commit(engine.plan_change(Changes(add_collections=["network-analysis"]), uid=0).id, uid=0)
    with pytest.raises(InvalidInput, match="network-analysis"):
        engine.plan_change(Changes(remove=["nmap"]), uid=0)
    with pytest.raises(NotFound):
        engine.plan_change(Changes(add_collections=["no-such-collection"]), uid=0)


def test_settings_change(engine, paths):
    plan = engine.plan_change(
        Changes(set={"firewall.allowedTCPPorts": [22], "kernel.package": "latest"}), uid=0
    )
    assert any("reboot" in r for r in plan.reboot_reasons)
    engine.commit(plan.id, uid=0)
    assert state_on_disk(paths)["settings"] == {"firewall.allowedTCPPorts": [22], "kernel.package": "latest"}
    engine.commit(engine.plan_change(Changes(reset=["kernel.package"]), uid=0).id, uid=0)
    assert state_on_disk(paths)["settings"] == {"firewall.allowedTCPPorts": [22]}


def test_noop_plan(engine, nix):
    engine.commit(engine.plan_change(Changes(install=["hello"]), uid=0).id, uid=0)
    builds = len(nix.builds)
    plan = engine.plan_change(Changes(install=["hello"]), uid=0)
    assert plan.noop and len(nix.builds) == builds


def test_stale_plan_is_refused(engine, paths):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    other = engine.plan_change(Changes(install=["hello"]), uid=0)
    engine.commit(other.id, uid=0)
    with pytest.raises(Conflict):
        engine.commit(plan.id, uid=0)
    assert state_on_disk(paths)["packages"] == ["hello"]


def test_lock_change_invalidates_plan(engine, paths):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    paths.lock_file.write_text('{"nodes": {"zed": {"locked": {"rev": "bbbb"}}}}')
    with pytest.raises(Conflict):
        engine.commit(plan.id, uid=0)


def test_plan_owner_enforced(engine):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=1000)
    with pytest.raises(PermissionDenied):
        engine.commit(plan.id, uid=1001)
    with pytest.raises(PermissionDenied):
        engine.discard(plan.id, uid=1001)
    assert engine.commit(plan.id, uid=1000)["result"] == "applied"


def test_one_transaction_at_a_time(engine):
    started, release = threading.Event(), threading.Event()

    def hold():
        with engine.exclusive("test"):
            started.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    started.wait(5)
    try:
        with pytest.raises(Busy):
            engine.plan_change(Changes(install=["firefox"]), uid=0)
    finally:
        release.set()
        t.join()


def test_upgrade_updates_lock_on_commit(engine, paths, nix):
    nix.upgrade_lock = '{"nodes": {"zed": {"locked": {"rev": "cccc"}}}}\n'
    plan = engine.plan_change(Changes(upgrade=True), uid=0)
    assert any("upgrade" in s for s in plan.summary)
    assert "aaaa" in paths.lock_file.read_text()
    engine.commit(plan.id, uid=0)
    assert "cccc" in paths.lock_file.read_text()


def test_upgrade_noop_when_current(engine, paths, nix):
    nix.upgrade_lock = paths.lock_file.read_text()
    plan = engine.plan_change(Changes(upgrade=True), uid=0)
    assert plan.noop and plan.summary == ["Zed is already up to date"]


def test_live_environment_refuses_changes(engine, paths):
    paths.live_marker.parent.mkdir(parents=True, exist_ok=True)
    paths.live_marker.write_text("")
    with pytest.raises(Unsupported, match="live environment"):
        engine.plan_change(Changes(install=["firefox"]), uid=0)


def test_new_unsupported_services_block_the_plan(engine, nix, paths):
    orig = nix.build_system

    def build(*a, **kw):
        top = orig(*a, **kw)
        (top / "zed" / "compatibility.json").write_text('{"services": ["cups"], "timers": []}')
        return top

    nix.build_system = build
    with pytest.raises(Unsupported, match="cups"):
        engine.plan_change(Changes(install=["firefox"]), uid=0)


def test_discard_removes_staged_files(engine, paths):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    assert (paths.plans_dir / plan.id).exists()
    engine.discard(plan.id, uid=0)
    assert not (paths.plans_dir / plan.id).exists()
    with pytest.raises(NotFound):
        engine.commit(plan.id, uid=0)


def test_apply_rebuilds_after_host_edit(engine, paths, nix):
    (paths.host_dir / "host.nix").write_text("{ ... }: { services.openssh.enable = true; }\n")
    plan = engine.plan_change(Changes(rebuild=True), uid=0)
    assert not plan.noop
    assert any("rebuild" in s for s in plan.summary)
    assert engine.commit(plan.id, uid=0)["result"] == "applied"


def test_host_edit_invalidates_plan(engine, paths):
    plan = engine.plan_change(Changes(install=["firefox"]), uid=0)
    (paths.host_dir / "host.nix").write_text("{ ... }: { networking.firewall.enable = false; }\n")
    with pytest.raises(Conflict):
        engine.commit(plan.id, uid=0)
