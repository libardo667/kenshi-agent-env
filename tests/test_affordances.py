"""Whole-denominator invariants for the unified affordance contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

import kenshi_agent.affordances as affordance_module
from kenshi_agent.action_contracts import (
    USE_GAME_BINDING_CONTRACT,
    CompletionOwner,
    completion_contract_for,
)
from kenshi_agent.affordances import (
    AFFORDANCE_ADAPTERS,
    SEMANTICALLY_ADAPTED_GAME_BINDINGS,
    AffordanceSource,
    bind_affordance,
    bound_affordance,
    offered_affordances,
    selection_for,
    terminal_affordance_receipt,
)
from kenshi_agent.models import (
    TIME_GAME_BINDINGS,
    AffordanceLifecycleStatus,
    CharacterState,
    ContextActionKind,
    ControlMode,
    Disposition,
    GameBinding,
    GameScreen,
    GameState,
    KnownMapDestination,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlanningMode,
    TelemetrySnapshot,
    UIState,
    UseGameBindingAction,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
    is_runtime_owned_visible_control,
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
    return Observation(
        run_id="affordance-test",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
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
            ui=ui or UIState(),
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
        completion = completion_contract_for(
            action,
            observation,
            selected_affordance=True,
        )
        if (
            USE_GAME_BINDING_CONTRACT.bind(action, observation).bound
            and not (
                completion.owner is CompletionOwner.RUNTIME_CONDITIONS
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


def test_ui_adapter_offers_every_and_only_current_non_item_control() -> None:
    controls = [
        VisibleUIControl(label="Accept", role="button", window="Talk", bounds=_bounds(1)),
        VisibleUIControl(label="A warning", role="text", window="Talk", bounds=_bounds(2)),
        VisibleUIControl(
            label="cell 3",
            role="item",
            window="SHOP",
            bounds=_bounds(3),
            item_name="Bread",
            item_base_value=31,
        ),
        VisibleUIControl(
            label="pause",
            role="button",
            window="HUD",
            widget_name="HUDRoot/TimeControls/Pause",
            bounds=_bounds(4),
        ),
    ]
    observation = _observation(
        capabilities=["ui.visible_controls"],
        ui=UIState(dialogue_open=True, visible_controls=controls),
    )
    expected = {
        (control.window, control.role, control.label)
        for control in controls
        if control.role != "item" and not is_runtime_owned_visible_control(control)
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


def test_screen_adapter_offers_exact_current_window_dismissal() -> None:
    observation = _observation(
        capabilities=["ui.visible_controls", "ui.inventory"],
        ui=UIState(
            active_screen="inventory",
            dialogue_open=False,
            modal_open=False,
            open_inventory_windows=1,
            visible_controls=[
                VisibleUIControl(
                    label="Inventory",
                    role="text",
                    window="Bark",
                    bounds=_bounds(1),
                )
            ],
        ),
    )
    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "dismiss_screen"
    )
    assert offer.target is not None
    assert offer.target.target_id == "Bark"
    bound = bind_affordance(selection_for(offer), observation)
    assert bound.operation.kind == "dismiss_screen"
    assert bound.operation.window == "Bark"


def test_context_adapter_preserves_every_executable_runtime_order_without_enumeration() -> None:
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
        ui=UIState(active_screen="world", dialogue_open=False, modal_open=False),
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


def test_inventory_adapter_bounds_transaction_quantities_from_current_cells() -> None:
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
        nearby=[vendor],
        active_shop_trader_count=1,
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
        nearby=[target],
    )
    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "approach_dialogue_target"
    )
    selection = selection_for(offer)
    bound = bind_affordance(selection, observation)
    assert bound.operation.kind == "approach_dialogue_target"
    assert bound.operation.target_id == target.id

    with pytest.raises(ValueError, match="target does not match"):
        bind_affordance(
            selection.model_copy(update={"target_id": "person-invented"}),
            observation,
        )

    later = observation.model_copy(
        update={"telemetry_stale": True, "telemetry_age_seconds": 9.0}
    )
    with pytest.raises(ValueError, match="absent"):
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
        completion = completion_contract_for(
            materialized.operation,
            observation,
            selected_affordance=True,
        )
        assert completion.owner is not CompletionOwner.STEP_CONDITIONS
        assert (
            completion.owner is not CompletionOwner.RUNTIME_CONDITIONS
            or completion.conditions
        )
        receipt = terminal_affordance_receipt(
            bound_affordance(materialized),
            status=AffordanceLifecycleStatus.SUCCEEDED,
            message="Runtime verified completion and cleanup.",
            telemetry_sequence=42,
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
        )


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
