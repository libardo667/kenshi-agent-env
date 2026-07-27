from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from kenshi_agent.config import PlanningConfig
from kenshi_agent.models import (
    ActivePlanContext,
    CharacterState,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    ControlMode,
    GameState,
    IdempotencyPolicy,
    InterruptPolicy,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlanningMode,
    PlanPatch,
    PlanStep,
    RiskBudget,
    SetSpeedAction,
    TelemetrySnapshot,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.planners import HeuristicPlanner, ScriptedPlanner, SubprocessPlanner
from kenshi_agent.planning import (
    PlanBudgetLedger,
    PlanValidationError,
    evaluate_condition,
    validate_future_plan_patch,
    validate_plan,
)
from kenshi_agent.skills import MacroRegistry


def revision(sequence: int, *, capability_epoch: int = 1) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        frame_sequence=sequence,
        capability_epoch=capability_epoch,
        observed_at_monotonic=float(sequence),
    )


def field_condition(
    path: str,
    expected: str | int | float | bool,
    *,
    required_capabilities: list[str] | None = None,
) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=path,
        operator=ConditionOperator.EQUALS,
        expected=expected,
        max_age_seconds=3.0,
        required_capabilities=required_capabilities or [],
    )


def fresh_condition() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def pause_step(
    step_id: str = "resume",
    *,
    on_success: str | None = None,
    on_failure: str | None = None,
    retry_budget: int = 0,
    idempotency: IdempotencyPolicy = IdempotencyPolicy.AT_MOST_ONCE,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=PauseAction(paused=False),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                True,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        failure_conditions=[],
        timeout_seconds=1.0,
        retry_budget=retry_budget,
        idempotency=idempotency,
        on_success=on_success,
        on_failure=on_failure,
    )


def speed_step(step_id: str = "accelerate") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=SetSpeedAction(speed=3),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.speed_multiplier",
                3.0,
                required_capabilities=["game.speed"],
            )
        ],
        failure_conditions=[],
        timeout_seconds=1.0,
        retry_budget=0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    )


def interruption_pause_step(
    step_id: str = "pause-interrupted-movement",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=PauseAction(paused=True),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.paused",
                True,
                required_capabilities=["game.pause"],
            ),
            field_condition(
                "telemetry.native_control.command_active",
                False,
            ),
        ],
        failure_conditions=[],
        timeout_seconds=2.0,
        retry_budget=0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    )


def plan_for(
    current_revision: WorldStateRevision,
    *,
    steps: list[PlanStep] | None = None,
    entry_step_id: str = "resume",
    max_actions: int = 2,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="survival-setup",
        plan_version=1,
        objective="Resume and accelerate safe mock time.",
        control_mode=ControlMode.INTERFACE_ONLY,
        based_on_revision=current_revision,
        assumptions=[fresh_condition()],
        steps=steps or [pause_step(on_success="accelerate"), speed_step()],
        entry_step_id=entry_step_id,
        max_actions=max_actions,
        max_wall_seconds=5.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


def observation(
    *,
    sequence: int = 4,
    paused: bool | None = True,
    speed: float | None = 1.0,
    capabilities: list[str] | None = None,
    stale: bool = False,
    age_seconds: float = 0.0,
) -> Observation:
    return Observation(
        run_id="planning",
        step_index=0,
        mode="mock",
        control_mode=ControlMode.INTERFACE_ONLY,
        world_revision=revision(sequence),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            identity_session_id=(
                "session-planning"
                if capabilities is not None and "identity.stable_handles" in capabilities
                else None
            ),
            capabilities=capabilities
            if capabilities is not None
            else ["game.pause", "game.speed", "game.time"],
            game=GameState(
                loaded=True,
                paused=paused,
                speed_multiplier=speed,
                elapsed_minutes=0.0,
            ),
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=age_seconds,
    )


def test_condition_evaluator_preserves_false_unknown_unavailable_and_stale() -> None:
    paused_false = field_condition(
        "telemetry.game.paused",
        False,
        required_capabilities=["game.pause"],
    )

    assert (
        evaluate_condition(paused_false, observation(paused=False)).result is ConditionResult.TRUE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=True)).result is ConditionResult.FALSE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=None)).result is ConditionResult.UNKNOWN
    )
    assert (
        evaluate_condition(
            paused_false,
            observation(paused=False, capabilities=["game.speed"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=False, stale=True)).result
        is ConditionResult.STALE
    )
    inferred_capability_gate = field_condition(
        "telemetry.game.paused",
        False,
    )
    assert (
        evaluate_condition(
            inferred_capability_gate,
            observation(paused=False, capabilities=["game.speed"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )


def test_exact_selection_count_requires_stable_identity_capability() -> None:
    exact_one = field_condition("telemetry.ui.selected_character_count", 1)
    current = observation(capabilities=["squad.basic", "identity.stable_handles"])
    assert current.telemetry is not None
    current.telemetry = current.telemetry.model_copy(
        update={
            "ui": UIState(
                selected_character_id="entity-player",
                selected_character_ids=["entity-player"],
            ),
            "squad": [
                CharacterState(id="entity-player", name="Wanderer", selected=True)
            ],
        }
    )

    assert evaluate_condition(exact_one, current).result is ConditionResult.TRUE
    assert (
        evaluate_condition(
            exact_one,
            observation(capabilities=["squad.basic"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )


def test_postcondition_requires_a_later_relevant_revision() -> None:
    condition = field_condition(
        "telemetry.game.paused",
        False,
        required_capabilities=["game.pause"],
    )
    action_revision = revision(8)

    same_revision = observation(sequence=8, paused=False)
    later_revision = observation(sequence=9, paused=False)

    assert (
        evaluate_condition(
            condition,
            same_revision,
            after_revision=action_revision,
        ).result
        is ConditionResult.STALE
    )
    assert (
        evaluate_condition(
            condition,
            later_revision,
            after_revision=action_revision,
        ).result
        is ConditionResult.TRUE
    )


def test_capability_epoch_advance_is_a_later_world_revision() -> None:
    before = revision(8, capability_epoch=1)
    after = before.model_copy(
        update={
            "capability_epoch": 2,
            "observed_at_monotonic": before.observed_at_monotonic + 0.1,
        }
    )

    assert after.is_later_than(before)
    assert not before.is_later_than(after)


@pytest.mark.parametrize(
    "steps",
    [
        [pause_step(on_success="missing")],
        [
            pause_step(on_success="accelerate"),
            speed_step("accelerate").model_copy(update={"on_success": "resume"}),
        ],
        [pause_step(), speed_step("unreachable")],
    ],
)
def test_plan_graph_rejects_invalid_branch_cycle_and_unreachable_steps(
    steps: list[PlanStep],
) -> None:
    with pytest.raises(ValidationError):
        plan_for(revision(1), steps=steps, max_actions=2)


def test_plan_rejects_retry_for_at_most_once_step() -> None:
    with pytest.raises(ValidationError, match="retry_budget"):
        pause_step(retry_budget=1)


def test_plan_policy_rejects_excessive_horizon_budget_and_stale_basis() -> None:
    current = observation(sequence=5)
    plan = plan_for(revision(5))
    macros = MacroRegistry({})

    with pytest.raises(PlanValidationError, match="steps"):
        validate_plan(
            plan,
            current,
            PlanningConfig(max_plan_steps=1),
            macros,
        )

    with pytest.raises(PlanValidationError, match="max_actions"):
        validate_plan(
            plan.model_copy(update={"max_actions": 3}),
            current,
            PlanningConfig(max_actions_per_plan=2),
            macros,
        )

    with pytest.raises(PlanValidationError, match="stale"):
        validate_plan(
            plan_for(revision(4)),
            current,
            PlanningConfig(),
            macros,
        )


def test_plan_patch_carries_optimistic_concurrency_basis() -> None:
    patch = PlanPatch(
        schema_version="1.0",
        plan_id="survival-setup",
        based_on_plan_version=1,
        based_on_revision=revision(7),
        replace_future_steps=[speed_step()],
        rationale="The safe setup still applies, but acceleration remains.",
    )

    assert patch.based_on_plan_version == 1
    assert patch.based_on_revision.telemetry_sequence == 7


def test_plan_patch_names_the_exact_active_step_when_requesting_interruption() -> None:
    patch = PlanPatch(
        schema_version="1.0",
        plan_id="survival-setup",
        based_on_plan_version=1,
        based_on_revision=revision(7),
        interrupt_active_step_id="resume",
        replace_future_steps=[speed_step()],
        rationale="Conditions changed enough to interrupt the exact active step.",
    )

    assert patch.interrupt_active_step_id == "resume"


def test_future_patch_requires_current_basis_and_cannot_restart_protected_steps() -> None:
    current = observation(sequence=7)
    active_plan = plan_for(current.world_revision)
    ledger = PlanBudgetLedger.from_plan(active_plan)
    ledger.reserve(PauseAction(paused=False), MacroRegistry({}))
    ledger.commit()
    patch = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        replace_future_steps=[speed_step("patched-speed")],
        rationale="Use the remaining safe speed step.",
    )

    candidate = validate_future_plan_patch(
        patch,
        active_plan=active_plan,
        planner_observation=current,
        current_observation=current,
        config=PlanningConfig(),
        macros=MacroRegistry({}),
        budget=ledger,
        remaining_run_actions=1,
        protected_step_ids={"resume"},
        require_current_basis=True,
    )

    assert candidate.plan_version == 2
    assert candidate.entry_step_id == "patched-speed"
    assert candidate.max_actions == 1

    with pytest.raises(PlanValidationError, match="stale"):
        validate_future_plan_patch(
            patch,
            active_plan=active_plan,
            planner_observation=current,
            current_observation=observation(sequence=8),
            config=PlanningConfig(),
            macros=MacroRegistry({}),
            budget=ledger,
            remaining_run_actions=1,
            protected_step_ids={"resume"},
            require_current_basis=True,
        )

    protected_patch = patch.model_copy(
        update={"replace_future_steps": [speed_step("resume")]}
    )
    with pytest.raises(PlanValidationError, match="active or completed"):
        validate_future_plan_patch(
            protected_patch,
            active_plan=active_plan,
            planner_observation=current,
            current_observation=current,
            config=PlanningConfig(),
            macros=MacroRegistry({}),
            budget=ledger,
            remaining_run_actions=1,
            protected_step_ids={"resume"},
            require_current_basis=True,
        )


def test_interrupt_patch_requires_exact_opt_in_and_a_pause_handoff() -> None:
    current = observation(sequence=7)
    interruptible_resume = pause_step(on_success="accelerate").model_copy(
        update={
            "interrupt_policy": InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH,
        }
    )
    active_plan = plan_for(
        current.world_revision,
        steps=[interruptible_resume, speed_step()],
        max_actions=3,
    )
    ledger = PlanBudgetLedger.from_plan(active_plan)
    ledger.reserve(active_plan.steps[0].action, MacroRegistry({}))
    ledger.commit()
    planner_observation = current.model_copy(
        update={
            "active_plan": ActivePlanContext(
                plan_id=active_plan.plan_id,
                plan_version=active_plan.plan_version,
                objective=active_plan.objective,
                active_step_id="resume",
                active_step_interrupt_policy=(
                    InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
                ),
                remaining_actions=ledger.remaining_actions,
            )
        }
    )

    wrong_step = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        interrupt_active_step_id="accelerate",
        replace_future_steps=[interruption_pause_step()],
        rationale="This names a future step, not the exact active step.",
    )
    with pytest.raises(PlanValidationError, match="exact active step"):
        validate_future_plan_patch(
            wrong_step,
            active_plan=active_plan,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            macros=MacroRegistry({}),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
            require_current_basis=True,
        )

    no_pause = wrong_step.model_copy(
        update={
            "interrupt_active_step_id": "resume",
            "replace_future_steps": [speed_step("unsafe-replacement")],
        }
    )
    with pytest.raises(PlanValidationError, match="confirmed pause handoff"):
        validate_future_plan_patch(
            no_pause,
            active_plan=active_plan,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            macros=MacroRegistry({}),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
            require_current_basis=True,
        )

    non_interruptible = active_plan.model_copy(
        update={
            "steps": [
                interruptible_resume.model_copy(
                    update={"interrupt_policy": InterruptPolicy.CANCEL_ON_REFLEX}
                ),
                speed_step(),
            ]
        }
    )
    valid = no_pause.model_copy(
        update={"replace_future_steps": [interruption_pause_step()]}
    )
    with pytest.raises(PlanValidationError, match="does not permit"):
        validate_future_plan_patch(
            valid,
            active_plan=non_interruptible,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            macros=MacroRegistry({}),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
            require_current_basis=True,
        )

    candidate = validate_future_plan_patch(
        valid,
        active_plan=active_plan,
        planner_observation=planner_observation,
        current_observation=current,
        config=PlanningConfig(),
        macros=MacroRegistry({}),
        budget=ledger,
        remaining_run_actions=2,
        protected_step_ids={"resume"},
        require_current_basis=True,
    )

    assert candidate.steps == [interruption_pause_step()]


def test_plan_budget_reservations_release_or_commit_transactionally() -> None:
    plan = plan_for(revision(1))
    ledger = PlanBudgetLedger.from_plan(plan)
    macros = MacroRegistry({})

    risk = ledger.reserve(PauseAction(paused=False), macros)
    assert ledger.remaining_actions == 1
    ledger.release(risk)
    assert ledger.remaining_actions == 2
    assert ledger.released_actions == 1

    ledger.reserve(PauseAction(paused=False), macros)
    ledger.commit()
    assert ledger.remaining_actions == 1
    assert ledger.committed_actions == 1


def test_plan_envelope_is_an_openai_compatible_strict_schema() -> None:
    schema = to_strict_json_schema(PlanEnvelope)
    condition_paths = schema["$defs"]["ConditionPath"]["enum"]

    def assert_supported_nodes(value: object) -> None:
        if isinstance(value, dict):
            assert value
            assert "oneOf" not in value
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
            for child in value.values():
                assert_supported_nodes(child)
        elif isinstance(value, list):
            for child in value:
                assert_supported_nodes(child)

    assert schema["type"] == "object"
    assert "telemetry.game.paused" in condition_paths
    assert "target.shop_inventory_owner" in condition_paths
    assert "game.paused" not in condition_paths
    assert "exists" not in schema["$defs"]["ConditionOperator"]["enum"]
    assert_supported_nodes(schema)


def test_builtin_heuristic_emits_a_two_step_continuous_plan() -> None:
    current = observation().model_copy(update={"planning_mode": PlanningMode.CONTINUOUS})

    output = asyncio.run(HeuristicPlanner().decide(current))

    assert isinstance(output, PlanEnvelope)
    assert [step.step_id for step in output.steps] == ["resume", "accelerate"]
    assert output.based_on_revision.same_snapshot_as(current.world_revision)


def test_scripted_and_subprocess_adapters_parse_continuous_plan(
    tmp_path: Path,
) -> None:
    current = observation().model_copy(update={"planning_mode": PlanningMode.CONTINUOUS})
    plan = plan_for(current.world_revision)
    script_path = tmp_path / "plan.jsonl"
    script_path.write_text(plan.model_dump_json() + "\n", encoding="utf-8")

    scripted_output = asyncio.run(ScriptedPlanner(script_path).decide(current))
    subprocess_output = asyncio.run(
        SubprocessPlanner(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1])",
                plan.model_dump_json(),
            ]
        ).decide(current)
    )

    assert isinstance(scripted_output, PlanEnvelope)
    assert isinstance(subprocess_output, PlanEnvelope)


def test_subprocess_adapter_parses_a_patch_for_the_exact_active_plan() -> None:
    current = observation().model_copy(
        update={
            "planning_mode": PlanningMode.CONTINUOUS,
            "active_plan": ActivePlanContext(
                plan_id="survival-setup",
                plan_version=1,
                objective="Keep the current movement responsive.",
                active_step_id="resume",
                remaining_actions=1,
            ),
        }
    )
    patch = PlanPatch(
        schema_version="1.0",
        plan_id="survival-setup",
        based_on_plan_version=1,
        based_on_revision=current.world_revision,
        replace_future_steps=[speed_step()],
        rationale="Keep the active step and revise only what follows it.",
    )

    output = asyncio.run(
        SubprocessPlanner(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1])",
                patch.model_dump_json(),
            ]
        ).decide(current)
    )

    assert output == patch
