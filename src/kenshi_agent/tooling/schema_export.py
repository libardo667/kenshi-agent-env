from __future__ import annotations

import json
from pathlib import Path

from ..affordances import AffordanceSelection
from ..core.affordance import AffordanceReceipt
from ..core.observation import Observation
from ..core.operation import ACTION_ADAPTER
from ..core.planning import (
    PlanEnvelope,
    PlannerDecision,
    PlanPatch,
)
from ..core.telemetry import TelemetrySnapshot
from ..core.transport import (
    ActionReceipt,
    NativeCommandRequest,
)
from ..planners.plan_proposal import PlanProposal
from .research_evidence import (
    DynamicObservations,
    ResearchCallSites,
    ResearchConclusion,
)


def export_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = {
        "affordance_selection.schema.json": AffordanceSelection.model_json_schema(),
        "plan_proposal.schema.json": PlanProposal.model_json_schema(),
        "affordance_receipt.schema.json": AffordanceReceipt.model_json_schema(),
        "telemetry.schema.json": TelemetrySnapshot.model_json_schema(),
        "observation.schema.json": Observation.model_json_schema(),
        "runtime_operation.schema.json": ACTION_ADAPTER.json_schema(),
        "runtime_decision.schema.json": PlannerDecision.model_json_schema(),
        "runtime_action_receipt.schema.json": ActionReceipt.model_json_schema(),
        "native_command_request.schema.json": NativeCommandRequest.model_json_schema(),
        "runtime_plan.schema.json": PlanEnvelope.model_json_schema(),
        "runtime_plan_patch.schema.json": PlanPatch.model_json_schema(),
        "research_call_sites.schema.json": ResearchCallSites.model_json_schema(),
        "research_dynamic_observations.schema.json": (
            DynamicObservations.model_json_schema()
        ),
        "research_conclusion.schema.json": ResearchConclusion.model_json_schema(),
    }
    paths: list[Path] = []
    for name, schema in schemas.items():
        path = output_dir / name
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
