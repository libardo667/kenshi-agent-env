"""Authoritative contracts for reusable semantic actions.

Before this catalog, an action's meaning was scattered: risk lived in
`planning`, control-mode rules in `safety`, routing in the executor, pointer
classification in the live environment, and the actual affordance in a
scenario-named macro string. Adding one reusable intention therefore meant
editing every one of those exact-name branches.

A contract states, in one place, everything the rest of the runtime needs to
route one typed action safely: who may author it, what capabilities it needs,
what its arguments must bind to in current observation, what it costs against
risk budgets, how it executes, and what evidence its receipt must carry. The
registry is deliberately a small typed Python mapping rather than a plugin
framework — it is meant to be read, and expanded, in one sitting.

The one rule that outranks convenience: an action may bind only to references
the current observation actually advertises, and a duplicate or ambiguous
reference fails closed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from .models import (
    GAME_BINDING_KEYS,
    Action,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ControlMode,
    DismissScreenAction,
    Disposition,
    EquipItemAction,
    IdempotencyPolicy,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NormalizedPointerBounds,
    Observation,
    PlanEnvelope,
    PointerActionClass,
    PurchaseItemAction,
    RecoverCameraViewAction,
    ScrollScreenAction,
    SellItemAction,
    SkillAction,
    UseGameBindingAction,
    WorldStateRevision,
    dialogue_targets,
    normalize_control_label,
)

# The installed plug-in still names this capability and wire command after the
# vendor specialization it was first built for, but the fact it authorizes is
# "the caller may issue a pathing order to a valid dialogue target". The generic
# names are the contract vocabulary; the legacy names remain accepted aliases so
# the proven DLL keeps working without a rebuild.
NATIVE_APPROACH_CAPABILITY = "control.approach_dialogue_target"
LEGACY_NATIVE_APPROACH_CAPABILITY = "control.approach_vendor"
NATIVE_APPROACH_CAPABILITY_ALIASES: frozenset[str] = frozenset(
    {NATIVE_APPROACH_CAPABILITY, LEGACY_NATIVE_APPROACH_CAPABILITY}
)
NATIVE_APPROACH_WIRE_COMMAND: Literal["approach_confirmed_vendor"] = "approach_confirmed_vendor"

NATIVE_MOVE_CAPABILITY = "control.move_to_character"
NATIVE_DIRECTION_CAPABILITY = "control.move_in_direction"
NATIVE_DIRECTION_WIRE_COMMAND: Literal["move_in_direction"] = "move_in_direction"
NATIVE_MOVE_WIRE_COMMAND: Literal["move_to_character"] = "move_to_character"
NATIVE_WALK_DESTINATION_REACHED_RESULT = "walk_destination_reached"

VISIBLE_CONTROLS_CAPABILITY = "ui.visible_controls"
CAMERA_RECOVERY_CAPABILITY = "camera.recovery"


class ActionExecution(StrEnum):
    """How the executor must run an action, not what the action means."""

    ATOMIC_HANDLER = "atomic_handler"
    MONITORED_OPTION = "monitored_option"


@dataclass(frozen=True, slots=True)
class ActionRiskCost:
    """What one attempt of this action spends from a plan's risk budgets."""

    pointer_actions: int = 0
    purchase_actions: int = 0
    native_assisted_actions: int = 0

    def as_tuple(self) -> tuple[int, int, int]:
        return (
            self.pointer_actions,
            self.purchase_actions,
            self.native_assisted_actions,
        )


@dataclass(frozen=True, slots=True)
class ReferenceBinding:
    """The result of resolving an action's arguments against current state."""

    bound: bool
    reason: str
    target_id: str | None = None
    resolved_label: str | None = None
    resolved_role: str | None = None
    resolved_bounds: NormalizedPointerBounds | None = None
    source_revision: WorldStateRevision | None = None
    # For item cells: what the game itself says the cell holds and is worth.
    item_name: str | None = None
    item_value: int | None = None
    # Camera-recovery-only facts resolved from the current world HUD.
    selected_character_name: str | None = None
    floor: int | None = None
    floor_up_bounds: NormalizedPointerBounds | None = None
    floor_down_bounds: NormalizedPointerBounds | None = None


def _unbound(reason: str) -> ReferenceBinding:
    return ReferenceBinding(bound=False, reason=reason)


def _capability_condition(path: ConditionPath, *, max_age_seconds: float) -> Condition:
    return Condition(
        kind=ConditionKind.CAPABILITY,
        path=path,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=max_age_seconds,
    )


def bind_approach_dialogue_target(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind an approach to one exact current dialogue target.

    Deliberately target-generic: the only question asked is whether the exact
    stable id is, right now, one of the people telemetry already says the agent
    could talk to. Vendor status is not consulted, so a shopkeeper and a
    wandering civilian bind identically.
    """

    if not isinstance(action, ApproachDialogueTargetAction):
        return _unbound("Action is not an approach_dialogue_target action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the approach target.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the target cannot be bound.")
    ui = telemetry.ui
    if ui.dialogue_open:
        if ui.dialogue_target_id == action.target_id:
            return _unbound(
                "Dialogue with the requested target is already open; the approach "
                "is already satisfied and must not be dispatched again."
            )
        return _unbound(
            "A dialogue with a different target is open and blocks a new approach; "
            "finish or close that dialogue first."
        )
    if ui.modal_open:
        return _unbound(
            "A modal interface is open and blocks a new approach; close it first."
        )
    matches = [
        target
        for target in dialogue_targets(telemetry.nearby_entities)
        if target.id == action.target_id
    ]
    if not matches:
        return _unbound(
            f"Target {action.target_id!r} is not a current valid dialogue target."
        )
    if len(matches) > 1:
        return _unbound(
            f"Target {action.target_id!r} matches {len(matches)} current entities; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to current dialogue target {target.name!r} ({target.id}) at "
            f"distance {target.distance if target.distance is not None else 'unknown'}."
        ),
        target_id=target.id,
        source_revision=observation.world_revision,
    )


def bind_move_to_character(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a walk to one exact currently observed nearby character.

    Deliberately looser than the approach binding in one respect and no other:
    the destination need not be talkable, because going somewhere is not the
    same as talking to someone, and requiring talkability is what confined the
    agent to whichever room it started in. Everything else holds - the id must
    match exactly one current entity, and an ambiguous or absent one fails
    closed.
    """

    if not isinstance(action, MoveToCharacterAction):
        return _unbound("Action is not a move_to_character action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the destination.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the destination cannot be bound.")
    matches = [
        entity for entity in telemetry.nearby_entities if entity.id == action.target_id
    ]
    if not matches:
        return _unbound(
            f"Destination {action.target_id!r} is not a currently observed nearby "
            "character."
        )
    if len(matches) > 1:
        return _unbound(
            f"Destination {action.target_id!r} matches {len(matches)} current "
            "entities; an ambiguous reference fails closed."
        )
    target = matches[0]
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to current nearby character {target.name!r} ({target.id}) at "
            f"distance {target.distance if target.distance is not None else 'unknown'}."
        ),
        target_id=target.id,
        source_revision=observation.world_revision,
    )


def bind_move_in_direction(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a directional walk to the character actually doing the walking.

    There is no external reference to resolve - the intended destination is
    derived from where the character already is - so this binder proves only
    that the game is running and somebody is selected to receive the order.
    The monitored option then owns the walk through the exact keyed native
    acknowledgement rather than inventing an external target.
    """

    if not isinstance(action, MoveInDirectionAction):
        return _unbound("Action is not a move_in_direction action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the walk.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the walk cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("The game is not loaded, so no order can be given.")
    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        return _unbound(
            f"{len(selected)} characters are selected; exactly one must be, so the "
            "order has an unambiguous walker."
        )
    walker = selected[0]
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to selected character {walker.name!r} walking "
            f"{action.distance_units:.0f} units on bearing "
            f"{action.bearing_degrees:.0f}."
        ),
        resolved_label=walker.name,
        source_revision=observation.world_revision,
    )


def bind_visible_control(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a control activation to exactly one currently advertised control.

    Bounds are read from telemetry, never authored. Any duplicate of the same
    label and role fails closed rather than picking the first, because "the
    button that says X" is not a reference when two of them say X.
    """

    if not isinstance(action, ActivateVisibleControlAction):
        return _unbound("Action is not an activate_visible_control action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the visible control.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the control cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so visible "
            "controls are unknown rather than absent."
        )
    controls = telemetry.ui.visible_controls
    if controls is None:
        return _unbound("The interface reports no current visible-control set.")
    wanted = normalize_control_label(action.exact_label)
    matches = [
        control
        for control in controls
        if normalize_control_label(control.label) == wanted
        and control.role == action.role
        # An empty `window` means "do not narrow"; naming one disambiguates a
        # label that several open windows share, such as a close button.
        and (not action.window or control.window == action.window)
    ]
    if not matches:
        return _unbound(
            f"No current {action.role} control matches label {action.exact_label!r}."
        )
    if len(matches) > 1:
        windows = sorted({control.window or "<no window>" for control in matches})
        return _unbound(
            f"{len(matches)} current {action.role} controls match label "
            f"{action.exact_label!r} (in {windows}); an ambiguous reference fails "
            "closed. Name the window to narrow it."
        )
    control = matches[0]
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to exactly one current {control.role} control "
            f"{control.label!r} at its observed bounds."
        ),
        resolved_label=control.label,
        resolved_role=control.role,
        resolved_bounds=control.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
    )


ITEM_ROLE = "item"


def _window_belongs_to(window: str, owner_name: str | None) -> bool:
    """Whether an inventory window caption names this character.

    Kenshi captions inventory windows in upper case ("HEP") while the character
    is named "Hep", so this has to be case-insensitive; comparing exactly would
    reject every real window.
    """

    if not owner_name:
        return False
    return normalize_control_label(window) == normalize_control_label(owner_name)



def _bind_item_cell(
    cell_label: str,
    observation: Observation,
    *,
    window: str | None = None,
    item_value: int | None = None,
) -> ReferenceBinding:
    """Resolve one exact inventory or shop cell from current telemetry.

    `window` narrows the search to one open inventory. A trade screen shows two
    side by side and the cell ordinals run across both, so on that screen the
    label alone is not a reference to anything in particular.

    `item_value` narrows further, because a label is not unique either: the live
    Barman stocks five cells all labelled "Tooth Pick", two with base value 809
    and three with base value 390 - different weapon grades wearing the same
    name. Base value separates them; it is not the final shop price.
    """

    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the item cell.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the item cell cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so item "
            "cells are unknown rather than absent."
        )
    wanted = normalize_control_label(cell_label)
    matches = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.role == ITEM_ROLE
        and normalize_control_label(control.label) == wanted
        and (window is None or control.window == window)
    ]
    if not matches:
        where = f" in window {window!r}" if window is not None else ""
        return _unbound(f"No current item cell matches {cell_label!r}{where}.")

    if len(matches) > 1 and item_value is not None:
        # Base value is only ever a tie-breaker between cells that share a name - the
        # Barman stocks five "Tooth Pick" at two grades. It is deliberately not
        # an assertion about the sale: a shop charges its own multiplier on an
        # item's value and that asking price is never exported, so requiring
        # equality refused every real purchase while reporting that the cell had
        # vanished. Narrow when it helps; never refuse.
        narrowed = [control for control in matches if control.item_value == item_value]
        if narrowed:
            matches = narrowed

    if len(matches) > 1:
        # Ambiguity only matters when the candidates differ in a way that could
        # change the outcome. A shop holding five identical Tooth Picks at the
        # same base value in the same window offers five interchangeable cells, and
        # refusing all of them makes stacked stock unbuyable - which is what the
        # live Barman's shelf actually did. Distinguishable duplicates still
        # fail closed.
        distinct = {
            (control.window, control.item_name, control.item_value)
            for control in matches
        }
        if len(distinct) > 1 or matches[0].item_name is None:
            return _unbound(
                f"{len(matches)} current item cells match {cell_label!r} and they "
                "are not interchangeable; an ambiguous reference fails closed."
            )
    cell = matches[0]
    return ReferenceBinding(
        bound=True,
        reason=f"Bound to current item cell {cell.label!r} at its observed bounds.",
        resolved_label=cell.label,
        resolved_role=cell.role,
        resolved_bounds=cell.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
        item_name=cell.item_name,
        item_value=cell.item_value,
    )


def bind_purchase_item(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a purchase to one exact named seller-owned cell.

    Current producers export the cell's item facts directly. Older producers
    fall back to a tooltip bound to the same cell. The exported `item_value` is
    base worth rather than an authoritative shop charge, so it may disambiguate
    stock but cannot prove the final debit. Deliberately says nothing about
    *what kind* of item is worth buying: that is task intent, not purchase
    safety.
    """

    if not isinstance(action, PurchaseItemAction):
        return _unbound("Action is not a purchase_item action.")
    cell = _bind_item_cell(
        action.cell_label,
        observation,
        window=action.window,
        item_value=action.expected_price,
    )
    if not cell.bound:
        return cell
    telemetry = observation.telemetry
    assert telemetry is not None

    # The cell itself now carries the game's own name and base value, which is
    # stronger evidence than text scraped from a tooltip - and requiring a
    # tooltip forced a hover, a replan, and a second model call before every
    # purchase. Prefer the cell's facts; fall back to the tooltip only when a
    # plug-in too old to export them is installed.
    cell_name = cell.item_name
    cell_value = cell.item_value
    if cell_name is not None and cell_value is not None:
        if action.item_name != cell_name:
            return _unbound(
                f"The cell holds {cell_name!r}, not {action.item_name!r}."
            )
        # Deliberately not checked. `item_value` is the item's worth, not what
        # this shop charges - a trader applies its own multiplier and the asking
        # price is never exported - so an `expected_price` that disagrees is the
        # planner being wrong about something it was never given, not evidence
        # that the wrong item is about to be bought. What the cell *is* remains
        # checked above, which is the part that protects against buying the
        # wrong thing. Money is bounded by the purse and by nothing else here.
    else:
        tooltip_text = telemetry.ui.tooltip_text
        tooltip_bounds = telemetry.ui.tooltip_source_bounds
        if (
            telemetry.ui.tooltip_visible is not True
            or not tooltip_text
            or tooltip_bounds is None
        ):
            return _unbound(
                "This plug-in does not name item cells, so a purchase needs a "
                "visible tooltip; hover the cell first."
            )
        assert cell.resolved_bounds is not None
        centre_x = (cell.resolved_bounds.min_x + cell.resolved_bounds.max_x) / 2.0
        centre_y = (cell.resolved_bounds.min_y + cell.resolved_bounds.max_y) / 2.0
        if not tooltip_bounds.contains(centre_x, centre_y):
            return _unbound(
                f"The visible tooltip does not belong to cell {action.cell_label!r}; "
                "it describes a different widget."
            )
        if action.item_name not in tooltip_text:
            return _unbound(
                f"The tooltip does not name {action.item_name!r}, so the item being "
                "bought is not the item described."
            )
        price_pattern = rf"(?<![A-Za-z0-9])c\.{action.expected_price}(?![0-9])"
        if re.search(price_pattern, tooltip_text) is None:
            return _unbound(
                f"The tooltip does not show price c.{action.expected_price}; the "
                "expected price disagrees with the interface."
            )

    seller = next(
        (entity for entity in telemetry.nearby_entities if entity.id == action.seller_id),
        None,
    )
    if (
        seller is None
        or seller.shop_inventory_owner is not True
        or seller.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound(
            "The seller is not a verified non-hostile shop owner."
        )
    # Ownership is proved by the cell sitting in the seller's own inventory
    # window, not by a count of shop traders in the world. `active_shop_trader_count`
    # is that registry - it read 5 in a bar with no trade open at all - so gating
    # on it being exactly 1 made this action unbindable everywhere.
    if not _window_belongs_to(action.window, seller.name):
        return _unbound(
            f"Window {action.window!r} is not the seller's own inventory "
            f"({seller.name!r}); the cell is not the shop's stock."
        )

    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound {action.item_name!r} to seller-owned cell "
            f"{cell.resolved_label!r} for seller {action.seller_id}; declared "
            f"value estimate c.{action.expected_price}."
        ),
        target_id=action.seller_id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
    )



def bind_sell_item(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a sale to a cell in the *selected character's own* inventory.

    The one thing that must not be got wrong here is whose item is being sold.
    A trade screen shows two inventories side by side, and the cell ordinals run
    across both, so "cell 12" alone is not a reference. The window caption must
    match the selected character's own name, which is observed rather than
    asserted; anything else - including the trader's window - fails closed.
    """

    if not isinstance(action, SellItemAction):
        return _unbound("Action is not a sell_item action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the sale.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the sale cannot be bound.")

    selected = next(
        (character for character in telemetry.squad if character.selected),
        None,
    )
    if selected is None or not selected.name:
        return _unbound(
            "No single selected character is named, so ownership of the cell "
            "cannot be established."
        )
    if not _window_belongs_to(action.window, selected.name):
        return _unbound(
            f"Window {action.window!r} is not the selected character's own "
            f"inventory ({selected.name!r}); selling from another owner's window "
            "is not permitted."
        )

    cell = _bind_item_cell(action.cell_label, observation, window=action.window)
    if not cell.bound:
        return cell
    if cell.item_name is not None and action.item_name != cell.item_name:
        return _unbound(f"The cell holds {cell.item_name!r}, not {action.item_name!r}.")

    buyer = next(
        (entity for entity in telemetry.nearby_entities if entity.id == action.buyer_id),
        None,
    )
    if (
        buyer is None
        or buyer.shop_inventory_owner is not True
        or buyer.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound("The buyer is not a verified non-hostile shop owner.")
    # A trade is open with this buyer exactly when their own inventory is on
    # screen beside ours. Counting shop traders in the world says nothing about
    # that - the same misreading that made `purchase_item` unbindable.
    if not any(
        control.role == ITEM_ROLE and _window_belongs_to(control.window, buyer.name)
        for control in (telemetry.ui.visible_controls or [])
    ):
        return _unbound(
            f"No inventory window belonging to {buyer.name!r} is open, so there is "
            "no trade to sell into."
        )

    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to cell {cell.resolved_label!r} in {selected.name!r}'s own "
            f"inventory, holding {action.item_name!r}, sold to {action.buyer_id}."
        ),
        target_id=action.buyer_id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
    )



def bind_equip_item(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind an equip to our own cell, and only while no trade is open.

    Right-click means "equip this" in an inventory and "sell this" in a trade,
    and Kenshi decides which by whether a trade partner is registered - not by
    anything in the gesture. An equip issued with a shop window up is therefore
    a sale that no postcondition can undo. Refusing whenever a trader is active
    is the only safe reading of an ambiguous gesture.
    """

    if not isinstance(action, EquipItemAction):
        return _unbound("Action is not an equip_item action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the equip.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the equip cannot be bound.")

    # `active_shop_trader_count` counts shop traders loaded in the world - it
    # reads 5 in a bar with nothing open - so it says nothing about whether a
    # trade is in progress. What does: a trade shows the shop's inventory
    # alongside ours, so exactly one open inventory window means the only one
    # open is our own, and this right-click equips rather than sells.
    if telemetry.ui.open_inventory_windows != 1:
        return _unbound(
            f"{telemetry.ui.open_inventory_windows} inventory windows are open; "
            "equipping requires exactly one, our own. With a shop's inventory "
            "also open this same right-click sells the item instead."
        )

    selected = next(
        (character for character in telemetry.squad if character.selected),
        None,
    )
    if selected is None or not selected.name:
        return _unbound(
            "No single selected character is named, so ownership of the cell "
            "cannot be established."
        )
    if not _window_belongs_to(action.window, selected.name):
        return _unbound(
            f"Window {action.window!r} is not the selected character's own "
            f"inventory ({selected.name!r})."
        )

    cell = _bind_item_cell(action.cell_label, observation, window=action.window)
    if not cell.bound:
        return cell
    if cell.item_name is not None and action.item_name != cell.item_name:
        return _unbound(f"The cell holds {cell.item_name!r}, not {action.item_name!r}.")

    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to cell {cell.resolved_label!r} holding {action.item_name!r} in "
            f"{selected.name!r}'s own inventory, with no trade open."
        ),
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
    )


def bind_dismiss_screen(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a dismissal to the screen that is actually open right now.

    The reference is the current screen. Refusing when the planner's belief
    disagrees with observation is what stops a stray Escape from closing
    something the planner never looked at.
    """

    if not isinstance(action, DismissScreenAction):
        return _unbound("Action is not a dismiss_screen action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the current screen.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the current screen cannot be bound.")
    current = telemetry.ui.active_screen
    if current is None:
        return _unbound("The current screen is unknown, so nothing may be dismissed.")
    if current != action.expected_screen:
        return _unbound(
            f"Expected screen {action.expected_screen!r} but the interface reports "
            f"{current!r}; dismissing the wrong screen is not permitted."
        )
    if not action.window:
        if telemetry.ui.dialogue_target_id is not None:
            # Escape does not back out of a Kenshi conversation. With a dialogue
            # open it opens the ESC menu, which costs a step to undo and leaves
            # the conversation exactly where it was. A conversation ends by
            # choosing the option that ends it.
            return _unbound(
                "A Kenshi conversation is not dismissed with a key: Escape opens "
                "the ESC menu and leaves the dialogue open. End it by choosing the "
                "closing dialogue option with activate_visible_control."
            )
        # A keyed screen with no window of its own is dismissed with the key.
        return ReferenceBinding(
            bound=True,
            reason=f"Bound to the currently open {current!r} screen.",
            resolved_label=current,
            source_revision=observation.world_revision,
        )

    # A named window is closed by its own close box, positioned from the rect
    # the window itself reports.
    owned = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.window == action.window
    ]
    if not owned:
        return _unbound(
            f"No window captioned {action.window!r} is currently open, so it "
            "cannot be closed."
        )
    rect = max(
        (control.bounds for control in owned),
        key=lambda b: (b.max_x - b.min_x) * (b.max_y - b.min_y),
    )
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to the {action.window!r} window on the {current!r} screen; its "
            "close box follows the window's own observed rect."
        ),
        resolved_label=action.window,
        resolved_bounds=rect.model_copy(deep=True),
        source_revision=observation.world_revision,
    )


def bind_use_game_binding(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a keypress to the game actually being in a state to receive it.

    There is no widget to resolve here - the reference is the game itself. What
    still has to be proved is that Kenshi is loaded and listening, because a
    keystroke sent at a loading screen or a dead telemetry stream vanishes with
    no evidence either way, which is exactly the silent failure this action
    exists to replace.
    """

    if not isinstance(action, UseGameBindingAction):
        return _unbound("Action is not a use_game_binding action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available, so the game cannot be bound.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the game cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("Kenshi has no loaded game to receive a binding.")
    key = GAME_BINDING_KEYS.get(action.binding)
    if key is None:
        return _unbound(f"No key is mapped for binding {action.binding.value!r}.")
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound {action.binding.value!r} to the current hard-coded default "
            f"Kenshi key {key!r} on a loaded game."
        ),
        resolved_label=action.binding.value,
        source_revision=observation.world_revision,
    )


def bind_recover_camera_view(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind recovery to one selected character and the current world HUD.

    The model names no coordinates. The controller resolves the selected
    character's portrait, current floor, and both floor arrows from fresh
    visible-control telemetry. Any ambiguity fails closed before input.
    """

    if not isinstance(action, RecoverCameraViewAction):
        return _unbound("Action is not a recover_camera_view action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind camera recovery.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so camera recovery cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("Kenshi has no loaded game whose camera can be recovered.")
    if telemetry.ui.active_screen != "world":
        return _unbound(
            "Camera recovery is allowed only on the world screen; current screen is "
            f"{telemetry.ui.active_screen!r}."
        )
    if telemetry.ui.modal_open is not False:
        return _unbound("Camera recovery requires a confirmed closed modal.")
    if telemetry.ui.dialogue_open is not False:
        return _unbound("Camera recovery requires dialogue to be closed.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound("Visible-control telemetry is unavailable.")
    if CAMERA_RECOVERY_CAPABILITY not in telemetry.capabilities:
        return _unbound("Window capture/scoring is unavailable.")

    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        return _unbound(
            f"{len(selected)} characters are selected; camera recovery requires "
            "exactly one unambiguous follow target."
        )
    character = selected[0]
    controls = telemetry.ui.visible_controls or []
    portrait_matches = [
        control
        for control in controls
        if control.role == "text"
        and normalize_control_label(control.label)
        == normalize_control_label(character.name)
        and control.bounds.min_y >= 0.75
    ]
    if len(portrait_matches) != 1:
        return _unbound(
            f"Selected character {character.name!r} has {len(portrait_matches)} "
            "unambiguous lower-HUD portrait labels; exactly one is required."
        )

    floor_matches: list[tuple[int, NormalizedPointerBounds]] = []
    for control in controls:
        if control.role != "text":
            continue
        match = re.fullmatch(r"\s*Floor\s+(-?\d+)\s*", control.label, re.IGNORECASE)
        if match is not None:
            floor_matches.append((int(match.group(1)), control.bounds))
    if len(floor_matches) != 1:
        return _unbound(
            f"The HUD exposes {len(floor_matches)} current floor labels; exactly "
            "one is required."
        )

    up_matches = [
        control
        for control in controls
        if control.role == "button"
        and control.label.casefold().endswith("_floorarrowup")
    ]
    down_matches = [
        control
        for control in controls
        if control.role == "button"
        and control.label.casefold().endswith("_floorarrowdown")
    ]
    if len(up_matches) != 1 or len(down_matches) != 1:
        return _unbound(
            "The HUD must expose exactly one floor-up and one floor-down button; "
            f"found {len(up_matches)} up and {len(down_matches)} down."
        )

    portrait = portrait_matches[0]
    floor = floor_matches[0][0]
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound camera recovery to selected character {character.name!r} "
            f"({character.id}), portrait {portrait.label!r}, and floor {floor}."
        ),
        target_id=character.id,
        resolved_label=portrait.label,
        resolved_role=portrait.role,
        resolved_bounds=portrait.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
        selected_character_name=character.name,
        floor=floor,
        floor_up_bounds=up_matches[0].bounds.model_copy(deep=True),
        floor_down_bounds=down_matches[0].bounds.model_copy(deep=True),
    )


def bind_scroll_screen(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a scroll to the observed bounds of one currently open window.

    The reference is the window, not a coordinate: the scroll lands at the
    centre of the rectangle its own controls occupy. A window with nothing
    exported in it fails closed rather than scrolling the world behind it,
    which is what a bare coordinate would have done.
    """

    if not isinstance(action, ScrollScreenAction):
        return _unbound("Action is not a scroll_screen action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the window.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the window cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so open "
            "windows are unknown rather than absent."
        )
    controls = telemetry.ui.visible_controls
    if not controls:
        return _unbound("The interface reports no current visible-control set.")
    members = [control for control in controls if control.window == action.window]
    if not members:
        return _unbound(
            f"No control currently belongs to a window named {action.window!r}, "
            "so there is nothing to scroll."
        )
    bounds = NormalizedPointerBounds(
        min_x=min(control.bounds.min_x for control in members),
        min_y=min(control.bounds.min_y for control in members),
        max_x=max(control.bounds.max_x for control in members),
        max_y=max(control.bounds.max_y for control in members),
    )
    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to window {action.window!r}, whose {len(members)} exported "
            "controls span the region to scroll."
        ),
        resolved_label=action.window,
        resolved_role="window",
        resolved_bounds=bounds,
        source_revision=observation.world_revision,
    )


@dataclass(frozen=True, slots=True)
class ActionContract:
    """Everything the runtime must know to route one typed action safely."""

    kind: str
    version: str
    model: type[BaseModel]
    summary: str
    argument_source: str
    planner_visible: bool
    allowed_control_modes: frozenset[ControlMode]
    required_capabilities: frozenset[str]
    capability_aliases: frozenset[str]
    pointer_class: PointerActionClass
    native_assisted: bool
    risk: ActionRiskCost
    max_primitive_actions: int
    reference_fields: tuple[str, ...]
    idempotency: IdempotencyPolicy
    execution: ActionExecution
    receipt_kind: str
    bind: Callable[[Action, Observation], ReferenceBinding]
    # Fields a step's own success conditions must check for this action. An
    # action whose effect is invisible in its receipt needs the plan to verify
    # it against the world, or a no-op reports success: three consecutive live
    # purchases moved no money, each reported DONE, and the agent looped because
    # nothing it could see said otherwise.
    verification_paths: frozenset[str] = frozenset()
    # The handler itself returns a typed terminal verdict based on evidence it
    # owns, so the planner must not invent a redundant postcondition.
    controller_verified: bool = False

    def missing_capabilities(self, capabilities: set[str] | frozenset[str]) -> list[str]:
        """Required capabilities absent from an observation, alias-aware.

        A capability with accepted aliases is satisfied by any one of them, so a
        plug-in that still emits the legacy name is not treated as incapable.
        """

        missing: list[str] = []
        for required in sorted(self.required_capabilities):
            if required in capabilities:
                continue
            if required in self.capability_aliases and (
                self.capability_aliases & set(capabilities)
            ):
                continue
            missing.append(required)
        return missing

    def allows_control_mode(self, control_mode: ControlMode) -> bool:
        return control_mode in self.allowed_control_modes


APPROACH_DIALOGUE_TARGET_CONTRACT = ActionContract(
    kind="approach_dialogue_target",
    version="1.0",
    model=ApproachDialogueTargetAction,
    summary=(
        "Issue Kenshi's native talk-to order for one exact current target. The "
        "native order may open nearby dialogue while paused and otherwise owns "
        "the monitored pathing lifecycle. Do not add a separate unpause step."
    ),
    argument_source="target_id must be an exact id from the observation's dialogue_targets.",
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_APPROACH_CAPABILITY,
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
    ),
    capability_aliases=NATIVE_APPROACH_CAPABILITY_ALIASES,
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=ActionRiskCost(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.MONITORED_OPTION,
    receipt_kind="semantic_approach",
    bind=bind_approach_dialogue_target,
)

MOVE_IN_DIRECTION_CONTRACT = ActionContract(
    kind="move_in_direction",
    version="1.0",
    model=MoveInDirectionAction,
    summary=(
        "Walk a bearing and distance from where the character stands, ordering "
        "a walk to a bare point rather than toward anyone. One monitored option "
        "owns the targetless native order through its exact command vector. "
        "Native completion is reported as walk_destination_reached."
    ),
    argument_source=(
        "bearing_degrees is clockwise from north (0 N, 90 E, 180 S, 270 W); "
        "distance_units is how far to walk. Neither is read from the "
        "observation - they are chosen."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({NATIVE_DIRECTION_CAPABILITY, "squad.health"}),
    capability_aliases=frozenset({NATIVE_DIRECTION_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=ActionRiskCost(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=(),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.MONITORED_OPTION,
    receipt_kind="semantic_move",
    bind=bind_move_in_direction,
)

MOVE_TO_CHARACTER_CONTRACT = ActionContract(
    kind="move_to_character",
    version="1.0",
    model=MoveToCharacterAction,
    summary=(
        "Walk to one exact currently observed nearby character without talking "
        "to them. This is how the agent goes somewhere: nearby characters are "
        "reported within four hundred units, so someone standing where you want "
        "to be is a destination. One monitored option owns the whole walk."
    ),
    argument_source=(
        "target_id must be an exact id from the observation's "
        "telemetry.nearby_entities."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_MOVE_CAPABILITY,
            "identity.stable_handles",
            "nearby.characters",
        }
    ),
    capability_aliases=frozenset({NATIVE_MOVE_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=ActionRiskCost(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.MONITORED_OPTION,
    receipt_kind="semantic_move",
    bind=bind_move_to_character,
)

ACTIVATE_VISIBLE_CONTROL_CONTRACT = ActionContract(
    kind="activate_visible_control",
    version="1.0",
    model=ActivateVisibleControlAction,
    summary=(
        "Activate exactly one control the interface currently advertises, using "
        "its observed bounds re-resolved inside the input lease."
    ),
    argument_source=(
        "exact_label and role must match exactly one non-ambiguous entry of the "
        "observation's visible_controls."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({VISIBLE_CONTROLS_CAPABILITY}),
    capability_aliases=frozenset(),
    # Bounds come from current telemetry and are re-read inside the lease, so
    # this action survives a resolution change and needs no calibrated profile.
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=ActionRiskCost(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("exact_label", "role"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_control",
    bind=bind_visible_control,
)

DISMISS_SCREEN_CONTRACT = ActionContract(
    kind="dismiss_screen",
    version="1.0",
    model=DismissScreenAction,
    summary=(
        "Close one currently bound trade or inventory window toward the world "
        "view. Active dialogue instead ends through an exact visible reply."
    ),
    argument_source=(
        "expected_screen must equal telemetry.ui.active_screen; inventory/trade "
        "also name the exact current owner window. Do not use for active dialogue."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    # One configured key; it carries no screen position at all.
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=False,
    risk=ActionRiskCost(),
    max_primitive_actions=1,
    reference_fields=("expected_screen",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_dismiss",
    bind=bind_dismiss_screen,
)

PURCHASE_ITEM_CONTRACT = ActionContract(
    kind="purchase_item",
    version="1.0",
    model=PurchaseItemAction,
    summary=(
        "Buy the item in one exact named seller-owned cell. Current cell facts "
        "bind identity; the final shop charge is confirmed only by later money."
    ),
    argument_source=(
        "cell_label, item_name, expected_price (from item_value), and window "
        "from one visible_controls item entry; seller_id is the exact stable id "
        "of that vendor group. item_value is base worth, not a guaranteed final "
        "shop charge."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            VISIBLE_CONTROLS_CAPABILITY,
            "ui.tooltip",
            "ui.inventory",
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.basic",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=ActionRiskCost(pointer_actions=1, purchase_actions=1),
    max_primitive_actions=1,
    reference_fields=("cell_label", "item_name", "expected_price", "seller_id"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_purchase",
    verification_paths=frozenset({"telemetry.game.money"}),
    bind=bind_purchase_item,
)


USE_GAME_BINDING_CONTRACT = ActionContract(
    kind="use_game_binding",
    version="1.0",
    model=UseGameBindingAction,
    summary=(
        "Press one named Kenshi control through the hard-coded shipped-default "
        "keymap: open inventory, map or stats, pause or set speed, move the "
        "camera, or change selection. This is how screens are entered - do not "
        "hunt for a widget when a binding exists. Customized keymaps are not "
        "currently read."
    ),
    argument_source=(
        "binding must be one of the catalogued GameBinding names; "
        "expected_effect states in one phrase what the press should change, "
        "and the step's success conditions must check it."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    # A keypress needs the game loaded and nothing else; requiring more would
    # withhold the one action that recovers from a screen we cannot identify.
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    # A key carries no screen position at all.
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=False,
    risk=ActionRiskCost(),
    max_primitive_actions=1,
    reference_fields=("binding",),
    # Set at construction below: toggles may not be retried, because a retry
    # undoes the first press instead of repeating it.
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_binding",
    bind=bind_use_game_binding,
)

RECOVER_CAMERA_VIEW_CONTRACT = ActionContract(
    kind="recover_camera_view",
    version="1.0",
    model=RecoverCameraViewAction,
    summary=(
        "Restore a usable selected-character-following world view through one "
        "bounded controller-owned transaction. The caller supplies no camera "
        "parameters and receives already_clear, recovered, or "
        "failed_after_bounded_attempts."
    ),
    argument_source=(
        "No arguments. The controller resolves the one selected character, its "
        "lower-HUD portrait, the current floor, and floor arrows from fresh "
        "telemetry, then scores retained frames."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset(
        {ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}
    ),
    required_capabilities=frozenset(
        {
            CAMERA_RECOVERY_CAPABILITY,
            "camera.position",
            "game.pause",
            "squad.basic",
            VISIBLE_CONTROLS_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=ActionRiskCost(pointer_actions=1),
    max_primitive_actions=15,
    reference_fields=(),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_camera_recovery",
    bind=bind_recover_camera_view,
    controller_verified=True,
)




SCROLL_SCREEN_CONTRACT = ActionContract(
    kind="scroll_screen",
    version="1.0",
    model=ScrollScreenAction,
    summary=(
        "Scroll inside one open window to reveal contents past the first "
        "screenful. Shop stock and inventory that are not currently rendered "
        "are not exported at all, so scrolling is the only way to find them."
    ),
    argument_source=(
        "window must exactly match the `window` of at least one current "
        "visible_controls entry; notches is negative to scroll further down "
        "the list and positive to scroll back up."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({VISIBLE_CONTROLS_CAPABILITY}),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    # A scroll commits nothing: it changes what is rendered, not the world.
    risk=ActionRiskCost(),
    max_primitive_actions=1,
    reference_fields=("window",),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_scroll",
    bind=bind_scroll_screen,
)


SELL_ITEM_CONTRACT = ActionContract(
    kind="sell_item",
    version="1.0",
    model=SellItemAction,
    summary=(
        "Sell one item from the selected character's own inventory to the shop "
        "currently being traded with. The mirror of purchase_item, and the only "
        "way the agent earns money rather than only spending it."
    ),
    argument_source=(
        "cell_label from a visible_controls entry with role 'item'; window must "
        "be the selected character's own name; item_name copied from that "
        "cell's own entry; buyer_id the exact stable id of the one active shop "
        "owner. No price is given: the shop's offer is not exported."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            VISIBLE_CONTROLS_CAPABILITY,
            "ui.inventory",
            "squad.inventory",
            "game.money",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    # Counted against the purchase budget: a sale is as irreversible as a buy.
    risk=ActionRiskCost(pointer_actions=1, purchase_actions=1),
    max_primitive_actions=1,
    reference_fields=("cell_label", "window", "buyer_id"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_sell",
    verification_paths=frozenset({"telemetry.game.money"}),
    bind=bind_sell_item,
)


EQUIP_ITEM_CONTRACT = ActionContract(
    kind="equip_item",
    version="1.0",
    model=EquipItemAction,
    summary=(
        "Equip the item in one cell of the selected character's own inventory. "
        "Refused while any trade is open, because there the same right-click "
        "sells the item instead."
    ),
    argument_source=(
        "cell_label from a visible_controls entry with role 'item'; window must "
        "be the selected character's own name; item_name copied from that "
        "cell's own entry."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {VISIBLE_CONTROLS_CAPABILITY, "ui.inventory", "squad.inventory"}
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=ActionRiskCost(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("cell_label", "window"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_equip",
    bind=bind_equip_item,
)

ACTION_CONTRACTS: dict[str, ActionContract] = {
    contract.kind: contract
    for contract in (
        APPROACH_DIALOGUE_TARGET_CONTRACT,
        MOVE_TO_CHARACTER_CONTRACT,
        MOVE_IN_DIRECTION_CONTRACT,
        ACTIVATE_VISIBLE_CONTROL_CONTRACT,
        DISMISS_SCREEN_CONTRACT,
        PURCHASE_ITEM_CONTRACT,
        USE_GAME_BINDING_CONTRACT,
        RECOVER_CAMERA_VIEW_CONTRACT,
        SCROLL_SCREEN_CONTRACT,
        SELL_ITEM_CONTRACT,
        EQUIP_ITEM_CONTRACT,
    )
}


def contract_for(action: Action) -> ActionContract | None:
    """The contract governing an action, or None for uncontracted actions."""

    return ACTION_CONTRACTS.get(action.kind)


def planner_visible_contracts(
    *,
    control_mode: ControlMode,
    capabilities: set[str] | frozenset[str],
) -> list[ActionContract]:
    """Contracts a planner may currently author, in stable order.

    Availability is truthful: a contract whose capabilities are missing is not
    advertised, so the planner never authors an action the runtime would have to
    refuse.
    """

    return [
        contract
        for contract in sorted(ACTION_CONTRACTS.values(), key=lambda item: item.kind)
        if contract.planner_visible
        and contract.allows_control_mode(control_mode)
        and not contract.missing_capabilities(capabilities)
    ]


@dataclass(slots=True)
class LegacyCompatibilityLedger:
    """Counts legacy macro translations so the old path can be retired on evidence.

    The old and new paths coexist deliberately during migration. Counting is how
    that stays a decision rather than a habit.
    """

    translations: dict[str, int] = field(default_factory=dict)

    def record(self, skill_name: str) -> None:
        self.translations[skill_name] = self.translations.get(skill_name, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.translations.values())

    def summary(self) -> dict[str, int]:
        return dict(sorted(self.translations.items()))


LEGACY_COMPATIBILITY = LegacyCompatibilityLedger()

# The single explicit compatibility seam. Each entry translates one calibrated
# scenario macro into the reusable action that supersedes it. The semantic
# actions themselves know nothing about these names.
_LEGACY_APPROACH_SKILLS = frozenset(
    {"approach_confirmed_vendor", "continue_confirmed_vendor_approach"}
)
_LEGACY_CONTROL_LABELS: dict[str, tuple[str, Literal["button", "text"]]] = {
    "choose_show_goods": ("Show me your goods.", "button"),
}


def translate_legacy_plan_actions(
    plan: PlanEnvelope,
    *,
    ledger: LegacyCompatibilityLedger | None = None,
) -> tuple[PlanEnvelope, dict[str, int]]:
    """Admit a legacy-macro plan through the one compatibility seam.

    Returns the plan with translatable macro steps replaced by their reusable
    semantic equivalents, plus a count of what was translated. Untranslatable
    steps are left exactly as they were, so this widens what the new path
    accepts without silently reinterpreting anything it does not understand.
    """

    recorder = ledger if ledger is not None else LEGACY_COMPATIBILITY
    counts: dict[str, int] = {}
    steps = []
    changed = False
    for step in plan.steps:
        action = step.action
        if isinstance(action, SkillAction):
            replacement = translate_legacy_skill(action, ledger=recorder)
            if replacement is not None:
                counts[action.name] = counts.get(action.name, 0) + 1
                steps.append(step.model_copy(update={"action": replacement}, deep=True))
                changed = True
                continue
        steps.append(step)
    if not changed:
        return plan, {}
    return plan.model_copy(update={"steps": steps}, deep=True), counts


def translate_legacy_skill(
    action: SkillAction,
    *,
    ledger: LegacyCompatibilityLedger | None = None,
) -> Action | None:
    """Translate one calibrated legacy macro into its reusable semantic action.

    Returns None when no translation exists, leaving the legacy macro path
    untouched. Translation is recorded so compatibility use stays measurable.
    """

    recorder = ledger if ledger is not None else LEGACY_COMPATIBILITY
    if action.name in _LEGACY_APPROACH_SKILLS:
        target_id = action.argument_map().get("target_id")
        if not isinstance(target_id, str) or not target_id:
            return None
        recorder.record(action.name)
        return ApproachDialogueTargetAction(target_id=target_id)
    label_role = _LEGACY_CONTROL_LABELS.get(action.name)
    if label_role is not None:
        label, role = label_role
        recorder.record(action.name)
        return ActivateVisibleControlAction(exact_label=label, role=role)
    return None
