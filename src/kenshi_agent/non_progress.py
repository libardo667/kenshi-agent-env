from __future__ import annotations

import hashlib

from .models import (
    Action,
    ActionOutcomeAssessment,
    HarvestResourceAction,
    Observation,
    PurchaseItemAction,
    PurchaseStatus,
    ResourceHarvestStatus,
    VisibleUIControl,
    normalize_control_label,
)


# The digest encoding has no independent behavior: callers compare opaque
# values produced by this function, never their spelling. Mutating repr/UTF-8/
# SHA-256 choices only manufactures byte-different but behavior-equivalent IDs.
# pragma: no mutate start
def _canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


# pragma: no mutate end


def _inventory_cell_state(
    control: VisibleUIControl,
) -> tuple[int | str | bool | None, ...]:
    return (
        control.item_base_value,
        control.item_name,
        control.item_quantity,
        control.item_type,
        control.selected_inventory_accepts_item,
        control.section,
    )


def _sorted_cell_state(
    controls: list[VisibleUIControl],
) -> tuple[tuple[int | str | bool | None, ...], ...]:
    return tuple(sorted(_inventory_cell_state(control) for control in controls))


def purchase_retry_state_fingerprint(
    action: PurchaseItemAction,
    observation: Observation,
) -> str | None:
    """Fingerprint only state that can make the exact failed purchase differ.

    Kenshi inventory capacity is spatial. Free cell count and visual movement
    are both insufficient: an item needs a contiguous rectangle, and ARRANGE
    can shuffle icons without changing whether that rectangle exists. Kenshi's
    own selected-character fit verdict therefore carries authority; elapsed
    time, bounds churn, messages, and the mere execution of ARRANGE do not.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return None
    ui = telemetry.ui
    if (
        ui.active_screen != "trade"
        or ui.visible_controls is None
        or ui.visible_controls_complete is not True
        or ui.selected_character_id is None
        or telemetry.game.money is None
    ):
        return None

    selected = [
        character
        for character in telemetry.squad
        if character.id == ui.selected_character_id and character.selected
    ]
    if len(selected) != 1:
        return None
    selected_character = selected[0]
    seller_window = normalize_control_label(action.window)
    if not seller_window:
        return None

    seller_cells = [
        control
        for control in ui.visible_controls
        if normalize_control_label(control.window) == seller_window
        and control.role == "item"
        and normalize_control_label(control.label)
        == normalize_control_label(action.cell_label)
        and control.item_name == action.item_name
    ]
    priced_cells = [
        control
        for control in seller_cells
        if control.item_base_value == action.expected_price
    ]
    if priced_cells:
        seller_cells = priced_cells
    if not seller_cells or any(
        control.selected_inventory_accepts_item is None
        for control in seller_cells
    ):
        return None
    seller = [
        entity
        for entity in telemetry.nearby_entities
        if entity.id == action.seller_id
        and entity.shop_inventory_owner is True
        and normalize_control_label(entity.name) == seller_window
    ]
    if len(seller) != 1:
        return None

    return _canonical_fingerprint(
        (
            telemetry.identity_session_id,
            telemetry.game.money,
            selected_character.id,
            _sorted_cell_state(seller_cells),
        )
    )


def harvest_retry_state_fingerprint(
    action: HarvestResourceAction,
    observation: Observation,
) -> str | None:
    """Fingerprint state that can make one failed harvest class differ.

    Destination capacity is spatial and is only authoritative while the
    controller has the output item and destination window open. After cleanup,
    a complete actor inventory is the conservative durable boundary: a retry is
    not new work until that inventory or the exact resource/task state changes.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return None
    actors = [member for member in telemetry.squad if member.id == action.actor_id]
    targets = [
        target
        for target in telemetry.world_targets
        if target.id == action.target_id and target.kind == "natural_resource"
    ]
    if (
        len(actors) != 1
        or actors[0].inventory_complete is not True
        or len(targets) != 1
    ):
        return None
    actor = actors[0]
    target = targets[0]
    inventory = tuple(
        sorted(
            [
                (
                    item.name,
                    item.quantity,
                    item.category,
                    item.charges,
                    item.stolen,
                    item.item_name,
                    item.item_base_value,
                    item.item_sell_value,
                    item.item_quantity,
                    item.item_type,
                    item.section,
                    item.equipped,
                )
                for item in actor.inventory
            ],
            key=repr,
        )
    )
    return _canonical_fingerprint(
        (
            telemetry.identity_session_id,
            actor.id,
            actor.current_goal,
            inventory,
            target.id,
            target.mining_resource_level,
            tuple(target.context_actions),
            target.default_task,
        )
    )


def retry_state_fingerprint(action: Action, observation: Observation) -> str | None:
    if isinstance(action, PurchaseItemAction):
        return purchase_retry_state_fingerprint(action, observation)
    if isinstance(action, HarvestResourceAction):
        return harvest_retry_state_fingerprint(action, observation)
    return None


def _same_retry_class(candidate: Action, previous: Action) -> bool:
    if isinstance(candidate, PurchaseItemAction) and isinstance(
        previous, PurchaseItemAction
    ):
        return (
            candidate.cell_label,
            candidate.item_name,
            candidate.expected_price,
            candidate.window,
            candidate.seller_id,
        ) == (
            previous.cell_label,
            previous.item_name,
            previous.expected_price,
            previous.window,
            previous.seller_id,
        )
    if isinstance(candidate, HarvestResourceAction) and isinstance(
        previous, HarvestResourceAction
    ):
        return (candidate.actor_id, candidate.target_id) == (
            previous.actor_id,
            previous.target_id,
        )
    return candidate == previous


def _definitive_no_op_status(action: Action) -> str | None:
    if isinstance(action, PurchaseItemAction):
        return PurchaseStatus.NOT_PURCHASED.value
    if isinstance(action, HarvestResourceAction):
        return ResourceHarvestStatus.NOT_HARVESTED.value
    return None


def _retry_state_label(action: Action) -> str:
    if isinstance(action, PurchaseItemAction):
        return "purchase"
    if isinstance(action, HarvestResourceAction):
        return "harvest"
    return "action"


def unchanged_definitive_no_op_reason(
    action: Action,
    observation: Observation,
) -> str | None:
    """Reject an exact retry until current evidence proves its cause changed."""

    expected_status = _definitive_no_op_status(action)
    if expected_status is None:
        return None
    current_fingerprint = retry_state_fingerprint(action, observation)
    current_session = (
        observation.telemetry.identity_session_id
        if observation.telemetry is not None
        else None
    )
    for outcome in reversed(observation.recent_action_outcomes):
        if not _same_retry_class(action, outcome.action):
            continue
        if (
            outcome.identity_session_id is not None
            and current_session is not None
            and outcome.identity_session_id != current_session
        ):
            return None
        if not (
            outcome.executed
            and outcome.assessment is ActionOutcomeAssessment.NO_OP
            and outcome.causal_revision_advanced is True
            and outcome.semantic_status == expected_status
        ):
            return None
        if (
            outcome.retry_state_fingerprint is not None
            and current_fingerprint is not None
            and outcome.retry_state_fingerprint != current_fingerprint
        ):
            return None
        return (
            f"repeats definitive no-op {outcome.outcome_id}; relevant "
            f"{_retry_state_label(action)} state is unchanged or cannot be "
            "proved changed"
        )
    return None
