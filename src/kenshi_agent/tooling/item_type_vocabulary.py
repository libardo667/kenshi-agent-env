"""Kenshi's own object-type vocabulary, captured from game source.

`getObjectsWithinSphere` takes an `itemType`, so which object categories the
plug-in scans is decided by which values it passes. Passing a curated few is
how the world-target surface ended up being resources and injured teammates:
the ceiling was set by the caller, not by the game.

Most of these 115 members are definition or savestate types with no spatial
instances. Which ones have world presence is Kenshi's answer to give. The scan
asks every value and keeps whatever comes back, so a category nobody thought
about is discovered rather than excluded by omission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

ITEM_TYPES_SNAPSHOT = (
    Path(__file__).resolve().parents[3] / "game_sources" / "kenshi" / "ItemType.h"
)

_ITEM_TYPE_ENUM = re.compile(
    r"\benum\s+(?:class\s+)?itemType\b[^\{]*\{(?P<body>.*?)\}\s*;",
    re.DOTALL,
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")

# Sentinels that are not real categories: a placeholder and the enum bound.
NON_CATEGORY_NAMES = frozenset({"____XXX___", "OBJECT_TYPE_MAX"})


@dataclass(frozen=True, slots=True)
class ItemTypeEntry:
    value: int
    name: str

    @property
    def is_scannable_category(self) -> bool:
        return self.name not in NON_CATEGORY_NAMES


@dataclass(frozen=True, slots=True)
class ItemTypeSource:
    entries: tuple[ItemTypeEntry, ...]
    sha256: str

    @property
    def scannable(self) -> tuple[ItemTypeEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_scannable_category)


def parse_item_type_enum(text: str) -> ItemTypeSource:
    """Parse C's implicit integer progression for Kenshi's ``itemType`` enum."""

    match = _ITEM_TYPE_ENUM.search(text)
    if match is None:
        raise ValueError("source does not declare enum itemType")

    body = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", match.group("body")))
    entries: list[ItemTypeEntry] = []
    next_value = 0
    for raw_entry in body.split(","):
        declaration = raw_entry.strip()
        if not declaration:
            continue
        name, separator, explicit_value = declaration.partition("=")
        name = name.strip()
        if _IDENTIFIER.fullmatch(name) is None:
            raise ValueError(f"invalid itemType name: {name!r}")
        if separator:
            try:
                next_value = int(explicit_value.strip(), 0)
            except ValueError as exc:
                raise ValueError(f"itemType {name!r} has a non-integer value") from exc
        entries.append(ItemTypeEntry(value=next_value, name=name))
        next_value += 1

    if not entries:
        raise ValueError("enum itemType has no members")
    names = [entry.name for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("enum itemType contains duplicate names")
    return ItemTypeSource(
        entries=tuple(entries),
        sha256=sha256(text.encode()).hexdigest(),
    )


def load_item_types(path: Path = ITEM_TYPES_SNAPSHOT) -> ItemTypeSource:
    return parse_item_type_enum(path.read_text(encoding="utf-8"))
