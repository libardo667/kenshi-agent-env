"""Final Stage 8 absence and single-owner acceptance gates."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from kenshi_agent.env.base import AgentEnvironment
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.env.mock import MockEnvironment
from kenshi_agent.env.replay import ReplayEnvironment

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "kenshi_agent"

RETIRED_MODULES = {
    "action_contracts.py",
    "affordance_parity.py",
    "affordance_surfaces.py",
    "authored_starts.py",
    "blocker_ledger.py",
    "context_action_parity.py",
    "context_action_vocabulary.py",
    "dev_cli.py",
    "doc_export.py",
    "fact_coverage.py",
    "gpu_events.py",
    "graphics_profile.py",
    "live_dev.py",
    "models.py",
    "native_contract_export.py",
    "operation_registry_audit.py",
    "overlay.py",
    "scenario_fixtures.py",
    "schema_export.py",
    "ui_affordances.py",
}

RETIRED_SYMBOLS = {
    "ACTION_CONTRACTS",
    "ActionContract",
    "ActionGuard",
    "LegacyMechanics",
    "MacroRegistry",
    "ReferenceBinding",
    "SkillAction",
    "_execute_live",
    "_execute_step",
    "_run_continuous",
    "_run_single_step",
    "completion_contract_for",
    "contract_for",
}

SINGLE_CLASS_OWNERS = {
    "AffordanceSelection",
    "ContinuityService",
    "ExecutionKernel",
    "HandlerRegistry",
    "OperationAuthority",
    "OperationBindingAuthority",
    "OutcomeRecorder",
    "PlannerContextAssembler",
    "RunCoordinator",
    "SafetySupervisor",
}

RETIRED_LIVE_CONFIG_FIELDS = {
    "allow_skills",
    "calibrated_macro_set_hash",
    "crop_client_area",
    "macros",
    "native_approach_skill",
    "pause_skill",
    "require_cli_execute_flag",
    "semantic_pointer_skills",
    "stateful_approach_skills",
    "stateful_movement_skills",
    "stop_when_terminated",
    "unpause_skill",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _production_trees() -> list[tuple[Path, ast.Module]]:
    return [(path, _tree(path)) for path in sorted(SOURCE.rglob("*.py"))]


def _defined_symbol(node: ast.AST) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    return None


def _mapping_keys(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    keys = {str(key) for key in value}
    for nested in value.values():
        keys.update(_mapping_keys(nested))
    return keys


def test_reconstruction_deleted_every_named_legacy_owner() -> None:
    assert not (SOURCE / "skills").is_dir() or not any(
        (SOURCE / "skills").glob("*.py")
    )
    assert not (SOURCE / "evals").is_dir() or not any(
        (SOURCE / "evals").glob("*.py")
    )
    assert all(not (SOURCE / module).exists() for module in RETIRED_MODULES)

    offenders: list[str] = []
    for path, tree in _production_trees():
        for node in ast.walk(tree):
            if (symbol := _defined_symbol(node)) in RETIRED_SYMBOLS:
                assert isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Name),
                )
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{symbol}"
                )
    assert offenders == []


def test_environment_contract_and_live_adapter_have_no_semantic_dispatch() -> None:
    assert "step" not in AgentEnvironment.__dict__
    assert "dispatch" not in AgentEnvironment.__dict__
    assert {LiveEnvironment, MockEnvironment, ReplayEnvironment} <= set(
        AgentEnvironment.__subclasses__()
    )

    live_tree = _tree(SOURCE / "env" / "live.py")
    semantic_imports = {
        alias.name
        for node in ast.walk(live_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "core.operation"
        for alias in node.names
        if alias.name.endswith("Action")
    }
    semantic_branches = [
        node.lineno
        for node in ast.walk(live_tree)
        if isinstance(node, (ast.If, ast.IfExp, ast.Match))
        and any(
            isinstance(child, ast.Attribute) and child.attr == "kind"
            for child in ast.walk(node)
        )
    ]
    assert semantic_imports == set()
    assert semantic_branches == []


def test_reconstruction_has_exactly_one_of_each_named_architecture_owner() -> None:
    class_owners: dict[str, list[str]] = {name: [] for name in SINGLE_CLASS_OWNERS}
    function_owners: list[str] = []
    registry_assignments: dict[str, list[str]] = {
        "OPERATION_DEFINITION_LIST": [],
        "OPERATION_DEFINITIONS": [],
    }
    for path, tree in _production_trees():
        relative = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in class_owners:
                class_owners[node.name].append(f"{relative}:{node.lineno}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name == "ensure_final_safe_state"
            ):
                function_owners.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in registry_assignments:
                    registry_assignments[node.id].append(f"{relative}:{node.lineno}")

    assert {
        name: locations for name, locations in class_owners.items() if len(locations) != 1
    } == {}
    assert len(function_owners) == 1
    assert all(len(locations) == 1 for locations in registry_assignments.values())


def test_canonical_live_config_has_no_retired_compatibility_field() -> None:
    payload = yaml.safe_load((ROOT / "config" / "live.yaml").read_text(encoding="utf-8"))
    assert _mapping_keys(payload).isdisjoint(RETIRED_LIVE_CONFIG_FIELDS)


def test_acceptance_reports_are_derived_from_current_owners() -> None:
    generated = ROOT / "docs" / "generated"
    assert {
        "AFFORDANCE_CATALOG.md",
        "AFFORDANCE_SURFACES.md",
        "CONTEXT_ACTION_PARITY.md",
        "GAME_BINDING_PARITY.md",
        "MODELED_INTERFACE_AUDIT.md",
        "OPERATION_DEFINITIONS.md",
    } <= {path.name for path in generated.glob("*.md")}
    assert not (generated / "OPERATION_QUEUE.md").exists()
    assert not (generated / "OBSERVED_BLOCKERS.md").exists()
