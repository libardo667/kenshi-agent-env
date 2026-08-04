"""Game-owned upper-bound vocabulary for world context actions.

Kenshi's ``TaskType`` enum contains both player orders and internal AI tasks.
It is therefore useful reconnaissance input, but it is not the denominator of
right-click affordances. That denominator is the runtime-filtered
``ContextMenu::orders`` list for a concrete selection and target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

TASK_TYPES_SNAPSHOT = (
    Path(__file__).resolve().parents[3]
    / "game_sources"
    / "kenshi"
    / "TaskType.h"
)

_TASK_TYPE_ENUM = re.compile(
    r"\benum\s+(?:class\s+)?TaskType\b[^\{]*\{(?P<body>.*?)\}\s*;",
    re.DOTALL,
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


@dataclass(frozen=True, slots=True)
class TaskTypeEntry:
    value: int
    name: str


@dataclass(frozen=True, slots=True)
class TaskTypeSource:
    entries: tuple[TaskTypeEntry, ...]
    sha256: str

    @property
    def names(self) -> frozenset[str]:
        return frozenset(entry.name for entry in self.entries)


def parse_task_type_enum(text: str) -> TaskTypeSource:
    """Parse C's implicit integer progression for Kenshi's ``TaskType`` enum."""

    match = _TASK_TYPE_ENUM.search(text)
    if match is None:
        raise ValueError(  # mutation: diagnostic-only
            "source does not declare enum TaskType"
        )

    body = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", match.group("body")))
    entries: list[TaskTypeEntry] = []
    next_value = 0
    for raw_entry in body.split(","):
        declaration = raw_entry.strip()
        if not declaration:
            continue
        name, separator, explicit_value = declaration.partition("=")
        name = name.strip()
        if _IDENTIFIER.fullmatch(name) is None:
            raise ValueError(f"invalid TaskType name: {name!r}")
        if separator:
            try:
                next_value = int(explicit_value.strip(), 0)
            except ValueError as exc:
                raise ValueError(
                    f"TaskType {name!r} has a non-integer value"
                ) from exc
        entries.append(TaskTypeEntry(value=next_value, name=name))
        next_value += 1

    if not entries:
        raise ValueError("enum TaskType has no members")  # mutation: diagnostic-only
    names = [entry.name for entry in entries]
    values = [entry.value for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError(  # mutation: diagnostic-only
            "enum TaskType contains duplicate names"
        )
    if len(values) != len(set(values)):
        raise ValueError(  # mutation: diagnostic-only
            "enum TaskType contains duplicate values"
        )
    return TaskTypeSource(
        entries=tuple(entries),
        sha256=sha256(text.encode()).hexdigest(),
    )


def load_task_types(path: Path = TASK_TYPES_SNAPSHOT) -> TaskTypeSource:
    return parse_task_type_enum(path.read_bytes().decode())
