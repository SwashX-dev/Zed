"""Zed tool collections (data/collections.json), shared with managed.nix."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from . import names
from .errors import NotFound


@dataclass(frozen=True)
class Collection:
    id: str
    title: str
    category: str
    description: str
    packages: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "packages": list(self.packages),
        }


class Catalog:
    def __init__(self, data: dict):
        self._cols: dict[str, Collection] = {}
        for raw in data["collections"]:
            c = Collection(
                id=names.collection_id(raw["id"]),
                title=raw["title"],
                category=raw["category"],
                description=raw["description"],
                packages=tuple(names.package_attr(p) for p in raw["packages"]),
            )
            if c.id in self._cols:
                raise ValueError(f"duplicate collection {c.id}")
            self._cols[c.id] = c

    @classmethod
    def load(cls) -> Catalog:
        text = resources.files("zedcore").joinpath("data/collections.json").read_text()
        return cls(json.loads(text))

    def __iter__(self):
        return iter(self._cols.values())

    def get(self, cid: str) -> Collection:
        names.collection_id(cid)
        try:
            return self._cols[cid]
        except KeyError:
            raise NotFound(f"unknown collection {cid!r}", hint="run 'zed collections' to list them") from None

    def packages_of(self, ids: list[str]) -> set[str]:
        return {p for cid in ids for p in self.get(cid).packages}
