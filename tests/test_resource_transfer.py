"""Causal proof for moving output from a resource into one character."""

from __future__ import annotations

from datetime import UTC, datetime

from kenshi_agent.models import (
    CharacterState,
    CollectResourceOutputAction,
    ControlMode,
    GameState,
    InventoryItem,
    NormalizedPointerBounds,
    Observation,
    ResourceTransferStatus,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.resource_transfer import evaluate_resource_transfer


def _bounds() -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=0.1,
        max_x=0.2,
        min_y=0.3,
        max_y=0.4,
    )


def observation(
    sequence: int,
    *,
    source_quantities: list[int],
    destination_quantities: list[int],
    source_complete: bool = True,
    destination_complete: bool = True,
    destination_window_open: bool = True,
) -> Observation:
    return Observation(
        run_id="resource-transfer",
        step_index=sequence,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "ui.visible_controls",
                "ui.context_inventory_target",
                "ui.inventory",
                "squad.inventory",
            ],
            game=GameState(loaded=True, paused=True),
            active_shop_trader_count=0,
            ui=UIState(
                active_screen=(
                    "trade" if destination_window_open else "inventory"
                ),
                modal_open=True,
                dialogue_open=False,
                open_inventory_windows=2 if destination_window_open else 1,
                context_inventory_target_id="entity-copper",
                visible_controls_complete=source_complete,
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
                visible_controls=[
                    VisibleUIControl(
                        label=f"Raw Iron {index}",
                        window="COPPER RESOURCE",
                        role="item",
                        item_name="Raw Iron",
                        item_quantity=quantity,
                        section="out",
                        bounds=_bounds(),
                    )
                    for index, quantity in enumerate(source_quantities)
                ]
                + (
                    [
                        VisibleUIControl(
                            label="close",
                            window="HEP",
                            role="button",
                            bounds=_bounds(),
                        )
                    ]
                    if destination_window_open
                    else []
                ),
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=destination_complete,
                    inventory=[
                        InventoryItem(
                            name="Raw Iron",
                            item_name="Raw Iron",
                            item_quantity=quantity,
                            section="main",
                        )
                        for quantity in destination_quantities
                    ],
                )
            ],
        ),
        telemetry_age_seconds=0.0,
    )


ACTION = CollectResourceOutputAction(
    target_id="entity-copper",
    cell_label="Raw Iron 0",
    item_name="Raw Iron",
    source_quantity=2,
    window="COPPER RESOURCE",
)


def test_equal_source_loss_and_destination_gain_is_transferred() -> None:
    evidence = evaluate_resource_transfer(
        ACTION,
        before=observation(
            10,
            source_quantities=[2, 1],
            destination_quantities=[3],
        ),
        after=observation(
            11,
            source_quantities=[1],
            destination_quantities=[5],
        ),
    )

    assert evidence.status is ResourceTransferStatus.TRANSFERRED
    assert evidence.source_quantity_before == 3
    assert evidence.source_quantity_after == 1
    assert evidence.destination_quantity_before == 3
    assert evidence.destination_quantity_after == 5
    assert evidence.observed_after_sequence == 11


def test_one_sided_change_never_proves_a_transfer() -> None:
    before = observation(10, source_quantities=[2], destination_quantities=[])
    source_only = evaluate_resource_transfer(
        ACTION,
        before=before,
        after=observation(11, source_quantities=[], destination_quantities=[]),
    )
    destination_only = evaluate_resource_transfer(
        ACTION,
        before=before,
        after=observation(11, source_quantities=[2], destination_quantities=[2]),
    )

    assert source_only.status is ResourceTransferStatus.NOT_TRANSFERRED
    assert destination_only.status is ResourceTransferStatus.NOT_TRANSFERRED


def test_noncausal_or_incomplete_observation_is_unverified() -> None:
    before = observation(10, source_quantities=[2], destination_quantities=[])

    same_revision = evaluate_resource_transfer(
        ACTION,
        before=before,
        after=observation(10, source_quantities=[], destination_quantities=[2]),
    )
    truncated_source = evaluate_resource_transfer(
        ACTION,
        before=before,
        after=observation(
            11,
            source_quantities=[],
            destination_quantities=[2],
            source_complete=False,
        ),
    )
    truncated_destination = evaluate_resource_transfer(
        ACTION,
        before=before,
        after=observation(
            11,
            source_quantities=[],
            destination_quantities=[2],
            destination_complete=False,
        ),
    )

    assert same_revision.status is ResourceTransferStatus.UNVERIFIED
    assert truncated_source.status is ResourceTransferStatus.UNVERIFIED
    assert truncated_destination.status is ResourceTransferStatus.UNVERIFIED


def test_closed_destination_window_never_proves_transfer() -> None:
    evidence = evaluate_resource_transfer(
        ACTION,
        before=observation(
            10,
            source_quantities=[2],
            destination_quantities=[],
            destination_window_open=False,
        ),
        after=observation(
            11,
            source_quantities=[],
            destination_quantities=[2],
            destination_window_open=False,
        ),
    )

    assert evidence.status is ResourceTransferStatus.UNVERIFIED
