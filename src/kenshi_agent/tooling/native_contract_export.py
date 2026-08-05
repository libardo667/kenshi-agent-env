from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_CAPABILITY_NAME = re.compile(r"^[a-z][a-z_]*(?:\.[a-z][a-z_]*)+$")


@dataclass(frozen=True, slots=True)
class GameplayCapabilities:
    always: tuple[str, ...]
    conditional: tuple[str, ...]


def load_gameplay_capabilities(path: Path) -> GameplayCapabilities:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "always",
        "conditional",
    }:
        raise ValueError("native capability manifest has unexpected fields")
    if payload["schema_version"] != 1:
        raise ValueError("native capability manifest schema_version must be 1")

    groups: dict[str, tuple[str, ...]] = {}
    for field in ("always", "conditional"):
        values = payload[field]
        if not isinstance(values, list) or not values:
            raise ValueError(f"native capability manifest {field} must be a non-empty list")
        if not all(
            isinstance(value, str) and _CAPABILITY_NAME.fullmatch(value)
            for value in values
        ):
            raise ValueError(f"native capability manifest {field} has an invalid name")
        groups[field] = tuple(values)

    combined = (*groups["always"], *groups["conditional"])
    if len(set(combined)) != len(combined):
        raise ValueError("native capability manifest contains duplicate names")
    return GameplayCapabilities(
        always=groups["always"],
        conditional=groups["conditional"],
    )


def _quoted_lines(values: tuple[str, ...]) -> list[str]:
    return [f'            "{value}",' for value in values]


def export_task_type_vocabulary_header(output_dir: Path) -> Path:
    """Carry Kenshi's own task vocabulary into the plug-in as source-derived data.

    The plug-in currently states what a target affords, using two hardcoded
    string literals. It should ask, and the API to ask with -
    `getPlayerTaskProbability(TaskType, target, out)` - needs a list of task
    values to iterate. That list must come from the game, not from a curated
    guess, or the ceiling simply moves rather than lifting.

    `game_sources/kenshi/TaskType.h` is a verbatim capture of KenshiLib's enum,
    already parsed for the parity report. This emits the same entries as C++
    data so the runtime probe and the Python reconciliation are bounded by one
    vocabulary with one provenance.
    """

    from .context_action_vocabulary import load_task_types

    source = load_task_types()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "TaskTypeVocabulary.generated.h"
    lines = [
        "#ifndef KENSHI_AGENT_TASK_TYPE_VOCABULARY_GENERATED_H",
        "#define KENSHI_AGENT_TASK_TYPE_VOCABULARY_GENERATED_H",
        "",
        "// Generated from game_sources/kenshi/TaskType.h; edits are overwritten.",
        "// Upper-bound vocabulary of Kenshi task types. Membership here does not",
        "// mean a task is player-orderable against any given target; it means the",
        "// value exists and may be probed. Kenshi answers the actual question.",
        "",
        "namespace KenshiAgentTelemetry",
        "{",
        "    struct TaskTypeVocabularyEntry",
        "    {",
        "        int value;",
        "        const char* name;",
        "    };",
        "",
        "    inline const TaskTypeVocabularyEntry* TaskTypeVocabulary(",
        "        unsigned int& count)",
        "    {",
        "        static const TaskTypeVocabularyEntry entries[] =",
        "        {",
        *(
            f'            {{ {entry.value}, "{entry.name}" }},'
            for entry in source.entries
        ),
        "        };",
        "        count = sizeof(entries) / sizeof(entries[0]);",
        "        return entries;",
        "    }",
        "}",
        "",
        "#endif",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_item_type_vocabulary_header(output_dir: Path) -> Path:
    """Carry Kenshi's object-type vocabulary into the plug-in.

    Which categories the world scan asks about decides what can ever be
    discovered. Curating that list is how the surface became two hardcoded
    kinds. This emits every member so the scan asks the game about all of
    them and keeps whatever has spatial instances.
    """

    from .item_type_vocabulary import load_item_types

    source = load_item_types()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "ItemTypeVocabulary.generated.h"
    lines = [
        "#ifndef KENSHI_AGENT_ITEM_TYPE_VOCABULARY_GENERATED_H",
        "#define KENSHI_AGENT_ITEM_TYPE_VOCABULARY_GENERATED_H",
        "",
        "// Generated from game_sources/kenshi/ItemType.h; edits are overwritten.",
        "// Every scannable object category Kenshi declares. Most have no spatial",
        "// instances and simply return nothing, which is the game answering",
        "// rather than this plug-in assuming.",
        "",
        "namespace KenshiAgentTelemetry",
        "{",
        "    struct ItemTypeVocabularyEntry",
        "    {",
        "        int value;",
        "        const char* name;",
        "    };",
        "",
        "    inline const ItemTypeVocabularyEntry* ItemTypeVocabulary(",
        "        unsigned int& count)",
        "    {",
        "        static const ItemTypeVocabularyEntry entries[] =",
        "        {",
        *(
            f'            {{ {entry.value}, "{entry.name}" }},'
            for entry in source.scannable
        ),
        "        };",
        "        count = sizeof(entries) / sizeof(entries[0]);",
        "        return entries;",
        "    }",
        "}",
        "",
        "#endif",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_gameplay_capabilities_header(
    manifest_path: Path,
    output_dir: Path,
) -> Path:
    manifest = load_gameplay_capabilities(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "GameplayCapabilities.generated.h"
    lines = [
        "#ifndef KENSHI_AGENT_GAMEPLAY_CAPABILITIES_GENERATED_H",
        "#define KENSHI_AGENT_GAMEPLAY_CAPABILITIES_GENERATED_H",
        "",
        "#include <ostream>",
        "",
        "namespace KenshiAgentTelemetry",
        "{",
        "    inline void AppendGameplayCapabilities(",
        "        std::ostream& json,",
        "        bool includeConditional)",
        "    {",
        "        static const char* const always[] =",
        "        {",
        *_quoted_lines(manifest.always),
        "        };",
        "        static const char* const conditional[] =",
        "        {",
        *_quoted_lines(manifest.conditional),
        "        };",
        "        json << \"[\";",
        "        bool first = true;",
        "        unsigned int index = 0;",
        "        for (index = 0; index < sizeof(always) / sizeof(always[0]); ++index)",
        "        {",
        "            if (!first)",
        "                json << \",\";",
        "            first = false;",
        "            json << \"\\\"\" << always[index] << \"\\\"\";",
        "        }",
        "        if (includeConditional)",
        "        {",
        "            for (index = 0;",
        "                 index < sizeof(conditional) / sizeof(conditional[0]);",
        "                 ++index)",
        "            {",
        "                if (!first)",
        "                    json << \",\";",
        "                first = false;",
        "                json << \"\\\"\" << conditional[index] << \"\\\"\";",
        "            }",
        "        }",
        "        json << \"]\";",
        "    }",
        "}",
        "",
        "#endif",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
