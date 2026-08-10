"""A total party loss must not be an absorbing state.

Kenshi has no game-over. When every character dies the save keeps running, the
squad empties, nothing is selected, and an agent that can only command its own
selection has no legal move left - it observes and plans forever about a world
it can no longer touch. That failure reports nothing, which for an unattended
instance is the worst shape available.

`shift_into_body` is the way out, so every fence between the planner and the
wire has to admit it with no roster and no selection. Each assertion here is one
fence that refused before.
"""

from __future__ import annotations

from kenshi_agent.core.interaction import RecipientScope
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode, ShiftIntoBodyAction
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.core.transport import NativeCommandRequest
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    OPERATION_DEFINITIONS,
    capture_recipient_basis,
)

STRANGER = "entity-stranger"


def _wiped_world() -> Observation:
    """Every character dead: empty squad, empty selection, a stranger nearby."""

    return Observation(
        run_id="total-loss",
        step_index=9,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=400, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=400,
            identity_session_id="session-total-loss",
            capabilities=sorted(
                OPERATION_DEFINITIONS["shift_into_body"].required_capabilities
                | {"game.pause", "game.time"}
            ),
            game=GameState(loaded=True, paused=True, elapsed_minutes=0.0),
            ui=UIState(active_screen="world", modal_open=False, dialogue_open=False),
            roster=[],
            nearby_entities=[
                NearbyEntity(
                    id=STRANGER,
                    name="Stranger",
                    kind="character",
                    is_animal=False,
                    faction="Drifters",
                    disposition=Disposition.NEUTRAL,
                    conscious=True,
                    distance=12.0,
                )
            ],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.05,
    )


def test_the_wiped_world_really_has_nobody_left() -> None:
    """Guard the premise, so the rest is not proving something about a full squad."""

    telemetry = _wiped_world().telemetry
    assert telemetry is not None
    assert telemetry.roster == []
    assert telemetry.selected_character_ids == []
    assert telemetry.primary_character_id is None


def test_a_body_is_still_offered_when_every_character_is_dead() -> None:
    from kenshi_agent.affordances import offered_affordances

    offers = [
        offer
        for offer in offered_affordances(_wiped_world())
        if offer.operation_kind == "shift_into_body"
    ]

    assert [offer.target.target_id for offer in offers if offer.target] == [STRANGER]


def test_body_shifting_is_elective_not_restricted_to_total_loss() -> None:
    """The implemented policy deliberately supersedes the original recovery fence."""

    from kenshi_agent.affordances import offered_affordances

    observation = _wiped_world()
    assert observation.telemetry is not None
    current = "entity-current-body"
    observation = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "roster": [
                        CharacterState(
                            id=current,
                            name="Current Body",
                            conscious=True,
                        )
                    ],
                    "primary_character_id": current,
                    "selected_character_ids": [current],
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                    ),
                }
            )
        }
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "shift_into_body"
    ]

    assert [offer.target.target_id for offer in offers if offer.target] == [STRANGER]


def test_the_shift_binds_with_no_roster_and_no_selection() -> None:
    definition = OPERATION_DEFINITIONS["shift_into_body"]
    action = ShiftIntoBodyAction(target_id=STRANGER)
    observation = _wiped_world()

    assert definition.is_currently_authorable(observation)
    assert definition.satisfies_recipient_scope(observation, action)
    binding = definition.bind(action, observation)
    assert getattr(binding, "bound", False), binding


def test_the_body_itself_is_the_recorded_recipient() -> None:
    """Without this the order is authored for nobody and refuses at dispatch."""

    definition = OPERATION_DEFINITIONS["shift_into_body"]
    basis = capture_recipient_basis(
        definition,
        ShiftIntoBodyAction(target_id=STRANGER),
        _wiped_world(),
    )

    assert basis is not None
    assert basis.scope is RecipientScope.NAMED_BODY
    assert basis.explicit_recipients == (STRANGER,)


def test_the_wire_accepts_a_shift_with_no_selected_recipients() -> None:
    request = NativeCommandRequest(
        schema_version="1.6",
        command_id="cmd-" + "0" * 32,
        command="shift_into_body",
        control_mode=ControlMode.NATIVE_ASSISTED,
        identity_session_id="session-total-loss",
        based_on_revision=WorldStateRevision(telemetry_sequence=400, capability_epoch=1),
        selected_character_ids=[],
        target_id=STRANGER,
    )

    assert request.selected_character_ids == []


def test_the_acknowledgement_echoes_the_empty_selection_truthfully() -> None:
    acknowledgement = NativeCommandAcknowledgement(
        command_id="cmd-" + "1" * 32,
        command="shift_into_body",
        status=NativeCommandStatus.COMPLETED,
        reason="shift_body_recruited",
        target_id=STRANGER,
        selected_character_ids=[],
        based_on_telemetry_sequence=400,
        acknowledged_at_telemetry_sequence=401,
        accepted_at_telemetry_sequence=401,
        terminal_at_telemetry_sequence=401,
    )

    assert acknowledgement.selected_character_ids == []


def test_every_other_command_still_requires_a_recipient() -> None:
    """The floor moved to where the command is known; it did not go away."""

    import pytest

    with pytest.raises(ValueError, match="at least one selected recipient"):
        NativeCommandRequest(
            schema_version="1.6",
            command_id="cmd-" + "0" * 32,
            command="move_to_character",
            control_mode=ControlMode.NATIVE_ASSISTED,
            identity_session_id="session-total-loss",
            based_on_revision=WorldStateRevision(telemetry_sequence=400, capability_epoch=1),
            selected_character_ids=[],
            target_id=STRANGER,
        )
