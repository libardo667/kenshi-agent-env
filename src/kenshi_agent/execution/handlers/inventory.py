"""Inventory ownership and verified transfer handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from ... import operation_definitions as operations
from ...input_boundary import ExecutionToken
from ...models import (
    Action,
    ActionReceipt,
    ClickAction,
    CollectResourceOutputAction,
    CommandDispatchContext,
    EquipItemAction,
    MouseButton,
    MoveCursorAction,
    ResourceTransferStatus,
    SemanticActionReceipt,
    Transition,
)
from ...operation_definitions import BoundOperation
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .kenshi_surface import KenshiControlSurface


class InventoryMechanicsPort(Protocol):
    async def equip_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def collect_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class InventoryDeliveryHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        transition = await _execute(self.operation, bound.operation, context)
        accepted = transition.receipt.accepted or transition.receipt.executed
        return _result(
            transition,
            status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
            reason=transition.receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


@dataclass(frozen=True, slots=True)
class ResourceTransferHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(CollectResourceOutputAction, bound.operation)
        transition = await _execute(self.operation, action, context)
        evidence = (
            transition.receipt.semantic.resource_transfer
            if transition.receipt.semantic is not None
            else None
        )
        succeeded = evidence is not None and evidence.status is ResourceTransferStatus.TRANSFERRED
        if evidence is not None:
            context.progress(
                "Accepted the controller-owned resource-transfer conservation verdict.",
                transition.observation,
                evidence={
                    "controller_verified": True,
                    "status": evidence.status.value,
                    "source_quantity_before": evidence.source_quantity_before,
                    "source_quantity_after": evidence.source_quantity_after,
                    "destination_quantity_before": evidence.destination_quantity_before,
                    "destination_quantity_after": evidence.destination_quantity_after,
                },
            )
        return _result(
            transition,
            status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
            reason=(
                f"Controller-owned resource transfer returned "
                f"{evidence.status.value!r}: {evidence.reason}"
                if evidence is not None
                else "Controller returned no typed resource-transfer evidence."
            ),
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


async def _execute(
    operation: Callable[..., Awaitable[Transition]],
    action: Action,
    context: OperationContext,
) -> Transition:
    if context.command is None:
        raise RuntimeError("Inventory operation has no command authority.")
    return await operation(action, command=context.command, token=context.token)


def _result(
    transition: Transition,
    *,
    status: OperationStatus,
    reason: str,
) -> OperationResult:
    return OperationResult(
        status=status,
        observation=transition.observation,
        reason=reason,
        transition=transition,
        terminated=transition.terminated,
        success=transition.success,
    )


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason="Inventory operation was cancelled.",
    )


def inventory_handlers(port: InventoryMechanicsPort) -> dict[str, OperationHandler]:
    return {
        "inventory.equip_item": InventoryDeliveryHandler(port.equip_item),
        "resources.collect_resource_output": ResourceTransferHandler(port.collect_resource_output),
    }


class KenshiInventoryMechanics:
    """Equip mechanics owned by the inventory window."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def equip_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_equip_operation
        )

    async def _execute_equip_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        return await self._execute_equip_item(cast(EquipItemAction, action), started)

    async def _execute_equip_item(
        self,
        action: EquipItemAction,
        started: datetime,
    ) -> ActionReceipt:
        """Right-click one of our own cells to equip it, with no trade open.

        The in-lease re-bind matters more here than anywhere else: the contract
        refuses while a trade is open because the identical gesture sells
        instead, and a trade window that opened during the polite wait would
        turn this equip into a sale.
        """

        binding, observation = self._surface.rebind_in_lease(
            operations.EQUIP_ITEM_DEFINITION,
            action,
            operations.BoundEquipmentCell,
        )
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        await self._surface.controller.execute(MoveCursorAction(x=x, y=y))
        if self._surface.controls_config.item_cell_hover_seconds:
            await asyncio.sleep(self._surface.controls_config.item_cell_hover_seconds)
        primitive_receipt = await self._surface.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
                hold_seconds=self._surface.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.EQUIP_ITEM_DEFINITION.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved no trade was open inside the input lease, so this "
                f"right-click equips rather than sells. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Equipped {action.item_name!r} from {action.window!r}. A later "
                    "observation must confirm the equipped gear change."
                ),
            }
        )
