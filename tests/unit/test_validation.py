import json

import pytest

from zedcore import names
from zedcore.errors import InvalidInput, NotFound
from zedcore.registry import Registry
from zedcore.state import ManagedState, load, write_atomic


@pytest.mark.parametrize(
    "ok", ["firefox", "python3Packages.scapy", "_1password-gui", "linuxPackages_latest.perf"]
)
def test_package_names_accepted(ok):
    assert names.package_attr(ok) == ok


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "firefox; rm -rf /",
        "$(id)",
        "a b",
        "../etc",
        "pkgs.lib.__splicedPackages",
        "a.b.c.d.e",
        "firefox#x",
        "-firefox",
        "x" * 65,
        '"firefox"',
        "firefox\n",
    ],
)
def test_package_names_rejected(bad):
    with pytest.raises(InvalidInput):
        names.package_attr(bad)


def test_service_and_collection_names():
    assert names.service_name("zed-sshd") == "zed-sshd"
    with pytest.raises(InvalidInput):
        names.service_name("../zed")
    with pytest.raises(InvalidInput):
        names.collection_id("Forensics")


def test_registry_types(registry: Registry):
    assert registry.validate("firewall.enable", False) is False
    with pytest.raises(InvalidInput):
        registry.validate("firewall.enable", "no")
    assert registry.validate("boot.loader.timeout", 5) == 5
    with pytest.raises(InvalidInput):
        registry.validate("boot.loader.timeout", 61)
    with pytest.raises(InvalidInput):
        registry.validate("boot.loader.timeout", True)
    assert registry.validate("firewall.allowedTCPPorts", [443, 22, 22]) == [22, 443]
    with pytest.raises(InvalidInput):
        registry.validate("firewall.allowedTCPPorts", [0])
    with pytest.raises(InvalidInput):
        registry.validate("network.hostName", "Bad_Host")
    with pytest.raises(InvalidInput):
        registry.validate("kernel.package", "rt")
    with pytest.raises(NotFound):
        registry.validate("no.such.key", 1)


def test_kernel_params_cannot_inject(registry: Registry):
    with pytest.raises(InvalidInput):
        registry.validate("kernel.params", ["quiet init=/bin/sh"])
    with pytest.raises(InvalidInput):
        registry.validate("kernel.params", ['"; x'])


def test_protected_services(registry: Registry):
    assert registry.validate("services.disabled", ["zed-bluetooth"]) == ["zed-bluetooth"]
    with pytest.raises(InvalidInput, match="zed-firewall"):
        registry.validate("services.disabled", ["zed-firewall"])


def test_timezone(registry: Registry, tmp_path):
    zi = tmp_path / "zoneinfo"
    (zi / "Europe").mkdir(parents=True)
    (zi / "Europe" / "Berlin").write_text("TZif")
    assert registry.validate("locale.timeZone", "Europe/Berlin", zoneinfo_dir=zi) == "Europe/Berlin"
    with pytest.raises(InvalidInput):
        registry.validate("locale.timeZone", "Mars/Olympus", zoneinfo_dir=zi)
    with pytest.raises(InvalidInput):
        registry.validate("locale.timeZone", "../../etc/passwd", zoneinfo_dir=zi)


def test_nullable_enum(registry: Registry):
    assert registry.validate("power.cpuFreqGovernor", None) is None
    with pytest.raises(InvalidInput):
        registry.validate("firewall.enable", None)


def test_every_option_setting_has_a_path(registry: Registry):
    for s in registry:
        if s.kind == "option":
            assert s.option and all(isinstance(x, str) for x in s.option)
        assert s.explain


def test_state_roundtrip_and_validation(registry, tmp_path):
    st = ManagedState(packages=["wireshark", "firefox"], settings={"firewall.enable": True})
    p = tmp_path / "managed" / "state.json"
    write_atomic(p, st.to_json())
    loaded = load(p, registry)
    assert loaded.packages == ["firefox", "wireshark"]
    assert loaded.digest() == st.digest()
    # Tampered files are rejected, not silently used.
    p.write_text(json.dumps({"version": 1, "packages": ["x; y"]}))
    with pytest.raises(InvalidInput):
        load(p, registry)
    p.write_text(json.dumps({"version": 1, "settings": {"firewall.enable": "off"}}))
    with pytest.raises(InvalidInput):
        load(p, registry)
    p.write_text("{not json")
    with pytest.raises(InvalidInput):
        load(p, registry)
    p.write_text(json.dumps({"version": 2}))
    with pytest.raises(InvalidInput):
        load(p, registry)


def test_write_atomic_leaves_no_temp_files(tmp_path):
    p = tmp_path / "f.json"
    write_atomic(p, "one")
    write_atomic(p, "two")
    assert p.read_text() == "two"
    assert [x.name for x in tmp_path.iterdir()] == ["f.json"]


def test_catalog_packages_are_valid(catalog):
    ids = [c.id for c in catalog]
    assert len(ids) == len(set(ids))
    for c in catalog:
        assert c.packages
        for p in c.packages:
            names.package_attr(p)


def test_registry_examples_validate(registry: Registry):
    # The same examples are applied to a full NixOS evaluation by the
    # `registry` flake check, so both sides agree on every setting.
    import json
    from importlib import resources

    raw = json.loads(resources.files("zedcore").joinpath("data/settings.json").read_text())
    for s in raw["settings"]:
        assert "example" in s, s["key"]
        registry.validate(s["key"], s["example"])
