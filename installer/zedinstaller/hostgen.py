"""Generate the /etc/zed host flake of a new installation.

Files written into <target>/etc/zed:

  flake.nix                   pins the Zed release (input `zed`)
  flake.lock                  locks it to exactly the source on the medium
  host.nix                    the user's file: account, boot loader
  zed-hardware.nix            installer-owned: detected GPUs
  hardware-configuration.nix  from nixos-generate-config (written by the job)
  managed/state.json          zedd's file: profile, desktops, machine settings

Everything a Zed tool can change later (host name, time zone, locale,
keyboard, desktops, packages) goes into managed/state.json, so the
installer's choices and later changes never conflict.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources

from zedcore.catalog import Catalog
from zedcore.errors import InvalidInput
from zedcore.registry import Registry
from zedcore.state import ManagedState

from .gpu import GpuPlan

USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
STATE_VERSION = "26.05"


def nix_string(s: str) -> str:
    """Quote arbitrary text as a Nix string literal."""
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def load_profiles() -> dict[str, dict]:
    data = json.loads(resources.files("zedcore").joinpath("data/profiles.json").read_text())
    return {p["id"]: p for p in data["profiles"]}


@dataclass
class Selections:
    username: str
    fullname: str = ""
    hostname: str = "zed"
    timezone: str | None = None
    locale: str | None = None
    keyboard_layout: str | None = None
    keyboard_variant: str | None = None
    console_keymap: str | None = None
    profile: str = "desktop"
    sessions: list[str] = field(default_factory=lambda: ["labwc"])
    extra_collections: list[str] = field(default_factory=list)
    extra_packages: list[str] = field(default_factory=list)
    allow_unfree: bool = False
    firmware: str = "efi"  # efi | bios
    efi_mount: str = "/boot"
    bios_device: str | None = None


def build_state(sel: Selections, gpu: GpuPlan, registry: Registry, catalog: Catalog) -> ManagedState:
    profiles = load_profiles()
    if sel.profile not in profiles:
        raise InvalidInput(f"unknown installation profile {sel.profile!r}")
    prof = profiles[sel.profile]
    raw = json.loads(json.dumps(prof["state"]))  # deep copy
    raw["version"] = 1
    raw["packages"] = sorted(set(raw.get("packages", [])) | set(sel.extra_packages))
    raw["collections"] = sorted(set(raw.get("collections", [])) | set(sel.extra_collections))
    for c in raw["collections"]:
        catalog.get(c)
    s = raw.setdefault("settings", {})
    s["network.hostName"] = sel.hostname
    if sel.timezone:
        s["locale.timeZone"] = sel.timezone
    if sel.locale:
        s["locale.defaultLocale"] = sel.locale
    if sel.keyboard_layout:
        s["desktop.keyboardLayout"] = sel.keyboard_layout
    if sel.console_keymap:
        s["locale.consoleKeyMap"] = sel.console_keymap
    if prof["desktop"]:
        s["desktop.enable"] = True
        s["desktop.sessions"] = sorted(set(sel.sessions))
    else:
        s["desktop.enable"] = False
    if sel.allow_unfree:
        s["nix.allowUnfree"] = True
    if gpu.nvidia_driver != "none":
        s["hardware.nvidiaDriver"] = gpu.nvidia_driver
    # Validates every value against the same registry zedd uses.
    return ManagedState.from_dict(raw, registry)


def host_nix(sel: Selections) -> str:
    if not USERNAME.fullmatch(sel.username) or sel.username == "root":
        raise InvalidInput(f"{sel.username!r} is not a valid user name")
    if sel.firmware == "efi":
        boot = f"""  boot.loader = {{
    grub = {{
      enable = true;
      efiSupport = true;
      device = "nodev";
      # Adds other operating systems (Windows, other Linux) to the boot menu.
      useOSProber = true;
    }};
    efi.canTouchEfiVariables = true;
    efi.efiSysMountPoint = {nix_string(sel.efi_mount)};
  }};"""
    elif sel.firmware == "bios":
        if not sel.bios_device or not re.fullmatch(r"/dev/[A-Za-z0-9/_.-]+", sel.bios_device):
            raise InvalidInput("BIOS installations need the disk to install GRUB on")
        boot = f"""  boot.loader.grub = {{
    enable = true;
    device = {nix_string(sel.bios_device)};
    useOSProber = true;
  }};"""
    else:
        raise InvalidInput(f"unknown firmware type {sel.firmware!r}")

    variant = ""
    if sel.keyboard_variant:
        variant = f"\n  services.xserver.xkb.variant = {nix_string(sel.keyboard_variant)};\n"

    return f"""# Your Zed configuration.
#
# Zed's tools never modify this file: edit it freely and apply it with
# `sudo zed apply` (or any NixOS option, see https://search.nixos.org/options).
# Settings changed with `zed config`, Zed Blueprint and Zed Arsenal are kept
# in managed/state.json instead.  If you set the same option here, the
# build reports a conflict rather than silently overriding either.
{{ config, lib, pkgs, ... }}:

{{
  imports = [ ./zed-hardware.nix ];

{boot}

  users.users.{nix_string(sel.username)} = {{
    isNormalUser = true;
    description = {nix_string(sel.fullname or sel.username)};
    extraGroups = [ "wheel" ];
  }};
  zed.desktop.users = [ {nix_string(sel.username)} ];
{variant}
  # The NixOS release this system was first installed with.  Do not change
  # it when upgrading; see https://nixos.org/manual/nixos/stable/#sec-upgrading
  system.stateVersion = "{STATE_VERSION}";
}}
"""


def zed_hardware_nix(gpu: GpuPlan) -> str:
    lines = [
        "# Written by the Zed installer from the detected hardware.  Zed's tools",
        "# may regenerate this file; put your own settings in host.nix.",
        "{",
        f"  zed.hardware.gpu.vendors = [ {' '.join(nix_string(v) for v in gpu.vendors)} ];",
        f"  zed.hardware.gpu.vaapi = [ {' '.join(nix_string(v) for v in gpu.vaapi)} ];",
    ]
    for g in gpu.gpus:
        lines.insert(3, f"  # {g.describe()}")
    if gpu.prime:
        lines.append("  zed.hardware.gpu.nvidia.prime = {")
        lines.append(f"    offload = {gpu.prime.get('offload', 'false')};")
        for k in ("nvidiaBusId", "intelBusId", "amdgpuBusId"):
            if k in gpu.prime:
                lines.append(f"    {k} = {nix_string(gpu.prime[k])};")
        lines.append("  };")
    lines.append("}")
    return "\n".join(lines) + "\n"


FLAKE_TEMPLATE = """{{
  description = "Zed host configuration";

  # The Zed release this machine follows.  `zed upgrade` moves it to the
  # newest tested release of the channel; `zed rollback` undoes that.
  inputs.zed.url = "{url}";

  outputs =
    {{ zed, ... }}:
    {{
      nixosConfigurations.zed = zed.lib.mkHost {{ hostDir = ./.; }};
    }};
}}
"""


@dataclass
class ZedSource:
    """The Zed source on the installation medium (from /etc/zed-installer/source.json)."""

    store_path: str
    nar_hash: str
    last_modified: int
    rev: str | None
    nixpkgs_node: dict
    owner: str = "SwashX-dev"
    repo: str = "Zed"
    channel: str = "stable"

    @classmethod
    def from_dict(cls, d: dict) -> ZedSource:
        return cls(
            store_path=d["storePath"],
            nar_hash=d["narHash"],
            last_modified=int(d.get("lastModified", 0)),
            rev=d.get("rev"),
            nixpkgs_node=d["nixpkgs"],
            owner=d.get("owner", "SwashX-dev"),
            repo=d.get("repo", "Zed"),
            channel=d.get("channel", "stable"),
        )

    @property
    def url(self) -> str:
        if self.rev:
            return f"github:{self.owner}/{self.repo}/{self.channel}"
        return f"path:{self.store_path}"

    def lock(self) -> dict:
        if self.rev:
            locked = {
                "type": "github",
                "owner": self.owner,
                "repo": self.repo,
                "rev": self.rev,
                "narHash": self.nar_hash,
                "lastModified": self.last_modified,
            }
            original = {"type": "github", "owner": self.owner, "repo": self.repo, "ref": self.channel}
        else:
            # Unpublished build: pin the exact source on the medium.
            locked = {
                "type": "path",
                "path": self.store_path,
                "narHash": self.nar_hash,
                "lastModified": self.last_modified,
            }
            original = {"type": "path", "path": self.store_path}
        return {
            "nodes": {
                "root": {"inputs": {"zed": "zed"}},
                "zed": {"inputs": {"nixpkgs": "nixpkgs"}, "locked": locked, "original": original},
                "nixpkgs": self.nixpkgs_node,
            },
            "root": "root",
            "version": 7,
        }


def host_files(
    sel: Selections, gpu: GpuPlan, source: ZedSource, registry: Registry, catalog: Catalog
) -> dict[str, str]:
    state = build_state(sel, gpu, registry, catalog)
    return {
        "flake.nix": FLAKE_TEMPLATE.format(url=source.url),
        "flake.lock": json.dumps(source.lock(), indent=2, sort_keys=True) + "\n",
        "host.nix": host_nix(sel),
        "zed-hardware.nix": zed_hardware_nix(gpu),
        "managed/state.json": state.to_json(),
    }
