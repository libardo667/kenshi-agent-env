"""Selection cardinality belongs to the operation contract, nowhere else.

`StatefulNativeMovementOption.prepare` carried its own rule: a singleton
selection for anything that was not a character move or a map travel. That
contradicted the declared contract - `perform_context_action` is
`CURRENT_SELECTION`, which broadcasts - and it contradicted Kenshi, whose
ordering API is selection-based throughout (`newPlayerTaskSelectedCharacters`,
`addOrderSelectedCharacters`, `addJobSelectedCharacters`,
`isOrderValidForSelection`). The plug-in has always issued orders that way.

The cost was that a two-character party could not mine. Every `operate` on an
iron deposit was refused after the contract had already allowed it, and the
refusal reached the bundle as "no causal transition".
"""

from __future__ import annotations

import pytest

from kenshi_agent.core.interaction import RecipientScope
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import PerformContextAction
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    GameState,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS
from kenshi_agent.options import StatefulNativeMovementOption

PRIMARY = "char-double"
SECOND = "char-hatsune"

# Derived from the contract rather than restated, so the fixture cannot drift
# into declaring less than the operation actually requires.
NATIVE_CAPABILITIES = sorted(
    OPERATION_DEFINITIONS["perform_context_action"].required_capabilities
    | {"game.pause", "squad.basic"}
)


def _observation(selected: list[str]) -> Observation:
    primary = PRIMARY if PRIMARY in selected else None
    return Observation(
        run_id="r",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=1,
            identity_session_id="sess-1",
            capabilities=NATIVE_CAPABILITIES,
            game=GameState(loaded=True, paused=False),
            world_targets=[
                WorldTarget(
                    id="entity-iron",
                    name="Iron Resource",
                    kind="natural_resource",
                    position=Vec3(x=0.0, y=0.0, z=0.0),
                    distance=12.0,
                    default_task="TASK_MINE",
                    context_actions=[ContextActionKind.OPERATE],
                )
            ],
            ui=UIState(
                active_screen="world",
                selected_character_id=primary,
                selected_character_ids=selected,
            ),
            squad=[
                CharacterState(id=PRIMARY, name="Double", selected=PRIMARY in selected),
                CharacterState(id=SECOND, name="Hatsune", selected=SECOND in selected),
            ],
        ),
    )


def _mining_option(action: PerformContextAction) -> StatefulNativeMovementOption:
    async def _never_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("prepare must not dispatch")

    return StatefulNativeMovementOption(
        option_id="opt-1",
        action=action,
        operation=_never_called,
        require_paused_start=False,
    )


@pytest.fixture
def mining_action() -> PerformContextAction:
    from kenshi_agent.core.telemetry import ContextActionKind

    return PerformContextAction(
        target_id="entity-iron",
        context_action=ContextActionKind.OPERATE,
    )


def test_mining_broadcasts_to_a_two_character_selection(
    mining_action: PerformContextAction,
) -> None:
    """The live failure: a broke-pair start could never mine."""

    option = _mining_option(mining_action)

    option.prepare(_observation([PRIMARY, SECOND]))


def test_mining_still_works_for_a_single_selected_character(
    mining_action: PerformContextAction,
) -> None:
    option = _mining_option(mining_action)

    option.prepare(_observation([PRIMARY]))


def test_an_empty_selection_is_still_refused(
    mining_action: PerformContextAction,
) -> None:
    """Deferring to the contract must not mean accepting anything."""

    from kenshi_agent.options import OptionLifecycleError

    option = _mining_option(mining_action)

    with pytest.raises(OptionLifecycleError):
        option.prepare(_observation([]))


def test_a_primary_outside_the_selection_cannot_be_represented() -> None:
    """The stronger guarantee: the telemetry model forbids it outright.

    `prepare` used to re-check this itself. It does not need to - a snapshot
    whose primary is not among the selected characters cannot be constructed,
    so the case is impossible rather than merely refused.
    """

    import pydantic

    with pytest.raises(pydantic.ValidationError):
        TelemetrySnapshot(
            sequence=1,
            identity_session_id="sess-1",
            capabilities=["identity.stable_handles", "squad.basic"],
            game=GameState(loaded=True, paused=False),
            ui=UIState(
                active_screen="world",
                selected_character_id=PRIMARY,
                selected_character_ids=[SECOND],
            ),
            squad=[
                CharacterState(id=PRIMARY, name="Double", selected=False),
                CharacterState(id=SECOND, name="Hatsune", selected=True),
            ],
        )


def test_the_contract_is_the_authority_being_deferred_to() -> None:
    """Guards the tests above against passing for the wrong reason."""

    definition = OPERATION_DEFINITIONS["perform_context_action"]

    assert definition.recipient_scope_for() is RecipientScope.CURRENT_SELECTION
