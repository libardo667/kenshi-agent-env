"""Whole-denominator invariants for the unified affordance contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

import kenshi_agent.affordances as affordance_module
from kenshi_agent.affordances import (
    AFFORDANCE_ADAPTERS,
    OPAQUE_CHARACTER_SELECTION_GAME_BINDINGS,
    OPERATION_BINDING_AUTHORITY,
    SEMANTICALLY_ADAPTED_GAME_BINDINGS,
    AffordanceSource,
    bind_affordance,
    bound_affordance,
    offered_affordances,
    selection_for,
    terminal_affordance_receipt,
)
from kenshi_agent.core.affordance import AffordanceLifecycleStatus
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    TIME_GAME_BINDINGS,
    ControlMode,
    GameBinding,
    GameScreen,
    UseGameBindingAction,
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
    is_runtime_owned_visible_control,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    APPROACH_DIALOGUE_TARGET_DEFINITION,
    USE_GAME_BINDING_DEFINITION,
    BoundActor,
    BoundOperation,
    TerminalOwner,
)


def _bounds(row: int) -> NormalizedPointerBounds:
    y = row / 20
    return NormalizedPointerBounds(min_x=0.1, max_x=0.3, min_y=y, max_y=y + 0.03)


def _observation(
    *,
    capabilities: list[str],
    ui: UIState | None = None,
    squad: list[CharacterState] | None = None,
    nearby: list[NearbyEntity] | None = None,
    targets: list[WorldTarget] | None = None,
    stale: bool = False,
    active_shop_trader_count: int = 0,
    money: int | None = None,
) -> Observation:
    effective_ui = ui or UIState()
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
        run_id="affordance-test",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=41, capability_epoch=2),
        telemetry=TelemetrySnapshot(
            sequence=41,
            identity_session_id="session-affordance-test",
            capabilities=capabilities,
            game=GameState(
                loaded=True,
                paused=True,
                speed_multiplier=1.0,
                money=money,
            ),
            ui=effective_ui,
            squad=squad or [],
            nearby_entities=nearby or [],
            world_targets=targets or [],
            active_shop_trader_count=active_shop_trader_count,
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=0.1,
    )


def test_named_binding_adapter_covers_its_whole_current_denominator() -> None:
    observation = _observation(
        capabilities=[
            "camera.position",
            "game.loaded",
            "game.pause",
            "game.speed",
            "host.quicksave_completion",
            "ui.management_screen",
            "ui.stats_window",
            "ui.visible_controls",
        ]
    )
    telemetry = observation.telemetry
    assert telemetry is not None
    expected = set()
    for binding in GameBinding:
        if binding in TIME_GAME_BINDINGS or binding in SEMANTICALLY_ADAPTED_GAME_BINDINGS:
            continue
        action = UseGameBindingAction(
            binding=binding,
            expected_effect=binding.value,
        )
        completion = USE_GAME_BINDING_DEFINITION.resolve_terminal(
            action,
            observation,
            selected_affordance=True,
        )
        if (
            USE_GAME_BINDING_DEFINITION.bind(action, observation).bound
            and not (
            completion.owner is TerminalOwner.RUNTIME_CONDITIONS
                and not completion.conditions
            )
        ):
            expected.add(binding.value)
    actual = {
        offer.semantic
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.GAME_BINDING
    }
    assert actual == expected


def test_exact_identity_selection_subsumes_opaque_character_bindings() -> None:
    bark = CharacterState(id="bark", name="Bark", selected=True)
    plant = CharacterState(id="plant", name="Plant", selected=False)
    base_ui = UIState(
        active_screen="world",
        dialogue_open=False,
        modal_open=False,
        selected_character_id=bark.id,
        selected_character_ids=[bark.id],
    )
    observation = _observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "squad.basic",
        ],
        ui=base_ui,
        squad=[bark, plant],
    )

    offers = offered_affordances(observation)
    semantics = {offer.semantic for offer in offers}
    assert not {
        binding.value for binding in OPAQUE_CHARACTER_SELECTION_GAME_BINDINGS
    } & semantics
    assert "select_all" not in semantics
    whole_party = next(offer for offer in offers if offer.semantic == "select_whole_party")
    assert whole_party.operation_kind == "use_game_binding"
    assert "Bark" in whole_party.description
    assert "Plant" in whole_party.description
    assert "complete current party" in whole_party.description
    singular = next(
        offer
        for offer in offers
        if offer.semantic == "select_only"
        and offer.target is not None
        and offer.target.target_id == plant.id
    )
    assert "only" in singular.description
    assert "deselecting every other" in singular.description

    # A modal suppresses exact selection itself, but must not resurrect the
    # opaque key bindings as a guard bypass. The next choice should close the UI.
    modal = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={"ui": base_ui.model_copy(update={"modal_open": True})}
            )
        },
        deep=True,
    )
    modal_semantics = {offer.semantic for offer in offered_affordances(modal)}
    assert "select_only" not in modal_semantics
    assert "select_whole_party" not in modal_semantics
    assert not {
        binding.value for binding in OPAQUE_CHARACTER_SELECTION_GAME_BINDINGS
    } & modal_semantics
    assert "select_all" not in modal_semantics

    all_selected = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "ui": base_ui.model_copy(
                        update={"selected_character_ids": [bark.id, plant.id]}
                    ),
                    "squad": [bark, plant.model_copy(update={"selected": True})],
                }
            )
        },
        deep=True,
    )
    assert "select_whole_party" not in {
        offer.semantic for offer in offered_affordances(all_selected)
    }


def test_ui_adapter_offers_only_controls_with_current_semantic_authority() -> None:
    controls = [
        VisibleUIControl(label="Accept", role="button", window="Talk", bounds=_bounds(1)),
        VisibleUIControl(
            label="1. Show me your goods.",
            role="text",
            window="Talk",
            bounds=_bounds(2),
        ),
        VisibleUIControl(label="A warning", role="text", window="Talk", bounds=_bounds(3)),
        VisibleUIControl(
            label="cell 3",
            role="item",
            window="SHOP",
            bounds=_bounds(4),
            item_name="Bread",
            item_base_value=31,
        ),
        VisibleUIControl(
            label="pause",
            role="button",
            window="HUD",
            widget_name="HUDRoot/TimeControls/Pause",
            bounds=_bounds(5),
        ),
    ]
    observation = _observation(
        capabilities=["ui.visible_controls"],
        ui=UIState(
            dialogue_open=True,
            dialogue_options=["1. Show me your goods."],
            visible_controls=controls,
        ),
    )
    expected = {
        (control.window, control.role, control.label)
        for control in controls
        if not is_runtime_owned_visible_control(control)
        and (
            control.role == "button"
            or control.label == "1. Show me your goods."
        )
    }
    actual = {
        (
            str(offer.operation_arguments["window"]),
            str(offer.operation_arguments["role"]),
            str(offer.operation_arguments["exact_label"]),
        )
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.DIALOGUE
        and offer.operation_kind == "activate_visible_control"
    }
    assert actual == expected


def test_screen_adapter_exposes_state_intent_not_toggle_mechanics() -> None:
    observation = _observation(
        capabilities=[
            "game.loaded",
            "ui.inventory",
            "ui.management_screen",
            "ui.stats_window",
            "ui.visible_controls",
        ],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            open_inventory_windows=0,
            stats_window_open=False,
            management_tab=-1,
            visible_controls=[],
        ),
    )
    offers = offered_affordances(observation)
    assert {
        offer.semantic for offer in offers if offer.operation_kind == "open_screen"
    } == {f"open_{screen.value}" for screen in GameScreen}
    assert not any(
        offer.semantic.startswith("toggle_")
        and offer.semantic in {
            "toggle_inventory",
            "toggle_stats",
            "toggle_map",
            "toggle_research",
            "toggle_crafting",
        }
        for offer in offers
    )


def test_character_adapter_exposes_one_runtime_chosen_squad_reunion() -> None:
    bark = CharacterState(
        id="entity-bark",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
        position=Vec3(x=0, y=0, z=0),
    )
    plant = CharacterState(
        id="entity-plant",
        name="Plant",
        alive=True,
        position=Vec3(x=80, y=0, z=0),
    )
    ruka = CharacterState(
        id="entity-ruka",
        name="Ruka",
        alive=True,
        position=Vec3(x=300, y=0, z=0),
    )
    observation = _observation(
        capabilities=[
            "control.regroup_with_squad_member",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.basic",
            "squad.health",
        ],
        squad=[bark, ruka, plant],
        ui=UIState(
            active_screen="world",
            selected_character_id=bark.id,
            selected_character_ids=[bark.id],
        ),
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "regroup_with_squad_member"
    ]

    assert len(offers) == 1
    assert offers[0].semantic == "reunite_squad"
    assert offers[0].target is not None
    assert offers[0].target.target_id == plant.id
    assert offers[0].operation_arguments == {
        "actor_id": bark.id,
        "target_id": plant.id,
    }


def test_character_adapter_prefers_exact_native_selection_over_portrait_geometry() -> None:
    bark = CharacterState(id="entity-bark", name="Bark", selected=True)
    plant = CharacterState(id="entity-plant", name="Plant")
    observation = _observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "squad.basic",
        ],
        squad=[bark, plant],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id=bark.id,
            selected_character_ids=[bark.id],
            visible_controls=[],
        ),
    )

    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == "select_only"
    )

    assert offer.operation_kind == "select_squad_member_exact"
    assert offer.target is not None
    assert offer.target.target_id == plant.id


def test_character_adapter_retains_exact_native_selection_from_a_group() -> None:
    bark = CharacterState(id="entity-bark", name="Bark", selected=True)
    plant = CharacterState(id="entity-plant", name="Plant", selected=True)
    observation = _observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "squad.basic",
        ],
        squad=[bark, plant],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id=bark.id,
            selected_character_ids=[bark.id, plant.id],
            visible_controls=[],
        ),
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == "select_only"
    ]

    assert len(offers) == 2
    assert {
        offer.operation_kind for offer in offers
    } == {"select_squad_member_exact"}
    assert {
        offer.target.target_id
        for offer in offers
        if offer.target is not None
    } == {bark.id, plant.id}


def test_group_selection_can_issue_broadcast_orders_and_still_narrow() -> None:
    bark = CharacterState(id="entity-bark", name="Bark", selected=True)
    plant = CharacterState(id="entity-plant", name="Plant", selected=True)
    resource = WorldTarget(
        id="resource-copper",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10, y=0, z=20),
        distance=25,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
    )
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "control.produce_resource_output",
            "control.select_squad_member",
            "game.pause",
            "identity.stable_handles",
            "squad.basic",
            "world.context_targets",
        ],
        squad=[bark, plant],
        targets=[resource],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id=bark.id,
            selected_character_ids=[bark.id, plant.id],
        ),
    )

    operation_kinds = {
        offer.operation_kind for offer in offered_affordances(observation)
    }

    # Narrowing the selection stays available; it is no longer the only thing a
    # group can do. A selection-broadcast order addresses whoever is selected,
    # so a pair standing at a resource can work it together rather than being
    # required to un-pair first.
    assert "select_squad_member_exact" in operation_kinds
    assert "perform_context_action" in operation_kinds


def test_screen_adapter_offers_exact_current_window_dismissal() -> None:
    bark = CharacterState(id="bark", name="Bark", selected=True)
    observation = _observation(
        capabilities=[
            "control.move_to_character",
            "identity.stable_handles",
            "nearby.characters",
            "squad.basic",
            "ui.inventory",
            "ui.visible_controls",
        ],
        ui=UIState(
            active_screen="inventory",
            dialogue_open=False,
            modal_open=True,
            open_inventory_windows=1,
            selected_character_id=bark.id,
            selected_character_ids=[bark.id],
            visible_controls=[
                VisibleUIControl(
                    label="Inventory",
                    role="text",
                    window="Bark",
                    bounds=_bounds(1),
                )
            ],
        ),
        squad=[bark],
        nearby=[
            NearbyEntity(
                id="barman",
                name="Barman",
                disposition=Disposition.NEUTRAL,
                distance=20,
            )
        ],
    )
    offers = offered_affordances(observation)
    offer = next(
        offer
        for offer in offers
        if offer.operation_kind == "dismiss_screen"
    )
    assert offer.target is not None
    assert offer.target.target_id == "Bark"
    bound = bind_affordance(selection_for(offer), observation)
    assert bound.operation.kind == "dismiss_screen"
    assert bound.operation.window == "Bark"
    assert not any(offer.operation_kind == "open_screen" for offer in offers)
    assert not {
        "approach_dialogue_target",
        "command_world_target",
        "exit_current_building",
        "harvest_resource",
        "move_in_direction",
        "move_to_character",
        "perform_context_action",
        "recover_camera_view",
        "regroup_with_squad_member",
        "respond_to_immediate_threat",
        "rotate_camera",
        "select_squad_member",
        "select_squad_member_exact",
        "travel_to_map_destination",
        "use_game_binding",
    } & {offer.operation_kind for offer in offers}


@pytest.mark.parametrize(
    ("screen", "ui"),
    [
        (
            GameScreen.INVENTORY,
            UIState(
                active_screen="inventory",
                modal_open=True,
                dialogue_open=False,
                open_inventory_windows=1,
            ),
        ),
        (
            GameScreen.STATS,
            UIState(
                active_screen="world",
                modal_open=True,
                dialogue_open=False,
                stats_window_open=True,
            ),
        ),
        *[
            (
                screen,
                UIState(
                    active_screen="world",
                    modal_open=True,
                    dialogue_open=False,
                    management_screen_open=True,
                    management_tab=tab,
                ),
            )
            for screen, tab in [
                (GameScreen.MAP, 0),
                (GameScreen.RESEARCH, 2),
                (GameScreen.CRAFTING, 3),
            ]
        ],
    ],
)
def test_every_named_open_screen_exposes_one_exact_close(
    screen: GameScreen,
    ui: UIState,
) -> None:
    observation = _observation(capabilities=[], ui=ui)

    offers = offered_affordances(observation)
    close = next(offer for offer in offers if offer.semantic == f"close_{screen.value}")

    assert close.operation_kind == "dismiss_screen"
    assert f"open_{screen.value}" not in {offer.semantic for offer in offers}
    bound = bind_affordance(selection_for(close), observation)
    assert bound.operation.kind == "dismiss_screen"
    assert bound.operation.expected_screen is screen


def test_context_adapter_preserves_every_executable_runtime_order_without_enumeration() -> None:
    actor = CharacterState(id="actor-1", name="Bark", selected=True)
    targets = [
        WorldTarget(
            id="resource-1",
            name="Iron Resource",
            kind="natural_resource",
            position=Vec3(x=10, y=0, z=20),
            distance=25,
            context_actions=[ContextActionKind.OPERATE],
            default_task="operate",
            screen_position=None,
        ),
        WorldTarget(
            id="machine-1",
            name="Machine",
            kind="building",
            position=Vec3(x=30, y=0, z=40),
            distance=50,
            context_actions=[ContextActionKind("repair")],
            default_task="repair",
            screen_position=Vec2(x=0.4, y=0.5),
        ),
    ]
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "world.context_targets",
            "world.context_target_screen_positions",
            "game.pause",
            "identity.stable_handles",
        ],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
        ),
        squad=[actor],
        targets=targets,
    )
    expected = {
        (target.id, order.value)
        for target in targets
        for order in target.context_actions
    }
    context_offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.CONTEXT_ORDER
    ]
    assert {
        (offer.target.target_id, offer.semantic)
        for offer in context_offers
        if offer.target is not None
    } == expected
    assert {offer.operation_kind for offer in context_offers} == {
        "perform_context_action",
        "command_world_target",
    }


def test_reviewed_first_aid_context_order_uses_the_generic_native_route() -> None:
    healer = CharacterState(id="squad-healer", name="Plant", selected=True)
    target = WorldTarget(
        id="squad-injured",
        name="Bark",
        kind="squad_character",
        position=Vec3(x=1, y=0, z=2),
        distance=3,
        context_actions=[ContextActionKind("first_aid")],
        default_task="first_aid",
    )
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "world.context_targets",
            "game.pause",
            "identity.stable_handles",
        ],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
            selected_character_id=healer.id,
            selected_character_ids=[healer.id],
        ),
        squad=[healer, CharacterState(id="squad-injured", name="Bark")],
        targets=[target],
    )

    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.target is not None and offer.target.target_id == target.id
    )

    assert offer.semantic == "first_aid"
    assert offer.operation_kind == "perform_context_action"


def test_inventory_adapter_binds_exact_open_vendor_when_other_traders_are_loaded() -> None:
    actor = CharacterState(
        id="actor-1",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
    )
    vendor = NearbyEntity(
        id="vendor-1",
        name="Trader",
        disposition=Disposition.FRIENDLY,
        has_vendor_list=True,
        shop_inventory_owner=True,
    )
    other_vendors = [
        NearbyEntity(
            id=f"vendor-{index}",
            name=f"Nearby Trader {index}",
            disposition=Disposition.FRIENDLY,
            has_vendor_list=True,
            shop_inventory_owner=False,
        )
        for index in (2, 3)
    ]
    observation = _observation(
        capabilities=[
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.inventory",
            "squad.basic",
            "ui.inventory",
            "ui.tooltip",
            "ui.visible_controls",
        ],
        money=95,
        squad=[actor],
        nearby=[vendor, *other_vendors],
        # This world-level count includes every loaded shopkeeper, not just the
        # exact vendor whose paired inventory window is open.
        active_shop_trader_count=3,
        ui=UIState(
            active_screen="trade",
            open_inventory_windows=2,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
            visible_controls=[
                VisibleUIControl(
                    label="vendor-cell",
                    role="item",
                    window="Trader",
                    bounds=_bounds(1),
                    item_name="Bread",
                    item_base_value=30,
                    item_quantity=4,
                ),
                VisibleUIControl(
                    label="actor-cell",
                    role="item",
                    window="Bark",
                    bounds=_bounds(2),
                    item_name="Cactus Rum",
                    item_quantity=2,
                ),
            ],
        ),
    )

    inventory = {
        offer.semantic: offer
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.INVENTORY
    }
    assert set(inventory) == {"buy", "sell"}
    assert inventory["buy"].parameters[0].maximum == 3
    assert inventory["sell"].parameters[0].maximum == 2
    assert inventory["buy"].operation_arguments["seller_id"] == vendor.id
    assert inventory["sell"].operation_arguments["buyer_id"] == vendor.id


@pytest.mark.parametrize("primary_id", ["actor-bark", "actor-plant"])
@pytest.mark.parametrize("reverse_squad_order", [False, True])
def test_trade_affordances_follow_the_exact_player_window_owner(
    primary_id: str,
    reverse_squad_order: bool,
) -> None:
    """Selection ordering cannot silently replace an observed UI owner."""

    bark = CharacterState(
        id="actor-bark",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
    )
    plant = CharacterState(
        id="actor-plant",
        name="Plant",
        selected=True,
        alive=True,
        conscious=True,
    )
    vendor = NearbyEntity(
        id="vendor-barman",
        name="Barman",
        disposition=Disposition.FRIENDLY,
        shop_inventory_owner=True,
    )
    squad = [bark, plant]
    if reverse_squad_order:
        squad.reverse()
    observation = _observation(
        capabilities=[
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.inventory",
            "squad.basic",
            "ui.inventory",
            "ui.tooltip",
            "ui.visible_controls",
        ],
        money=1000,
        squad=squad,
        nearby=[vendor],
        active_shop_trader_count=1,
        ui=UIState(
            active_screen="trade",
            open_inventory_windows=2,
            selected_character_id=primary_id,
            selected_character_ids=[bark.id, plant.id],
            visible_controls=[
                VisibleUIControl(
                    label="vendor-cell",
                    role="item",
                    window="BARMAN",
                    bounds=_bounds(1),
                    item_name="WANTED: The Preacher",
                    item_base_value=0,
                    item_quantity=1,
                ),
                VisibleUIControl(
                    label="actor-cell",
                    role="item",
                    window="BARK",
                    bounds=_bounds(2),
                    item_name="Copper",
                    item_quantity=5,
                ),
            ],
        ),
    )

    inventory = {
        offer.semantic: offer
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.INVENTORY
    }

    assert set(inventory) == {"buy", "sell"}
    assert inventory["buy"].target is not None
    assert inventory["buy"].target.label == "WANTED: The Preacher"
    assert inventory["sell"].target is not None
    assert inventory["sell"].target.label == "Copper"


def test_map_adapter_offers_every_currently_travelable_exact_destination() -> None:
    actor = CharacterState(
        id="actor-1",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
    )
    observation = _observation(
        capabilities=[
            "control.travel_to_map_destination",
            "game.location.identity",
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.health",
            "world.known_map_destinations",
        ],
        squad=[actor],
        ui=UIState(
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
        ),
    )
    assert observation.telemetry is not None
    observation.telemetry.game = observation.telemetry.game.model_copy(
        update={
            "location_id": "current-town",
            "location_name": "Current Town",
            "inside_town_walls": True,
        }
    )
    observation.telemetry.known_map_destinations = [
        KnownMapDestination(
            id="current-town",
            name="Current Town",
            distance=1000,
            has_gates=True,
        ),
        KnownMapDestination(
            id="destination-1",
            name="Squin",
            distance=9000,
            has_gates=True,
        ),
        KnownMapDestination(
            id="destination-2",
            name="The Hub",
            distance=12000,
            has_gates=False,
        ),
    ]

    assert {
        offer.target.target_id
        for offer in offered_affordances(observation)
        if offer.source is AffordanceSource.MAP and offer.target is not None
    } == {"destination-1", "destination-2"}


def test_exact_current_offer_is_the_only_action_language() -> None:
    actor = CharacterState(id="actor-1", name="Bark", selected=True)
    target = NearbyEntity(
        id="person-1",
        name="Wanderer",
        is_animal=False,
        has_dialogue=True,
        disposition=Disposition.FRIENDLY,
        distance=15,
    )
    observation = _observation(
        capabilities=[
            "control.approach_dialogue_target",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        ],
        squad=[actor],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
        ),
        nearby=[target],
    )
    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "approach_dialogue_target"
    )
    selection = selection_for(offer)
    bound = bind_affordance(selection, observation)
    assert isinstance(bound, BoundOperation)
    assert bound.definition is APPROACH_DIALOGUE_TARGET_DEFINITION
    assert isinstance(bound.binding, BoundActor)
    assert bound.binding.source_revision == observation.world_revision
    assert bound.based_on_revision == observation.world_revision
    assert bound.operation.kind == "approach_dialogue_target"
    assert bound.operation.target_id == target.id
    assert (
        OPERATION_BINDING_AUTHORITY.rebind(bound, observation)
        == bound
    )

    assert observation.telemetry is not None
    later_revision = observation.world_revision.model_copy(
        update={"telemetry_sequence": observation.telemetry.sequence + 1}
    )
    later_current = observation.model_copy(
        update={
            "world_revision": later_revision,
            "telemetry": observation.telemetry.model_copy(
                update={"sequence": observation.telemetry.sequence + 1}
            ),
        }
    )
    rebound = OPERATION_BINDING_AUTHORITY.rebind(bound, later_current)
    assert rebound.affordance == bound.affordance
    assert rebound.based_on_revision == later_revision
    assert rebound.binding.source_revision == later_revision

    with pytest.raises(ValueError, match="is offered, but not on"):
        bind_affordance(
            selection.model_copy(update={"target_id": "person-invented"}),
            observation,
        )

    later = observation.model_copy(
        update={"telemetry_stale": True, "telemetry_age_seconds": 9.0}
    )
    with pytest.raises(ValueError, match="no current choice is named"):
        bind_affordance(selection, later)


def test_planner_projection_excludes_runtime_mechanics() -> None:
    selected = CharacterState(
        id="actor-1",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
        blood=100,
        in_combat=True,
        position=Vec3(x=0, y=0, z=0),
    )
    observation = _observation(
        capabilities=[
            "control.respond_to_immediate_threat",
            "control.move_in_direction",
            "game.pause",
            "game.speed",
            "nearby.visible_entities",
            "squad.health",
        ],
        squad=[selected],
        nearby=[
            NearbyEntity(
                id="hostile-1",
                name="Hungry Bandit",
                is_animal=False,
                disposition=Disposition.HOSTILE,
                distance=10,
                visible=True,
                position=Vec3(x=10, y=0, z=0),
            )
        ],
        ui=UIState(
            selected_character_id=selected.id,
            selected_character_ids=[selected.id],
        ),
    )
    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == "respond_to_immediate_threat"
    )
    digest = offer.planner_digest()
    assert set(digest) == {
        "affordance_id",
        "semantic",
        "source",
        "description",
        "target",
        "parameters",
    }
    assert "policy" not in digest
    assert "operation_kind" not in digest
    assert "operation_arguments" not in digest

    bound = bind_affordance(selection_for(offer, strategy="withdraw"), observation)
    assert bound.operation.kind == "respond_to_immediate_threat"
    assert bound.operation.strategy.value == "withdraw"
    with pytest.raises(ValueError, match="must be one of"):
        bind_affordance(selection_for(offer, strategy="panic"), observation)


def test_stale_or_absent_telemetry_has_no_affordance_surface() -> None:
    stale = _observation(capabilities=[], stale=True)
    absent = Observation(run_id="absent", step_index=0, mode="live")
    assert offered_affordances(stale) == ()
    assert offered_affordances(absent) == ()


def test_every_adapter_closes_with_the_same_runtime_owned_lifecycle() -> None:
    observation = _observation(
        capabilities=["camera.position", "game.pause", "game.speed"]
    )
    offers = offered_affordances(observation)
    assert offers

    for offer in offers:
        required = {
            spec.name: (
                int(spec.minimum or 1)
                if spec.kind.value == "integer"
                else float(spec.minimum or 1)
                if spec.kind.value == "number"
                else spec.choices[0]
                if spec.choices
                else "runtime-owned"
            )
            for spec in offer.parameters
            if spec.required
        }
        materialized = bind_affordance(selection_for(offer, **required), observation)
        completion = materialized.definition.resolve_terminal(
            materialized.operation,
            observation,
            selected_affordance=True,
        )
        assert completion.owner is not TerminalOwner.STEP_CONDITIONS
        assert (
            completion.owner is not TerminalOwner.RUNTIME_CONDITIONS
            or completion.conditions
        )
        receipt = terminal_affordance_receipt(
            bound_affordance(materialized),
            status=AffordanceLifecycleStatus.SUCCEEDED,
            message="Runtime verified completion and cleanup.",
            telemetry_sequence=42,
            execution_started=True,
            monitoring_started=(
                bound_affordance(materialized).execution.value
                in {"monitored", "composite"}
            ),
        )
        statuses = [event.status for event in receipt.lifecycle]
        assert statuses[:3] == [
            AffordanceLifecycleStatus.OFFERED,
            AffordanceLifecycleStatus.BOUND,
            AffordanceLifecycleStatus.EXECUTING,
        ]
        assert statuses[-1] is AffordanceLifecycleStatus.SUCCEEDED
        assert receipt.affordance.source is offer.source
        assert receipt.affordance.operation_kind == materialized.operation.kind

    with pytest.raises(ValueError, match="status must be terminal"):
        terminal_affordance_receipt(
            bound_affordance(materialized),
            status=AffordanceLifecycleStatus.OFFERED,
            message="An offered affordance is not a terminal receipt.",
            telemetry_sequence=42,
            execution_started=False,
            monitoring_started=False,
        )


def test_rejected_before_dispatch_receipt_does_not_invent_execution() -> None:
    observation = _observation(
        capabilities=["camera.position", "game.pause", "game.speed"]
    )
    offer = next(iter(offered_affordances(observation)))
    materialized = bind_affordance(selection_for(offer), observation)

    receipt = terminal_affordance_receipt(
        bound_affordance(materialized),
        status=AffordanceLifecycleStatus.REJECTED,
        message="A precondition failed before dispatch.",
        telemetry_sequence=42,
        execution_started=False,
        monitoring_started=False,
    )

    assert [event.status for event in receipt.lifecycle] == [
        AffordanceLifecycleStatus.OFFERED,
        AffordanceLifecycleStatus.BOUND,
        AffordanceLifecycleStatus.REJECTED,
    ]


def test_adapter_declarations_guard_every_emitted_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation(capabilities=[])
    runtime_adapter = AFFORDANCE_ADAPTERS[0]
    monkeypatch.setattr(
        affordance_module,
        "AFFORDANCE_ADAPTERS",
        (replace(runtime_adapter, operation_kinds=frozenset()),),
    )

    with pytest.raises(RuntimeError, match="emitted undeclared operation"):
        offered_affordances(observation)
