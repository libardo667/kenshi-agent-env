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
    IdempotencyPolicy,
    InspectItemCellAction,
    NormalizedPointerBounds,
    Observation,
    PlanEnvelope,
    PointerActionClass,
    PurchaseItemAction,
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

VISIBLE_CONTROLS_CAPABILITY = "ui.visible_controls"


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


def _bind_item_cell(
    cell_label: str,
    observation: Observation,
    *,
    window: str | None = None,
) -> ReferenceBinding:
    """Resolve one exact inventory or shop cell from current telemetry.

    `window` narrows the search to one open inventory. A trade screen shows two
    side by side and the cell ordinals run across both, so on that screen the
    label alone is not a reference to anything in particular.
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
    if len(matches) > 1:
        return _unbound(
            f"{len(matches)} current item cells match {cell_label!r}; an ambiguous "
            "reference fails closed."
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


def bind_inspect_item_cell(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    if not isinstance(action, InspectItemCellAction):
        return _unbound("Action is not an inspect_item_cell action.")
    return _bind_item_cell(action.cell_label, observation)


def bind_purchase_item(
    action: Action,
    observation: Observation,
) -> ReferenceBinding:
    """Bind a purchase to a cell whose *own* tooltip names this item and price.

    The calibrated predecessor took model-authored coordinates and merely checked
    they landed inside the tooltip's source. Here the cell is the reference and
    the tooltip must belong to it, so "buy what I am looking at" is checked
    rather than asserted. Deliberately says nothing about *what kind* of item is
    worth buying: that is task intent, not purchase safety.
    """

    if not isinstance(action, PurchaseItemAction):
        return _unbound("Action is not a purchase_item action.")
    cell = _bind_item_cell(action.cell_label, observation)
    if not cell.bound:
        return cell
    telemetry = observation.telemetry
    assert telemetry is not None

    # The cell itself now carries the game's own name and price, which is
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
        if action.expected_price != cell_value:
            return _unbound(
                f"The cell's price is c.{cell_value}, not c.{action.expected_price}."
            )
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
        telemetry.active_shop_trader_count != 1
        or seller is None
        or seller.shop_inventory_owner is not True
        or seller.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound(
            "The seller is not the single verified non-hostile shop owner currently "
            "trading."
        )

    return ReferenceBinding(
        bound=True,
        reason=(
            f"Bound to cell {cell.resolved_label!r}, whose own tooltip names "
            f"{action.item_name!r} at c.{action.expected_price} from seller "
            f"{action.seller_id}."
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
    if action.window != selected.name:
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
        telemetry.active_shop_trader_count != 1
        or buyer is None
        or buyer.shop_inventory_owner is not True
        or buyer.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound(
            "The buyer is not the single verified non-hostile shop owner currently "
            "trading."
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
        # Dialogue has no window of its own and is dismissed with a key.
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


def _dismiss_authorization_conditions(
    action: Action,
    *,
    max_age_seconds: float,
) -> list[Condition]:
    if not isinstance(action, DismissScreenAction):
        return []
    return [
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_ACTIVE_SCREEN,
            operator=ConditionOperator.EQUALS,
            expected=action.expected_screen,
            max_age_seconds=max_age_seconds,
        )
    ]


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
            f"Bound {action.binding.value!r} to Kenshi's own {key!r} key on a "
            "loaded game."
        ),
        resolved_label=action.binding.value,
        source_revision=observation.world_revision,
    )


def _game_binding_authorization_conditions(
    action: Action,
    *,
    max_age_seconds: float,
) -> list[Condition]:
    if not isinstance(action, UseGameBindingAction):
        return []
    return [
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_GAME_LOADED,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=max_age_seconds,
        )
    ]



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


def _approach_authorization_conditions(
    action: Action,
    *,
    max_age_seconds: float,
) -> list[Condition]:
    if not isinstance(action, ApproachDialogueTargetAction):
        return []
    return [
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TARGET_HAS_DIALOGUE,
            operator=ConditionOperator.EQUALS,
            expected=True,
            target_id=action.target_id,
            max_age_seconds=max_age_seconds,
        ),
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TARGET_DISPOSITION,
            operator=ConditionOperator.NOT_EQUALS,
            expected="hostile",
            target_id=action.target_id,
            max_age_seconds=max_age_seconds,
        ),
    ]


def _visible_control_authorization_conditions(
    action: Action,
    *,
    max_age_seconds: float,
) -> list[Condition]:
    if not isinstance(action, ActivateVisibleControlAction):
        return []
    return [
        _capability_condition(
            ConditionPath.UI_VISIBLE_CONTROLS_CAPABILITY,
            max_age_seconds=max_age_seconds,
        ),
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_VISIBLE_CONTROL_COUNT,
            operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
            expected=1,
            max_age_seconds=max_age_seconds,
        ),
    ]


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
    authorization_conditions: Callable[..., list[Condition]]

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
        "Walk to one exact current dialogue target and open dialogue with it. "
        "One monitored option owns the whole approach; it needs no follow-up "
        "continuation action."
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
    authorization_conditions=_approach_authorization_conditions,
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
    authorization_conditions=_visible_control_authorization_conditions,
)

DISMISS_SCREEN_CONTRACT = ActionContract(
    kind="dismiss_screen",
    version="1.0",
    model=DismissScreenAction,
    summary=(
        "Close the screen that is currently open, returning toward the world "
        "view. Names the screen it expects so it cannot dismiss the wrong one."
    ),
    argument_source=(
        "expected_screen must equal the observation's current "
        "telemetry.ui.active_screen."
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
    authorization_conditions=_dismiss_authorization_conditions,
)

INSPECT_ITEM_CELL_CONTRACT = ActionContract(
    kind="inspect_item_cell",
    version="1.0",
    model=InspectItemCellAction,
    summary=(
        "Hover one inventory or shop cell so its tooltip appears, revealing what "
        "the cell actually holds. Emits no click and changes nothing."
    ),
    argument_source=(
        "cell_label must match exactly one current visible_controls entry whose "
        "role is 'item'."
    ),
    planner_visible=True,
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({VISIBLE_CONTROLS_CAPABILITY, "ui.tooltip"}),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    # Moving the pointer is not a pointer *action*: nothing is committed.
    risk=ActionRiskCost(),
    max_primitive_actions=1,
    reference_fields=("cell_label",),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=ActionExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_inspect",
    bind=bind_inspect_item_cell,
    authorization_conditions=_visible_control_authorization_conditions,
)

PURCHASE_ITEM_CONTRACT = ActionContract(
    kind="purchase_item",
    version="1.0",
    model=PurchaseItemAction,
    summary=(
        "Buy the item in one exact cell at the price its own tooltip shows. "
        "Hover the cell first; the tooltip is the evidence."
    ),
    argument_source=(
        "cell_label from visible_controls (role 'item'); item_name and "
        "expected_price copied from that cell's current tooltip; seller_id the "
        "exact stable id of the one active shop owner."
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
    bind=bind_purchase_item,
    authorization_conditions=_visible_control_authorization_conditions,
)


USE_GAME_BINDING_CONTRACT = ActionContract(
    kind="use_game_binding",
    version="1.0",
    model=UseGameBindingAction,
    summary=(
        "Press one of Kenshi's own named controls: open the inventory, map or "
        "stats window, pause or set game speed, move the camera, or change the "
        "selected character. This is how screens are entered - do not hunt for "
        "a widget to click when a binding exists."
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
    authorization_conditions=_game_binding_authorization_conditions,
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
    authorization_conditions=_visible_control_authorization_conditions,
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
    bind=bind_sell_item,
    authorization_conditions=_visible_control_authorization_conditions,
)

ACTION_CONTRACTS: dict[str, ActionContract] = {
    contract.kind: contract
    for contract in (
        APPROACH_DIALOGUE_TARGET_CONTRACT,
        ACTIVATE_VISIBLE_CONTROL_CONTRACT,
        DISMISS_SCREEN_CONTRACT,
        INSPECT_ITEM_CELL_CONTRACT,
        PURCHASE_ITEM_CONTRACT,
        USE_GAME_BINDING_CONTRACT,
        SCROLL_SCREEN_CONTRACT,
        SELL_ITEM_CONTRACT,
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
