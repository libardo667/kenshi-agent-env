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
    ApproachDialogueTargetAction,
    ControlMode,
    ExitCurrentBuildingAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
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
    TelemetrySnapshot,
    UIState,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    APPROACH_DIALOGUE_TARGET_DEFINITION,
    EXIT_CURRENT_BUILDING_DEFINITION,
    MOVE_IN_DIRECTION_DEFINITION,
    MOVE_TO_CHARACTER_DEFINITION,
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
    roster: list[CharacterState] | None = None,
    primary_character_id: str | None = None,
    selected_character_ids: list[str] | None = None,
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
            primary_character_id=primary_character_id,
            selected_character_ids=selected_character_ids or [],
            active_shop_trader_count=active_shop_trader_count,
            roster=roster or [],
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
            "roster.basic",
            "roster.health",
        ],
        roster=[actor, target],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
        ui=UIState(
            active_screen="world",
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
                    "roster": [
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
    actor = CharacterState(id="entity-bark", name="Bark")
    target = CharacterState(id="entity-plant", name="Plant")
    state = observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "roster.basic",
        ],
        roster=[actor, target],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
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
    """Every case here exercised a retired clicking operation."""






class TestExitCurrentBuildingBinding:
    def test_binds_only_one_selected_character_confirmed_indoors(self) -> None:
        state = observation(
            capabilities=[
                "control.exit_current_building",
                "identity.stable_handles",
                "roster.indoors",
            ],
            primary_character_id="entity-hep",
            selected_character_ids=["entity-hep"],
            ui=UIState(),
            roster=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
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
            primary_character_id="entity-hep",
            selected_character_ids=["entity-hep"],
            ui=UIState(),
            roster=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
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
        operator_capacity=2,
        current_operator_ids=[],
        current_operators_complete=True,
        output_inventory=[],
        output_inventory_complete=True,
        screen_position=screen_position,
    )


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
                "world.resource_operators",
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
        assert "engine capacity 2" in binding.reason
        assert "queued work are not operator acceptance" in binding.reason

    def test_production_withholds_without_operator_or_output_fidelity(self) -> None:
        action = ProduceResourceOutputAction(target_id="entity-copper")
        clear_ui = UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
        )
        without_capability = observation(
            ui=clear_ui,
            capabilities=[
                "control.produce_resource_output",
                "world.context_targets",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[natural_resource()],
        )
        incomplete_operators = natural_resource().model_copy(
            update={"current_operators_complete": False}
        )
        without_complete_operators = observation(
            ui=clear_ui,
            capabilities=[
                "control.produce_resource_output",
                "world.context_targets",
                "world.resource_operators",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[incomplete_operators],
        )
        incomplete_output = natural_resource().model_copy(
            update={"output_inventory_complete": False}
        )
        without_complete_output = observation(
            ui=clear_ui,
            capabilities=[
                "control.produce_resource_output",
                "world.context_targets",
                "world.resource_operators",
                "game.pause",
                "identity.stable_handles",
            ],
            world_targets=[incomplete_output],
        )

        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(
            action, without_capability
        ).bound
        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(
            action, without_complete_operators
        ).bound
        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(
            action, without_complete_output
        ).bound

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
                "world.resource_operators",
                "game.pause",
            ],
            world_targets=[natural_resource(), natural_resource()],
        )

        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, absent).bound
        assert not PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, unadvertised).bound
        ambiguous_binding = PRODUCE_RESOURCE_OUTPUT_DEFINITION.bind(action, ambiguous)
        assert not ambiguous_binding.bound
        assert "ambiguous" in ambiguous_binding.reason












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
            "roster.health",
        ],
        roster=[
            CharacterState(
                id="entity-selected",
                name="Streak",
            )
        ],
        primary_character_id="entity-selected",
        selected_character_ids=["entity-selected"],
        ui=UIState(),
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
        CharacterState(id="entity-bark", name="Bark"),
        CharacterState(id="entity-plant", name="Plant"),
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
            "roster.basic",
            "roster.health",
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
        roster=squad,
        primary_character_id="entity-bark",
        selected_character_ids=["entity-bark", "entity-plant"],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
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
            "roster.health",
        ],
        roster=[
            CharacterState(
                id="entity-selected",
                name="Streak",
            )
        ],
        primary_character_id="entity-selected",
        selected_character_ids=["entity-selected"],
        ui=UIState(),
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
            "roster.health",
        ],
        roster=[
            CharacterState(
                id="entity-selected",
                name="Streak",
            )
        ],
        primary_character_id="entity-selected",
        selected_character_ids=["entity-selected"],
        ui=UIState(),
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
            "roster.health",
        ],
        roster=[
            CharacterState(
                id="entity-primary-local",
                name="Kole",
            ),
            CharacterState(
                id="entity-remote-groupmate",
                name="Polly",
            ),
        ],
        primary_character_id="entity-primary-local",
        selected_character_ids=[
            "entity-primary-local",
            "entity-remote-groupmate",
        ],
        ui=UIState(),
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

    def test_threat_response_is_offered_only_for_a_grounded_safe_paused_threat(
        self,
    ) -> None:
        capabilities = [
            "game.pause",
            "game.speed",
            "control.move_in_direction",
            "nearby.visible_entities",
            "roster.health",
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
            roster=[
                CharacterState(
                    id="entity-bark",
                    name="Bark",
                    alive=True,
                    conscious=True,
                    blood=100.0,
                    in_combat=True,
                    position=Vec3(x=10.0, y=0.0, z=0.0),
                )
            ],
            primary_character_id="entity-bark",
            selected_character_ids=["entity-bark"],
            ui=UIState(),
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
                "roster.basic",
                "roster.inventory",
            ],
            roster=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
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
                "roster.basic",
                "roster.inventory",
            ],
            roster=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
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
