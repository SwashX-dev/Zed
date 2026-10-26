"""The settings registry: which system settings Zed tools may change.

The registry (data/settings.json) is shared with nix/modules/managed.nix,
which maps each validated value onto its NixOS option.  A setting that is
not in the registry cannot be changed through zedd at all.
"""

from __future__ import annotations

import json
import re
import zoneinfo
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import InvalidInput, NotFound

# Core services the system cannot boot or be administered without.  Refused
# by `services.disabled`; managed.nix enforces the same list.
PROTECTED_SERVICES = frozenset(
    {
        "zed-specialfs",
        "zed-modules",
        "zed-sysctl",
        "zed-udevd",
        "zed-coldplug",
        "zed-mounts",
        "zed-tmpfiles",
        "zed-wrappers",
        "zed-sysinit",
        "zed-dbus",
        "zed-nix-daemon",
        "zed-firewall",
        "zedd",
    }
)


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str
    section: str
    title: str
    type: str
    explain: str
    option: tuple[str, ...] | None = None
    choices: tuple[str, ...] = ()
    nullable: bool = False
    min: int | None = None
    max: int | None = None
    pattern: str | None = None
    items: dict = field(default_factory=dict)
    max_items: int | None = None
    validator: str | None = None
    reboot: bool = False
    danger: str | None = None

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "kind": self.kind,
            "section": self.section,
            "title": self.title,
            "type": self.type,
            "explain": self.explain,
            "reboot": self.reboot,
        }
        for k in ("choices", "min", "max", "pattern", "danger"):
            v = getattr(self, k)
            if v not in (None, ()):
                d[k] = list(v) if isinstance(v, tuple) else v
        if self.nullable:
            d["nullable"] = True
        if self.items:
            d["items"] = self.items
        if self.max_items is not None:
            d["maxItems"] = self.max_items
        if self.option:
            d["option"] = ".".join(self.option)
        return d


def _check_scalar(key: str, spec: dict, value: Any, *, zoneinfo_dir: Path | None) -> Any:
    t = spec["type"]
    if t == "bool":
        if not isinstance(value, bool):
            raise InvalidInput(f"{key}: expected true or false")
        return value
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidInput(f"{key}: expected a whole number")
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and value < lo or hi is not None and value > hi:
            raise InvalidInput(f"{key}: {value} is outside {lo}..{hi}")
        return value
    if t == "string":
        if not isinstance(value, str):
            raise InvalidInput(f"{key}: expected text")
        pat = spec.get("pattern")
        if pat and not re.fullmatch(pat, value):
            raise InvalidInput(f"{key}: {value!r} is not an accepted value")
        if spec.get("validator") == "timezone" and not _valid_timezone(value, zoneinfo_dir):
            raise InvalidInput(f"{key}: {value!r} is not a known time zone")
        return value
    if t == "enum":
        if value not in spec["choices"]:
            raise InvalidInput(f"{key}: {value!r} is not one of {', '.join(spec['choices'])}")
        return value
    raise InvalidInput(f"{key}: unsupported setting type {t!r}")


def _valid_timezone(tz: str, zoneinfo_dir: Path | None) -> bool:
    if ".." in tz.split("/"):
        return False
    if zoneinfo_dir is not None and zoneinfo_dir.is_dir():
        return (zoneinfo_dir / tz).is_file()
    try:
        available = zoneinfo.available_timezones()
    except Exception:  # noqa: BLE001 - no tz database at all
        available = set()
    return tz in available if available else True


class Registry:
    def __init__(self, data: dict):
        self.sections = [(s["id"], s["title"]) for s in data["sections"]]
        section_ids = {s for s, _ in self.sections}
        self._settings: dict[str, Setting] = {}
        for raw in data["settings"]:
            if raw["section"] not in section_ids:
                raise ValueError(f"setting {raw['key']} has unknown section {raw['section']}")
            s = Setting(
                key=raw["key"],
                kind=raw["kind"],
                section=raw["section"],
                title=raw["title"],
                type=raw["type"],
                explain=raw["explain"],
                option=tuple(raw["option"]) if "option" in raw else None,
                choices=tuple(raw.get("choices", ())),
                nullable=raw.get("nullable", False),
                min=raw.get("min"),
                max=raw.get("max"),
                pattern=raw.get("pattern"),
                items=raw.get("items", {}),
                max_items=raw.get("maxItems"),
                validator=raw.get("validator"),
                reboot=raw.get("reboot", False),
                danger=raw.get("danger"),
            )
            if s.kind == "option" and not s.option:
                raise ValueError(f"setting {s.key} of kind 'option' has no option path")
            if s.key in self._settings:
                raise ValueError(f"duplicate setting {s.key}")
            self._settings[s.key] = s

    @classmethod
    def load(cls) -> Registry:
        text = resources.files("zedcore").joinpath("data/settings.json").read_text()
        return cls(json.loads(text))

    def __iter__(self):
        return iter(self._settings.values())

    def get(self, key: str) -> Setting:
        try:
            return self._settings[key]
        except KeyError:
            raise NotFound(
                f"unknown setting {key!r}", hint="run 'zed config list' to see the settings Zed manages"
            ) from None

    def validate(self, key: str, value: Any, *, zoneinfo_dir: Path | None = None) -> Any:
        s = self.get(key)
        if value is None:
            if s.nullable:
                return None
            raise InvalidInput(f"{key} cannot be unset to null; use 'zed config reset {key}'")
        if s.type == "list":
            if not isinstance(value, list):
                raise InvalidInput(f"{key}: expected a list")
            if s.max_items is not None and len(value) > s.max_items:
                raise InvalidInput(f"{key}: at most {s.max_items} entries")
            out = [_check_scalar(key, s.items, v, zoneinfo_dir=zoneinfo_dir) for v in value]
            # Lists are sets in practice; keep them canonical.
            out = sorted(set(out), key=lambda v: (str(type(v)), v))
            if key == "services.disabled":
                bad = sorted(set(out) & PROTECTED_SERVICES)
                if bad:
                    raise InvalidInput(
                        f"{', '.join(bad)} cannot be disabled: the system needs them to boot or be managed"
                    )
            return out
        spec = {
            "type": s.type,
            "min": s.min,
            "max": s.max,
            "pattern": s.pattern,
            "choices": list(s.choices),
            "validator": s.validator,
        }
        return _check_scalar(key, spec, value, zoneinfo_dir=zoneinfo_dir)
