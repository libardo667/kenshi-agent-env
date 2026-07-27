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
