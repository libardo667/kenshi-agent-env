"""Reusable semantic actions and their authoritative contracts.

The point of these tests is reuse, not coverage of one scenario: the same
approach action must bind a vendor and a non-vendor identically, and the same
activation action must work for unrelated labels. Anything that would only pass
for the calibrated Barman chain is a regression of the whole milestone.
"""

from __future__ import annotations

from kenshi_agent.action_contracts import (
    ACTION_CONTRACTS,
    ACTIVATE_VISIBLE_CONTROL_CONTRACT,
    APPROACH_DIALOGUE_TARGET_CONTRACT,
    COMMAND_WORLD_TARGET_CONTRACT,
    EXIT_CURRENT_BUILDING_CONTRACT,
    MOVE_IN_DIRECTION_CONTRACT,
    MOVE_TO_CHARACTER_CONTRACT,
    OPEN_CONTEXT_INVENTORY_CONTRACT,
    PERFORM_CONTEXT_ACTION_CONTRACT,
    PRODUCE_RESOURCE_OUTPUT_CONTRACT,
    PURCHASE_ITEM_CONTRACT,
    ROTATE_CAMERA_CONTRACT,
    TRAVEL_TO_MAP_DESTINATION_CONTRACT,
    LegacyCompatibilityLedger,
    contract_for,
    planner_visible_contracts,
    translate_legacy_skill,
)
from kenshi_agent.models import (
    ACTION_ADAPTER,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CameraRotationDirection,
    CharacterState,
    ClickAction,
    CollectResourceOutputAction,
    CommandWorldTargetAction,
    Condition,
    ConditionKind,
    ConditionOperator,
    ContextActionKind,
    ControlMode,
    Disposition,
    ExitCurrentBuildingAction,
    GameState,
    IdempotencyPolicy,
    KnownMapDestination,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    OpenContextInventoryAction,
    PerformContextAction,
    PlanStep,
    PointerActionClass,
    ProduceResourceOutputAction,
    PurchaseItemAction,
    RotateCameraAction,
    SkillAction,
    SkillArgument,
    TelemetrySnapshot,
    TravelToMapDestinationAction,
    UIState,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
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
            ui=ui or UIState(visible_controls=controls),
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


class TestApproachBindsAnyDialogueTarget:
    """The whole point: approach is not a commerce affordance."""

    def test_contract_is_native_only_and_coordinate_independent(self) -> None:
        assert (
            APPROACH_DIALOGUE_TARGET_CONTRACT.pointer_class
            is PointerActionClass.COORDINATE_INDEPENDENT
        )
        assert APPROACH_DIALOGUE_TARGET_CONTRACT.risk.pointer_actions == 0
        assert APPROACH_DIALOGUE_TARGET_CONTRACT.risk.native_assisted_actions == 1

    def test_binds_a_vendor(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()]),
        )
        assert binding.bound
        assert binding.target_id == VENDOR_ID

    def test_binds_a_non_vendor_identically(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
            observation(entities=[civilian()]),
        )
        assert binding.bound
        assert binding.target_id == CIVILIAN_ID

    def test_rejects_a_target_absent_from_current_state(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
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
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id="entity-bandit"),
            observation(entities=[hostile]),
        )
        assert not binding.bound

    def test_stale_telemetry_cannot_bind(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()], stale=True),
        )
        assert not binding.bound
        assert "stale" in binding.reason

    def test_rejects_approach_while_dialogue_with_another_target_is_open(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
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
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
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
    def test_binds_two_unrelated_labels_with_the_same_action(self) -> None:
        controls = [
            VisibleUIControl(label="Show me your goods.", role="button", bounds=_bounds(0.5)),
            VisibleUIControl(label="Goodbye.", role="button", bounds=_bounds(0.6)),
        ]
        state = observation(controls=controls, capabilities=["ui.visible_controls"])

        first = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
            state,
        )
        second = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound

    def test_missing_capability_is_unknown_not_absent(self) -> None:
        state = observation(controls=None, capabilities=[])
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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

        binding = EXIT_CURRENT_BUILDING_CONTRACT.bind(
            ExitCurrentBuildingAction(),
            state,
        )

        assert binding.bound
        assert binding.resolved_label == "Hep"
        assert EXIT_CURRENT_BUILDING_CONTRACT.pointer_class is (
            PointerActionClass.COORDINATE_INDEPENDENT
        )
        assert EXIT_CURRENT_BUILDING_CONTRACT.reference_fields == ()
        assert EXIT_CURRENT_BUILDING_CONTRACT.controller_verified

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

        binding = EXIT_CURRENT_BUILDING_CONTRACT.bind(
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

        binding = PERFORM_CONTEXT_ACTION_CONTRACT.bind(
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

        binding = PERFORM_CONTEXT_ACTION_CONTRACT.bind(
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

        binding = PERFORM_CONTEXT_ACTION_CONTRACT.bind(
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
                modal_open=None,
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

        binding = PERFORM_CONTEXT_ACTION_CONTRACT.bind(
            PerformContextAction(
                target_id=target.id,
                context_action=ContextActionKind.OPERATE,
            ),
            state,
        )
        digest_kinds = {entry["kind"] for entry in state.semantic_action_digest()}

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

        absent = COMMAND_WORLD_TARGET_CONTRACT.bind(action, missing_geometry)
        ambiguous = COMMAND_WORLD_TARGET_CONTRACT.bind(action, duplicate)

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

        binding = PRODUCE_RESOURCE_OUTPUT_CONTRACT.bind(
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

        assert not PRODUCE_RESOURCE_OUTPUT_CONTRACT.bind(action, absent).bound
        assert not PRODUCE_RESOURCE_OUTPUT_CONTRACT.bind(action, unadvertised).bound
        ambiguous_binding = PRODUCE_RESOURCE_OUTPUT_CONTRACT.bind(action, ambiguous)
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

        assert OPEN_CONTEXT_INVENTORY_CONTRACT.bind(action, clear).bound
        assert not OPEN_CONTEXT_INVENTORY_CONTRACT.bind(action, blocked).bound


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
    ) -> Observation:
        target = natural_resource()
        visible_controls = [
            VisibleUIControl(
                label="Raw Iron",
                window="COPPER RESOURCE",
                role="item",
                item_name="Raw Iron",
                item_quantity=source_quantity,
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

    def test_binds_exact_output_cell_to_exact_context_target(self) -> None:
        binding = ACTION_CONTRACTS["collect_resource_output"].bind(
            self._action(),
            self._state(),
        )

        assert binding.bound
        assert binding.target_id == "entity-copper"
        assert binding.item_name == "Raw Iron"
        assert binding.resolved_bounds == _bounds(0.5)

    def test_requires_the_selected_characters_open_destination_inventory(self) -> None:
        contract = ACTION_CONTRACTS["collect_resource_output"]

        binding = contract.bind(
            self._action(),
            self._state(player_inventory_open=False),
        )

        assert not binding.bound
        assert "selected character" in binding.reason
        assert "inventory" in binding.reason

    def test_loaded_shop_traders_never_override_exact_resource_window_owners(
        self,
    ) -> None:
        contract = ACTION_CONTRACTS["collect_resource_output"]

        for loaded_shop_traders in range(257):
            binding = contract.bind(
                self._action(),
                self._state(
                    active_screen="trade",
                    active_shop_trader_count=loaded_shop_traders,
                ),
            )

            assert binding.bound, binding.reason

    def test_rejects_wrong_target_section_and_quantity(self) -> None:
        contract = ACTION_CONTRACTS["collect_resource_output"]
        wrong_target = self._state(context_target_id="entity-other")

        assert not contract.bind(self._action(), wrong_target).bound
        assert "collect_resource_output" not in {
            entry["kind"] for entry in wrong_target.semantic_action_digest()
        }
        assert not contract.bind(
            self._action(), self._state(section="main")
        ).bound
        assert not contract.bind(
            self._action(source_quantity=2),
            self._state(source_quantity=1),
        ).bound

    def test_a_third_inventory_owner_fails_closed(self) -> None:
        state = self._state(active_shop_trader_count=2)
        assert state.telemetry is not None
        controls = list(state.telemetry.ui.visible_controls or [])
        controls.append(
            VisibleUIControl(
                label="Dried Meat",
                window="ZU",
                role="item",
                item_name="Dried Meat",
                item_quantity=5,
                section="main",
                bounds=_bounds(0.9),
            )
        )
        unexplained_window = state.model_copy(
            update={
                "telemetry": state.telemetry.model_copy(
                    update={
                        "ui": state.telemetry.ui.model_copy(
                            update={
                                "open_inventory_windows": 3,
                                "visible_controls": controls,
                            }
                        )
                    }
                )
            },
            deep=True,
        )

        binding = ACTION_CONTRACTS["collect_resource_output"].bind(
            self._action(),
            unexplained_window,
        )

        assert not binding.bound
        assert "exactly two inventory windows" in binding.reason

    def test_rejects_incomplete_source_or_destination_observation(self) -> None:
        state = self._state()
        assert state.telemetry is not None
        incomplete_controls = state.model_copy(
            update={
                "telemetry": state.telemetry.model_copy(
                    update={
                        "ui": state.telemetry.ui.model_copy(
                            update={"visible_controls_complete": False}
                        )
                    }
                )
            },
            deep=True,
        )
        incomplete_inventory = state.model_copy(
            update={
                "telemetry": state.telemetry.model_copy(
                    update={
                        "squad": [
                            state.telemetry.squad[0].model_copy(
                                update={"inventory_complete": False}
                            )
                        ]
                    }
                )
            },
            deep=True,
        )

        contract = ACTION_CONTRACTS["collect_resource_output"]
        assert not contract.bind(self._action(), incomplete_controls).bound
        assert not contract.bind(self._action(), incomplete_inventory).bound


class TestContractCatalog:
    def test_monitored_movement_terminals_own_their_success_verdicts(self) -> None:
        for contract in (
            APPROACH_DIALOGUE_TARGET_CONTRACT,
            MOVE_TO_CHARACTER_CONTRACT,
            MOVE_IN_DIRECTION_CONTRACT,
            TRAVEL_TO_MAP_DESTINATION_CONTRACT,
            EXIT_CURRENT_BUILDING_CONTRACT,
        ):
            assert contract.controller_verified

    def test_contracts_are_registered_by_kind(self) -> None:
        assert set(ACTION_CONTRACTS) == {
            "approach_dialogue_target",
            "command_world_target",
            "move_to_character",
            "move_in_direction",
        "open_screen",
            "travel_to_map_destination",
            "exit_current_building",
            "perform_context_action",
            "produce_resource_output",
            "harvest_resource",
            "open_context_inventory",
            "collect_resource_output",
            "activate_visible_control",
            "dismiss_screen",
            "purchase_item",
            "rotate_camera",
            "select_squad_member",
            "use_game_binding",
            "scroll_screen",
            "sell_item",
            "equip_item",
            "recover_camera_view",
        }
        assert contract_for(ApproachDialogueTargetAction(target_id=VENDOR_ID)) is (
            APPROACH_DIALOGUE_TARGET_CONTRACT
        )
        assert contract_for(
            CommandWorldTargetAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            )
        ) is COMMAND_WORLD_TARGET_CONTRACT
        assert contract_for(
            RotateCameraAction(direction=CameraRotationDirection.RIGHT)
        ) is ROTATE_CAMERA_CONTRACT
        assert contract_for(
            PerformContextAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            )
        ) is PERFORM_CONTEXT_ACTION_CONTRACT
        assert contract_for(ClickAction(x=0.5, y=0.5)) is None

    def test_approach_is_withheld_from_interface_only(self) -> None:
        visible = planner_visible_contracts(
            control_mode=ControlMode.INTERFACE_ONLY,
            capabilities=set(APPROACH_CAPABILITIES) | {"ui.visible_controls"},
        )
        kinds = [contract.kind for contract in visible]
        assert "approach_dialogue_target" not in kinds
        assert "activate_visible_control" in kinds

    def test_missing_capability_withholds_an_action(self) -> None:
        visible = planner_visible_contracts(
            control_mode=ControlMode.NATIVE_ASSISTED,
            capabilities={"ui.visible_controls"},
        )
        kinds = [contract.kind for contract in visible]
        assert "activate_visible_control" in kinds
        # Purchase needs the tooltip capability, which is absent here.
        assert "purchase_item" not in kinds

    def test_legacy_capability_alias_still_satisfies_the_contract(self) -> None:
        """The installed plug-in emits the vendor-named capability."""

        assert not APPROACH_DIALOGUE_TARGET_CONTRACT.missing_capabilities(
            set(APPROACH_CAPABILITIES)
        )

    def test_generic_capability_name_also_satisfies_the_contract(self) -> None:
        capabilities = {
            "control.approach_dialogue_target",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        assert not APPROACH_DIALOGUE_TARGET_CONTRACT.missing_capabilities(capabilities)


def test_exact_known_map_destination_has_one_controller_owned_travel_contract() -> None:
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

    contract = contract_for(action)

    assert contract is not None
    assert contract.controller_verified
    assert contract.bind(action, state).bound
    missing = action.model_copy(update={"destination_id": "entity-undiscovered-town"})
    assert not contract.bind(missing, state).bound


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

    contract = contract_for(action)

    assert contract is not None
    binding = contract.bind(action, state)
    assert not binding.bound
    assert "already local" in binding.reason
    assert "travel_to_map_destination" not in {
        item.kind
        for item in planner_visible_contracts(
            control_mode=ControlMode.NATIVE_ASSISTED,
            capabilities=set(state.telemetry.capabilities),
            observation=state,
        )
    }


def test_map_travel_cannot_bind_the_exact_current_town_after_gate_entry() -> None:
    state = observation(
        capabilities=[
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "game.location",
            "game.location.identity",
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

    contract = contract_for(action)

    assert contract is not None
    binding = contract.bind(action, state)
    assert not binding.bound
    assert "already inside" in binding.reason
    assert "travel_to_map_destination" not in {
        item.kind
        for item in planner_visible_contracts(
            control_mode=ControlMode.NATIVE_ASSISTED,
            capabilities=set(state.telemetry.capabilities),
            observation=state,
        )
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


class TestLegacyCompatibilityAdapter:
    def test_translates_the_vendor_macro_and_counts_it(self) -> None:
        ledger = LegacyCompatibilityLedger()
        action = translate_legacy_skill(
            SkillAction(
                name="approach_confirmed_vendor",
                args=[SkillArgument(name="target_id", value=VENDOR_ID)],
            ),
            ledger=ledger,
        )
        assert isinstance(action, ApproachDialogueTargetAction)
        assert action.target_id == VENDOR_ID
        assert ledger.summary() == {"approach_confirmed_vendor": 1}

    def test_translates_the_calibrated_dialogue_macro(self) -> None:
        ledger = LegacyCompatibilityLedger()
        action = translate_legacy_skill(SkillAction(name="choose_show_goods"), ledger=ledger)
        assert isinstance(action, ActivateVisibleControlAction)
        assert action.exact_label == "Show me your goods."
        assert ledger.total == 1

    def test_untranslatable_macro_is_left_alone(self) -> None:
        ledger = LegacyCompatibilityLedger()
        assert translate_legacy_skill(SkillAction(name="eat_food"), ledger=ledger) is None
        assert ledger.total == 0


class TestSemanticActionsAreAdvertised:
    def test_digest_reports_available_actions_and_argument_sources(self) -> None:
        state = observation(
            entities=[vendor()],
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
            capabilities=[*APPROACH_CAPABILITIES, "ui.visible_controls"],
        )
        digest = state.semantic_action_digest()
        kinds = {entry["kind"] for entry in digest}
        assert {"approach_dialogue_target", "activate_visible_control", "dismiss_screen"} <= kinds
        assert all(entry["argument_source"] for entry in digest)

    def test_modal_withholds_blocked_world_action_but_keeps_recovery(self) -> None:
        target = WorldTarget(
            id="entity-copper",
            name="Copper Resource",
            kind="natural_resource",
            position=Vec3(x=1.0, y=0.0, z=2.0),
            distance=40.0,
            context_actions=[ContextActionKind.OPERATE],
            default_task="operate_machinery",
        )
        capabilities = [
            "control.produce_resource_output",
            "control.open_context_inventory",
            "world.context_targets",
            "ui.context_inventory_target",
            "ui.visible_controls",
            "game.pause",
            "game.speed",
            "squad.basic",
            "squad.health",
            "squad.inventory",
            "ui.inventory",
            "identity.stable_handles",
        ]
        selected = CharacterState(
            id="entity-bark",
            name="Bark",
            selected=True,
            alive=True,
            conscious=True,
            down=False,
            in_combat=False,
            inventory_complete=True,
        )
        world = observation(
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id=selected.id,
                selected_character_ids=[selected.id],
            ),
            capabilities=capabilities,
            squad=[selected],
            world_targets=[target],
        )
        inventory = observation(
            ui=UIState(
                active_screen="inventory",
                modal_open=True,
                dialogue_open=False,
                selected_character_id=selected.id,
                selected_character_ids=[selected.id],
            ),
            capabilities=capabilities,
            squad=[selected],
            world_targets=[target],
        )

        world_kinds = {entry["kind"] for entry in world.semantic_action_digest()}
        inventory_kinds = {
            entry["kind"] for entry in inventory.semantic_action_digest()
        }

        assert "harvest_resource" in world_kinds
        assert "harvest_resource" not in inventory_kinds
        assert "produce_resource_output" not in world_kinds
        assert "open_context_inventory" not in world_kinds
        assert "collect_resource_output" not in world_kinds
        assert "perform_context_action" not in world_kinds
        assert "dismiss_screen" in inventory_kinds

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


class TestLegacyPlanTranslation:
    def test_a_legacy_macro_plan_enters_as_semantic_actions(self) -> None:
        from kenshi_agent.action_contracts import translate_legacy_plan_actions
        from kenshi_agent.models import (
            Condition,
            ConditionKind,
            ConditionOperator,
            ConditionPath,
            PlanEnvelope,
            PlanStep,
            RiskBudget,
        )

        fresh = Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        dialogue = Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_DIALOGUE_OPEN,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        legacy = PlanEnvelope(
            schema_version="1.0",
            plan_id="legacy-food",
            objective="Legacy calibrated chain.",
            control_mode=ControlMode.NATIVE_ASSISTED,
            based_on_revision=WorldStateRevision(telemetry_sequence=5, capability_epoch=1),
            assumptions=[fresh],
            steps=[
                PlanStep(
                    step_id="approach",
                    action=SkillAction(
                        name="approach_confirmed_vendor",
                        args=[SkillArgument(name="target_id", value=VENDOR_ID)],
                    ),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                    on_success="goods",
                ),
                PlanStep(
                    step_id="goods",
                    action=SkillAction(name="choose_show_goods"),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                    on_success="unrelated",
                ),
                PlanStep(
                    step_id="unrelated",
                    action=SkillAction(name="eat_food"),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                ),
            ],
            entry_step_id="approach",
            max_actions=4,
            max_wall_seconds=60.0,
            max_game_seconds=120.0,
            risk_budget=RiskBudget(
                max_pointer_actions=2,
                max_purchase_actions=0,
                max_native_assisted_actions=1,
            ),
        )

        ledger = LegacyCompatibilityLedger()
        translated, counts = translate_legacy_plan_actions(legacy, ledger=ledger)

        kinds = [step.action.kind for step in translated.steps]
        assert kinds == ["approach_dialogue_target", "activate_visible_control", "skill"]
        assert counts == {"approach_confirmed_vendor": 1, "choose_show_goods": 1}
        # An untranslatable macro is left exactly as it was.
        assert translated.steps[2].action == legacy.steps[2].action
        assert ledger.total == 2

    def test_a_plan_with_no_legacy_macros_is_returned_unchanged(self) -> None:
        from kenshi_agent.action_contracts import translate_legacy_plan_actions
        from kenshi_agent.models import (
            Condition,
            ConditionKind,
            ConditionOperator,
            ConditionPath,
            PlanEnvelope,
            PlanStep,
            RiskBudget,
        )

        fresh = Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        plan = PlanEnvelope(
            schema_version="1.0",
            plan_id="already-generic",
            objective="Already reusable.",
            control_mode=ControlMode.NATIVE_ASSISTED,
            based_on_revision=WorldStateRevision(telemetry_sequence=5, capability_epoch=1),
            assumptions=[fresh],
            steps=[
                PlanStep(
                    step_id="approach",
                    action=ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    preconditions=[fresh],
                    success_conditions=[
                        Condition(
                            kind=ConditionKind.FIELD,
                            path=ConditionPath.TELEMETRY_UI_DIALOGUE_OPEN,
                            operator=ConditionOperator.EQUALS,
                            expected=True,
                            max_age_seconds=3.0,
                        )
                    ],
                    timeout_seconds=30.0,
                )
            ],
            entry_step_id="approach",
            max_actions=2,
            max_wall_seconds=60.0,
            max_game_seconds=120.0,
            risk_budget=RiskBudget(
                max_pointer_actions=0,
                max_purchase_actions=0,
                max_native_assisted_actions=1,
            ),
        )
        translated, counts = translate_legacy_plan_actions(plan, ledger=LegacyCompatibilityLedger())
        assert translated is plan
        assert counts == {}


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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="item_0", role="button"),
            state,
        )
        assert not binding.bound

    def test_absent_cell_fails_closed(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
            capabilities=["ui.visible_controls", "ui.tooltip", "nearby.shop_owners"],
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

    def test_a_purchase_matching_its_own_tooltip_binds(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state())
        assert binding.bound, binding.reason
        assert binding.target_id == self.SELLER
        assert binding.resolved_bounds == _bounds(0.5)

    def test_a_tooltip_describing_another_widget_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(), self._state(tooltip_over_cell=False)
        )
        assert not binding.bound
        assert "does not belong to cell" in binding.reason

    def test_a_wrong_item_name_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(item_name="Ancient Katana"), self._state()
        )
        assert not binding.bound
        assert "does not name" in binding.reason

    def test_a_wrong_price_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(expected_price=5), self._state())
        assert not binding.bound
        assert "does not show price" in binding.reason

    def test_a_price_that_is_only_a_substring_is_refused(self) -> None:
        """c.52 must not satisfy a claim of c.5."""

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(expected_price=5),
            self._state(tooltip="Dried Meat\n[Food]\nValue c.52"),
        )
        assert not binding.bound

    def test_no_visible_tooltip_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(), self._state(tooltip_visible=False)
        )
        assert not binding.bound
        assert "hover the cell first" in binding.reason

    def test_a_seller_who_is_not_the_active_shop_owner_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state(shop_owner=False))
        assert not binding.bound
        assert "verified non-hostile shop owner" in binding.reason

    def test_a_cell_outside_the_sellers_window_is_refused(self) -> None:
        """Ownership is the cell's window, not a count of traders in the world.

        `active_shop_trader_count` is a registry of shop traders loaded in the
        world - it reads 5 in a bar with nothing open - so gating on it being
        exactly 1 made this action unbindable everywhere, which is why the agent
        could open a shop and never buy. What proves the item is the shop's is
        that the cell sits in the shop's own inventory window.
        """

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(cell_label="item_7", window="HEP"), self._state()
        )
        assert not binding.bound
        assert "not the seller's own inventory" in binding.reason

    def test_many_traders_in_the_world_do_not_block_a_purchase(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state(traders=5))
        assert binding.bound, binding.reason

    def test_the_window_caption_matches_the_seller_case_insensitively(self) -> None:
        """Kenshi captions the window "BARMAN" while the character is "Barman"."""

        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state())
        assert binding.bound, binding.reason

    def test_an_absent_cell_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(cell_label="item_99"), self._state()
        )
        assert not binding.bound
        assert "No current item cell" in binding.reason

    def test_purchase_is_at_most_once_and_costs_a_purchase_budget(self) -> None:
        assert PURCHASE_ITEM_CONTRACT.idempotency is IdempotencyPolicy.AT_MOST_ONCE
        assert PURCHASE_ITEM_CONTRACT.risk.purchase_actions == 1
        action = self._action(quantity=3)
        assert PURCHASE_ITEM_CONTRACT.risk_for(action).as_tuple() == (3, 3, 0)
        assert PURCHASE_ITEM_CONTRACT.primitive_action_bound_for(action) == 6

    def test_purchase_says_nothing_about_what_kind_of_item_is_worth_buying(self) -> None:
        """Task intent lives in config, not in the purchase contract."""

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(item_name="Ancient Katana", expected_price=865),
            self._state(tooltip="Ancient Katana\n[Weapon]\nValue c.865"),
        )
        assert binding.bound, binding.reason


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
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="ARRANGE", role="button"),
            self._two_windows(),
        )
        assert not binding.bound
        assert "Name the window" in binding.reason

    def test_naming_the_window_resolves_it(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(
                exact_label="ARRANGE", role="button", window="BARMAN"
            ),
            self._two_windows(),
        )
        assert binding.bound
        assert binding.resolved_bounds == _bounds(0.7)

    def test_naming_a_window_that_does_not_have_it_fails_closed(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
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
            capabilities=["ui.visible_controls", "ui.tooltip", "nearby.shop_owners"],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": 1,
                        "ui": telemetry.ui.model_copy(update={"active_screen": "trade"}),
                    }
                )
            },
            deep=True,
        )

    def test_a_named_cell_needs_no_tooltip(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Bread",
                expected_price=52,
                window="BARMAN",
            seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert binding.bound, binding.reason

    def test_a_price_that_disagrees_with_the_cell_is_refused_with_the_real_one(
        self,
    ) -> None:
        """The cell states the charge, so a disagreeing price is now a defect.

        This assertion used to run the other way, on the reasoning that a
        trader applies its own multiplier and "the asking price is never
        exported". The export was simply of the wrong side of the trade: the
        sell value, what the trader pays out. `item_base_value` is the charge,
        live-confirmed against a debit, so a mismatch means the plan is
        reasoning about money the game never quoted.

        The refusal has to name the real price. A plan told only that its
        number is wrong can do nothing but guess a second one.
        """

        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Bread",
                expected_price=5,
                window="BARMAN",
                seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert not binding.bound
        assert "costs 52" in binding.reason
        assert "declare expected_price 52" in binding.reason

    def test_a_mispriced_purchase_is_not_reported_as_a_missing_cell(self) -> None:
        """The failure this must never regress to.

        Narrowing candidates by price once refused outright when nothing
        matched, so a wrong `expected_price` came back as "no current item cell
        matches" - which sent the agent hunting a c.38 Dried Meat it was
        looking straight at. Narrowing stays permissive; only the explicit
        price check rejects, and it says what is actually wrong.

        Needs two same-named cells or the narrowing branch never runs at all,
        which is what made an earlier version of this test unable to fail for
        its own stated reason.
        """

        duplicates = [
            VisibleUIControl(
                label="Tooth Pick",
                role="item",
                window="BARMAN",
                bounds=_bounds(offset),
                item_name="Tooth Pick",
                item_base_value=390,
                item_quantity=1,
            )
            for offset in (0.4, 0.5)
        ]

        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Tooth Pick",
                item_name="Tooth Pick",
                expected_price=5,
                window="BARMAN",
                seller_id="entity-barman",
            ),
            self._trade_state(duplicates),
        )
        assert not binding.bound
        assert "No current item cell matches" not in binding.reason
        assert "costs 390" in binding.reason

    def test_a_name_that_disagrees_with_the_cell_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Ancient Katana",
                expected_price=52,
                window="BARMAN",
            seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert not binding.bound
        assert "holds 'Bread'" in binding.reason


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

    def test_a_stack_of_identical_items_is_not_ambiguous(self) -> None:
        """Two Greenfruit are two Greenfruit; either will do.

        The binder already resolves interchangeable cells, but the digest
        counted bare labels and flagged both. Since the prompt forbids
        authoring an ambiguous entry, a stack of anything became unsellable and
        the agent refused its own duplicate stock on our own advice.
        """
        state = observation(
            controls=[self._cell("Greenfruit", 22), self._cell("Greenfruit", 22)],
            capabilities=["ui.visible_controls"],
        )
        entries = state.visible_control_digest()
        assert entries and not any(entry["ambiguous"] for entry in entries)

        from kenshi_agent.action_contracts import SELL_ITEM_CONTRACT
        from kenshi_agent.models import SellItemAction

        binding = SELL_ITEM_CONTRACT.bind(
            SellItemAction(
                cell_label="Greenfruit",
                item_name="Greenfruit",
                window="HEP",
                buyer_id=VENDOR_ID,
            ),
            state,
        )
        # Selling needs more than an unambiguous cell - a selected owner, a
        # buyer - so this does not assert it binds. It asserts the two agree
        # about ambiguity, which is the thing that disagreed.
        assert "ambiguous" not in binding.reason, (
            f"the binder called interchangeable stock ambiguous: {binding.reason}"
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
        state = observation(
            entities=[seller],
            controls=[cell],
            capabilities=["ui.visible_controls", "ui.tooltip", "nearby.shop_owners"],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": 1,
                        "ui": telemetry.ui.model_copy(update={"active_screen": "trade"}),
                    }
                )
            },
            deep=True,
        )

    def test_every_cell_fact_survives_the_binding(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Bread",
                expected_price=52,
                window="BARMAN",
                seller_id="entity-barman",
            ),
            self._state(),
        )
        assert binding.bound, binding.reason
        assert binding.item_name == "Bread"
        assert binding.item_base_value == 52
        assert binding.item_sell_value == 13
        assert binding.item_quantity == 3
