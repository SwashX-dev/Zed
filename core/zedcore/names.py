"""Validation of identifiers that reach Nix or s6-rc.

These checks run before any identifier is used, and the identifiers are
only ever passed to subprocesses as discrete argv elements or as JSON data,
never interpolated into Nix code or shell strings.
"""

from __future__ import annotations

import re

from .errors import InvalidInput

# One attribute-path segment of nixpkgs: firefox, python3Packages, _1password-gui.
_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_MAX_SEGMENTS = 4

_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_COLLECTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def package_attr(name: str) -> str:
    """Validate a nixpkgs attribute path such as 'firefox' or 'python3Packages.scapy'."""
    if not isinstance(name, str) or not name:
        raise InvalidInput("package name must be a non-empty string")
    segments = name.split(".")
    if len(segments) > _MAX_SEGMENTS:
        raise InvalidInput(f"package name {name!r} has too many components")
    for seg in segments:
        if not _SEGMENT.fullmatch(seg):
            raise InvalidInput(
                f"{name!r} is not a valid package name",
                hint="package names are nixpkgs attribute paths, e.g. 'firefox' or 'python3Packages.scapy'",
            )
        if seg.startswith("__"):
            raise InvalidInput(f"{name!r} refers to an internal nixpkgs attribute")
    return name


def service_name(name: str) -> str:
    if not isinstance(name, str) or not _SERVICE.fullmatch(name):
        raise InvalidInput(f"{name!r} is not a valid service name")
    return name


def collection_id(name: str) -> str:
    if not isinstance(name, str) or not _COLLECTION.fullmatch(name):
        raise InvalidInput(f"{name!r} is not a valid collection id")
    return name
