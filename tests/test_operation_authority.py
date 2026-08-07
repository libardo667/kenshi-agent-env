"""One bound-operation identity and one fresh authorization path."""

from __future__ import annotations

from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY
from kenshi_agent.config import SafetyConfig
from kenshi_agent.core.authority import AuthorizationCode
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ApproachDialogueTargetAction,
    ControlMode,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_authority import OperationAuthority
from kenshi_agent.safety import OperationPolicy


def _bounds(x: float) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=x,
        max_x=x + 0.1,
        min_y=0.4,
        max_y=0.5,
    )


def _observation(
    *,
    sequence: int = 10,
    controls: list[VisibleUIControl] | None = None,
) -> Observation:
    return Observation(
        run_id="operation-authority-test",
        step_index=1,
        mode="live",
        control_mode=ControlMode.INTERFACE_ONLY,
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            identity_session_id="authority-session",
            capabilities=["ui.visible_controls"],
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                active_screen="trade",
                modal_open=False,
                dialogue_open=False,
                visible_controls=controls if controls is not None else [],
            ),
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def _control(x: float = 0.2) -> VisibleUIControl:
    return VisibleUIControl(label="Goodbye.", role="button", bounds=_bounds(x))


def _policy(*, allow: bool = True) -> OperationPolicy:
    return OperationPolicy(
        SafetyConfig(
            allow_action_kinds=["activate_visible_control"] if allow else [],
            max_wait_seconds=3.0,
            max_actions_per_minute=100,
        ),
        control_mode=ControlMode.INTERFACE_ONLY,
    )





def test_capability_and_selection_refusals_remain_distinct() -> None:
    actor = CharacterState(id="actor-1", name="Bark", selected=True)
    target = NearbyEntity(
        id="target-1",
        name="Wanderer",
        is_animal=False,
        has_dialogue=True,
        conscious=True,
        disposition=Disposition.NEUTRAL,
    )
    capabilities = [
        "control.approach_dialogue_target",
        "identity.stable_handles",
        "nearby.characters",
        "nearby.roles",
    ]
    state = _observation(controls=[]).model_copy(
        update={
            "control_mode": ControlMode.NATIVE_ASSISTED,
            "telemetry": _observation(controls=[]).telemetry.model_copy(
                update={
                    "capabilities": capabilities,
                    "squad": [actor],
                    "nearby_entities": [target],
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                        selected_character_id=actor.id,
                        selected_character_ids=[actor.id],
                    ),
                }
            ),
        }
    )
    policy = OperationPolicy(
        SafetyConfig(
            allow_action_kinds=["approach_dialogue_target"],
            max_wait_seconds=3.0,
            max_actions_per_minute=100,
        ),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    scheduled = OPERATION_BINDING_AUTHORITY.bind(
        ApproachDialogueTargetAction(target_id=target.id),
        state,
        affordance=None,
    )
    authority = OperationAuthority(policy, OPERATION_BINDING_AUTHORITY)
    assert state.telemetry is not None

    missing_capability = state.model_copy(
        update={"telemetry": state.telemetry.model_copy(update={"capabilities": []})},
        deep=True,
    )
    invalid_selection = state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "ui": state.telemetry.ui.model_copy(
                        update={
                            "selected_character_id": None,
                            "selected_character_ids": [],
                        }
                    )
                }
            )
        },
        deep=True,
    )

    assert authority.evaluate(scheduled, missing_capability).code is (
        AuthorizationCode.CAPABILITY_UNAVAILABLE
    )
    assert authority.evaluate(scheduled, invalid_selection).code is (
        AuthorizationCode.SELECTION_INVALID
    )


