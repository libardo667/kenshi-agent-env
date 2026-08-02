from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from kenshi_agent.affordance_surfaces import (
    SURFACES,
    SurfaceStatus,
    render_surface_registry,
)
from kenshi_agent.context_action_vocabulary import (
    load_task_types,
    parse_task_type_enum,
)

INSTALLED_ENUMS = Path(
    "/mnt/c/Hub/Projects/CppProjects/KenshiLib_Examples_deps/"
    "KenshiLib/Include/kenshi/Enums.h"
)


def test_task_type_parser_preserves_c_enum_values() -> None:
    text = """
        enum Unrelated { IGNORE_ME };
        enum TaskType {
            NULL_TASK,
            MOVE = 4,
            BUILD,
        };
        """
    source = parse_task_type_enum(text)

    assert [(entry.value, entry.name) for entry in source.entries] == [
        (0, "NULL_TASK"),
        (4, "MOVE"),
        (5, "BUILD"),
    ]
    assert source.sha256 == sha256(text.encode()).hexdigest()


def test_task_type_parser_ignores_comments_and_empty_declarations() -> None:
    source = parse_task_type_enum(
        """
        enum TaskType {
            NULL_TASK,
            // A line comment is not a task.
            /* Nor is a block comment. */ MOVE = 0x10,
            ,
            BUILD,
        };
        """
    )

    assert [(entry.value, entry.name) for entry in source.entries] == [
        (0, "NULL_TASK"),
        (16, "MOVE"),
        (17, "BUILD"),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("enum SomethingElse { NONE };", "does not declare"),
        ("enum TaskType {};", "has no members"),
        ("enum TaskType { NOT-AN-IDENTIFIER };", "invalid TaskType name"),
        ("enum TaskType { NULL_TASK = OTHER_VALUE };", "non-integer value"),
        ("enum TaskType { NULL_TASK, NULL_TASK };", "duplicate names"),
        ("enum TaskType { NULL_TASK = 1, MOVE = 1 };", "duplicate values"),
    ],
)
def test_task_type_parser_rejects_ambiguous_sources(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_task_type_enum(text)


def test_world_context_vocabulary_is_not_reported_as_the_denominator() -> None:
    source = load_task_types()
    surface = next(item for item in SURFACES if item.key == "world_context_action")

    assert surface.status is SurfaceStatus.SOURCE_IDENTIFIED
    assert surface.enumerated is None
    assert surface.candidate_vocabulary is not None
    assert surface.candidate_vocabulary.path.is_file()
    assert surface.candidate_vocabulary.enumerated == len(source.entries)
    rendered = "\n".join(render_surface_registry())
    assert (
        f"candidate vocabulary: {len(source.entries)} from "
        "game_sources/kenshi/TaskType.h"
    ) in rendered
    assert "witnessed per-target ContextMenu::orders" in rendered
    assert "not a global denominator" in rendered


@pytest.mark.skipif(
    not INSTALLED_ENUMS.is_file(),
    reason="The pinned KenshiLib Enums.h is unavailable on this host",
)
def test_captured_task_type_vocabulary_matches_installed_kenshilib() -> None:
    captured = load_task_types()
    installed = parse_task_type_enum(INSTALLED_ENUMS.read_text(encoding="utf-8"))

    assert captured.entries == installed.entries
