from __future__ import annotations

import json
from pathlib import Path

from .models import (
    ACTION_ADAPTER,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlanPatch,
    TelemetrySnapshot,
)


def export_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = {
        "action.schema.json": ACTION_ADAPTER.json_schema(),
        "telemetry.schema.json": TelemetrySnapshot.model_json_schema(),
        "observation.schema.json": Observation.model_json_schema(),
        "decision.schema.json": PlannerDecision.model_json_schema(),
        "plan.schema.json": PlanEnvelope.model_json_schema(),
        "plan_patch.schema.json": PlanPatch.model_json_schema(),
    }
    paths: list[Path] = []
    for name, schema in schemas.items():
        path = output_dir / name
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
