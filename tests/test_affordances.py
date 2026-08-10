"""Whole-denominator invariants for the unified affordance contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

import kenshi_agent.affordances as affordance_module
from kenshi_agent.affordances import (
    AFFORDANCE_ADAPTERS,
    OPERATION_BINDING_AUTHORITY,
    AffordanceSource,
    bind_affordance,
    bound_affordance,
    offered_affordances,
    selection_for,
    terminal_affordance_receipt,
)
from kenshi_agent.core.affordance import AffordanceLifecycleStatus
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode
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
    Vec3,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    APPROACH_DIALOGUE_TARGET_DEFINITION,
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
    roster: list[CharacterState] | None = None,
    primary_character_id: str | None = None,
    selected_character_ids: list[str] | None = None,
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
            roster=roster or [],
            primary_character_id=primary_character_id,
            selected_character_ids=selected_character_ids or [],
            nearby_entities=nearby or [],
            world_targets=targets or [],
            active_shop_trader_count=active_shop_trader_count,
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=0.1,
    )






def test_character_adapter_exposes_one_runtime_chosen_squad_reunion() -> None:
    bark = CharacterState(
        id="entity-bark",
        name="Bark",
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
            "roster.basic",
            "roster.health",
        ],
        roster=[bark, ruka, plant],
        primary_character_id=bark.id,
        selected_character_ids=[bark.id],
        ui=UIState(
            active_screen="world",
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


def test_blocking_dialogue_and_modal_offer_one_native_return_to_world() -> None:
    observation = _observation(
        capabilities=["control.close_active_interface", "ui.dialogue"],
        ui=UIState(
            active_screen="dialogue",
            modal_open=True,
            dialogue_open=True,
            dialogue_target_id="entity-hobbs",
            dialogue_options=["Who are you?", "Goodbye."],
        ),
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "close_active_interface"
    ]

    assert len(offers) == 1
    assert offers[0].semantic == "return_to_world"
    bound = bind_affordance(selection_for(offers[0]), observation)
    assert bound.definition.kind == "close_active_interface"


def test_rendered_prospecting_window_offers_native_return_to_world() -> None:
    observation = _observation(
        capabilities=["control.close_active_interface", "ui.prospecting"],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
            prospecting_window_open=True,
        ),
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "close_active_interface"
    ]

    assert len(offers) == 1
    bound = bind_affordance(selection_for(offers[0]), observation)
    assert bound.definition.kind == "close_active_interface"


def test_character_adapter_prefers_exact_native_selection_over_portrait_geometry() -> None:
    bark = CharacterState(id="entity-bark", name="Bark")
    plant = CharacterState(id="entity-plant", name="Plant")
    observation = _observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "roster.basic",
        ],
        roster=[bark, plant],
        primary_character_id=bark.id,
        selected_character_ids=[bark.id],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
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
    bark = CharacterState(id="entity-bark", name="Bark")
    plant = CharacterState(id="entity-plant", name="Plant")
    observation = _observation(
        capabilities=[
            "control.select_squad_member",
            "identity.stable_handles",
            "roster.basic",
        ],
        roster=[bark, plant],
        primary_character_id=bark.id,
        selected_character_ids=[bark.id, plant.id],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
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
    bark = CharacterState(id="entity-bark", name="Bark")
    plant = CharacterState(id="entity-plant", name="Plant")
    resource = WorldTarget(
        id="resource-copper",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10, y=0, z=20),
        distance=25,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
        operator_capacity=1,
        current_operator_ids=[bark.id],
        current_operators_complete=True,
        output_inventory_complete=True,
    )
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "control.produce_resource_output",
            "control.select_squad_member",
            "game.pause",
            "identity.stable_handles",
            "roster.basic",
            "world.context_targets",
            "world.resource_operators",
        ],
        roster=[bark, plant],
        targets=[resource],
        primary_character_id=bark.id,
        selected_character_ids=[bark.id, plant.id],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
        ),
    )

    offers = offered_affordances(observation)
    operation_kinds = {offer.operation_kind for offer in offers}

    # Narrowing the selection stays available; it is no longer the only thing a
    # group can do. The offer keeps the broadcast recipient set distinct from
    # Kenshi's accepted set: two are selected, capacity is one, and only Bark
    # is an operator. The planner may ask the group but may not report both as
    # working the resource.
    assert "select_squad_member_exact" in operation_kinds
    assert "perform_context_action" in operation_kinds
    assert "produce_resource_output" in operation_kinds
    resource_descriptions = "\n".join(
        offer.description
        for offer in offers
        if offer.operation_kind
        in {"perform_context_action", "produce_resource_output"}
    )
    assert "1 operator slots" in resource_descriptions
    assert bark.id in resource_descriptions
    assert plant.id not in resource_descriptions
    assert "not proof of operator acceptance" in resource_descriptions


def test_resource_operations_are_withheld_without_complete_engine_state() -> None:
    actor = CharacterState(id="entity-bark", name="Bark")
    incomplete = WorldTarget(
        id="resource-copper",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10, y=0, z=20),
        distance=25,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
        operator_capacity=1,
        current_operator_ids=[actor.id],
        current_operators_complete=False,
        output_inventory_complete=False,
    )
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "control.produce_resource_output",
            "game.pause",
            "identity.stable_handles",
            "world.context_targets",
            "world.resource_operators",
        ],
        roster=[actor],
        targets=[incomplete],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
    )

    operation_kinds = {
        offer.operation_kind for offer in offered_affordances(observation)
    }
    assert "perform_context_action" not in operation_kinds
    assert "produce_resource_output" not in operation_kinds


def test_resource_output_and_inventory_pair_are_both_offered() -> None:
    actor = CharacterState(
        id="entity-bark",
        name="Bark",
        alive=True,
        conscious=True,
        down=False,
    )
    resource = WorldTarget(
        id="resource-copper",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10, y=0, z=20),
        distance=25,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
        operator_capacity=1,
        current_operator_ids=[],
        current_operators_complete=True,
        output_inventory_complete=True,
    )
    observation = _observation(
        capabilities=[
            "control.open_trade_window",
            "control.produce_resource_output",
            "game.pause",
            "identity.stable_handles",
            "world.context_targets",
            "world.resource_operators",
        ],
        roster=[actor],
        nearby=[
            NearbyEntity(id=f"entity-nearby-{index:02d}", name=f"Nearby {index}")
            for index in range(12)
        ],
        targets=[resource],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
        ),
    )

    offers = offered_affordances(observation)
    resource_offers = [
        offer
        for offer in offers
        if offer.target is not None and offer.target.target_id == resource.id
    ]

    assert {
        (offer.semantic, offer.operation_kind) for offer in resource_offers
    } == {
        ("produce_resource_output", "produce_resource_output"),
        ("open_trade_window", "open_trade_window"),
    }



def test_reviewed_first_aid_context_order_uses_the_generic_native_route() -> None:
    healer = CharacterState(id="squad-healer", name="Plant")
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
        primary_character_id=healer.id,
        selected_character_ids=[healer.id],
        ui=UIState(
            active_screen="world",
            dialogue_open=False,
            modal_open=False,
        ),
        roster=[healer, CharacterState(id="squad-injured", name="Bark")],
        targets=[target],
    )

    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.target is not None and offer.target.target_id == target.id
    )

    assert offer.semantic == "first_aid"
    assert offer.operation_kind == "perform_context_action"




def test_map_adapter_offers_every_currently_travelable_exact_destination() -> None:
    actor = CharacterState(
        id="actor-1",
        name="Bark",
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
            "roster.health",
            "world.known_map_destinations",
        ],
        roster=[actor],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
        ui=UIState(
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
    actor = CharacterState(id="actor-1", name="Bark")
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
        roster=[actor],
        primary_character_id=actor.id,
        selected_character_ids=[actor.id],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
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
            "roster.health",
        ],
        roster=[selected],
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
        primary_character_id=selected.id,
        selected_character_ids=[selected.id],
        ui=UIState(
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
    runtime_adapter = next(
        adapter for adapter in AFFORDANCE_ADAPTERS if adapter.name == "runtime"
    )
    monkeypatch.setattr(
        affordance_module,
        "AFFORDANCE_ADAPTERS",
        (replace(runtime_adapter, operation_kinds=frozenset()),),
    )

    with pytest.raises(RuntimeError, match="emitted undeclared operation"):
        offered_affordances(observation)
