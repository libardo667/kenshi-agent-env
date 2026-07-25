"""The generic interaction policy: properties, not a recipe.

These tests deliberately assert that several *different* plans are acceptable.
A policy that only admits one blessed sequence would pass a "does the Barman
chain work" test and still have failed this milestone.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kenshi_agent.dialogue_interaction import (
    dialogue_interaction_policy_errors,
    dialogue_interaction_rebase_errors,
)
from kenshi_agent.models import (
    Action,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    ClickAction,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ConditionResult,
    ControlMode,
    Disposition,
    GameState,
    IdempotencyPolicy,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    SkillAction,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
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
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                ),
            ]
        )
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []

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
            ]
        )
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []

    def test_a_single_action_plan_is_acceptable(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=CIVILIAN_ID))],
            pointer=0,
        )
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []

    def test_the_same_approach_action_accepts_a_non_vendor(self) -> None:
        composed = plan(
            [
                step(
                    "approach",
                    ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
                    success=[dialogue_open_with(CIVILIAN_ID)],
                )
            ],
            pointer=0,
        )
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []


class TestGenericPolicyRejections:
    def test_a_raw_primitive_cannot_even_be_expressed_as_a_plan_step(self) -> None:
        """A raw coordinate carries no evidence of what it would activate.

        The policy used to be the only thing refusing these, which meant the
        response schema still offered the planner five primitives it was never
        allowed to pick. `PlanStep.action` is now the narrower `PlannerAction`,
        so a primitive fails at parse time and never reaches the policy.
        """
        from kenshi_agent.models import HotkeyAction, KeyAction

        for action in (
            ClickAction(x=0.5, y=0.5),
            KeyAction(key="space"),
            HotkeyAction(keys=["ctrl", "s"]),
        ):
            with pytest.raises(ValidationError):
                step("raw", action, success=[screen_is("trade")])

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
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("raw controller primitive" in error for error in errors)

    def test_legacy_skill_action_has_no_contract(self) -> None:
        composed = plan(
            [step("legacy", SkillAction(name="choose_show_goods"), success=[screen_is("trade")])],
            native=0,
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("no authoritative action contract" in error for error in errors)

    def test_unbound_control_label_is_rejected(self) -> None:
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
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("does not bind to current state" in error for error in errors)

    def test_ambiguous_control_label_is_rejected(self) -> None:
        duplicated = [
            VisibleUIControl(label="Trade", role="button", bounds=bounds(0.5)),
            VisibleUIControl(label="Trade", role="button", bounds=bounds(0.7)),
        ]
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(exact_label="Trade", role="button"),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
        )
        errors = dialogue_interaction_policy_errors(composed, observation(controls=duplicated))
        assert any("ambiguous" in error for error in errors)

    def test_unknown_target_is_rejected(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id="entity-ghost"))],
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("does not bind to current state" in error for error in errors)

    def test_native_action_is_rejected_in_interface_only(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=VENDOR_ID))],
            pointer=0,
            control_mode=ControlMode.INTERFACE_ONLY,
        )
        errors = dialogue_interaction_policy_errors(
            composed,
            observation(controls=TRADE_CONTROLS, control_mode=ControlMode.INTERFACE_ONLY),
        )
        assert any("not permitted in control mode" in error for error in errors)

    def test_missing_capability_is_rejected(self) -> None:
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed,
            observation(controls=TRADE_CONTROLS, capabilities=["game.time", "ui.dialogue"]),
        )
        assert any("unavailable capabilities" in error for error in errors)

    def test_underdeclared_native_budget_is_rejected(self) -> None:
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=VENDOR_ID))],
            native=0,
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("native-assisted cost" in error for error in errors)

    def test_underdeclared_pointer_budget_is_rejected(self) -> None:
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("pointer cost" in error for error in errors)

    def test_retrying_an_at_most_once_action_is_rejected(self) -> None:
        composed = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
                    retry_budget=1,
                )
            ],
            native=0,
            pointer=2,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("retries an at-most-once action" in error for error in errors)

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
                    "approach",
                    ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    success=[control_mode_only],
                )
            ],
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("no causal success condition" in error for error in errors)

    def test_stale_telemetry_is_rejected(self) -> None:
        state = observation(controls=TRADE_CONTROLS)
        stale = state.model_copy(update={"telemetry_stale": True}, deep=True)
        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id=VENDOR_ID))],
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(composed, stale)
        assert any("fresh telemetry" in error for error in errors)


# ---------------------------------------------------------------------------
# Rebasing a plan that aged during a slow strategic call.
#
# The sequence number always moves during a ~25s hosted call, so the question
# that decides safety is whether the plan's references still bind — not whether
# the counter changed.
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
        assert dialogue_interaction_rebase_errors(chain_plan(), planner_view, current) == []

    def test_an_unchanged_revision_is_not_a_rebase(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, planner_view)
        assert any("causally later" in error for error in errors)

    def test_a_target_that_left_the_valid_set_refuses(self) -> None:
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
            update={"telemetry": telemetry.model_copy(update={"nearby_entities": entities})},
            deep=True,
        )
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, current)
        assert any("changed while the planner was thinking" in error for error in errors)

    def test_a_control_that_became_ambiguous_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        telemetry = current.telemetry
        assert telemetry is not None
        duplicated = [*TRADE_CONTROLS, VisibleUIControl(
            label="Show me your goods.", role="button", bounds=bounds(0.8)
        )]
        current = current.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"visible_controls": duplicated})}
                )
            },
            deep=True,
        )
        control_first = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
        )
        errors = dialogue_interaction_rebase_errors(control_first, planner_view, current)
        assert any("ambiguous" in error for error in errors)

    def test_a_control_that_disappeared_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        telemetry = current.telemetry
        assert telemetry is not None
        current = current.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"visible_controls": []})}
                )
            },
            deep=True,
        )
        control_first = plan(
            [
                step(
                    "activate",
                    ActivateVisibleControlAction(
                        exact_label="Show me your goods.", role="button"
                    ),
                    success=[screen_is("trade")],
                )
            ],
            native=0,
        )
        errors = dialogue_interaction_rebase_errors(control_first, planner_view, current)
        assert any("changed while the planner was thinking" in error for error in errors)

    def test_withdrawn_capability_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view)
        telemetry = current.telemetry
        assert telemetry is not None
        reduced = [c for c in telemetry.capabilities if c != "ui.visible_controls"]
        current = current.model_copy(
            update={"telemetry": telemetry.model_copy(update={"capabilities": reduced})},
            deep=True,
        )
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, current)
        assert any("withdrawn" in error for error in errors)

    def test_human_input_during_planning_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view).model_copy(
            update={"events": ["human_input_detected"]}, deep=True
        )
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, current)
        assert any("input authority was withdrawn" in error for error in errors)

    def test_control_mode_change_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view).model_copy(
            update={"control_mode": ControlMode.INTERFACE_ONLY}, deep=True
        )
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, current)
        assert any("control mode changed" in error for error in errors)

    def test_stale_current_telemetry_refuses(self) -> None:
        planner_view = observation(controls=TRADE_CONTROLS)
        current = later(planner_view).model_copy(update={"telemetry_stale": True}, deep=True)
        errors = dialogue_interaction_rebase_errors(chain_plan(), planner_view, current)
        assert any("stale" in error for error in errors)

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
        errors = dialogue_interaction_rebase_errors(forged, planner_view, current)
        assert any("immutable planner snapshot" in error for error in errors)


class TestRunControlActions:
    """A plan may include run control; it binds to nothing and ends the plan."""

    def test_a_plan_ending_in_stop_is_accepted(self) -> None:
        from kenshi_agent.models import StopAction

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
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []

    def test_a_stop_only_plan_needs_no_causal_success_condition(self) -> None:
        from kenshi_agent.models import ConditionPath, StopAction

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
        assert dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        ) == []

    def test_run_control_steps_do_not_block_a_rebase(self) -> None:
        from kenshi_agent.models import StopAction

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
        assert dialogue_interaction_rebase_errors(
            composed, planner_view, later(planner_view)
        ) == []


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
            pointer=0,
            native=0,
        )

    def test_dismissing_the_open_screen_is_accepted(self) -> None:
        from kenshi_agent.models import DismissScreenAction

        state = observation(controls=TRADE_CONTROLS)
        telemetry = state.telemetry
        assert telemetry is not None
        trading = state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"active_screen": "trade"})}
                )
            },
            deep=True,
        )
        composed = self._plan_with(DismissScreenAction(expected_screen="trade"))
        assert dialogue_interaction_policy_errors(composed, trading) == []

    def test_dismissing_a_screen_that_is_not_open_fails_closed(self) -> None:
        from kenshi_agent.models import DismissScreenAction

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
        composed = self._plan_with(DismissScreenAction(expected_screen="trade"))
        errors = dialogue_interaction_policy_errors(composed, in_world)
        assert any("does not bind to current state" in error for error in errors)

    def test_dismiss_costs_no_pointer_or_native_budget(self) -> None:
        from kenshi_agent.action_contracts import DISMISS_SCREEN_CONTRACT

        assert DISMISS_SCREEN_CONTRACT.risk.as_tuple() == (0, 0, 0)
        # It is available without any capability, in either control mode.
        assert not DISMISS_SCREEN_CONTRACT.missing_capabilities(set())
        assert DISMISS_SCREEN_CONTRACT.allows_control_mode(ControlMode.INTERFACE_ONLY)


class TestCapabilityAliases:
    """The generic capability name must work against a legacy-named plug-in."""

    def test_either_approach_capability_name_satisfies_the_other(self) -> None:
        from kenshi_agent.planning import capability_satisfied

        legacy_only = {"control.approach_vendor"}
        generic_only = {"control.approach_dialogue_target"}
        assert capability_satisfied("control.approach_dialogue_target", legacy_only)
        assert capability_satisfied("control.approach_vendor", generic_only)
        assert not capability_satisfied("control.approach_vendor", set())

    def test_an_unrelated_capability_is_not_aliased(self) -> None:
        from kenshi_agent.planning import capability_satisfied

        assert not capability_satisfied("ui.tooltip", {"control.approach_vendor"})

    def test_a_plan_requiring_the_generic_name_runs_on_a_legacy_plugin(self) -> None:
        """The exact failure that stopped run p8-longform-05."""

        from kenshi_agent.planning import evaluate_condition

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

    Requiring every step to bind against the *current* observation quietly made
    real multi-step plans impossible — "dismiss the dialogue" cannot bind before
    an approach has opened one. Only the entry step must bind now; the rest are
    bound when reached and again inside the input lease.
    """

    def _approach_then_dismiss(self) -> PlanEnvelope:
        from kenshi_agent.models import DismissScreenAction

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
                    DismissScreenAction(expected_screen="dialogue"),
                    success=[screen_is("world")],
                ),
            ],
            pointer=0,
        )

    def test_a_plan_whose_later_step_needs_future_state_is_accepted(self) -> None:
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
        assert dialogue_interaction_policy_errors(self._approach_then_dismiss(), in_world) == []

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
        assert dialogue_interaction_rebase_errors(
            self._approach_then_dismiss(), in_world, later(in_world)
        ) == []

    def test_an_unbindable_entry_step_is_still_refused(self) -> None:
        """Relaxing future steps must not relax the step about to run."""

        composed = plan(
            [step("approach", ApproachDialogueTargetAction(target_id="entity-ghost"))],
            pointer=0,
        )
        errors = dialogue_interaction_policy_errors(
            composed, observation(controls=TRADE_CONTROLS)
        )
        assert any("does not bind to current state" in error for error in errors)


class TestIdempotencyClaims:
    """A step may be more cautious than its contract, never less."""

    def _plan_with(self, idem: IdempotencyPolicy, retries: int = 0) -> PlanEnvelope:
        from kenshi_agent.models import ScrollScreenAction

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

    def _trade_state(self) -> Observation:
        state = observation(
            controls=[
                VisibleUIControl(
                    label="item_3", role="item", window="BARMAN", bounds=bounds(0.5)
                ),
            ],
            capabilities=[*CAPABILITIES, "ui.tooltip"],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"active_screen": "trade"})}
                )
            },
            deep=True,
        )

    def test_declaring_at_most_once_for_a_retryable_action_is_accepted(self) -> None:
        """The exact loop that stalled an open-ended live run."""

        errors = dialogue_interaction_policy_errors(
            self._plan_with(IdempotencyPolicy.AT_MOST_ONCE), self._trade_state()
        )
        assert errors == [], errors

    def test_the_contract_idempotency_is_also_accepted(self) -> None:
        errors = dialogue_interaction_policy_errors(
            self._plan_with(IdempotencyPolicy.SAFE_TO_RETRY), self._trade_state()
        )
        assert errors == [], errors

    def test_claiming_retryable_for_an_at_most_once_action_is_refused(self) -> None:
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
        errors = dialogue_interaction_policy_errors(composed, observation(controls=TRADE_CONTROLS))
        assert any("may not be retried" in error for error in errors)
