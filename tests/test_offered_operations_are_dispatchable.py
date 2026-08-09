"""Every route that reaches the wire can form a valid request.

This exists because the same defect has now happened twice, and both times it
was invisible until a live run.

`survey_local_resources` fell through to the targeted route, was refused for
carrying no target_id, and its native command counter stayed at zero across two
live runs while the operation was cheerfully offered to the agent every step.
Then `perform_character_order` reached `_context_action_for_target`, which
resolves a semantic against `world_targets.context_actions` and returns None for
any command not on its list -- so the order name was dropped, the request failed
its own shape validation for naming no action, and the run aborted three steps
in.

The class is not "a missing branch". It is that an operation can be offered to
the planner and be structurally incapable of dispatch, with nothing between the
offer and a live Kenshi to notice. These tests put something between them.
"""

from __future__ import annotations

from typing import Any

import pytest

from kenshi_agent.core.interaction import RecipientScope
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    ControlMode,
    OpenTradeWindowAction,
    PerformCharacterOrderAction,
    PerformContextAction,
)
from kenshi_agent.core.telemetry import (
    NATIVE_COMMANDS_NAMING_AN_ACTION,
    AdvertisedTask,
    AdvertisedTaskSource,
    CharacterState,
    ContextActionKind,
    Disposition,
    GameState,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.core.transport import CommandDispatchContext
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.execution.handlers.kenshi_surface import KenshiControlSurface
from kenshi_agent.native_commands import (
    NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
    NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
    NATIVE_TRADE_WINDOW_WIRE_COMMAND,
)
from kenshi_agent.operation_definitions import wire_fields_for
from kenshi_agent.options import StatefulNativeMovementOption

ACTOR = "char-tuner"
SUBJECT = "entity-polly"
ORDER = "unprovoked_focused_melee_attack"


def _snapshot() -> TelemetrySnapshot:
    """One world where an order and a context action are both live."""

    return TelemetrySnapshot(
        sequence=41,
        identity_session_id="dispatchable-session",
        capabilities=[
            "control.perform_character_order",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.orderable_tasks",
            "roster.basic",
        ],
        game=GameState(loaded=True, paused=True),
        ui=UIState(active_screen="world"),
        primary_character_id=ACTOR,
        selected_character_ids=[ACTOR],
        roster=[
            CharacterState(id=ACTOR, name="Tuner", alive=True, conscious=True)
        ],
        nearby_entities=[
            NearbyEntity(
                id=SUBJECT,
                name="Polly",
                kind="character",
                is_animal=False,
                has_dialogue=True,
                disposition=Disposition.NEUTRAL,
                conscious=True,
                distance=12.0,
                advertised_tasks=[
                    AdvertisedTask(
                        value=254,
                        name="UNPROVOKED_FOCUSED_MELEE_ATTACK",
                        source=AdvertisedTaskSource.MENU,
                    )
                ],
                advertised_tasks_probed=True,
            )
        ],
        world_targets=[
            WorldTarget(
                id="entity-copper",
                name="Copper Resource",
                kind="natural_resource",
                distance=9.0,
                position=Vec3(x=1.0, y=0.0, z=2.0),
                default_task="operate_machinery",
                context_actions=[ContextActionKind("operate")],
            )
        ],
    )


class _StubReadResult:
    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        self.snapshot = snapshot
        self.stale = False


class _StubTelemetryReader:
    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        self._snapshot = snapshot

    def read(self) -> _StubReadResult:
        return _StubReadResult(self._snapshot)


class _StubPort:
    """The narrowest external host the request builder actually reads."""

    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        self.telemetry_reader = _StubTelemetryReader(snapshot)
        self.control_mode = ControlMode.NATIVE_ASSISTED
        self.run_id = "dispatchable"
        self._step_index = 1
        self._capability_epoch = 1

    def _observation_from_snapshot(self, snapshot: TelemetrySnapshot, **_: Any) -> Observation:
        return Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            control_mode=ControlMode.NATIVE_ASSISTED,
            world_revision=WorldStateRevision(
                telemetry_sequence=snapshot.sequence,
                capability_epoch=self._capability_epoch,
            ),
            telemetry=snapshot,
            telemetry_stale=False,
            telemetry_age_seconds=0.01,
        )


def _command() -> CommandDispatchContext:
    return CommandDispatchContext(
        command_id="cmd-" + "0" * 32,
        based_on_revision=WorldStateRevision(telemetry_sequence=41, capability_epoch=1),
        authored_recipient_scope=RecipientScope.CURRENT_SELECTION.value,
        authored_primary=ACTOR,
        authored_selection=[ACTOR],
    )


def _request(wire_command: str, *, target_id: str, context_action: str | None):
    """Build a request the way a handler does: with the operation's projection.

    The action is real rather than None because the request's fields now come
    from the operation projecting itself, which is the point - a request and the
    acknowledgement it will be matched against are the same mapping read in
    opposite directions, so a test that supplied no action was testing a shape
    that can no longer occur.
    """

    if wire_command == NATIVE_TRADE_WINDOW_WIRE_COMMAND:
        action: Action = OpenTradeWindowAction(
            first_owner_id=ACTOR,
            second_owner_id=target_id,
            window_type="auto",
        )
    elif wire_command == NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
        action = PerformContextAction(
            target_id=target_id,
            context_action=ContextActionKind(context_action or "operate"),
        )
    else:
        action = PerformCharacterOrderAction(
            target_id=target_id, order=context_action or ORDER
        )
    surface = KenshiControlSurface(_StubPort(_snapshot()))  # type: ignore[arg-type]
    return surface._native_approach_request(
        target_id,
        _command(),
        action=action,
        require_vendor_role=False,
        require_dialogue_target=False,
        wire_fields=wire_fields_for(action),
        wire_command=wire_command,  # type: ignore[arg-type]
        context_action=(
            ContextActionKind(context_action) if context_action is not None else None
        ),
    )


def test_a_character_order_reaches_the_wire_carrying_its_order_name() -> None:
    """The exact regression: the order name was dropped between handler and wire.

    `perform_character_order` names one of Kenshi's own tasks, and the request
    schema refuses a request that names none. Losing it here produced a
    validation error at dispatch rather than a refusal at binding, which is the
    difference between a legible "Kenshi will not do that" and a mid-run abort.
    """

    request = _request(
        NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
        target_id=SUBJECT,
        context_action=ORDER,
    )

    assert request.command == NATIVE_CHARACTER_ORDER_WIRE_COMMAND
    assert request.target_id == SUBJECT
    assert str(request.context_action) == ORDER


@pytest.mark.parametrize("wire_command", sorted(NATIVE_COMMANDS_NAMING_AN_ACTION))
def test_every_command_that_must_name_an_action_can_form_one(wire_command: str) -> None:
    """The class, not the instance.

    Whichever route a command takes through the request builder, if the wire
    shape requires a named action then a request built for it must carry one.
    A new command added to `NATIVE_COMMANDS_NAMING_AN_ACTION` without a branch
    that supplies its semantic fails here instead of in a live run.
    """

    if wire_command == NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
        target_id, context_action = "entity-copper", "operate"
    else:
        target_id, context_action = SUBJECT, ORDER

    request = _request(wire_command, target_id=target_id, context_action=context_action)

    assert str(request.context_action) != ""


def test_an_order_the_target_stopped_advertising_is_refused_at_the_wire() -> None:
    """Re-proved at the point the bytes are formed, not only at binding.

    The offer, the binding, and the request are three separate moments, and
    Kenshi can withdraw an order between any two of them.
    """

    with pytest.raises(RuntimeError, match="no longer advertises"):
        _request(
            NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
            target_id=SUBJECT,
            context_action="loot_target",
        )


def _acknowledgement(
    *,
    command: str,
    target_id: str,
    context_action: str,
) -> NativeCommandAcknowledgement:
    return NativeCommandAcknowledgement(
        command_id="cmd-" + "0" * 32,
        command=command,  # type: ignore[arg-type]
        status=NativeCommandStatus.ACCEPTED,
        reason="issued",
        target_id=target_id,
        context_action=ContextActionKind(context_action),
        selected_character_ids=[ACTOR],
        based_on_telemetry_sequence=41,
        acknowledged_at_telemetry_sequence=42,
        accepted_at_telemetry_sequence=42,
    )


def _order_option(action: Any) -> StatefulNativeMovementOption:
    """An option positioned exactly where identity is decided.

    `_wire_command` is derived from the action's own contract, so setting the
    action is the whole setup; anything else would be asserting the mapping
    rather than using it.
    """

    option = StatefulNativeMovementOption.__new__(StatefulNativeMovementOption)
    option.action = action
    option.selected_character_ids = [ACTOR]
    return option


def test_an_accepted_order_is_attributed_to_the_option_that_issued_it() -> None:
    """The failure that outlasted the wire fix.

    The plug-in accepted and issued the order and Kenshi obeyed it -- the two
    characters entered combat -- and the run still failed on an identity
    mismatch, because `perform_character_order` had no match rule and fell
    through to a quiet `return False`.
    """

    option = _order_option(PerformCharacterOrderAction(target_id=SUBJECT, order=ORDER))

    assert option._matches_identity(
        _acknowledgement(
            command=NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
            target_id=SUBJECT,
            context_action=ORDER,
        )
    )


def test_one_order_does_not_satisfy_a_wait_for_another_on_the_same_person() -> None:
    """The order is part of the identity, not decoration.

    A downed bandit affords both looting and a finishing blow at once. Matching
    on target alone would let the acknowledgement for either satisfy a wait for
    the other, which is the same collapse that once made two offers on one
    person look like a hallucinated affordance id.
    """

    option = _order_option(
        PerformCharacterOrderAction(target_id=SUBJECT, order="loot_target")
    )

    assert not option._matches_identity(
        _acknowledgement(
            command=NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
            target_id=SUBJECT,
            context_action=ORDER,
        )
    )


def test_an_unprobed_target_cannot_have_an_order_formed_for_it() -> None:
    """Silence about what someone affords is not permission at the wire either."""

    snapshot = _snapshot()
    snapshot.nearby_entities[0].advertised_tasks_probed = False
    surface = KenshiControlSurface(_StubPort(snapshot))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="not probed"):
        surface._native_approach_request(
            SUBJECT,
            _command(),
            action=PerformCharacterOrderAction(target_id=SUBJECT, order=ORDER),
            require_vendor_role=False,
            require_dialogue_target=False,
            wire_fields=wire_fields_for(
                PerformCharacterOrderAction(target_id=SUBJECT, order=ORDER)
            ),
            wire_command=NATIVE_CHARACTER_ORDER_WIRE_COMMAND,  # type: ignore[arg-type]
            context_action=ContextActionKind(ORDER),
        )
