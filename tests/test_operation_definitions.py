"""Reusable semantic operations and their authoritative definitions.

The point of these tests is reuse, not coverage of one scenario: the same
approach action must bind a vendor and a non-vendor identically, and the same
activation action must work for unrelated labels. Anything that would only pass
for the calibrated Barman chain is a regression of the whole milestone.
"""

from __future__ import annotations

from kenshi_agent.affordances import offered_affordances
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ACTION_ADAPTER,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CollectResourceOutputAction,
    CommandWorldTargetAction,
    ControlMode,
    ExitCurrentBuildingAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    OpenContextInventoryAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
    PurchaseItemAction,
    RegroupWithSquadMemberAction,
    SelectSquadMemberExactAction,
    TravelToMapDestinationAction,
)
from kenshi_agent.core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    PlanStep,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    Disposition,
    GameState,
    KnownMapDestination,
    NearbyEntity,
    NormalizedPointerBounds,
    OpenInventory,
    TelemetrySnapshot,
    UIState,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    ACTIVATE_VISIBLE_CONTROL_DEFINITION,
    APPROACH_DIALOGUE_TARGET_DEFINITION,
    COMMAND_WORLD_TARGET_DEFINITION,
    EXIT_CURRENT_BUILDING_DEFINITION,
    MOVE_IN_DIRECTION_DEFINITION,
    MOVE_TO_CHARACTER_DEFINITION,
    OPEN_CONTEXT_INVENTORY_DEFINITION,
    PERFORM_CONTEXT_ACTION_DEFINITION,
    PRODUCE_RESOURCE_OUTPUT_DEFINITION,
    REGROUP_WITH_SQUAD_MEMBER_DEFINITION,
    SELECT_SQUAD_MEMBER_EXACT_DEFINITION,
    TRAVEL_TO_MAP_DESTINATION_DEFINITION,
    BoundNamedTarget,
)

VENDOR_ID = "entity-barman"
CIVILIAN_ID = "entity-wanderer"

APPROACH_CAPABILITIES = [
    "control.approach_vendor",
    "identity.stable_handles",
    "nearby.characters",
    "nearby.roles",
]


def _bounds(y: float) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(min_x=0.1, max_x=0.4, min_y=y, max_y=y + 0.05)


def observation(
    *,
    entities: list[NearbyEntity] | None = None,
    controls: list[VisibleUIControl] | None = None,
    ui: UIState | None = None,
    capabilities: list[str] | None = None,
    squad: list[CharacterState] | None = None,
    stale: bool = False,
    control_mode: ControlMode = ControlMode.NATIVE_ASSISTED,
    world_targets: list[WorldTarget] | None = None,
    active_shop_trader_count: int = 0,
    game: GameState | None = None,
) -> Observation:
    effective_ui = ui or UIState(visible_controls=controls)
    effective_ui = effective_ui.model_copy(
        update={
            "active_screen": effective_ui.active_screen or "world",
            "modal_open": (
                effective_ui.modal_open
                if effective_ui.modal_open is not None
                else False
            ),
            "dialogue_open": (
                effective_ui.dialogue_open
                if effective_ui.dialogue_open is not None
                else False
            ),
        }
    )
    return Observation(
        run_id="contract-test",
        step_index=1,
        mode="live",
        control_mode=control_mode,
        world_revision=WorldStateRevision(telemetry_sequence=10, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=10,
            identity_session_id="session-contract-test",
            capabilities=capabilities if capabilities is not None else APPROACH_CAPABILITIES,
            game=game or GameState(loaded=True, paused=True),
            ui=effective_ui,
            active_shop_trader_count=active_shop_trader_count,
            squad=squad or [],
            nearby_entities=entities or [],
            world_targets=world_targets or [],
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=0.1,
    )


def vendor(distance: float = 30.0) -> NearbyEntity:
    return NearbyEntity(
        id=VENDOR_ID,
        name="Barman",
        is_animal=False,
        has_dialogue=True,
        has_vendor_list=True,
        is_squad_leader=True,
        disposition=Disposition.NEUTRAL,
        distance=distance,
    )


def civilian(distance: float = 12.0) -> NearbyEntity:
    """A talkable person who owns no shop and leads no squad."""

    return NearbyEntity(
        id=CIVILIAN_ID,
        name="Nomad Wanderer",
        is_animal=False,
        has_dialogue=True,
        has_vendor_list=False,
        is_squad_leader=False,
        disposition=Disposition.FRIENDLY,
        distance=distance,
    )


def test_squad_regroup_binds_a_selected_actor_to_a_distinct_downed_squadmate() -> None:
    actor = CharacterState(
        id="entity-bark",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
        position=Vec3(x=0.0, y=0.0, z=0.0),
    )
    target = CharacterState(
        id="entity-plant",
        name="Plant",
        alive=True,
        conscious=False,
        down=True,
        position=Vec3(x=1000.0, y=0.0, z=500.0),
    )
    state = observation(
        capabilities=[
            "control.regroup_with_squad_member",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.basic",
            "squad.health",
        ],
        squad=[actor, target],
        ui=UIState(
            active_screen="world",
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
        ),
    )
    action = RegroupWithSquadMemberAction(
        actor_id=actor.id,
        target_id=target.id,
    )

    binding = REGROUP_WITH_SQUAD_MEMBER_DEFINITION.bind(action, state)

    assert binding.bound
    assert binding.target_id == target.id
    assert REGROUP_WITH_SQUAD_MEMBER_DEFINITION.max_primitive_actions == 5
    assert REGROUP_WITH_SQUAD_MEMBER_DEFINITION.controller_verified

    already_together = state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "squad": [
                        actor,
                        target.model_copy(
                            update={"position": Vec3(x=5.0, y=0.0, z=5.0)}
                        ),
                    ]
                }
            )
        }
    )
    assert not REGROUP_WITH_SQUAD_MEMBER_DEFINITION.bind(
        action,
        already_together,
    ).bound


def test_squad_selection_prefers_exact_native_identity_without_portrait_geometry() -> None:
    actor = CharacterState(id="entity-bark", name="Bark", selected=True)
    target = CharacterState(id="entity-plant", name="Plant")
    state = observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "squad.basic",
        ],
        squad=[actor, target],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
            visible_controls=[],
        ),
    )

    binding = SELECT_SQUAD_MEMBER_EXACT_DEFINITION.bind(
        SelectSquadMemberExactAction(target_id=target.id),
        state,
    )

    assert binding.bound
    assert isinstance(binding, BoundNamedTarget)
    assert binding.target_id == target.id
    assert binding.resolved_label == target.name
    assert not hasattr(binding, "resolved_bounds")


class TestApproachBindsAnyDialogueTarget:
    """The whole point: approach is not a commerce affordance."""

    def test_contract_is_native_only_and_coordinate_independent(self) -> None:
        assert (
            APPROACH_DIALOGUE_TARGET_DEFINITION.pointer_class
            is PointerActionClass.COORDINATE_INDEPENDENT
        )
        assert APPROACH_DIALOGUE_TARGET_DEFINITION.risk.pointer_actions == 0
        assert APPROACH_DIALOGUE_TARGET_DEFINITION.risk.native_assisted_actions == 1

    def test_binds_a_vendor(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()]),
        )
        assert binding.bound
        assert binding.target_id == VENDOR_ID

    def test_binds_a_non_vendor_identically(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
            observation(entities=[civilian()]),
        )
        assert binding.bound
        assert binding.target_id == CIVILIAN_ID

    def test_rejects_a_target_absent_from_current_state(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id="entity-ghost"),
            observation(entities=[vendor(), civilian()]),
        )
        assert not binding.bound
        assert "not a current valid dialogue target" in binding.reason

    def test_rejects_a_hostile_target(self) -> None:
        hostile = NearbyEntity(
            id="entity-bandit",
            name="Dust Bandit",
            is_animal=False,
            has_dialogue=True,
            disposition=Disposition.HOSTILE,
            distance=8.0,
        )
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id="entity-bandit"),
            observation(entities=[hostile]),
        )
        assert not binding.bound

    def test_stale_telemetry_cannot_bind(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()], stale=True),
        )
        assert not binding.bound
        assert "stale" in binding.reason

    def test_rejects_approach_while_dialogue_with_another_target_is_open(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
            observation(
                entities=[vendor(), civilian()],
                ui=UIState(
                    active_screen="dialogue",
                    modal_open=True,
                    dialogue_open=True,
                    dialogue_target_id=VENDOR_ID,
                ),
            ),
        )
        assert not binding.bound
        assert "different target" in binding.reason
        assert "close that dialogue" in binding.reason

    def test_rejects_redundant_approach_to_active_dialogue_target(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_DEFINITION.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(
                entities=[vendor()],
                ui=UIState(
                    active_screen="dialogue",
                    modal_open=True,
                    dialogue_open=True,
                    dialogue_target_id=VENDOR_ID,
                ),
            ),
        )
        assert not binding.bound
        assert "already open" in binding.reason


class TestVisibleControlBinding:
    def test_runtime_owned_time_widgets_are_not_generic_controls(self) -> None:
        speed_name = "0,000,000,073,247,990_TimeSpeedButton2"
        controls = [
            VisibleUIControl(
                label=speed_name,
                role="button",
                widget_name=speed_name,
                bounds=_bounds(0.5),
            ),
            VisibleUIControl(
                label="Show me your goods.",
                role="button",
                widget_name="0,000,000,073,247,990_DialogueChoiceButton",
                bounds=_bounds(0.6),
            ),
        ]
        state = observation(controls=controls, capabilities=["ui.visible_controls"])

        assert [
            entry["exact_label"] for entry in state.visible_control_digest()
        ] == ["Show me your goods."]

        speed_binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label=speed_name, role="button"),
            state,
        )
        dialogue_binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(
                exact_label="Show me your goods.",
                role="button",
            ),
            state,
        )

        assert not speed_binding.bound
        assert "runtime-owned" in speed_binding.reason
        assert dialogue_binding.bound

    def test_binds_two_unrelated_labels_with_the_same_action(self) -> None:
        controls = [
            VisibleUIControl(label="Show me your goods.", role="button", bounds=_bounds(0.5)),
            VisibleUIControl(label="Goodbye.", role="button", bounds=_bounds(0.6)),
        ]
        state = observation(controls=controls, capabilities=["ui.visible_controls"])

        first = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
            state,
        )
        second = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="Goodbye.", role="button"),
            state,
        )

        assert first.bound and second.bound
        assert first.resolved_bounds == _bounds(0.5)
        assert second.resolved_bounds == _bounds(0.6)

    def test_label_whitespace_and_case_normalize(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Show me  your goods.", role="button", bounds=_bounds(0.5))
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="show me your goods.", role="button"),
            state,
        )
        assert binding.bound
        assert binding.resolved_label == "Show me  your goods."

    def test_duplicate_label_fails_closed(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5)),
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.7)),
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound
        assert "ambiguous" in binding.reason

    def test_role_mismatch_does_not_bind(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="Trade", role="text", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound

    def test_missing_capability_is_unknown_not_absent(self) -> None:
        state = observation(controls=None, capabilities=[])
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound
        assert "unavailable" in binding.reason


class TestExitCurrentBuildingBinding:
    def test_binds_only_one_selected_character_confirmed_indoors(self) -> None:
        state = observation(
            capabilities=[
                "control.exit_current_building",
                "identity.stable_handles",
                "squad.indoors",
            ],
            ui=UIState(
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    indoors=True,
                )
            ],
        )

        binding = EXIT_CURRENT_BUILDING_DEFINITION.bind(
            ExitCurrentBuildingAction(),
            state,
        )

        assert binding.bound
        assert binding.resolved_label == "Hep"
        assert EXIT_CURRENT_BUILDING_DEFINITION.pointer_class is (
            PointerActionClass.COORDINATE_INDEPENDENT
        )
        assert EXIT_CURRENT_BUILDING_DEFINITION.reference_fields == ()
        assert EXIT_CURRENT_BUILDING_DEFINITION.controller_verified

    def test_controller_owned_exit_allows_no_redundant_world_postcondition(
        self,
    ) -> None:
        step = PlanStep(
            step_id="exit",
            action=ExitCurrentBuildingAction(),
            preconditions=[
                Condition(
                    kind=ConditionKind.TELEMETRY_FRESH,
                    operator=ConditionOperator.EQUALS,
                    expected=True,
                    max_age_seconds=3.0,
                )
            ],
            success_conditions=[],
            timeout_seconds=30,
        )

        assert step.success_conditions == []

    def test_rejects_an_outdoor_character(self) -> None:
        state = observation(
            ui=UIState(
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    indoors=False,
                )
            ],
        )

        binding = EXIT_CURRENT_BUILDING_DEFINITION.bind(
            ExitCurrentBuildingAction(),
            state,
        )

        assert not binding.bound
        assert "not confirmed inside" in binding.reason


class TestPerformContextAction:
    def test_binds_exact_advertised_object_action_pair(self) -> None:
        target = WorldTarget(
            id="entity-copper",
            name="Copper Resource",
            kind="natural_resource",
            position=Vec3(x=1.0, y=0.0, z=2.0),
            distance=40.0,
            context_actions=[ContextActionKind.OPERATE],
            default_task="operate_machinery",
            mining_resource_level=0.8,
        )
        state = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            capabilities=[
                "control.perform_context_action",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[target],
        )

        binding = PERFORM_CONTEXT_ACTION_DEFINITION.bind(
            PerformContextAction(
                target_id=target.id,
                context_action=ContextActionKind.OPERATE,
            ),
            state,
        )

        assert binding.bound
        assert binding.target_id == target.id
        assert binding.resolved_label == "operate"

    def test_rejects_action_not_advertised_by_exact_target(self) -> None:
        target = WorldTarget(
            id="entity-copper",
            name="Copper Resource",
            kind="natural_resource",
            position=Vec3(x=1.0, y=0.0, z=2.0),
            distance=40.0,
            context_actions=[],
            default_task="operate_machinery",
        )
        state = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            world_targets=[target],
        )

        binding = PERFORM_CONTEXT_ACTION_DEFINITION.bind(
            PerformContextAction(
                target_id=target.id,
                context_action=ContextActionKind.OPERATE,
            ),
            state,
        )

        assert not binding.bound
        assert "does not currently advertise" in binding.reason

    def test_binds_without_an_undocumented_engine_probability(self) -> None:
        target = WorldTarget(
            id="entity-copper",
            name="Copper Resource",
            kind="natural_resource",
            position=Vec3(x=1.0, y=0.0, z=2.0),
            distance=40.0,
            context_actions=[ContextActionKind.OPERATE],
            default_task="operate_machinery",
        )
        state = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            world_targets=[target],
        )

        binding = PERFORM_CONTEXT_ACTION_DEFINITION.bind(
            PerformContextAction(
                target_id=target.id,
                context_action=ContextActionKind.OPERATE,
            ),
            state,
        )

        assert binding.bound
        assert binding.target_id == target.id

    def test_unknown_interface_state_fails_closed(self) -> None:
        target = WorldTarget(
            id="entity-copper",
            name="Copper Resource",
            kind="natural_resource",
            position=Vec3(x=1.0, y=0.0, z=2.0),
            distance=40.0,
            context_actions=[ContextActionKind.OPERATE],
            default_task="operate_machinery",
        )
        state = observation(
            ui=UIState(
                active_screen="world",
                dialogue_open=False,
            ),
            capabilities=[
                "control.perform_context_action",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[target],
        )
        assert state.telemetry is not None
        state.telemetry.ui.modal_open = None

        binding = PERFORM_CONTEXT_ACTION_DEFINITION.bind(
            PerformContextAction(
                target_id=target.id,
                context_action=ContextActionKind.OPERATE,
            ),
            state,
        )
        digest_kinds = {offer.operation_kind for offer in offered_affordances(state)}

        assert not binding.bound
        assert "not confirmed clear" in binding.reason
        assert "perform_context_action" not in digest_kinds


def natural_resource(
    *,
    target_id: str = "entity-copper",
    name: str = "Copper Resource",
    actions: list[ContextActionKind] | None = None,
    screen_position: Vec2 | None = None,
) -> WorldTarget:
    return WorldTarget(
        id=target_id,
        name=name,
        kind="natural_resource",
        position=Vec3(x=1.0, y=0.0, z=2.0),
        distance=40.0,
        context_actions=(
            [ContextActionKind.OPERATE] if actions is None else actions
        ),
        default_task="operate_machinery",
        screen_position=screen_position,
    )


class TestWorldTargetCommandContract:
    def test_missing_geometry_and_ambiguous_ids_fail_closed(self) -> None:
        action = CommandWorldTargetAction(
            target_id="entity-copper",
            context_action=ContextActionKind.OPERATE,
        )
        clear_ui = UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
        )
        missing_geometry = observation(
            ui=clear_ui,
            capabilities=[
                "world.context_targets",
                "world.context_target_screen_positions",
            ],
            world_targets=[natural_resource()],
        )
        duplicate = observation(
            ui=clear_ui,
            capabilities=[
                "world.context_targets",
                "world.context_target_screen_positions",
            ],
            world_targets=[
                natural_resource(screen_position=Vec2(x=0.4, y=0.6)),
                natural_resource(screen_position=Vec2(x=0.5, y=0.7)),
            ],
        )

        absent = COMMAND_WORLD_TARGET_DEFINITION.bind(action, missing_geometry)
        ambiguous = COMMAND_WORLD_TARGET_DEFINITION.bind(action, duplicate)

        assert not absent.bound
        assert "no current on-screen command geometry" in absent.reason
        assert not ambiguous.bound
        assert "ambiguous" in ambiguous.reason


class TestResourceProductionContracts:
    def test_production_binds_one_exact_advertised_natural_resource(self) -> None:
        target = natural_resource()
        state = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            capabilities=[
                "control.produce_resource_output",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[target],
        )

        binding = PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(
            ProduceResourceOutputAction(target_id=target.id),
            state,
        )

        assert binding.bound
        assert binding.target_id == target.id

    def test_production_rejects_absent_unadvertised_and_ambiguous_targets(self) -> None:
        action = ProduceResourceOutputAction(target_id="entity-copper")
        clear_ui = UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
        )
        absent = observation(ui=clear_ui, world_targets=[])
        unadvertised = observation(
            ui=clear_ui,
            world_targets=[natural_resource(actions=[])],
        )
        # Duplicate identity is malformed whenever the stable-identity
        # capability is present. Remove it here to exercise the binder's own
        # fail-closed ambiguity rule independently.
        ambiguous = observation(
            ui=clear_ui,
            capabilities=[
                "control.produce_resource_output",
                "world.context_targets",
                "game.pause",
            ],
            world_targets=[natural_resource(), natural_resource()],
        )

        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, absent).bound
        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, unadvertised).bound
        ambiguous_binding = PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, ambiguous)
        assert not ambiguous_binding.bound
        assert "ambiguous" in ambiguous_binding.reason

    def test_open_inventory_binds_the_exact_target_and_fails_closed_on_a_modal(
        self,
    ) -> None:
        target = natural_resource()
        clear = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            capabilities=[
                "control.open_context_inventory",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[target],
        )
        blocked = clear.model_copy(
            update={
                "telemetry": clear.telemetry.model_copy(
                    update={
                        "ui": UIState(
                            active_screen="world",
                            modal_open=True,
                            dialogue_open=False,
                        )
                    }
                )
            },
            deep=True,
        )
        action = OpenContextInventoryAction(target_id=target.id)

        assert OPEN_CONTEXT_INVENTORY_DEFINITION.bind(action, clear).bound
        assert not OPEN_CONTEXT_INVENTORY_DEFINITION.bind(action, blocked).bound

    def test_a_second_inventory_may_be_opened_while_one_is_already_open(
        self,
    ) -> None:
        """Two open windows is what a transfer is, not a conflict.

        The old fence refused whenever `modal_open` was true, and an open
        inventory sets `modal_open`. So the source could be opened or the
        destination could be opened, never both - which is why moving an item
        needed a mouse and why looting, buying and giving looked like three
        different problems instead of one.
        """

        target = natural_resource()
        with_one_open = observation(
            ui=UIState(
                active_screen="inventory",
                modal_open=True,
                dialogue_open=False,
                open_inventories=[
                    OpenInventory(
                        owner_id="entity-squadmate",
                        owner_name="Little",
                        owner_kind="character",
                        player_owned=True,
                        money=0,
                        total_weight=3.0,
                    )
                ],
            ),
            capabilities=[
                "control.open_context_inventory",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[target],
        )

        bound = OPEN_CONTEXT_INVENTORY_DEFINITION.bind(
            OpenContextInventoryAction(target_id=target.id), with_one_open
        )

        assert bound.bound

    def test_any_observed_owner_can_have_its_inventory_opened(self) -> None:
        """Not just mining crates. The narrow lookup was ours, not Kenshi's.

        This resolved through `world_targets` for a `natural_resource`, so a
        body the agent had just knocked out and ordered `loot_target` on was
        unreachable by construction. Kenshi opens by handle and does not ask
        what kind of thing the handle names.
        """

        body = NearbyEntity(
            id="entity-bandit",
            name="Hungry bandit",
            kind="character",
            is_animal=False,
            has_dialogue=False,
            disposition=Disposition.HOSTILE,
            conscious=False,
            distance=2.0,
        )
        world = observation(
            ui=UIState(active_screen="world", modal_open=False, dialogue_open=False),
            capabilities=[
                "control.open_context_inventory",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            entities=[body],
        )

        bound = OPEN_CONTEXT_INVENTORY_DEFINITION.bind(
            OpenContextInventoryAction(target_id=body.id), world
        )

        assert bound.bound
        assert "Hungry bandit" in bound.reason


class TestCollectResourceOutput:
    @staticmethod
    def _state(
        *,
        context_target_id: str = "entity-copper",
        section: str = "out",
        active_screen: str = "inventory",
        source_quantity: int = 2,
        player_inventory_open: bool = True,
        active_shop_trader_count: int = 0,
        selected_inventory_accepts_item: bool | None = True,
    ) -> Observation:
        target = natural_resource()
        visible_controls = [
            VisibleUIControl(
                label="Raw Iron",
                window="COPPER RESOURCE",
                role="item",
                item_name="Raw Iron",
                item_quantity=source_quantity,
                selected_inventory_accepts_item=selected_inventory_accepts_item,
                section=section,
                bounds=_bounds(0.5),
            )
        ]
        if player_inventory_open:
            visible_controls.append(
                VisibleUIControl(
                    label="Wooden Backpack",
                    window="HEP",
                    role="item",
                    item_name="Wooden Backpack",
                    item_quantity=1,
                    section="main",
                    bounds=_bounds(0.7),
                )
            )
        return observation(
            ui=UIState(
                active_screen=active_screen,
                modal_open=True,
                dialogue_open=False,
                open_inventory_windows=2 if player_inventory_open else 1,
                context_inventory_target_id=context_target_id,
                visible_controls_complete=True,
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
                visible_controls=visible_controls,
            ),
            capabilities=[
                "ui.visible_controls",
                "ui.context_inventory_target",
                "ui.inventory",
                "squad.inventory",
                "world.context_targets",
                "identity.stable_handles",
            ],
            active_shop_trader_count=active_shop_trader_count,
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
            world_targets=[target],
        )

    @staticmethod
    def _action(*, source_quantity: int = 2) -> CollectResourceOutputAction:
        return CollectResourceOutputAction(
            target_id="entity-copper",
            cell_label="Raw Iron",
            item_name="Raw Iron",
            source_quantity=source_quantity,
            window="COPPER RESOURCE",
        )








class TestDefinitionPolicy:
    def test_monitored_movement_terminals_own_their_success_verdicts(self) -> None:
        for contract in (
            APPROACH_DIALOGUE_TARGET_DEFINITION,
            MOVE_TO_CHARACTER_DEFINITION,
            MOVE_IN_DIRECTION_DEFINITION,
            TRAVEL_TO_MAP_DESTINATION_DEFINITION,
            EXIT_CURRENT_BUILDING_DEFINITION,
        ):
            assert contract.controller_verified

    def test_approach_is_withheld_from_interface_only(self) -> None:
        state = observation(
            control_mode=ControlMode.INTERFACE_ONLY,
            capabilities=set(APPROACH_CAPABILITIES) | {"ui.visible_controls"},
            entities=[vendor()],
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
        )
        kinds = [offer.operation_kind for offer in offered_affordances(state)]
        assert "approach_dialogue_target" not in kinds
        assert "activate_visible_control" in kinds


    def test_legacy_capability_alias_still_satisfies_the_contract(self) -> None:
        """The installed plug-in emits the vendor-named capability."""

        assert not APPROACH_DIALOGUE_TARGET_DEFINITION.missing_capabilities(
            set(APPROACH_CAPABILITIES)
        )

    def test_generic_capability_name_also_satisfies_the_contract(self) -> None:
        capabilities = {
            "control.approach_dialogue_target",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        assert not APPROACH_DIALOGUE_TARGET_DEFINITION.missing_capabilities(capabilities)


def test_exact_known_map_destination_has_one_controller_owned_travel_contract() -> None:
    state = observation(
        capabilities=[
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.health",
        ],
        squad=[
            CharacterState(
                id="entity-selected",
                name="Streak",
                selected=True,
            )
        ],
        ui=UIState(
            selected_character_id="entity-selected",
            selected_character_ids=["entity-selected"],
        ),
    )
    assert state.telemetry is not None
    state = state.model_copy(
        update={
            "telemetry": TelemetrySnapshot.model_validate(
                state.telemetry.model_dump(mode="python")
                | {
                    "known_map_destinations": [
                        {
                            "id": "entity-known-town",
                            "name": "The Hub",
                            "distance": 1250.0,
                        }
                    ]
                }
            )
        },
        deep=True,
    )
    action = ACTION_ADAPTER.validate_python(
        {
            "kind": "travel_to_map_destination",
            "destination_id": "entity-known-town",
        }
    )

    contract = TRAVEL_TO_MAP_DESTINATION_DEFINITION

    assert contract.controller_verified
    assert contract.bind(action, state).bound
    missing = action.model_copy(update={"destination_id": "entity-undiscovered-town"})
    assert not contract.bind(missing, state).bound


def test_exact_selection_travel_and_ordinary_movement_bind_a_current_squad_group() -> None:
    squad = [
        CharacterState(id="entity-bark", name="Bark", selected=True),
        CharacterState(id="entity-plant", name="Plant", selected=True),
    ]
    state = observation(
        capabilities=[
            "control.select_squad_member",
            "control.move_to_character",
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "nearby.characters",
            "squad.basic",
            "squad.health",
        ],
        entities=[
            NearbyEntity(
                id="entity-barman",
                name="Barman",
                is_animal=False,
                disposition=Disposition.NEUTRAL,
                distance=20.0,
            )
        ],
        squad=squad,
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id="entity-bark",
            selected_character_ids=["entity-bark", "entity-plant"],
        ),
    )
    assert state.telemetry is not None
    state.telemetry.known_map_destinations = [
        KnownMapDestination(
            id="entity-known-town",
            name="The Hub",
            distance=1250.0,
        )
    ]

    selection = SELECT_SQUAD_MEMBER_EXACT_DEFINITION.bind(
        SelectSquadMemberExactAction(target_id="entity-plant"),
        state,
    )
    travel = TRAVEL_TO_MAP_DESTINATION_DEFINITION.bind(
        TravelToMapDestinationAction(destination_id="entity-known-town"),
        state,
    )
    movement = MOVE_TO_CHARACTER_DEFINITION.bind(
        MoveToCharacterAction(target_id="entity-barman"),
        state,
    )

    assert selection.bound
    assert travel.bound
    assert movement.bound
    travel_offer = next(
        offer
        for offer in offered_affordances(state)
        if offer.operation_kind == "travel_to_map_destination"
    )
    assert travel_offer.semantic == "travel_squad"
    assert "2 selected squad members" in travel_offer.description
    movement_offer = next(
        offer
        for offer in offered_affordances(state)
        if offer.operation_kind == "move_to_character"
    )
    assert movement_offer.semantic == "move_squad_to"
    assert "2 selected squad members" in movement_offer.description

    modal = state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "ui": state.telemetry.ui.model_copy(
                        update={
                            "active_screen": "inventory",
                            "dialogue_open": False,
                            "modal_open": True,
                            "open_inventory_windows": 1,
                        }
                    )
                }
            )
        },
        deep=True,
    )
    assert not TRAVEL_TO_MAP_DESTINATION_DEFINITION.bind(
        TravelToMapDestinationAction(destination_id="entity-known-town"),
        modal,
    ).bound
    assert not MOVE_TO_CHARACTER_DEFINITION.bind(
        MoveToCharacterAction(target_id="entity-barman"),
        modal,
    ).bound
    assert not MOVE_IN_DIRECTION_DEFINITION.bind(
        MoveInDirectionAction(
            bearing_degrees=90,
            distance_units=100,
            expected_effect="Move east.",
        ),
        modal,
    ).bound


def test_map_travel_cannot_bind_a_destination_already_reached() -> None:
    state = observation(
        capabilities=[
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "identity.stable_handles",
            "squad.health",
        ],
        squad=[
            CharacterState(
                id="entity-selected",
                name="Streak",
                selected=True,
            )
        ],
        ui=UIState(
            selected_character_id="entity-selected",
            selected_character_ids=["entity-selected"],
        ),
    )
    assert state.telemetry is not None
    state.telemetry.known_map_destinations = [
        KnownMapDestination(
            id="entity-known-town",
            name="The Hub",
            distance=5.0,
        )
    ]
    action = TravelToMapDestinationAction(destination_id="entity-known-town")

    contract = TRAVEL_TO_MAP_DESTINATION_DEFINITION

    binding = contract.bind(action, state)
    assert not binding.bound
    assert "already local" in binding.reason
    assert "travel_to_map_destination" not in {
        offer.operation_kind for offer in offered_affordances(state)
    }


def test_map_travel_cannot_bind_the_exact_current_town_after_gate_entry() -> None:
    state = observation(
        capabilities=[
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "game.location",
            "game.location.identity",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.health",
        ],
        squad=[
            CharacterState(
                id="entity-selected",
                name="Streak",
                selected=True,
            )
        ],
        ui=UIState(
            selected_character_id="entity-selected",
            selected_character_ids=["entity-selected"],
        ),
        game=GameState(
            loaded=True,
            paused=True,
            location_id="entity-known-town",
            location_name="Squin",
            inside_town_walls=True,
        ),
    )
    assert state.telemetry is not None
    state.telemetry.known_map_destinations = [
        KnownMapDestination(
            id="entity-known-town",
            name="Squin",
            distance=1300.0,
            has_gates=True,
        )
    ]
    action = TravelToMapDestinationAction(destination_id="entity-known-town")

    contract = TRAVEL_TO_MAP_DESTINATION_DEFINITION

    binding = contract.bind(action, state)
    assert not binding.bound
    assert "already inside" in binding.reason
    assert "travel_to_map_destination" not in {
        offer.operation_kind for offer in offered_affordances(state)
    }

    state.telemetry.game = state.telemetry.game.model_copy(
        update={"inside_town_walls": False}
    )
    assert contract.bind(action, state).bound

    state.telemetry.game = state.telemetry.game.model_copy(
        update={
            "location_id": "entity-other-town",
            "location_name": "Admag",
            "inside_town_walls": True,
        }
    )
    assert contract.bind(action, state).bound

    state.telemetry.game = state.telemetry.game.model_copy(
        update={
            "location_id": "entity-known-town",
            "location_name": "Squin",
            "inside_town_walls": False,
        }
    )
    state.telemetry.known_map_destinations[0] = (
        state.telemetry.known_map_destinations[0].model_copy(
            update={"has_gates": False}
        )
    )
    assert not contract.bind(action, state).bound


def test_group_map_travel_is_not_hidden_by_a_primary_member_already_in_town() -> None:
    state = observation(
        capabilities=[
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "game.location",
            "game.location.identity",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.health",
        ],
        squad=[
            CharacterState(
                id="entity-primary-local",
                name="Kole",
                selected=True,
            ),
            CharacterState(
                id="entity-remote-groupmate",
                name="Polly",
                selected=True,
            ),
        ],
        ui=UIState(
            selected_character_id="entity-primary-local",
            selected_character_ids=[
                "entity-primary-local",
                "entity-remote-groupmate",
            ],
        ),
        game=GameState(
            loaded=True,
            paused=True,
            location_id="entity-known-town",
            location_name="The Hub",
            inside_town_walls=False,
        ),
    )
    assert state.telemetry is not None
    state.telemetry.known_map_destinations = [
        KnownMapDestination(
            id="entity-known-town",
            name="The Hub",
            # Native group telemetry reports the farthest selected member.
            distance=1700.0,
            has_gates=False,
        )
    ]
    action = TravelToMapDestinationAction(destination_id="entity-known-town")

    binding = TRAVEL_TO_MAP_DESTINATION_DEFINITION.bind(action, state)
    assert binding.bound
    travel_offer = next(
        offer
        for offer in offered_affordances(state)
        if offer.operation_kind == "travel_to_map_destination"
    )
    assert travel_offer.semantic == "travel_squad"
    assert state.known_map_destination_digest()[0]["travel_available"] is True


class TestAffordancesAreAdvertised:
    def test_digest_reports_available_semantics_and_exact_targets(self) -> None:
        actor = CharacterState(id="entity-player", name="Player", selected=True)
        state = observation(
            entities=[vendor()],
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
            capabilities=[*APPROACH_CAPABILITIES, "ui.visible_controls"],
            squad=[actor],
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                visible_controls=[
                    VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))
                ],
                selected_character_id=actor.id,
                selected_character_ids=[actor.id],
            ),
        )
        offers = offered_affordances(state)
        kinds = {offer.operation_kind for offer in offers}
        assert {"approach_dialogue_target", "activate_visible_control"} <= kinds
        assert all(offer.affordance_id for offer in offers)

    def test_threat_response_is_offered_only_for_a_grounded_safe_paused_threat(
        self,
    ) -> None:
        capabilities = [
            "game.pause",
            "game.speed",
            "control.move_in_direction",
            "nearby.visible_entities",
            "squad.health",
        ]
        threatened = observation(
            entities=[
                NearbyEntity(
                    id="entity-bandit",
                    name="Dust Bandit",
                    disposition=Disposition.HOSTILE,
                    distance=8.0,
                    visible=True,
                    position=Vec3(x=0.0, y=0.0, z=0.0),
                )
            ],
            capabilities=capabilities,
            squad=[
                CharacterState(
                    id="entity-bark",
                    name="Bark",
                    selected=True,
                    alive=True,
                    conscious=True,
                    blood=100.0,
                    in_combat=True,
                    position=Vec3(x=10.0, y=0.0, z=0.0),
                )
            ],
            ui=UIState(
                selected_character_id="entity-bark",
                selected_character_ids=["entity-bark"],
            ),
            game=GameState(loaded=True, paused=True, speed_multiplier=1.0),
        )

        assert "respond_to_immediate_threat" in {
            offer.operation_kind for offer in offered_affordances(threatened)
        }

        assert threatened.telemetry is not None
        clear = threatened.model_copy(
            update={
                "telemetry": threatened.telemetry.model_copy(
                    update={"nearby_entities": []}
                )
            },
            deep=True,
        )
        assert "respond_to_immediate_threat" not in {
            offer.operation_kind for offer in offered_affordances(clear)
        }


    def test_visible_control_digest_marks_ambiguity(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5)),
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.7)),
                VisibleUIControl(label="Leave", role="button", bounds=_bounds(0.9)),
            ],
            capabilities=["ui.visible_controls"],
        )
        digest = {
            entry["exact_label"]: entry["ambiguous"]
            for entry in state.visible_control_digest()
        }
        assert digest == {"Trade": True, "Leave": False}

    def test_digest_is_empty_without_the_capability(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
            capabilities=[],
        )
        assert state.visible_control_digest() == []


class TestItemCellControls:
    """Shop and inventory grid cells bind like any other advertised control."""

    def test_an_item_cell_binds_by_ordinal_and_role(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5)),
                VisibleUIControl(label="item_1", role="item", bounds=_bounds(0.6)),
                VisibleUIControl(label="ARRANGE", role="button", bounds=_bounds(0.9)),
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="item_1", role="item"),
            state,
        )
        assert binding.bound
        assert binding.resolved_role == "item"
        assert binding.resolved_bounds == _bounds(0.6)

    def test_an_item_ordinal_does_not_match_a_button(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="item_0", role="button"),
            state,
        )
        assert not binding.bound

    def test_absent_cell_fails_closed(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="item_9", role="item"),
            state,
        )
        assert not binding.bound

    def test_item_cells_appear_in_the_digest_with_ambiguity_marked(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5)),
                VisibleUIControl(label="item_1", role="item", bounds=_bounds(0.6)),
            ],
            capabilities=["ui.visible_controls"],
        )
        digest = state.visible_control_digest()
        assert {e["exact_label"] for e in digest} == {"item_0", "item_1"}
        assert all(e["role"] == "item" for e in digest)
        assert not any(e["ambiguous"] for e in digest)


class TestPurchaseSafety:
    """The purchase fence, lifted out of the calibrated food policy.

    The legacy version took model-authored x/y and merely checked they landed
    inside the tooltip's source. Here the cell is the reference and the tooltip
    must belong to *it*, so "buy what I am looking at" is proven rather than
    asserted.
    """

    SELLER = "entity-barman"

    def _state(
        self,
        *,
        tooltip: str | None = "Dried Meat\n[Food]\nValue c.52",
        tooltip_visible: bool = True,
        tooltip_over_cell: bool = True,
        traders: int = 1,
        shop_owner: bool = True,
    ) -> Observation:
        # A real trade screen shows both inventories, and the cell ordinals run
        # across both, so the window is the only thing saying whose item it is.
        cell = VisibleUIControl(
            label="item_3", role="item", window="BARMAN", bounds=_bounds(0.5)
        )
        # Ordinals come from one counter spanning every window, so they are
        # unique across both inventories.
        # Carries its own facts, so binding reaches the ownership check rather
        # than stopping at the tooltip.
        our_cell = VisibleUIControl(
            label="item_7",
            role="item",
            window="HEP",
            bounds=_bounds(0.2),
            item_name="Dried Meat",
            item_base_value=52,
        )
        # The tooltip's source is the cell itself unless told otherwise.
        source = _bounds(0.5) if tooltip_over_cell else _bounds(0.8)
        seller = NearbyEntity(
            id=self.SELLER,
            name="Barman",
            is_animal=False,
            has_dialogue=True,
            has_vendor_list=True,
            is_squad_leader=True,
            shop_inventory_owner=shop_owner,
            disposition=Disposition.NEUTRAL,
            distance=3.0,
            conscious=True,
        )
        state = observation(
            entities=[seller],
            controls=[cell, our_cell],
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": traders,
                        "ui": telemetry.ui.model_copy(
                            update={
                                "active_screen": "trade",
                                "open_inventory_windows": 2,
                                "selected_character_id": "entity-hep",
                                "selected_character_ids": ["entity-hep"],
                                "tooltip_visible": tooltip_visible,
                                "tooltip_text": tooltip,
                                "tooltip_source_bounds": source,
                            }
                        ),
                    }
                )
            },
            deep=True,
        )

    def _action(self, **overrides: object) -> PurchaseItemAction:
        fields: dict[str, object] = {
            "cell_label": "item_3",
            "item_name": "Dried Meat",
            "expected_price": 52,
            "window": "BARMAN",
            "seller_id": self.SELLER,
        }
        fields.update(overrides)
        return PurchaseItemAction(**fields)  # type: ignore[arg-type]














class TestWindowAttribution:
    """Several open windows share labels; the owning window disambiguates."""

    def _two_windows(self) -> Observation:
        return observation(
            controls=[
                VisibleUIControl(label="ARRANGE", role="button", bounds=_bounds(0.5), window="HEP"),
                VisibleUIControl(
                    label="ARRANGE", role="button", bounds=_bounds(0.7), window="BARMAN"
                ),
            ],
            capabilities=["ui.visible_controls"],
        )

    def test_a_label_shared_by_two_windows_is_ambiguous(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(exact_label="ARRANGE", role="button"),
            self._two_windows(),
        )
        assert not binding.bound
        assert "Name the window" in binding.reason

    def test_naming_the_window_resolves_it(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(
                exact_label="ARRANGE", role="button", window="BARMAN"
            ),
            self._two_windows(),
        )
        assert binding.bound
        assert binding.resolved_bounds == _bounds(0.7)

    def test_naming_a_window_that_does_not_have_it_fails_closed(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_DEFINITION.bind(
            ActivateVisibleControlAction(
                exact_label="ARRANGE", role="button", window="NOBODY"
            ),
            self._two_windows(),
        )
        assert not binding.bound

    def test_open_window_captions_are_reported_in_order(self) -> None:
        assert self._two_windows().open_window_captions() == ["HEP", "BARMAN"]

    def test_the_digest_reports_the_owning_window(self) -> None:
        digest = self._two_windows().visible_control_digest()
        assert {entry["window"] for entry in digest} == {"HEP", "BARMAN"}
        # Same label, different windows: neither is ambiguous within its own.
        assert not any(entry["ambiguous"] for entry in digest)


class TestPurchaseUsesExportedCellFacts:
    """Buying something visible must be one step, not hover-then-replan.

    Requiring a tooltip made sense while cells were opaque ordinals. Once the
    cell carries the game's own name and price, demanding a tooltip forced a
    hover, a replan and a second model call before every purchase — the agent
    spent whole runs inspecting instead of buying.
    """

    def _trade_state(self, controls: list[VisibleUIControl] | None = None) -> Observation:
        cell = VisibleUIControl(
            label="Bread",
            role="item",
            window="BARMAN",
            bounds=_bounds(0.5),
            item_name="Bread",
            item_base_value=52,
            item_quantity=3,
        )
        if controls is not None:
            cell_list = controls
        else:
            cell_list = [cell]
        cell_list = [
            *cell_list,
            VisibleUIControl(
                label="HEP",
                role="text",
                window="HEP",
                bounds=_bounds(0.2),
            ),
        ]
        seller = NearbyEntity(
            id="entity-barman",
            name="Barman",
            is_animal=False,
            has_dialogue=True,
            shop_inventory_owner=True,
            disposition=Disposition.NEUTRAL,
            distance=3.0,
            conscious=True,
        )
        state = observation(
            entities=[seller],
            controls=cell_list,
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": 1,
                        "ui": telemetry.ui.model_copy(
                            update={
                                "active_screen": "trade",
                                "open_inventory_windows": 2,
                                "selected_character_id": "entity-hep",
                                "selected_character_ids": ["entity-hep"],
                            }
                        ),
                    }
                )
            },
            deep=True,
        )





class TestAmbiguityMatchesTheBinder:
    """The digest's advice must not be stricter than the rule it describes."""

    def _cell(self, name: str, value: int, window: str = "HEP") -> VisibleUIControl:
        return VisibleUIControl(
            label=name,
            role="item",
            window=window,
            item_name=name,
            item_base_value=value,
            bounds=_bounds(0.5),
        )


    def test_same_name_at_different_prices_still_fails_closed(self) -> None:
        """Distinguishable duplicates are a real ambiguity: the price differs."""
        state = observation(
            controls=[self._cell("Tooth Pick", 809), self._cell("Tooth Pick", 390)],
            capabilities=["ui.visible_controls"],
        )
        assert all(entry["ambiguous"] for entry in state.visible_control_digest())


class TestPurchaseBindingCarriesCellFacts:
    """The binding is the executor's only view of what it is clicking.

    `bind_purchase_item` rebuilt its result from label, role and bounds alone,
    so the executor knew where to click and nothing about the item there. An
    unaffordable purchase could then only be discovered by attempting it and
    watching for a delta that never arrived, which is indistinguishable from a
    click that missed. Anything the cell knows has to survive the binding.
    """

    def _state(self) -> Observation:
        cell = VisibleUIControl(
            label="Bread",
            role="item",
            window="BARMAN",
            bounds=_bounds(0.5),
            item_name="Bread",
            item_base_value=52,
            item_sell_value=13,
            item_quantity=3,
        )
        seller = NearbyEntity(
            id="entity-barman",
            name="Barman",
            is_animal=False,
            has_dialogue=True,
            shop_inventory_owner=True,
            disposition=Disposition.NEUTRAL,
            distance=3.0,
            conscious=True,
        )
        player_window = VisibleUIControl(
            label="HEP",
            role="text",
            window="HEP",
            bounds=_bounds(0.2),
        )
        state = observation(
            entities=[seller],
            controls=[cell, player_window],
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": 1,
                        "ui": telemetry.ui.model_copy(
                            update={
                                "active_screen": "trade",
                                "open_inventory_windows": 2,
                                "selected_character_id": "entity-hep",
                                "selected_character_ids": ["entity-hep"],
                            }
                        ),
                    }
                )
            },
            deep=True,
        )

