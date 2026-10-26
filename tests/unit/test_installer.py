import json

import pytest

from zedcore.errors import InvalidInput
from zedinstaller import compat, gpu, hostgen


def fake_pci(root, devices):
    base = root / "bus" / "pci" / "devices"
    for addr, (cls, vendor, device, boot) in devices.items():
        d = base / addr
        d.mkdir(parents=True)
        (d / "class").write_text(f"0x{cls:06x}\n")
        (d / "vendor").write_text(f"0x{vendor:04x}\n")
        (d / "device").write_text(f"0x{device:04x}\n")
        if boot is not None:
            (d / "boot_vga").write_text(f"{int(boot)}\n")
    # A non-display device that must be ignored.
    d = base / "0000:00:1f.3"
    d.mkdir(parents=True)
    (d / "class").write_text("0x040300\n")
    (d / "vendor").write_text("0x8086\n")
    (d / "device").write_text("0x51c8\n")
    return root


def test_detect_hybrid_laptop(tmp_path):
    root = fake_pci(
        tmp_path,
        {
            "0000:00:02.0": (0x030000, 0x8086, 0xA7A0, True),
            "0000:01:00.0": (0x030200, 0x10DE, 0x28A0, False),
        },
    )
    gpus = gpu.detect(root)
    assert [g.vendor for g in gpus] == ["intel", "nvidia"]
    assert gpus[1].nix_bus_id == "PCI:1:0:0"

    free = gpu.plan(gpus, allow_unfree=False, want_proprietary_nvidia=True)
    assert free.nvidia_driver == "nouveau" and not free.needs_network
    assert free.hybrid and "intel-media-driver" in free.vaapi

    prop = gpu.plan(gpus, allow_unfree=True, want_proprietary_nvidia=True)
    assert prop.nvidia_driver == "proprietary-open"
    assert prop.needs_network
    assert prop.prime == {"offload": "true", "nvidiaBusId": "PCI:1:0:0", "intelBusId": "PCI:0:2:0"}


def test_old_nvidia_and_amd(tmp_path):
    root = fake_pci(tmp_path, {"0000:03:00.0": (0x030000, 0x10DE, 0x1180, True)})
    p = gpu.plan(gpu.detect(root), allow_unfree=True, want_proprietary_nvidia=True)
    assert p.nvidia_driver == "nouveau"  # Kepler: not supported by the current branch
    root2 = fake_pci(tmp_path / "b", {"0000:03:00.0": (0x030000, 0x1002, 0x73BF, True)})
    p2 = gpu.plan(gpu.detect(root2), allow_unfree=False, want_proprietary_nvidia=False)
    assert p2.vendors == ["amd"] and p2.nvidia_driver == "none"


def test_no_sysfs(tmp_path):
    assert gpu.detect(tmp_path) == []
    assert gpu.plan([], allow_unfree=False, want_proprietary_nvidia=False).notes


def test_nix_string_escaping():
    assert hostgen.nix_string('a "b" ${x} \\ c\nd') == '"a \\"b\\" \\${x} \\\\ c\\nd"'


def sel(**kw):
    base = dict(
        username="alice",
        fullname='Alice "A" ${evil}',
        hostname="zedbox",
        timezone="Europe/Berlin",
        locale="en_US.UTF-8",
        keyboard_layout="us",
        profile="security",
        sessions=["labwc", "niri"],
    )
    base.update(kw)
    return hostgen.Selections(**base)


SOURCE = hostgen.ZedSource(
    store_path="/nix/store/aaaa-source",
    nar_hash="sha256-AAAA",
    last_modified=1,
    rev=None,
    nixpkgs_node={"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "x"}},
)


def test_host_files(registry, catalog):
    files = hostgen.host_files(sel(), gpu.GpuPlan(gpus=[]), SOURCE, registry, catalog)
    state = json.loads(files["managed/state.json"])
    assert state["settings"]["network.hostName"] == "zedbox"
    assert state["settings"]["desktop.sessions"] == ["labwc", "niri"]
    assert state["settings"]["desktop.enable"] is True
    assert "forensics" in state["collections"]
    assert '"Alice \\"A\\" \\${evil}"' in files["host.nix"]
    assert "useOSProber = true" in files["host.nix"]
    lock = json.loads(files["flake.lock"])
    assert lock["nodes"]["zed"]["locked"]["type"] == "path"
    assert 'inputs.zed.url = "path:/nix/store/aaaa-source"' in files["flake.nix"]


def test_released_source_follows_channel():
    src = hostgen.ZedSource(**{**SOURCE.__dict__, "rev": "abc123"})
    assert src.url == "github:SwashX-dev/Zed/stable"
    lock = src.lock()
    assert lock["nodes"]["zed"]["locked"]["rev"] == "abc123"
    assert lock["nodes"]["zed"]["original"]["ref"] == "stable"


def test_minimal_profile_has_no_desktop(registry, catalog):
    st = hostgen.build_state(sel(profile="minimal", sessions=[]), gpu.GpuPlan(gpus=[]), registry, catalog)
    assert st.settings["desktop.enable"] is False and "desktop.sessions" not in st.settings


def test_invalid_input_is_rejected(registry, catalog):
    with pytest.raises(InvalidInput):
        hostgen.host_nix(sel(username="Root;rm"))
    with pytest.raises(InvalidInput):
        hostgen.build_state(sel(hostname="bad_host"), gpu.GpuPlan(gpus=[]), registry, catalog)
    with pytest.raises(InvalidInput):
        hostgen.host_nix(sel(firmware="bios", bios_device="/dev/sda; reboot"))


def test_compat_rules():
    plan = gpu.GpuPlan(gpus=[])
    assert compat.check(sel(), plan, online=False, memory_mib=8192).ok
    r = compat.check(sel(sessions=[]), plan, online=True, memory_mib=8192)
    assert not r.ok and "desktop session" in r.errors[0]
    r = compat.check(sel(sessions=["gnome"]), plan, online=True, memory_mib=8192)
    assert not r.ok
    needs_net = gpu.GpuPlan(gpus=[], nvidia_driver="proprietary-open", needs_network=True)
    assert not compat.check(sel(), needs_net, online=False, memory_mib=8192).ok
    assert compat.check(sel(), needs_net, online=True, memory_mib=8192).ok
    r = compat.check(
        sel(extra_packages=["gimp"]), plan, online=False, memory_mib=8192, packages_on_medium={"firefox"}
    )
    assert not r.ok and "gimp" in r.errors[0]


def test_every_profile_builds_a_valid_state(registry, catalog):
    for pid in hostgen.load_profiles():
        hostgen.build_state(sel(profile=pid), gpu.GpuPlan(gpus=[]), registry, catalog)
