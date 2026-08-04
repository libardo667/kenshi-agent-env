"""Execution and authority fitness checks derived from the reconstruction plan."""

from __future__ import annotations

import ast
from pathlib import Path

from kenshi_agent.env import AgentEnvironment, LiveEnvironment, MockEnvironment, ReplayEnvironment
from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "kenshi_agent"
HANDLERS = SOURCE / "execution" / "handlers"

_RETIRED_EXECUTION_OWNERS = {
    "_execute_step",
    "_execute_live",
    "_execute_resource_harvest",
    "_execute_monitored_option",
    # Stage 3: single_step and continuous are scheduling policies, not runtimes.
    "_run_single_step",
    "_run_continuous",
}

# The composition root exists to name the families, never to hold mechanics.
_MECHANICS_COMPOSITION_ROOT = "KenshiOperationMechanics"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_environment_contract_has_no_operation_dispatch_owner() -> None:
    for environment in (
        AgentEnvironment,
        LiveEnvironment,
        MockEnvironment,
        ReplayEnvironment,
    ):
        assert "step" not in environment.__dict__
        assert "dispatch" not in environment.__dict__


def test_retired_execution_owners_are_absent_from_production() -> None:
    found: list[str] = []
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in _RETIRED_EXECUTION_OWNERS
            ):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
    assert found == []


def test_live_environment_is_a_small_semantic_free_external_adapter() -> None:
    path = SOURCE / "env" / "live.py"
    source = path.read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 1500

    model_imports: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.ImportFrom) and node.module == "models":
            model_imports.update(alias.name for alias in node.names)
    assert model_imports <= {
        "QUICKSAVE_COMPLETION_CAPABILITY",
        "ControlMode",
        "NativeControlState",
        "Observation",
        "TelemetrySnapshot",
        "WorldStateRevision",
    }


def test_handler_methods_and_kernel_entrypoint_stay_orchestration_sized() -> None:
    oversized: list[str] = []
    paths = [*sorted((SOURCE / "execution" / "handlers").glob("*.py"))]
    for path in paths:
        for node in ast.walk(_tree(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assert node.end_lineno is not None
            lines = node.end_lineno - node.lineno + 1
            if lines > 250:
                oversized.append(f"{path.relative_to(ROOT)}:{node.name}:{lines}")

    kernel = _tree(SOURCE / "execution" / "kernel.py")
    execute = next(
        node
        for node in ast.walk(kernel)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
    )
    assert execute.end_lineno is not None
    assert execute.end_lineno - execute.lineno + 1 <= 250
    assert oversized == []


def _mechanics_classes() -> dict[str, tuple[Path, ast.ClassDef]]:
    found: dict[str, tuple[Path, ast.ClassDef]] = {}
    for path in sorted(HANDLERS.glob("*.py")):
        for node in _tree(path).body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mechanics"):
                found[node.name] = (path, node)
    return found


def _defined_methods(node: ast.ClassDef) -> set[str]:
    return {
        member.name
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_no_class_owns_more_than_one_operation_family() -> None:
    """The demolished god class must not reassemble under a new name.

    Every private operation belongs to exactly one family, so a class that
    implements two families' mechanics has taken back the authority Stage 2
    distributed. The composition root is allowed to name them all precisely
    because it implements none of them.
    """

    family_of_operation = {
        definition.kind: definition.handler_key.split(".", 1)[0]
        for definition in OPERATION_DEFINITION_LIST
    }
    offenders: list[str] = []
    for name, (path, node) in _mechanics_classes().items():
        implemented = _defined_methods(node) & set(family_of_operation)
        families = {family_of_operation[operation] for operation in implemented}
        if len(families) > 1:
            offenders.append(f"{path.relative_to(ROOT)}:{name} spans {sorted(families)}")
    assert offenders == []


def test_the_mechanics_composition_root_implements_nothing_itself() -> None:
    path, node = _mechanics_classes()[_MECHANICS_COMPOSITION_ROOT]
    assert _defined_methods(node) == set(), (
        f"{path.relative_to(ROOT)}:{_MECHANICS_COMPOSITION_ROOT} gained behavior; "
        "it may only compose the family mechanics."
    )
    assert len(node.bases) >= 2


def test_every_operation_family_has_exactly_one_live_mechanics_owner() -> None:
    family_of_operation = {
        definition.kind: definition.handler_key.split(".", 1)[0]
        for definition in OPERATION_DEFINITION_LIST
    }
    owners: dict[str, list[str]] = {}
    for name, (_path, node) in _mechanics_classes().items():
        for operation in _defined_methods(node) & set(family_of_operation):
            owners.setdefault(operation, []).append(name)
    duplicated = {op: names for op, names in owners.items() if len(names) > 1}
    assert duplicated == {}

    # Cognitive operations are portable services, not Kenshi mechanics.
    mechanical = {
        definition.kind
        for definition in OPERATION_DEFINITION_LIST
        if definition.handler_key.split(".", 1)[0] not in {"cognition"}
        and definition.kind not in {"noop", "stop", "harvest_resource"}
    }
    assert set(owners) == mechanical


def test_the_control_surface_holds_no_operation_semantics() -> None:
    """The shared surface is external delivery only: no operation may live in it."""

    operations = {definition.kind for definition in OPERATION_DEFINITION_LIST}
    surface = next(
        node
        for node in _tree(HANDLERS / "kenshi_surface.py").body
        if isinstance(node, ast.ClassDef) and node.name == "KenshiControlSurface"
    )
    assert _defined_methods(surface) & operations == set()


def test_the_run_loop_carries_no_operation_family_logic() -> None:
    """Sequencing must not know what any particular operation means.

    The coordinator decides when to observe, plan, execute and record. Which
    operation is running is the operation definition's business and the
    handler's, so a semantic action class or operation kind appearing inside the
    loop means scheduling has taken back knowledge Stage 2 distributed.
    """

    kinds = {definition.kind for definition in OPERATION_DEFINITION_LIST}
    families = {
        definition.handler_key.split(".", 1)[0] for definition in OPERATION_DEFINITION_LIST
    }
    # Pause is host control shared by preemption and final safe state, not an
    # operation family the loop reasons about.
    allowed = {"Action", "StopAction", "PauseAction", "PlannerAction"}

    coordinator = next(
        node
        for node in _tree(SOURCE / "run_coordinator.py").body
        if isinstance(node, ast.ClassDef) and node.name == "RunCoordinator"
    )
    loop = next(
        member
        for member in coordinator.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and member.name == "_run_scheduled"
    )
    found: list[str] = []
    for node in ast.walk(loop):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in (kinds | families)
        ):
            found.append(f"run_coordinator.py:{node.lineno}:{node.value}")
        if isinstance(node, ast.Name) and node.id.endswith("Action") and node.id not in allowed:
            found.append(f"run_coordinator.py:{node.lineno}:{node.id}")
    assert found == []


def test_stage_three_has_one_physical_run_sequencing_owner() -> None:
    """The composition root delegates; only RunCoordinator owns the loop."""

    runtime = _tree(SOURCE / "runtime.py")
    runtime_class = next(
        node
        for node in runtime.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentRuntime"
    )
    runtime_methods = _defined_methods(runtime_class)
    assert "_run_scheduled" not in runtime_methods
    assert "_run_single_step" not in runtime_methods
    assert "_run_continuous" not in runtime_methods

    coordinator = next(
        node
        for node in _tree(SOURCE / "run_coordinator.py").body
        if isinstance(node, ast.ClassDef) and node.name == "RunCoordinator"
    )
    assert "_run_scheduled" in _defined_methods(coordinator)


def test_plan_executor_owns_only_plan_local_execution() -> None:
    """Stage 3's narrowed executor must not reabsorb application services."""

    path = SOURCE / "continuous_executor.py"
    tree = _tree(path)
    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden_modules = {
        "advisor_service",
        "continuity_service",
        "env",
        "future_planning",
        "operation_authority",
        "outcome_recorder",
        "planner_context",
        "planner_service",
    }
    assert imported_modules & forbidden_modules == set()
    assert not any(module.startswith("execution.handlers") for module in imported_modules)

    executor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ContinuousPlanExecutor"
    )
    constructor = next(
        node
        for node in executor.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameters = {argument.arg for argument in constructor.args.kwonlyargs}
    assert parameters == {
        "operations",
        "reflexes",
        "logger",
        "clock",
        "state_store",
        "planning_config",
        "event",
    }


def test_stage_four_deleted_the_mutable_action_guard_owner() -> None:
    classes = {
        node.name
        for path in SOURCE.rglob("*.py")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
    }
    assert "ActionGuard" not in classes

    policy = next(
        node
        for node in _tree(SOURCE / "safety.py").body
        if isinstance(node, ast.ClassDef) and node.name == "OperationPolicy"
    )
    assert _defined_methods(policy).isdisjoint({"reserve", "commit", "release"})
    policy_source = ast.unparse(policy)
    for mutable_budget_field in (
        "_action_times",
        "_purchase_count",
        "_pending_primitive_count",
        "_reservations",
    ):
        assert mutable_budget_field not in policy_source

    ledger = next(
        node
        for node in _tree(SOURCE / "action_budget.py").body
        if isinstance(node, ast.ClassDef) and node.name == "ActionBudgetLedger"
    )
    assert {"reserve", "commit", "release"} <= _defined_methods(ledger)

    coordinator_source = (SOURCE / "run_coordinator.py").read_text(encoding="utf-8")
    for authority_owner in ("OperationPolicy", "OperationAuthority", "ActionBudgetLedger"):
        assert authority_owner not in coordinator_source
    assert "operation_port" not in coordinator_source


def test_fresh_binding_calls_route_through_the_one_binding_authority() -> None:
    """Definitions bind only inside the affordance/binding owner.

    Callers may ask the process-wide authority or an injected
    ``OperationBindingAuthority`` to bind. They may not invoke an operation
    definition's binder directly and grow another fresh-binding implementation.
    """

    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        if path.name == "affordances.py":
            continue
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "bind":
                continue
            receiver = ast.unparse(node.func.value)
            if receiver not in {"OPERATION_BINDING_AUTHORITY", "self.binding"}:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{receiver}.bind")
    assert offenders == []


def test_kenshi_surface_is_external_delivery_not_operation_policy() -> None:
    path = HANDLERS / "kenshi_surface.py"
    source = path.read_text(encoding="utf-8")
    surface = next(
        node
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef) and node.name == "KenshiControlSurface"
    )
    assert _defined_methods(surface).isdisjoint(
        {"classify_pointer_action", "rebind_in_lease", "_is_task_start_only"}
    )
    for semantic_dependency in (
        "operation_definitions",
        "definition_for(",
        "OperationDefinition",
        "ApproachDialogueTargetAction",
        ".bind(",
        "native_task_started_reasons",
    ):
        assert semantic_dependency not in source


def test_live_plan_policy_contains_no_current_operation_eligibility() -> None:
    path = SOURCE / "live_plan_policy.py"
    source = path.read_text(encoding="utf-8")
    for operation_policy_dependency in (
        "definition_for",
        ".bind(",
        "resolve_terminal",
        ".idempotency",
    ):
        assert operation_policy_dependency not in source
    policy = next(
        node
        for node in _tree(path).body
        if isinstance(node, ast.FunctionDef) and node.name == "live_plan_policy_errors"
    )
    assert [argument.arg for argument in policy.args.args] == ["plan"]
