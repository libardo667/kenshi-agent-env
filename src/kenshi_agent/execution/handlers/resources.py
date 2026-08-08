"""Native resource production and inventory-transfer operations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast

from ... import native_commands
from ... import operation_definitions as operations
from ...config import PlanningConfig
from ...core.evidence import SemanticActionReceipt
from ...core.operation import (
    Action,
    OpenTradeWindowAction,
    PerformContextAction,
    ProduceResourceOutputAction,
    TransferItemAction,
)
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from ...input_boundary import ExecutionToken
from ..types import OperationHandler
from .kenshi_surface import KenshiControlSurface
from .movement import AtomicMovementHandler, NativeMovementHandler


class ResourceMechanicsPort(Protocol):
    async def perform_context_action(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def produce_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def transfer_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def open_trade_window(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

def resource_handlers(
    port: ResourceMechanicsPort,
    planning_config: PlanningConfig,
) -> dict[str, OperationHandler]:
    return {
        "resources.perform_context_action": NativeMovementHandler(
            port.perform_context_action, planning_config
        ),
        "resources.produce_resource_output": NativeMovementHandler(
            port.produce_resource_output, planning_config
        ),
        "resources.transfer_item": AtomicMovementHandler(
            port.transfer_item,
            verify_native_terminal=True,
        ),
        "resources.open_trade_window": AtomicMovementHandler(
            port.open_trade_window,
            verify_native_terminal=True,
        ),
    }

class KenshiResourceMechanics:
    """Production, context-action, and resource-transfer mechanics."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface


    async def perform_context_action(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_context_operation
        )

    async def produce_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_produce_operation
        )


    async def open_trade_window(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=self._execute_trade_window_operation,
        )

    async def transfer_item(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=self._execute_transfer_operation,
        )


    async def _execute_context_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_context_action(
            cast(PerformContextAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_produce_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_produce_resource_output(
            cast(ProduceResourceOutputAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_trade_window_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_open_trade_window(
            cast(OpenTradeWindowAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_transfer_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_transfer_item(
            cast(TransferItemAction, action),
            started,
            await self._surface.require_command(command),
        )



    async def _execute_context_action(
        self,
        action: PerformContextAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one reviewed default task on one exact observed world object."""

        pulse_seconds = self._surface.controls_config.native_movement_pulse_seconds
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.PERFORM_CONTEXT_ACTION_DEFINITION.version,
            target_id=action.target_id,
            resolved_label=action.context_action.value,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact advertised world object/action pair and delegated "
                "the reviewed Kenshi default task plus terminal AI-goal proof to "
                "native code."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            require_vendor_role=False,
            wire_fields=operations.wire_fields_for(action),
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=native_commands.NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
            context_action=action.context_action,
            require_dialogue_target=False,
            task_started_reasons=(
                operations.PERFORM_CONTEXT_ACTION_DEFINITION.native_task_started_reasons
            ),
        )

    async def _execute_produce_resource_output(
        self,
        action: ProduceResourceOutputAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Retain one exact mining job until native output proof is terminal."""

        pulse_seconds = self._surface.controls_config.native_movement_pulse_seconds
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.PRODUCE_RESOURCE_OUTPUT_DEFINITION.version,
            target_id=action.target_id,
            resolved_label="produce_output",
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact reviewed natural resource. Native code owns "
                "the task through actual output, and adopts matching active work "
                "without reissuing it."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            require_vendor_role=False,
            wire_fields=operations.wire_fields_for(action),
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=native_commands.NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
            require_dialogue_target=False,
            minimum_output_quantity=action.minimum_output_quantity,
        )


    async def _execute_open_trade_window(
        self,
        action: OpenTradeWindowAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Pair two inventories through Kenshi's own trade-window call."""

        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.OPEN_TRADE_WINDOW_DEFINITION.version,
            target_id=action.first_owner_id,
            resolved_label=f"{action.window_type} with {action.second_owner_id}",
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-proved both parties observed, then required native terminal "
                "evidence that two inventory windows are open."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.first_owner_id,
            pulse_seconds=self._surface.controls_config.native_movement_pulse_seconds,
            require_vendor_role=False,
            wire_fields=operations.wire_fields_for(action),
            semantic=semantic,
            wire_command=native_commands.NATIVE_TRADE_WINDOW_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
        )

    async def _execute_transfer_item(
        self,
        action: TransferItemAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Move one item between two open inventories, with no pointer at all.

        The five operations this replaces each drove a mouse over a cell.
        Kenshi's own `RClickAutoTrade` takes a section and a slot, performs the
        move, and returns why not when it refuses - so the terminal reason here
        is the engine's word, and success additionally requires that something
        actually moved rather than merely being permitted.
        """

        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.TRANSFER_ITEM_DEFINITION.version,
            target_id=action.source_owner_id,
            resolved_label=f"{action.item_name} to {action.destination_owner_id}",
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-proved both inventories open and the named item still in its "
                "slot, then let Kenshi adjudicate the transfer and report its own "
                "verdict."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.source_owner_id,
            pulse_seconds=self._surface.controls_config.native_movement_pulse_seconds,
            require_vendor_role=False,
            wire_fields=operations.wire_fields_for(action),
            semantic=semantic,
            wire_command=native_commands.NATIVE_TRANSFER_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
        )
