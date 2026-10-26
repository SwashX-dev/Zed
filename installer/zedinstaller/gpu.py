"""Graphics hardware detection for the installer.

Reads PCI devices from sysfs (no lspci needed) and decides which driver
configuration the installed system gets.  The result is data; hostgen turns
it into the installer-owned zed-hardware.nix.

Policy:
* Intel, AMD and virtual GPUs use the free kernel drivers (i915/xe, amdgpu,
  virtio/bochs) with Mesa; Zed adds VA-API video decoding drivers.
* NVIDIA defaults to the free nouveau driver, which works offline.  The
  proprietary driver is offered when the user allows proprietary software;
  it is downloaded and built during installation, so it needs a network
  connection.  Turing (2018) and newer GPUs use NVIDIA's open kernel module.
* Laptops with an integrated GPU plus an NVIDIA GPU get PRIME render offload
  when the proprietary driver is chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

VENDORS = {
    0x8086: "intel",
    0x1002: "amd",
    0x10DE: "nvidia",
    0x1AF4: "virtio",
    0x1234: "qemu",
    0x15AD: "vmware",
    0x80EE: "virtualbox",
    0x1414: "hyperv",
}

# PCI class codes of display controllers (VGA, XGA, 3D, other).
DISPLAY_CLASSES = {0x0300, 0x0301, 0x0302, 0x0380}

# First device id of the Turing generation; NVIDIA's open kernel modules
# support Turing and newer.
NVIDIA_TURING_FIRST = 0x1E00
# Maxwell and newer are supported by the current proprietary driver branch.
NVIDIA_MAXWELL_FIRST = 0x1340


@dataclass(frozen=True)
class Gpu:
    address: str  # domain:bus:device.function, e.g. 0000:01:00.0
    vendor_id: int
    device_id: int
    class_code: int
    boot_vga: bool

    @property
    def vendor(self) -> str:
        return VENDORS.get(self.vendor_id, "unknown")

    @property
    def nix_bus_id(self) -> str:
        """PCI address in the PCI:bus:device:function decimal form NixOS expects."""
        _, bus, devfn = self.address.split(":")
        dev, fn = devfn.split(".")
        return f"PCI:{int(bus, 16)}:{int(dev, 16)}:{int(fn, 16)}"

    def describe(self) -> str:
        return f"{self.vendor} [{self.vendor_id:04x}:{self.device_id:04x}] at {self.address}"


@dataclass
class GpuPlan:
    gpus: list[Gpu]
    nvidia_driver: str = "none"  # none | nouveau | proprietary-open | proprietary
    prime: dict[str, str] = field(default_factory=dict)
    vaapi: list[str] = field(default_factory=list)
    needs_network: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def vendors(self) -> list[str]:
        return sorted({g.vendor for g in self.gpus})

    @property
    def hybrid(self) -> bool:
        v = set(self.vendors)
        return "nvidia" in v and bool(v & {"intel", "amd"})

    def to_dict(self) -> dict:
        return {
            "gpus": [g.describe() for g in self.gpus],
            "vendors": self.vendors,
            "hybrid": self.hybrid,
            "nvidiaDriver": self.nvidia_driver,
            "prime": self.prime,
            "vaapi": self.vaapi,
            "needsNetwork": self.needs_network,
            "notes": self.notes,
        }


def _read_hex(p: Path) -> int | None:
    try:
        return int(p.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def detect(sysfs: Path = Path("/sys")) -> list[Gpu]:
    gpus = []
    devices = sysfs / "bus" / "pci" / "devices"
    if not devices.is_dir():
        return gpus
    for dev in sorted(devices.iterdir()):
        cls = _read_hex(dev / "class")
        if cls is None or (cls >> 8) not in DISPLAY_CLASSES:
            continue
        vendor = _read_hex(dev / "vendor")
        device = _read_hex(dev / "device")
        if vendor is None or device is None:
            continue
        boot_vga = (dev / "boot_vga").is_file() and (dev / "boot_vga").read_text().strip() == "1"
        gpus.append(Gpu(dev.name, vendor, device, cls >> 8, boot_vga))
    return gpus


def plan(gpus: list[Gpu], *, allow_unfree: bool, want_proprietary_nvidia: bool) -> GpuPlan:
    p = GpuPlan(gpus=gpus)
    vendors = {g.vendor for g in gpus}
    if not gpus:
        p.notes.append("No display controller was detected; the generic modesetting driver will be used.")
    if "intel" in vendors:
        p.vaapi += ["intel-media-driver", "intel-vaapi-driver"]
    if "amd" in vendors:
        p.notes.append("AMD graphics use the free amdgpu driver with Mesa (OpenGL, Vulkan and VA-API).")

    nvidia = [g for g in gpus if g.vendor == "nvidia"]
    if nvidia:
        newest = max(g.device_id for g in nvidia)
        if want_proprietary_nvidia and allow_unfree and newest >= NVIDIA_MAXWELL_FIRST:
            p.nvidia_driver = "proprietary-open" if newest >= NVIDIA_TURING_FIRST else "proprietary"
            p.needs_network = True
            p.notes.append(
                "The proprietary NVIDIA driver will be downloaded and built during installation "
                "(network connection required)."
            )
            if p.hybrid:
                igpu = next(g for g in gpus if g.vendor in ("intel", "amd"))
                p.prime = {
                    "offload": "true",
                    "nvidiaBusId": nvidia[0].nix_bus_id,
                    ("intelBusId" if igpu.vendor == "intel" else "amdgpuBusId"): igpu.nix_bus_id,
                }
                p.notes.append(
                    "Hybrid graphics: the integrated GPU drives the display; run demanding "
                    "programs on the NVIDIA GPU with `nvidia-offload PROGRAM`."
                )
        else:
            p.nvidia_driver = "nouveau"
            if want_proprietary_nvidia and not allow_unfree:
                p.notes.append(
                    "The proprietary NVIDIA driver needs 'Allow proprietary software'; using nouveau."
                )
            elif want_proprietary_nvidia:
                p.notes.append(
                    "This NVIDIA GPU predates Maxwell; the current proprietary driver does not support it."
                )
            p.notes.append(
                "NVIDIA graphics use the free nouveau driver. Performance and power management are "
                "limited; the proprietary driver can be enabled later with "
                "'zed config set hardware.nvidiaDriver proprietary'."
            )
    if vendors & {"qemu", "virtio", "vmware", "virtualbox", "hyperv"}:
        p.notes.append("A virtual machine GPU was detected; Wayland sessions may use software rendering.")
    return p
