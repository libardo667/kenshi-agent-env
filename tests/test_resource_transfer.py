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
    ResourceTransferEvidence,
    ResourceTransferStatus,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.resource_transfer import (
    begin_resource_transfer,
    evaluate_resource_transfer,
    finalize_resource_transfer,
    resource_transfer_layout_error,
)


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
    assert evidence.selected_character_id == "entity-hep"
    assert evidence.observed_after_sequence == 11


def test_every_positive_conserved_quantity_transfers_but_zero_does_not() -> None:
    for quantity in range(1, 6):
        evidence = evaluate_resource_transfer(
            ACTION,
            before=observation(
                10,
                source_quantities=[quantity],
                destination_quantities=[],
            ),
            after=observation(
                11,
                source_quantities=[],
                destination_quantities=[quantity],
            ),
        )
        assert evidence.status is ResourceTransferStatus.TRANSFERRED

    unchanged = evaluate_resource_transfer(
        ACTION,
        before=observation(10, source_quantities=[2], destination_quantities=[]),
        after=observation(11, source_quantities=[2], destination_quantities=[]),
    )
    assert unchanged.status is ResourceTransferStatus.NOT_TRANSFERRED


def test_selection_identity_must_agree_across_every_telemetry_surface() -> None:
    base = observation(10, source_quantities=[2], destination_quantities=[])
    telemetry = base.telemetry
    assert telemetry is not None
    second = telemetry.squad[0].model_copy(
        update={"id": "entity-second", "name": "Second"}
    )
    inconsistent = (
        telemetry.model_copy(
            update={
                "ui": telemetry.ui.model_copy(
                    update={"selected_character_ids": []}
                )
            }
        ),
        telemetry.model_copy(
            update={
                "ui": telemetry.ui.model_copy(
                    update={"selected_character_id": "entity-second"}
                )
            }
        ),
        telemetry.model_copy(update={"squad": [*telemetry.squad, second]}),
    )

    for candidate in inconsistent:
        assert (
            resource_transfer_layout_error(
                ACTION,
                base.model_copy(update={"telemetry": candidate}),
            )
            is not None
        )


def test_layout_rejects_stale_or_ambiguous_inventory_ownership() -> None:
    base = observation(10, source_quantities=[2], destination_quantities=[])

    assert (
        resource_transfer_layout_error(
            ACTION,
            base.model_copy(update={"telemetry_stale": True}),
        )
        is not None
    )
    for source_caption in ("", "HEP"):
        ambiguous = ACTION.model_copy(update={"window": source_caption})
        assert resource_transfer_layout_error(ambiguous, base) is not None


def test_source_quantity_ignores_every_partial_cell_match() -> None:
    before = observation(10, source_quantities=[2], destination_quantities=[])
    telemetry = before.telemetry
    assert telemetry is not None
    controls = list(telemetry.ui.visible_controls or [])
    controls.extend(
        [
            VisibleUIControl(
                label="same item, wrong owner",
                window="OTHER RESOURCE",
                role="item",
                item_name="Raw Iron",
                item_quantity=100,
                section="other",
                bounds=_bounds(),
            ),
            VisibleUIControl(
                label="same owner, wrong cell",
                window=ACTION.window,
                role="item",
                item_name="Stone",
                item_quantity=200,
                section="other",
                bounds=_bounds(),
            ),
            VisibleUIControl(
                label="unrelated item cell",
                window="OTHER RESOURCE",
                role="item",
                item_name="Stone",
                item_quantity=300,
                section="other",
                bounds=_bounds(),
            ),
        ]
    )
    before = before.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={"ui": telemetry.ui.model_copy(update={"visible_controls": controls})}
            )
        }
    )

    baseline = begin_resource_transfer(ACTION, before)

    assert baseline.source_quantity_before == 2


def test_unverified_evidence_retains_the_current_selected_identity() -> None:
    before = observation(10, source_quantities=[2], destination_quantities=[])
    baseline = ResourceTransferEvidence(
        status=ResourceTransferStatus.UNVERIFIED,
        target_id=ACTION.target_id,
        selected_character_id=None,
        item_name=ACTION.item_name,
        source_quantity_before=2,
        destination_quantity_before=0,
        reason="Synthetic incomplete baseline.",
    )

    evidence = finalize_resource_transfer(
        ACTION,
        baseline=baseline,
        before_revision=before.world_revision,
        after=observation(11, source_quantities=[], destination_quantities=[2]),
    )

    assert evidence.status is ResourceTransferStatus.UNVERIFIED
    assert evidence.selected_character_id == "entity-hep"


def test_selection_change_during_transfer_is_unverified() -> None:
    before = observation(10, source_quantities=[2], destination_quantities=[])
    after = observation(11, source_quantities=[], destination_quantities=[2])
    telemetry = after.telemetry
    assert telemetry is not None
    replacement = telemetry.squad[0].model_copy(
        update={"id": "entity-replacement", "name": "Replacement"}
    )
    after = after.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "squad": [replacement],
                    "ui": telemetry.ui.model_copy(
                        update={
                            "selected_character_id": replacement.id,
                            "selected_character_ids": [replacement.id],
                            "visible_controls": [
                                *list(telemetry.ui.visible_controls or []),
                                VisibleUIControl(
                                    label="close",
                                    window=replacement.name,
                                    role="button",
                                    bounds=_bounds(),
                                ),
                            ],
                        }
                    ),
                }
            )
        }
    )

    evidence = evaluate_resource_transfer(ACTION, before=before, after=after)

    assert evidence.status is ResourceTransferStatus.UNVERIFIED


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
