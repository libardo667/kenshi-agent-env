"""The mandatory live plan policy: properties, not a recipe.

These tests deliberately assert that several *different* plans are acceptable.
A policy that only admits one blessed sequence would pass a "does the Barman
chain work" test and still have failed this milestone.
"""

from __future__ import annotations

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    ClickAction,
    ControlMode,
    GameBinding,
    IdempotencyPolicy,
    SkillAction,
    UseGameBindingAction,
)
from kenshi_agent.core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ConditionResult,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)
from kenshi_agent.core.telemetry import (
    Disposition,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.live_plan_policy import (
    live_plan_policy_errors,
    live_plan_rebase_errors,
)

VENDOR_ID = "entity-barman"
CIVILIAN_ID = "entity-wanderer"
CAPABILITIES = [
    "control.approach_vendor",
    "game.time",
    "identity.stable_handles",
    "nearby.characters",
    "nearby.roles",
    "ui.dialogue",
    "ui.dialogue.target",
    "ui.visible_controls",
]

REVISION = WorldStateRevision(telemetry_sequence=42, capability_epoch=3)


def bounds(y: float) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(min_x=0.1, max_x=0.4, min_y=y, max_y=y + 0.05)


def person(entity_id: str, name: str, *, vendor: bool) -> NearbyEntity:
    return NearbyEntity(
        id=entity_id,
        name=name,
        is_animal=False,
        has_dialogue=True,
        has_vendor_list=vendor,
        is_squad_leader=vendor,
        disposition=Disposition.NEUTRAL,
        distance=25.0,
        conscious=True,
    )


def observation(
    *,
    controls: list[VisibleUIControl] | None = None,
    capabilities: list[str] | None = None,
    control_mode: ControlMode = ControlMode.NATIVE_ASSISTED,
) -> Observation:
    return Observation(
        run_id="policy-test",
        step_index=1,
        mode="live",
        control_mode=control_mode,
        world_revision=REVISION,
        telemetry=TelemetrySnapshot(
            sequence=42,
            identity_session_id="session-policy-test",
            capabilities=capabilities if capabilities is not None else CAPABILITIES,
            game=GameState(loaded=True, paused=True, elapsed_minutes=120.0),
            ui=UIState(visible_controls=controls),
            nearby_entities=[
                person(VENDOR_ID, "Barman", vendor=True),
                person(CIVILIAN_ID, "Nomad Wanderer", vendor=False),
            ],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def freshness() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def dialogue_open_with(target_id: str) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath.TELEMETRY_UI_DIALOGUE_TARGET_ID,
        operator=ConditionOperator.EQUALS,
        expected=target_id,
        max_age_seconds=3.0,
    )


def screen_is(name: str) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath.TELEMETRY_UI_ACTIVE_SCREEN,
        operator=ConditionOperator.EQUALS,
        expected=name,
        max_age_seconds=3.0,
    )


def step(
    step_id: str,
    action: Action,
    *,
    success: list[Condition] | None = None,
    on_success: str | None = None,
    idempotency: IdempotencyPolicy = IdempotencyPolicy.AT_MOST_ONCE,
    retry_budget: int = 0,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=action,
        preconditions=[freshness()],
        success_conditions=success or [dialogue_open_with(VENDOR_ID)],
        timeout_seconds=30.0,
        idempotency=idempotency,
        retry_budget=retry_budget,
        on_success=on_success,
    )


def plan(
    steps: list[PlanStep],
    *,
    entry: str | None = None,
    pointer: int = 1,
    native: int = 1,
    control_mode: ControlMode = ControlMode.NATIVE_ASSISTED,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="generic-interaction",
        objective="Open dialogue with a valid current target and activate one control.",
        control_mode=control_mode,
        based_on_revision=REVISION,
        assumptions=[freshness()],
        steps=steps,
        entry_step_id=entry or steps[0].step_id,
        max_actions=len(steps) + 1,
        max_wall_seconds=60.0,
        max_game_seconds=120.0,
        risk_budget=RiskBudget(
            max_pointer_actions=pointer,
            max_purchase_actions=0,
            max_native_assisted_actions=native,
        ),
    )


TRADE_CONTROLS = [
    VisibleUIControl(label="Show me your goods.", role="button", bounds=bounds(0.5)),
    VisibleUIControl(label="Goodbye.", role="button", bounds=bounds(0.6)),
]


class TestGenericComposition:
    def test_runtime_owned_completion_needs_no_model_authored_duplicate(self) -> None:
        composed = plan(
            [
                PlanStep(
                    step_id="open-inventory",
                    action=UseGameBindingAction(
                        binding=GameBinding.TOGGLE_INVENTORY,
                        expected_effect="open the selected character inventory",
                    ),
                    preconditions=[freshness()],
                    success_conditions=[],
                    timeout_seconds=30.0,
                )
            ],
            pointer=0,
            native=0,
        )
        assert not live_plan_policy_errors(composed)

    def test_approach_then_activate_is_accepted(self) -> None:
        composed = plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    on_success="activate",
                ),
                step(
                    "activate",
                    ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
                    success=[screen_is("trade")],
                ),
            ],
            pointer=2,
        )
        assert live_plan_policy_errors(composed) == []

    def test_a_different_order_is_equally_acceptable(self) -> None:
        """The policy prescribes no sequence."""

        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(exact_label="Goodbye.", role="button"),
                    success=[screen_is("world")],
                    on_success="approach",
                ),
                step("approach", ApproachDialogueTargetAction(target_id=CIVILIAN_ID)),
            ],
            pointer=2,
        )
        assert live_plan_policy_errors(composed) == []

    def test_a_single_action_plan_is_acceptable(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=CIVILIAN_ID))],
            pointer=1,
        )
        assert live_plan_policy_errors(composed) == []

    def test_the_same_approach_action_accepts_a_non_vendor(self) -> None:
        composed = plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
                    success=[dialogue_open_with(CIVILIAN_ID)],
                )
            ],
            pointer=1,
        )
        assert live_plan_policy_errors(composed) == []


class TestGenericPolicyRejections:
    def test_a_raw_primitive_is_absent_from_the_hosted_selection_schema(self) -> None:
        """A raw coordinate carries no evidence of what it would activate.

        PlanStep is now runtime-private and can carry adapter operations. The
        hosted model sees only AffordanceSelection, so primitives have no
        representable branch at all.
        """
        from kenshi_agent.planners.plan_proposal import PlanProposal

        schema = PlanProposal.model_json_schema()
        blob = str(schema)
        for primitive in ("ClickAction", "KeyAction", "HotkeyAction"):
            assert primitive not in blob

    def test_a_raw_primitive_smuggled_past_the_schema_is_still_refused(self) -> None:
        """Validation is the first defence, not the only one."""
        smuggled = PlanStep.model_construct(
            step_id="click",
            action=ClickAction(x=0.5, y=0.5),
            preconditions=[freshness()],
            success_conditions=[screen_is("trade")],
            timeout_seconds=30.0,
            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            retry_budget=0,
        )
        composed = plan([smuggled], native=0)
        errors = live_plan_policy_errors(composed)
        assert any("raw controller primitive" in error for error in errors)

    def test_configured_skill_uses_the_operation_definition_path(self) -> None:
        composed = plan(
            [step("legacy", SkillAction(name="choose_show_goods"), success=[screen_is("trade")])],
            native=0,
            pointer=0,
        )
        errors = live_plan_policy_errors(composed)
        assert errors == []

    def test_current_operation_eligibility_is_not_plan_structure(self) -> None:
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(exact_label="Buy everything", role="button"),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
        )
        errors = live_plan_policy_errors(composed)
        assert errors == []

    def test_underdeclared_native_budget_is_rejected(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=VENDOR_ID))],
            native=0,
            pointer=0,
        )
        errors = live_plan_policy_errors(composed)
        assert any("native-assisted cost" in error for error in errors)

    def test_underdeclared_pointer_budget_is_rejected(self) -> None:
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
            pointer=0,
        )
        errors = live_plan_policy_errors(composed)
        assert any("pointer cost" in error for error in errors)

    def test_non_causal_success_condition_is_rejected(self) -> None:
        control_mode_only = Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.CONTROL_MODE,
            operator=ConditionOperator.EQUALS,
            expected="native_assisted",
            max_age_seconds=3.0,
        )
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.",
                        role="button",
                    ),
                    success=[control_mode_only],
                )
            ],
            pointer=1,
        )
        errors = live_plan_policy_errors(composed)
        assert any("none witness a causal world change" in error for error in errors)


# ---------------------------------------------------------------------------
# Rebasing a plan that aged during a slow strategic call.
#
# The sequence number always moves during a ~25s hosted call, so the question
# that belongs here is whether the plan's own basis, assumptions, and input
# ownership still hold — not whether current operation eligibility changed.
# ---------------------------------------------------------------------------


def later(observation: Observation, *, sequence: int = 60) -> Observation:
    """The same world, several telemetry ticks later."""

    telemetry = observation.telemetry
    assert telemetry is not None
    return observation.model_copy(
        update={
            "world_revision": WorldStateRevision(
                telemetry_sequence=sequence,
                capability_epoch=REVISION.capability_epoch,
                observed_at_monotonic=observation.world_revision.observed_at_monotonic + 25.0,
            ),
            "telemetry": telemetry.model_copy(update={"sequence": sequence}, deep=True),
        },
        deep=True,
    )


def chain_plan() -> PlanEnvelope:
    return plan(
        [
            step(
                "approach",
                ApproachDialogueTargetAction(target_id=VENDOR_ID),
                on_success="activate",
            ),
            step(
                "activate",
                ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
                success=[screen_is("trade")],
            ),
        ]
    )


class TestRebaseAcrossPlannerLatency:
    def test_a_purely_older_sequence_rebases(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        assert live_plan_rebase_errors(chain_plan(), planner_view, current) == []

    def test_an_unchanged_revision_is_not_a_rebase(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        errors = live_plan_rebase_errors(chain_plan(), planner_view, planner_view)
        assert any("causally later" in error for error in errors)

    def test_operation_eligibility_changes_do_not_belong_to_rebase_policy(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        telemetry = current.telemetry
        assert telemetry is not None
        # The vendor turned hostile while the planner was thinking.
        entities = [
            entity.model_copy(update={"disposition": Disposition.HOSTILE}, deep=True)
            if entity.id == VENDOR_ID
            else entity
            for entity in telemetry.nearby_entities
        ]
        current = current.model_copy(
            update={
                "control_mode": ControlMode.INTERFACE_ONLY,
                "telemetry": telemetry.model_copy(
                    update={"nearby_entities": entities, "capabilities": []}
                ),
            },
            deep=True,
        )
        assert live_plan_rebase_errors(chain_plan(), planner_view, current) == []

    def test_human_input_during_planning_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view).model_copy(
            update={"events": ["human_input_detected"]}, deep=True
        )
        errors = live_plan_rebase_errors(chain_plan(), planner_view, current)
        assert any("input authority was withdrawn" in error for error in errors)

    def test_a_plan_whose_basis_is_not_its_planner_snapshot_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        forged = chain_plan().model_copy(
            update={
                "based_on_revision": WorldStateRevision(
                    telemetry_sequence=999, capability_epoch=REVISION.capability_epoch
                )
            },
            deep=True,
        )
        errors = live_plan_rebase_errors(forged, planner_view, current)
        assert any("immutable planner snapshot" in error for error in errors)


class TestRunControlActions:
    """A plan may include run control; it binds to nothing and ends the plan."""

    def test_a_plan_ending_in_stop_is_accepted(self) -> None:
        from kenshi_agent.core.operation import StopAction

        composed = plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    on_success="done",
                ),
                step(
                    "done",
                    StopAction(reason="Await the already active approach."),
                    success=[screen_is("world")],
                ),
            ],
            pointer=1,
        )
        assert live_plan_policy_errors(composed) == []

    def test_noncausal_authored_terminal_is_rejected_even_for_stop(self) -> None:
        from kenshi_agent.core.operation import StopAction
        from kenshi_agent.core.planning import ConditionPath

        control_mode_only = Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.CONTROL_MODE,
            operator=ConditionOperator.EQUALS,
            expected="native_assisted",
            max_age_seconds=3.0,
        )
        composed = plan(
            [step("halt", StopAction(reason="Nothing safe to do."), success=[control_mode_only])],
            pointer=0,
            native=0,
        )
        errors = live_plan_policy_errors(composed)
        assert any("none witness a causal world change" in error for error in errors)

    def test_run_control_steps_do_not_block_a_rebase(self) -> None:
        from kenshi_agent.core.operation import StopAction

        composed = plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    on_success="done",
                ),
                step(
                    "done",
                    StopAction(reason="Await the already active approach."),
                    success=[screen_is("world")],
                ),
            ],
            pointer=0,
        )
        planner_view = observation(controls=TRADE_CONTROLS)
        assert live_plan_rebase_errors(composed, planner_view, later(planner_view)) == []


class TestDismissScreen:
    """Exiting an interface is a first-class step, not a raw Escape key."""

    def _plan_with(self, action: Action, *, screen: str | None = None) -> PlanEnvelope:
        return plan(
            [
                step(
                    "leave",
                    action,
                    success=[screen_is(screen or "world")],
                )
            ],
            pointer=1,
            native=0,
        )

    def test_dismissing_the_open_screen_is_accepted(self) -> None:
        from kenshi_agent.core.operation import DismissScreenAction

        composed = self._plan_with(DismissScreenAction(expected_screen="trade"))
        assert live_plan_policy_errors(composed) == []

    def test_dismissing_a_screen_that_is_not_open_is_left_to_authority(self) -> None:
        from kenshi_agent.core.operation import DismissScreenAction

        composed = self._plan_with(DismissScreenAction(expected_screen="trade"))
        assert live_plan_policy_errors(composed) == []

    def test_dismiss_costs_one_pointer_and_no_native_budget(self) -> None:
        from kenshi_agent.operation_definitions import DISMISS_SCREEN_DEFINITION

        assert DISMISS_SCREEN_DEFINITION.risk.as_tuple() == (1, 0, 0)
        # It is available without any capability, in either control mode.
        assert not DISMISS_SCREEN_DEFINITION.missing_capabilities(set())
        assert DISMISS_SCREEN_DEFINITION.allows_control_mode(ControlMode.INTERFACE_ONLY)


class TestCapabilityAliases:
    """The generic capability name must work against a legacy-named plug-in."""

    def test_either_approach_capability_name_satisfies_the_other(self) -> None:
        from kenshi_agent.condition_evaluation import capability_satisfied

        legacy_only = {"control.approach_vendor"}
        generic_only = {"control.approach_dialogue_target"}
        assert capability_satisfied("control.approach_dialogue_target", legacy_only)
        assert capability_satisfied("control.approach_vendor", generic_only)
        assert not capability_satisfied("control.approach_vendor", set())

    def test_an_unrelated_capability_is_not_aliased(self) -> None:
        from kenshi_agent.condition_evaluation import capability_satisfied

        assert not capability_satisfied("ui.tooltip", {"control.approach_vendor"})

    def test_a_plan_requiring_the_generic_name_runs_on_a_legacy_plugin(self) -> None:
        """The exact failure that stopped run p8-longform-05."""

        from kenshi_agent.condition_evaluation import evaluate_condition

        generic = Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
            required_capabilities=["control.approach_dialogue_target"],
        )
        # Telemetry advertises only the legacy vendor-era name.
        state = observation(controls=TRADE_CONTROLS, capabilities=list(CAPABILITIES))
        assert "control.approach_vendor" in CAPABILITIES
        assert "control.approach_dialogue_target" not in CAPABILITIES
        result = evaluate_condition(generic, state)
        assert result.result is ConditionResult.TRUE, result.reason


class TestFutureStepsMayReferenceFutureState:
    """The point of composing: later steps describe state that does not exist yet.

    Plan structure never binds operations against current state. The operation
    authority binds each step when scheduled and again inside the input lease.
    """

    def _approach_then_reply(self) -> PlanEnvelope:
        from kenshi_agent.core.operation import ActivateVisibleControlAction

        return plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    on_success="leave",
                ),
                step(
                    "leave",
                    # Nothing is open yet; this refers to what the approach creates.
                    ActivateVisibleControlAction(
                        exact_label="Goodbye.",
                        role="button",
                    ),
                    success=[screen_is("world")],
                ),
            ],
            pointer=2,
        )

    def test_a_plan_whose_later_step_needs_future_state_is_accepted(self) -> None:
        assert live_plan_policy_errors(self._approach_then_reply()) == []

    def test_the_same_plan_rebases_across_planner_latency(self) -> None:
        state = observation(controls=TRADE_CONTROLS)
        telemetry = state.telemetry
        assert telemetry is not None
        in_world = state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"active_screen": "world"})}
                )
            },
            deep=True,
        )
        assert live_plan_rebase_errors(self._approach_then_reply(), in_world, later(in_world)) == []

    def test_entry_step_binding_is_also_left_to_operation_authority(self) -> None:

        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id="entity-ghost"))],
            pointer=0,
        )
        assert live_plan_policy_errors(composed) == []


class TestIdempotencyClaims:
    """Plan policy judges the step's retry declaration, not operation policy."""

    def _plan_with(self, idem: IdempotencyPolicy, retries: int = 0) -> PlanEnvelope:
        from kenshi_agent.core.operation import ScrollScreenAction

        return plan(
            [
                step(
                    "scroll",
                    ScrollScreenAction(window="BARMAN", notches=-3),
                    success=[screen_is("trade")],
                    idempotency=idem,
                    retry_budget=retries,
                )
            ],
            pointer=0,
            native=0,
        )

    def test_declaring_at_most_once_for_a_retryable_action_is_accepted(self) -> None:
        """The exact loop that stalled an open-ended live run."""

        errors = live_plan_policy_errors(self._plan_with(IdempotencyPolicy.AT_MOST_ONCE))
        assert errors == [], errors

    def test_the_contract_idempotency_is_also_accepted(self) -> None:
        errors = live_plan_policy_errors(self._plan_with(IdempotencyPolicy.SAFE_TO_RETRY))
        assert errors == [], errors

    def test_retryable_declaration_without_a_retry_is_structurally_valid(self) -> None:
        composed = plan(
            [
                step(
                    "buy",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
                )
            ],
            pointer=0,
        )
        assert live_plan_policy_errors(composed) == []


class TestDerivedRiskBudget:
    """The plan's steps are its declaration of what it will spend."""

    def _buying_plan(self, declared: int) -> PlanEnvelope:
        from kenshi_agent.core.operation import PurchaseItemAction
        from kenshi_agent.core.planning import RiskBudget

        composed = plan(
            [
                step(
                    "buy",
                    PurchaseItemAction(
                        cell_label="Greenfruit",
                        item_name="Greenfruit",
                        expected_price=22,
                        window="BARMAN",
                        seller_id=VENDOR_ID,
                    ),
                    success=[screen_is("trade")],
                )
            ],
            pointer=1,
            native=0,
        )
        return composed.model_copy(
            update={
                "risk_budget": RiskBudget(
                    max_pointer_actions=1,
                    max_purchase_actions=declared,
                    max_native_assisted_actions=0,
                )
            }
        )

    def test_a_plan_that_buys_no_longer_has_to_also_say_it_buys(self) -> None:
        """Four plans in one live run died for exactly this omission."""
        from kenshi_agent.live_plan_policy import with_covering_risk_budget

        covered = with_covering_risk_budget(self._buying_plan(declared=0))
        assert covered.risk_budget.max_purchase_actions == 1

    def test_headroom_the_planner_asked_for_is_left_alone(self) -> None:
        """A higher budget states intent across the patches that may follow."""
        from kenshi_agent.live_plan_policy import with_covering_risk_budget

        covered = with_covering_risk_budget(self._buying_plan(declared=5))
        assert covered.risk_budget.max_purchase_actions == 5

    def test_deriving_the_budget_does_not_reintroduce_binding_policy(self) -> None:
        from kenshi_agent.live_plan_policy import with_covering_risk_budget

        covered = with_covering_risk_budget(self._buying_plan(declared=0))
        errors = live_plan_policy_errors(covered)
        assert not any("purchase budget" in error for error in errors)
        assert errors == []


def test_capability_presence_can_never_count_as_causal_effect_proof() -> None:
    """A capability says a fact is observable, not that the intended effect occurred."""
    from kenshi_agent.core.planning import (
        Condition,
        ConditionKind,
        ConditionOperator,
    )
    from kenshi_agent.live_plan_policy import _is_causal_condition

    condition = Condition(
        kind=ConditionKind.FIELD,
        path="camera.position",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
    )
    assert condition.kind is ConditionKind.CAPABILITY
    assert not _is_causal_condition(condition.kind, condition.path)
    # Run bookkeeping still is not evidence the world moved.
    assert not _is_causal_condition(ConditionKind.FIELD, "control_mode")


def test_plan_structure_does_not_decide_operation_semantics() -> None:

    speed_effect = Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath.TELEMETRY_GAME_SPEED_MULTIPLIER,
        operator=ConditionOperator.EQUALS,
        expected=3.0,
        max_age_seconds=3.0,
        required_capabilities=["game.speed"],
    )
    composed = plan(
        [
            step(
                "accelerate",
                UseGameBindingAction(
                    binding=GameBinding.SPEED_3,
                    expected_effect="set the third speed gear",
                ),
                success=[speed_effect],
            )
        ],
        pointer=0,
        native=0,
    )

    errors = live_plan_policy_errors(composed)

    assert errors == []
