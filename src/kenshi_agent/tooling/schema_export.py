from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capability_manifest import CapabilityManifest
from .generation_manifest import GenerationManifest
from .schema_documents import base_schema_documents


def schema_documents() -> dict[str, dict[str, Any]]:
    """Return the in-memory schema authority used by export and provenance."""

    schemas = base_schema_documents()
    schemas["generation_manifest.schema.json"] = GenerationManifest.model_json_schema()
    schemas["capability-manifest.schema.json"] = CapabilityManifest.model_json_schema()
    return schemas


def export_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = schema_documents()
    paths: list[Path] = []
    for name, schema in schemas.items():
        path = output_dir / name
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
