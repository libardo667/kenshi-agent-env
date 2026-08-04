"""Bounded purchase and sale handlers with conservation terminals."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeAlias, cast

from ... import operation_definitions as operations
from ...affordances import OPERATION_BINDING_AUTHORITY
from ...core.evidence import (
    PurchaseEvidence,
    PurchaseStatus,
    SaleEvidence,
    SaleStatus,
    SemanticActionReceipt,
)
from ...core.observation import Observation
from ...core.operation import (
    Action,
    ClickAction,
    MouseButton,
    MoveCursorAction,
    PurchaseItemAction,
    SellItemAction,
)
from ...core.telemetry import (
    TelemetrySnapshot,
    normalize_control_label,
)
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from ...input_boundary import ExecutionToken
from ...operation_definitions import BoundOperation
from ...telemetry import TelemetryReadError
from ...ui_messages import causally_new_game_message, game_message_panel_texts
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .input_binding import authorized_input_binding
from .kenshi_surface import NATIVE_COMMAND_POLL_SECONDS, KenshiControlSurface


class TradeMechanicsPort(Protocol):
    async def purchase_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def sell_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class PurchaseHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(PurchaseItemAction, bound.operation)
        transition = await _execute(self.operation, action, context)
        evidence = (
            transition.receipt.semantic.purchase
            if transition.receipt.semantic is not None
            else None
        )
        succeeded = evidence is not None and evidence.status is PurchaseStatus.PURCHASED
        if evidence is not None:
            context.progress(
                "Accepted the controller-owned purchase conservation verdict.",
                transition.observation,
                evidence={
                    "controller_verified": True,
                    "status": evidence.status.value,
                    "expected_price": evidence.expected_price,
                    "requested_quantity": evidence.requested_quantity,
                    "purchased_quantity": evidence.purchased_quantity,
                    "money_before": evidence.money_before,
                    "money_after": evidence.money_after,
                    "inventory_quantity_before": evidence.inventory_quantity_before,
                    "inventory_quantity_after": evidence.inventory_quantity_after,
                },
            )
        return _verified_result(
            transition,
            succeeded=succeeded,
            reason=(
                f"Controller-owned purchase returned {evidence.status.value!r}: {evidence.reason}"
                if evidence is not None
                else "Controller returned no typed purchase conservation evidence."
            ),
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Purchase was cancelled.")


@dataclass(frozen=True, slots=True)
class SaleHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(SellItemAction, bound.operation)
        transition = await _execute(self.operation, action, context)
        evidence = (
            transition.receipt.semantic.sale if transition.receipt.semantic is not None else None
        )
        succeeded = evidence is not None and evidence.status is SaleStatus.SOLD
        if evidence is not None:
            context.progress(
                "Accepted the controller-owned sale conservation verdict.",
                transition.observation,
                evidence={
                    "controller_verified": True,
                    "status": evidence.status.value,
                    "requested_quantity": evidence.requested_quantity,
                    "sold_quantity": evidence.sold_quantity,
                    "money_before": evidence.money_before,
                    "money_after": evidence.money_after,
                    "inventory_quantity_before": evidence.inventory_quantity_before,
                    "inventory_quantity_after": evidence.inventory_quantity_after,
                },
            )
        return _verified_result(
            transition,
            succeeded=succeeded,
            reason=(
                f"Controller-owned sale returned {evidence.status.value!r}: {evidence.reason}"
                if evidence is not None
                else "Controller returned no typed sale conservation evidence."
            ),
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Sale was cancelled.")


async def _execute(
    operation: Callable[..., Awaitable[Transition]],
    action: Action,
    context: OperationContext,
) -> Transition:
    if context.command is None:
        raise RuntimeError("Trade operation has no command authority.")
    return await operation(action, command=context.command, token=context.token)


def _verified_result(
    transition: Transition,
    *,
    succeeded: bool,
    reason: str,
) -> OperationResult:
    return OperationResult(
        status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
        observation=transition.observation,
        reason=reason,
        transition=transition,
        terminated=transition.terminated,
        success=transition.success,
    )


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
    reason: str,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason=reason,
    )


def trade_handlers(port: TradeMechanicsPort) -> dict[str, OperationHandler]:
    return {
        "trade.purchase_item": PurchaseHandler(port.purchase_item),
        "trade.sell_item": SaleHandler(port.sell_item),
    }


PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 2.0
SALE_OBSERVATION_TIMEOUT_SECONDS = 2.0

TradeBinding: TypeAlias = operations.BoundPurchaseCell | operations.BoundSaleCell


@dataclass(frozen=True, slots=True)
class _BoundedTradeOutcome:
    status: Literal["completed", "partial", "not_completed", "outcome_unknown"]
    completed_quantity: int
    selected_character_id: str
    money_before: int
    money_after: int | None
    inventory_quantity_before: int
    inventory_quantity_after: int | None
    observed_after_sequence: int | None
    primitive_actions: int
    initial_binding: TradeBinding
    initial_observation: Observation
    reason: str


class KenshiTradeMechanics:
    """Bounded purchase and sale mechanics with conservation evidence."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def purchase_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_purchase_operation(
                current, started, dispatch, token
            ),
        )

    async def sell_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_sale_operation(
                current, started, dispatch, token
            ),
        )

    async def _execute_purchase_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_purchase_item(cast(PurchaseItemAction, action), started, token)

    async def _execute_sale_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_sell_item(cast(SellItemAction, action), started, token)

    async def _execute_purchase_item(
        self,
        action: PurchaseItemAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Buy a bounded quantity with per-unit identity and conservation proof."""

        outcome = await self._execute_bounded_trade(
            action,
            direction="purchase",
            observation_timeout_seconds=PURCHASE_OBSERVATION_TIMEOUT_SECONDS,
            token=token,
        )
        status = {
            "completed": PurchaseStatus.PURCHASED,
            "partial": PurchaseStatus.PARTIALLY_PURCHASED,
            "not_completed": PurchaseStatus.NOT_PURCHASED,
            "outcome_unknown": PurchaseStatus.OUTCOME_UNKNOWN,
        }[outcome.status]
        evidence = PurchaseEvidence(
            status=status,
            seller_id=action.seller_id,
            selected_character_id=outcome.selected_character_id,
            item_name=action.item_name,
            expected_price=action.expected_price,
            requested_quantity=action.quantity,
            purchased_quantity=outcome.completed_quantity,
            money_before=outcome.money_before,
            money_after=outcome.money_after,
            inventory_quantity_before=outcome.inventory_quantity_before,
            inventory_quantity_after=outcome.inventory_quantity_after,
            observed_after_sequence=outcome.observed_after_sequence,
            reason=outcome.reason,
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.PURCHASE_ITEM_DEFINITION.version,
            target_id=action.seller_id,
            resolved_label=outcome.initial_binding.resolved_label,
            resolved_role=outcome.initial_binding.resolved_role,
            resolved_bounds=outcome.initial_binding.resolved_bounds,
            source_revision=outcome.initial_observation.world_revision,
            revalidation=(
                "Re-bound the exact seller-owned item cell before every unit and "
                "required later exact window-owner inventory gain with the exact "
                "quoted purse charge, including zero, before continuing. "
                f"{outcome.initial_binding.reason}"
            ),
            purchase=evidence,
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=outcome.primitive_actions > 0,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=outcome.primitive_actions,
            message=outcome.reason,
            semantic=semantic,
        )

    def _trade_refusal_before_input(
        self,
        action: PurchaseItemAction | SellItemAction,
        binding: TradeBinding,
        *,
        direction: Literal["purchase", "sale"],
        money: int,
    ) -> str | None:
        """Name a reason this unit cannot trade, before any input is sent.

        A trade that binds, clicks and moves nothing used to report only "later
        telemetry showed no purse or window-owner inventory change" - a sentence
        every possible cause produces, so an agent reading it learns nothing it
        can act on and neither does a reader of the log. Cells now carry their
        own price and stack size, so the resource reasons are answerable here,
        without spending input to discover them.

        Returning None means the known preconditions hold. That is the point:
        it turns a silent failure afterwards into evidence that the mechanism
        is at fault rather than the purse or the shelf.
        """

        if direction != "purchase":
            return None
        purchase_binding = cast(operations.BoundPurchaseCell, binding)
        price = purchase_binding.item_base_value
        if price is not None and money < price:
            return (
                f"{action.item_name!r} costs {price} and the purse holds "
                f"{money}; sell something or choose an item at or under {money}."
            )
        available = purchase_binding.item_quantity
        if available is not None and available < 1:
            return (
                f"the bound cell no longer holds any {action.item_name!r}; "
                "the shelf changed after an earlier transfer."
            )
        if (
            purchase_binding.item_name is not None
            and purchase_binding.item_name != action.item_name
        ):
            return (
                f"the bound cell now holds {purchase_binding.item_name!r}, not "
                f"{action.item_name!r}; the shelf re-indexed under this binding."
            )
        return None

    def _trade_preconditions_note(
        self,
        binding: TradeBinding,
        *,
        direction: Literal["purchase", "sale"],
        money: int,
    ) -> str:
        """State what was true *at binding*, for a failure that follows.

        Without this a reader cannot tell an unaffordable purchase from a
        right-click that did not land, because both end in no observed delta.

        Every number here comes from the binding, which is why the tense
        matters. A first version said "the cell held 1" in the present, and a
        live run then produced exactly that sentence while telemetry showed the
        trade window holding no item cells at all - the binding was up to the
        full conservation timeout out of date. Reporting remembered state as
        current is the same defect this whole message exists to remove, so the
        wording pins when it was true and claims nothing about now.
        """

        if direction != "purchase":
            return ""
        purchase_binding = cast(operations.BoundPurchaseCell, binding)
        price = purchase_binding.item_base_value
        if price is None:
            return ""
        stock = purchase_binding.item_quantity
        stocked = f" and the bound cell held {stock}" if stock is not None else ""
        return (
            f" When the click was sent the purse held {money} against a price "
            f"of {price}{stocked}, so neither the purse nor the shelf explains "
            "this; the cell may also have gone since."
        )

    async def _execute_bounded_trade(
        self,
        action: PurchaseItemAction | SellItemAction,
        *,
        direction: Literal["purchase", "sale"],
        observation_timeout_seconds: float,
        token: ExecutionToken | None,
    ) -> _BoundedTradeOutcome:
        binding_type = (
            operations.BoundPurchaseCell if direction == "purchase" else operations.BoundSaleCell
        )
        initial_rebound, initial_observation = authorized_input_binding(
            action,
            token,
            binding_type,
        )
        scheduled_bound = token.authorized_bound if token is not None else None
        if scheduled_bound is None:
            raise RuntimeError("Trade execution lost its authorized bound operation.")
        initial_binding = cast(TradeBinding, initial_rebound)
        telemetry = initial_observation.telemetry
        assert telemetry is not None
        selected_character_id, money_before, inventory_before = self._trade_state(
            telemetry,
            action.item_name,
            expected_character_id=initial_binding.inventory_owner_id,
        )

        current_money = money_before
        current_inventory = inventory_before
        current_sequence = telemetry.sequence
        final_money: int | None = money_before
        final_inventory: int | None = inventory_before
        final_sequence: int | None = telemetry.sequence
        completed_quantity = 0
        primitive_actions = 0
        status: Literal["completed", "partial", "not_completed", "outcome_unknown"] = (
            "not_completed"
        )
        reason = "No trade input was sent."
        binding = initial_binding
        operation = "purchase" if direction == "purchase" else "sale"

        for unit_index in range(action.quantity):
            if unit_index:
                rebound, rebind_reason, rebound_snapshot = self._try_rebind_trade(
                    action,
                    scheduled_bound,
                    direction=direction,
                    selected_character_id=selected_character_id,
                    expected_money=current_money,
                    expected_inventory=current_inventory,
                )
                if rebound is None:
                    status = "partial" if completed_quantity else "not_completed"
                    reason = (
                        f"Stopped after {completed_quantity}/{action.quantity}: {rebind_reason}"
                    )
                    break
                binding = rebound
                assert rebound_snapshot is not None
                message_baseline = game_message_panel_texts(rebound_snapshot)
            else:
                message_baseline = game_message_panel_texts(telemetry)

            remaining = action.quantity - completed_quantity
            refusal = self._trade_refusal_before_input(
                action,
                binding,
                direction=direction,
                money=current_money,
            )
            if refusal is not None:
                status = "partial" if completed_quantity else "not_completed"
                reason = f"Stopped after {completed_quantity}/{action.quantity}: {refusal}"
                break
            self._ensure_trade_can_continue(operation)
            bounds = binding.resolved_bounds
            assert bounds is not None
            x = (bounds.min_x + bounds.max_x) / 2.0
            y = (bounds.min_y + bounds.max_y) / 2.0
            move_receipt = await self._surface.controller.execute(MoveCursorAction(x=x, y=y))
            primitive_actions += move_receipt.primitive_actions
            if self._surface.controls_config.item_cell_hover_seconds:
                await asyncio.sleep(self._surface.controls_config.item_cell_hover_seconds)
            self._ensure_trade_can_continue(operation)
            click_receipt = await self._surface.controller.execute(
                ClickAction(
                    x=x,
                    y=y,
                    button=MouseButton.RIGHT,
                    hold_seconds=self._surface.controls_config.control_activation_hold_seconds,
                )
            )
            primitive_actions += click_receipt.primitive_actions

            (
                transfer_status,
                observed_money,
                observed_inventory,
                observed_sequence,
                outcome_reason,
            ) = await self._wait_for_trade_conservation(
                item_name=action.item_name,
                direction=direction,
                quoted_unit_price=(
                    action.expected_price if isinstance(action, PurchaseItemAction) else None
                ),
                selected_character_id=selected_character_id,
                money_before=current_money,
                inventory_before=current_inventory,
                after_sequence=current_sequence,
                remaining_quantity=remaining,
                message_baseline=message_baseline,
                timeout_seconds=observation_timeout_seconds,
            )
            final_money = observed_money
            final_inventory = observed_inventory
            final_sequence = observed_sequence
            if transfer_status == "transferred":
                assert observed_money is not None
                assert observed_inventory is not None
                assert observed_sequence is not None
                transferred = (
                    observed_inventory - current_inventory
                    if direction == "purchase"
                    else current_inventory - observed_inventory
                )
                completed_quantity += transferred
                current_money = observed_money
                current_inventory = observed_inventory
                current_sequence = observed_sequence
                if completed_quantity >= action.quantity:
                    status = "completed"
                    reason = (
                        f"Conserved {completed_quantity}/{action.quantity} "
                        f"{action.item_name!r} {operation}s through matching "
                        + (
                            "quoted charge and exact window-owner inventory gain."
                            if direction == "purchase"
                            else "purse gain and exact window-owner inventory loss."
                        )
                    )
                    break
                continue

            if transfer_status == "not_transferred":
                status = "partial" if completed_quantity else "not_completed"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity}: "
                    f"{outcome_reason}"
                    + self._trade_preconditions_note(
                        binding,
                        direction=direction,
                        money=current_money,
                    )
                )
            elif transfer_status == "refused":
                status = "partial" if completed_quantity else "not_completed"
                reason = f"Stopped after {completed_quantity}/{action.quantity}: {outcome_reason}"
            else:
                status = "outcome_unknown"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity} "
                    f"confirmed {operation}s because the last delivery is "
                    f"ambiguous: {outcome_reason}"
                )
            break
        else:
            if completed_quantity == action.quantity:
                status = "completed"
                reason = (
                    f"Conserved all {completed_quantity} requested "
                    f"{action.item_name!r} {operation}s."
                )

        return _BoundedTradeOutcome(
            status=status,
            completed_quantity=completed_quantity,
            selected_character_id=selected_character_id,
            money_before=money_before,
            money_after=final_money,
            inventory_quantity_before=inventory_before,
            inventory_quantity_after=final_inventory,
            observed_after_sequence=final_sequence,
            primitive_actions=primitive_actions,
            initial_binding=initial_binding,
            initial_observation=initial_observation,
            reason=reason,
        )

    def _trade_state(
        self,
        telemetry: TelemetrySnapshot,
        item_name: str,
        *,
        expected_character_id: str | None = None,
    ) -> tuple[str, int, int]:
        selected_ids = telemetry.ui.selected_character_ids
        if expected_character_id is None:
            raise RuntimeError("Trade conservation requires an exact inventory-window owner.")
        if expected_character_id not in selected_ids:
            raise RuntimeError(
                "Trade conservation requires the exact inventory-window owner to remain selected."
            )
        selected_character_id = expected_character_id
        selected = [
            character
            for character in telemetry.squad
            if character.id == selected_character_id and character.selected
        ]
        if len(selected) != 1 or selected[0].inventory_complete is not True:
            raise RuntimeError(
                "Trade conservation requires the exact window owner with a "
                "complete inventory export."
            )
        if telemetry.game.money is None:
            raise RuntimeError("Trade conservation requires known current money.")
        normalized_name = normalize_control_label(item_name)
        quantity = sum(
            (item.item_quantity if item.item_quantity is not None else item.quantity)
            for item in selected[0].inventory
            if normalize_control_label(item.name) == normalized_name
        )
        return selected_character_id, telemetry.game.money, quantity

    def _try_rebind_trade(
        self,
        action: PurchaseItemAction | SellItemAction,
        scheduled_bound: BoundOperation,
        *,
        direction: Literal["purchase", "sale"],
        selected_character_id: str,
        expected_money: int,
        expected_inventory: int,
    ) -> tuple[TradeBinding | None, str, TelemetrySnapshot | None]:
        try:
            result = self._surface.telemetry_reader.read()
        except TelemetryReadError as exc:
            return None, f"telemetry could not be read ({exc}).", None
        if result.stale:
            return None, "telemetry became stale before the next unit.", None
        observation = self._surface.port._observation_from_snapshot(result.snapshot)
        binding_type = (
            operations.BoundPurchaseCell if direction == "purchase" else operations.BoundSaleCell
        )
        try:
            rebound = OPERATION_BINDING_AUTHORITY.bind(
                action,
                observation,
                affordance=scheduled_bound.affordance,
            )
            binding = operations.require_bound(rebound.binding, binding_type)
        except (RuntimeError, ValueError) as exc:
            return None, str(exc), None
        trade_binding = cast(TradeBinding, binding)
        if trade_binding.inventory_owner_id != selected_character_id:
            return (
                None,
                "the exact player inventory-window owner changed between units.",
                None,
            )
        try:
            character_id, money, inventory = self._trade_state(
                result.snapshot,
                action.item_name,
                expected_character_id=selected_character_id,
            )
        except RuntimeError as exc:
            return None, str(exc), None
        if (
            character_id != selected_character_id
            or money != expected_money
            or inventory != expected_inventory
        ):
            return (
                None,
                "purse or exact window-owner inventory changed between bound units.",
                None,
            )
        return trade_binding, trade_binding.reason, result.snapshot

    def _ensure_trade_can_continue(self, operation: str) -> None:
        if self._surface.controller.emergency_stop_pressed(self._surface.port.emergency_stop_key):
            raise RuntimeError(
                f"Emergency stop interrupted the {operation}; no further input was sent."
            )
        if self._surface.controller.user_input_detected():
            raise RuntimeError(
                f"Human input interrupted the {operation}; no further input was sent."
            )

    async def _wait_for_trade_conservation(
        self,
        *,
        item_name: str,
        direction: Literal["purchase", "sale"],
        quoted_unit_price: int | None,
        selected_character_id: str,
        money_before: int,
        inventory_before: int,
        after_sequence: int,
        remaining_quantity: int,
        message_baseline: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[
        Literal["transferred", "refused", "not_transferred", "outcome_unknown"],
        int | None,
        int | None,
        int | None,
        str,
    ]:
        deadline = time.monotonic() + timeout_seconds
        latest: tuple[int, int, int] | None = None
        mismatch_reason: str | None = None
        while True:
            try:
                result = self._surface.telemetry_reader.read()
            except TelemetryReadError:
                result = None
            if (
                result is not None
                and not result.stale
                and result.snapshot.sequence > after_sequence
            ):
                try:
                    _, money_after, inventory_after = self._trade_state(
                        result.snapshot,
                        item_name,
                        expected_character_id=selected_character_id,
                    )
                except RuntimeError as exc:
                    mismatch_reason = str(exc)
                else:
                    latest = (
                        money_after,
                        inventory_after,
                        result.snapshot.sequence,
                    )
                    money_delta = (
                        money_before - money_after
                        if direction == "purchase"
                        else money_after - money_before
                    )
                    inventory_delta = (
                        inventory_after - inventory_before
                        if direction == "purchase"
                        else inventory_before - inventory_after
                    )
                    money_label = "purse loss" if direction == "purchase" else "purse gain"
                    inventory_label = (
                        "carried-item gain" if direction == "purchase" else "carried-item loss"
                    )
                    if direction == "purchase":
                        if quoted_unit_price is None:
                            raise RuntimeError(
                                "Purchase conservation requires the quoted unit price."
                            )
                        expected_money_delta = quoted_unit_price * inventory_delta
                        conserved = (
                            1 <= inventory_delta <= remaining_quantity
                            and money_delta == expected_money_delta
                        )
                    else:
                        expected_money_delta = None
                        conserved = money_delta > 0 and 1 <= inventory_delta <= remaining_quantity
                    if conserved:
                        if direction == "purchase":
                            assert expected_money_delta is not None
                            outcome = (
                                f"Observed exact c.{money_delta} quoted charge for "
                                f"{inventory_delta} {inventory_label} at "
                                f"c.{quoted_unit_price} each."
                            )
                        else:
                            outcome = (
                                f"Observed c.{money_delta} {money_label} and "
                                f"{inventory_delta} matching {inventory_label}."
                            )
                        return (
                            "transferred",
                            money_after,
                            inventory_after,
                            result.snapshot.sequence,
                            outcome,
                        )
                    if money_delta != 0 or inventory_delta != 0:
                        if direction == "purchase":
                            mismatch_reason = (
                                f"purse loss {money_delta} did not equal quoted "
                                f"charge c.{quoted_unit_price} times carried-item "
                                f"gain {inventory_delta}; remaining bound quantity "
                                f"was {remaining_quantity}."
                            )
                        else:
                            mismatch_reason = (
                                f"{money_label} {money_delta} and {inventory_label} "
                                f"{inventory_delta} do not conservatively match the "
                                f"remaining bound {remaining_quantity}."
                            )
                    else:
                        refusal = causally_new_game_message(
                            result.snapshot,
                            message_baseline,
                        )
                        if refusal is not None:
                            return (
                                "refused",
                                money_after,
                                inventory_after,
                                result.snapshot.sequence,
                                f"Kenshi refused the {direction}: {refusal}",
                            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if mismatch_reason is not None:
                    values = latest or (None, None, None)
                    return (
                        "outcome_unknown",
                        values[0],
                        values[1],
                        values[2],
                        mismatch_reason,
                    )
                if latest is not None:
                    return (
                        "not_transferred",
                        latest[0],
                        latest[1],
                        latest[2],
                        "later telemetry showed no purse or window-owner inventory change.",
                    )
                return (
                    "outcome_unknown",
                    None,
                    None,
                    None,
                    "no causally later complete inventory observation arrived.",
                )
            await asyncio.sleep(min(NATIVE_COMMAND_POLL_SECONDS, remaining))

    async def _execute_sell_item(
        self,
        action: SellItemAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Sell a bounded quantity with per-unit identity and conservation proof."""

        outcome = await self._execute_bounded_trade(
            action,
            direction="sale",
            observation_timeout_seconds=SALE_OBSERVATION_TIMEOUT_SECONDS,
            token=token,
        )
        status = {
            "completed": SaleStatus.SOLD,
            "partial": SaleStatus.PARTIALLY_SOLD,
            "not_completed": SaleStatus.NOT_SOLD,
            "outcome_unknown": SaleStatus.OUTCOME_UNKNOWN,
        }[outcome.status]
        evidence = SaleEvidence(
            status=status,
            buyer_id=action.buyer_id,
            selected_character_id=outcome.selected_character_id,
            item_name=action.item_name,
            requested_quantity=action.quantity,
            sold_quantity=outcome.completed_quantity,
            money_before=outcome.money_before,
            money_after=outcome.money_after,
            inventory_quantity_before=outcome.inventory_quantity_before,
            inventory_quantity_after=outcome.inventory_quantity_after,
            observed_after_sequence=outcome.observed_after_sequence,
            reason=outcome.reason,
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.SELL_ITEM_DEFINITION.version,
            target_id=action.buyer_id,
            resolved_label=outcome.initial_binding.resolved_label,
            resolved_role=outcome.initial_binding.resolved_role,
            resolved_bounds=outcome.initial_binding.resolved_bounds,
            source_revision=outcome.initial_observation.world_revision,
            revalidation=(
                "Re-bound the exact player-window-owned item cell before "
                "every unit and required a later matching purse gain plus "
                "exact window-owner inventory loss before continuing. "
                f"{outcome.initial_binding.reason}"
            ),
            sale=evidence,
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=outcome.primitive_actions > 0,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=outcome.primitive_actions,
            message=outcome.reason,
            semantic=semantic,
        )
