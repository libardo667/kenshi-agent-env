"""Controller-owned conservation proof for contextual resource output."""

from __future__ import annotations

from .models import (
    CharacterState,
    CollectResourceOutputAction,
    Observation,
    ResourceTransferEvidence,
    ResourceTransferStatus,
    WorldStateRevision,
    normalize_control_label,
)


def _selected_character(observation: Observation) -> CharacterState | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    selected = [character for character in telemetry.squad if character.selected]
    if (
        len(selected) != 1
        or telemetry.ui.selected_character_ids != [selected[0].id]
        or telemetry.ui.selected_character_id != selected[0].id
    ):
        return None
    return selected[0]


def resource_transfer_layout_error(
    action: CollectResourceOutputAction,
    observation: Observation,
) -> str | None:
    """Return why the source-to-character transfer route is not explicit.

    Kenshi will hover and right-click a building output cell with only that
    building window open, but it will not move the item anywhere. The selected
    character's own inventory window is the destination route. `active_screen`
    calls this two-window layout "trade" even when no trader exists, so window
    ownership and the native trader count—not the coarse screen label—own the
    safety decision.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return "Fresh inventory telemetry is required for resource transfer."
    ui = telemetry.ui
    if ui.dialogue_open is not False:
        return "Resource transfer requires no open dialogue."
    if ui.active_screen not in {"inventory", "trade"}:
        return "Resource transfer requires the inventory interface."
    if telemetry.active_shop_trader_count != 0:
        return (
            "An active shop trader makes a right-click a commercial action, "
            "so resource collection fails closed."
        )
    if ui.open_inventory_windows != 2:
        return (
            "Resource transfer requires exactly two inventory windows: the "
            "exact source and the selected character's own inventory."
        )
    controls = ui.visible_controls
    if ui.visible_controls_complete is not True or controls is None:
        return (
            "Complete visible controls are required to prove both inventory "
            "window owners."
        )
    selected = _selected_character(observation)
    if selected is None or not selected.name:
        return (
            "One exact named selected character is required as the transfer "
            "destination."
        )
    source = normalize_control_label(action.window)
    destination = normalize_control_label(selected.name)
    if not source or source == destination:
        return (
            "Source and selected-character inventory captions are ambiguous."
        )
    if not any(
        normalize_control_label(control.window) == destination
        for control in controls
    ):
        return (
            f"The selected character's own inventory ({selected.name!r}) is not "
            "confirmed open, so right-click has no destination."
        )
    return None


def _source_quantity(
    action: CollectResourceOutputAction,
    observation: Observation,
) -> int | None:
    telemetry = observation.telemetry
    if (
        telemetry is None
        or resource_transfer_layout_error(action, observation) is not None
        or telemetry.ui.context_inventory_target_id != action.target_id
    ):
        return None
    controls = telemetry.ui.visible_controls
    if controls is None:
        return None
    quantities: list[int] = []
    for control in controls:
        if (
            control.role == "item"
            and control.window == action.window
            and control.section == action.section
            and control.item_name == action.item_name
        ):
            if control.item_quantity is None:
                return None
            quantities.append(control.item_quantity)
    return sum(quantities)


def _destination_quantity(
    action: CollectResourceOutputAction,
    observation: Observation,
) -> tuple[str | None, int | None]:
    selected = _selected_character(observation)
    if (
        selected is None
        or selected.inventory_complete is not True
        or observation.telemetry_stale
    ):
        return None, None
    quantity = sum(
        item.item_quantity if item.item_quantity is not None else item.quantity
        for item in selected.inventory
        if normalize_control_label(item.name)
        == normalize_control_label(action.item_name)
    )
    return selected.id, quantity


def evaluate_resource_transfer(
    action: CollectResourceOutputAction,
    *,
    before: Observation,
    after: Observation,
) -> ResourceTransferEvidence:
    """Prove one UI transfer only when equal quantities cross the boundary.

    Source disappearance and destination appearance are each insufficient:
    either can be caused by truncation, another inventory mutation, or a UI
    refresh. Both bounded lists must be complete and a causally later revision
    must conserve the exact positive quantity.
    """

    return finalize_resource_transfer(
        action,
        baseline=begin_resource_transfer(action, before),
        before_revision=before.world_revision,
        after=after,
    )


def begin_resource_transfer(
    action: CollectResourceOutputAction,
    observation: Observation,
) -> ResourceTransferEvidence:
    """Capture complete source and destination baselines before input."""

    source = _source_quantity(action, observation)
    selected_id, destination = _destination_quantity(action, observation)
    return ResourceTransferEvidence(
        status=ResourceTransferStatus.UNVERIFIED,
        target_id=action.target_id,
        selected_character_id=selected_id,
        item_name=action.item_name,
        source_quantity_before=source,
        destination_quantity_before=destination,
        reason="Captured complete pre-input source and destination quantities.",
    )


def finalize_resource_transfer(
    action: CollectResourceOutputAction,
    *,
    baseline: ResourceTransferEvidence,
    before_revision: WorldStateRevision,
    after: Observation,
) -> ResourceTransferEvidence:
    """Finish a retained baseline against one later observation."""

    source_after = _source_quantity(action, after)
    selected_id, destination_after = _destination_quantity(action, after)
    sequence = after.world_revision.telemetry_sequence

    def evidence(
        status: ResourceTransferStatus,
        reason: str,
    ) -> ResourceTransferEvidence:
        return ResourceTransferEvidence(
            status=status,
            target_id=action.target_id,
            selected_character_id=baseline.selected_character_id or selected_id,
            item_name=action.item_name,
            source_quantity_before=baseline.source_quantity_before,
            source_quantity_after=source_after,
            destination_quantity_before=baseline.destination_quantity_before,
            destination_quantity_after=destination_after,
            observed_after_sequence=sequence,
            reason=reason,
        )

    if not after.world_revision.is_later_than(before_revision):
        return evidence(
            ResourceTransferStatus.UNVERIFIED,
            "No causally later world revision observed the attempted transfer.",
        )
    if (
        baseline.source_quantity_before is None
        or source_after is None
        or baseline.destination_quantity_before is None
        or destination_after is None
        or baseline.selected_character_id is None
        or selected_id != baseline.selected_character_id
    ):
        return evidence(
            ResourceTransferStatus.UNVERIFIED,
            (
                "Exact source or destination inventory was absent, stale, "
                "incomplete, or changed selection."
            ),
        )
    source_loss = baseline.source_quantity_before - source_after
    destination_gain = destination_after - baseline.destination_quantity_before
    if source_loss > 0 and source_loss == destination_gain:
        return evidence(
            ResourceTransferStatus.TRANSFERRED,
            (
                f"Conserved {source_loss} {action.item_name!r} from exact "
                "resource output into the selected character."
            ),
        )
    return evidence(
        ResourceTransferStatus.NOT_TRANSFERRED,
        (
            f"Transfer conservation failed: source loss {source_loss}, "
            f"destination gain {destination_gain}."
        ),
    )
