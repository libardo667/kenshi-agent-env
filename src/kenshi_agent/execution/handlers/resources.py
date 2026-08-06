"""Resource production, inventory access, and bounded harvest transactions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from typing import Any, Literal, Protocol, cast

from ... import native_commands
from ... import operation_definitions as operations
from ...affordances import OperationBindingAuthority
from ...config import PlanningConfig
from ...core.authority import AuthorizationCode
from ...core.evidence import (
    ResourceHarvestEvidence,
    ResourceHarvestStatus,
    ResourceTransferEvidence,
    ResourceTransferStatus,
    SemanticActionReceipt,
)
from ...core.observation import Observation
from ...core.operation import (
    Action,
    ClickAction,
    CollectResourceOutputAction,
    DismissScreenAction,
    GameBinding,
    GameScreen,
    HarvestResourceAction,
    MouseButton,
    MoveCursorAction,
    OpenContextInventoryAction,
    PerformContextAction,
    ProduceResourceOutputAction,
    SetSpeedAction,
    UseGameBindingAction,
)
from ...core.telemetry import (
    NativeCommandStatus,
    normalize_control_label,
)
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
    new_command_id,
)
from ...input_boundary import ExecutionToken
from ...operation_authority import AuthorizationDecision, OperationAuthority
from ...operation_definitions import BoundOperation
from ...options import StatefulNativeMovementOption
from ...resource_transfer import begin_resource_transfer, finalize_resource_transfer
from ...safety import SafetyViolation
from ...world_state import CommandCausalityError
from ..monitor_types import StagedPatch
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .input_binding import authorized_input_binding
from .kenshi_surface import KenshiControlSurface
from .movement import (
    AtomicMovementHandler,
    NativeMovementHandler,
    run_prepared_option,
)

ResourceOperation = Callable[..., Coroutine[Any, Any, Transition]]


class ResourceMechanicsPort(Protocol):
    async def perform_context_action(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def produce_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def open_context_inventory(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def set_speed(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def use_game_binding(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def collect_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def dismiss_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(slots=True)
class _HarvestState:
    current: Observation
    receipts: list[ActionReceipt] = field(default_factory=list)
    staged_patch: StagedPatch | None = None
    interrupted: bool = False
    failure_reason: str | None = None
    production_command_id: str | None = None
    production_task_released: bool = False
    inventory_command_id: str | None = None
    transfer: ResourceTransferEvidence | None = None
    item_name: str | None = None


@dataclass(frozen=True, slots=True)
class HarvestHandler:
    port: ResourceMechanicsPort
    authority: OperationAuthority
    binding: OperationBindingAuthority
    planning_config: PlanningConfig

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(HarvestResourceAction, bound.operation)
        observation = context.world.latest
        if observation is None or observation.telemetry is None:
            raise RuntimeError("No current telemetry is available for resource harvest.")
        if context.command is None or context.token is None:
            raise RuntimeError("Resource harvest has no outer command authority.")
        actors = [
            character
            for character in observation.telemetry.squad
            if character.id == action.actor_id and character.selected
        ]
        targets = [
            target
            for target in observation.telemetry.world_targets
            if target.id == action.target_id and target.kind == "natural_resource"
        ]
        if len(actors) != 1 or len(targets) != 1:
            return OperationResult(
                status=OperationStatus.REJECTED,
                observation=observation,
                reason="The exact harvest actor or source no longer binds.",
            )
        state = _HarvestState(current=observation)
        await self._production(action, state, context)
        await self._inventory_transfer(action, state, context)
        cleanup_confirmed, cleanup_reason = await self._cleanup(
            action,
            actors[0].name,
            targets[0].name,
            state,
            context,
        )
        return self._terminal_result(
            bound,
            action,
            actors[0].name,
            targets[0].name,
            state,
            context,
            cleanup_confirmed=cleanup_confirmed,
            cleanup_reason=cleanup_reason,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return OperationResult(
            status=OperationStatus.CANCELLED,
            observation=context.world.latest or active.started_observation,
            reason="Resource harvest was cancelled.",
        )

    async def _production(
        self,
        action: HarvestResourceAction,
        state: _HarvestState,
        context: OperationContext,
    ) -> None:
        try:
            state.current, speed_receipt = await self._set_speed(
                action,
                state.current,
                context,
                speed=3,
                expected_multiplier=5.0,
                require_running=True,
                require_safe_actor=True,
                retain_outer_authority=True,
            )
            if speed_receipt is not None:
                state.receipts.append(speed_receipt)
            production = ProduceResourceOutputAction(
                target_id=action.target_id,
                minimum_output_quantity=action.quantity,
            )
            phase_bound = self._require_phase_authority(
                production,
                action.actor_id,
                state.current,
                require_safe_actor=True,
            )
            command, token = self._phase_authority(
                phase_bound,
                action.actor_id,
                state.current,
                context,
                require_safe_actor=True,
                retain_outer_authority=True,
            )
            state.production_command_id = command.command_id
            option = StatefulNativeMovementOption(
                option_id=(
                    f"harvest-production-{context.scope.plan_id}-"
                    f"{context.scope.plan_version}-{context.scope.step_id}"
                ),
                action=production,
                operation=partial(self.port.produce_resource_output, production),
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            phase_context = replace(context, command=command, token=token)
            result = await run_prepared_option(
                option,
                state.current,
                phase_context,
            )
            if result.transition is None:
                raise SafetyViolation(result.reason)
            state.current = self._publish_phase(
                state.current,
                result.transition,
                command.command_id,
                context,
            )
            state.receipts.append(result.transition.receipt)
            production_acknowledgement = None
            if state.current.telemetry is not None:
                production_acknowledgement = (
                    state.current.telemetry.native_control.acknowledgement_for(
                        command.command_id
                    )
                )
            if production_acknowledgement is None:
                production_acknowledgement = (
                    result.transition.receipt.native_acknowledgement
                )
            state.production_task_released = bool(
                production_acknowledgement is not None
                and production_acknowledgement.reason
                == operations.NATIVE_RESOURCE_TASK_RELEASED_RESULT
            )
            state.staged_patch = cast(StagedPatch | None, result.staged_patch)
            state.interrupted = result.status is OperationStatus.INTERRUPTED
            if not result.succeeded:
                state.failure_reason = (
                    f"Resource production did not reach its exact requested yield: {result.reason}"
                )
        except Exception as exc:
            state.failure_reason = (
                f"Resource harvest production failed closed: {type(exc).__name__}: {exc}"
            )
        finally:
            latest = context.world.latest
            if latest is not None and latest.world_revision.is_later_than(
                state.current.world_revision
            ):
                state.current = latest
            try:
                state.current, receipt = await self._set_speed(
                    action,
                    state.current,
                    context,
                    speed=1,
                    expected_multiplier=1.0,
                    require_running=False,
                    require_safe_actor=False,
                    retain_outer_authority=False,
                )
                if receipt is not None:
                    state.receipts.append(receipt)
            except Exception as exc:
                self._append_failure(
                    state,
                    f"Resource harvest could not restore normal speed: {type(exc).__name__}: {exc}",
                )

    async def _inventory_transfer(
        self,
        action: HarvestResourceAction,
        state: _HarvestState,
        context: OperationContext,
    ) -> None:
        if state.failure_reason is not None:
            return
        try:
            opened, state.current, state.inventory_command_id = await self._dispatch_phase(
                OpenContextInventoryAction(target_id=action.target_id),
                self.port.open_context_inventory,
                action.actor_id,
                state.current,
                context,
            )
            state.receipts.append(opened.receipt)
            acknowledgement = opened.receipt.native_acknowledgement
            if not (
                acknowledgement is not None
                and acknowledgement.status is NativeCommandStatus.COMPLETED
                and acknowledgement.reason == "exact_context_inventory_open"
                and acknowledgement.target_id == action.target_id
            ):
                state.failure_reason = "Native inventory open lacked exact target terminal proof."
                return
            toggled, state.current, _ = await self._dispatch_phase(
                UseGameBindingAction(
                    binding=GameBinding.TOGGLE_INVENTORY,
                    expected_effect=(
                        "Open the exact selected actor's inventory beside the resource output."
                    ),
                ),
                self.port.use_game_binding,
                action.actor_id,
                state.current,
                context,
            )
            state.receipts.append(toggled.receipt)
            await self._collect_bounded_yield(action, state, context)
        except Exception as exc:
            self._append_failure(
                state,
                f"Resource harvest inventory phase failed closed: {type(exc).__name__}: {exc}",
            )

    async def _collect_bounded_yield(
        self,
        action: HarvestResourceAction,
        state: _HarvestState,
        context: OperationContext,
    ) -> None:
        first: ResourceTransferEvidence | None = None
        last: ResourceTransferEvidence | None = None
        transferred = 0
        for _ in range(action.quantity):
            collect = self._collect_action(action, state.current)
            if state.item_name is not None and collect.item_name != state.item_name:
                state.failure_reason = "Resource output identity changed during bounded collection."
                break
            state.item_name = collect.item_name
            transition, state.current, _ = await self._dispatch_phase(
                collect,
                self.port.collect_resource_output,
                action.actor_id,
                state.current,
                context,
            )
            state.receipts.append(transition.receipt)
            evidence = (
                transition.receipt.semantic.resource_transfer
                if transition.receipt.semantic is not None
                else None
            )
            source_loss, destination_gain = self._transfer_delta(evidence)
            if not (
                evidence is not None
                and evidence.status is ResourceTransferStatus.TRANSFERRED
                and evidence.target_id == action.target_id
                and evidence.selected_character_id == action.actor_id
                and evidence.item_name == state.item_name
                and 1 <= source_loss == destination_gain <= 5
                and transferred + source_loss <= action.quantity
            ):
                state.failure_reason = "Resource transfer lacked exact bounded conservation proof."
                break
            first = first or evidence
            last = evidence
            transferred += source_loss
            if transferred >= action.quantity:
                break
        if first is not None and last is not None:
            state.transfer = ResourceTransferEvidence(
                status=ResourceTransferStatus.TRANSFERRED,
                target_id=action.target_id,
                selected_character_id=action.actor_id,
                item_name=first.item_name,
                source_quantity_before=first.source_quantity_before,
                source_quantity_after=last.source_quantity_after,
                destination_quantity_before=first.destination_quantity_before,
                destination_quantity_after=last.destination_quantity_after,
                observed_after_sequence=last.observed_after_sequence,
                reason=(
                    f"Conserved {transferred} {first.item_name!r} through the "
                    "bounded controller-owned collection loop."
                ),
            )
        if state.failure_reason is None and transferred < action.quantity:
            state.failure_reason = (
                "Bounded resource collection ended before the requested yield was "
                f"conserved ({transferred}/{action.quantity})."
            )

    async def _cleanup(
        self,
        action: HarvestResourceAction,
        actor_name: str,
        target_name: str,
        state: _HarvestState,
        context: OperationContext,
    ) -> tuple[bool, str]:
        for owner_name in (target_name, actor_name):
            telemetry = state.current.telemetry
            if telemetry is None:
                return False, "Cleanup lost telemetry."
            if telemetry.ui.open_inventory_windows == 0:
                break
            window = self._inventory_window(state.current, owner_name)
            if window is None:
                return False, f"Cleanup could not bind {owner_name!r} inventory."
            if telemetry.ui.active_screen == "trade":
                dismiss = DismissScreenAction(expected_screen="trade", window=window)
            elif telemetry.ui.active_screen == "inventory":
                dismiss = DismissScreenAction(
                    expected_screen=GameScreen.INVENTORY,
                    window=window,
                )
            else:
                return False, (
                    f"Cleanup found unexpected active screen {telemetry.ui.active_screen!r}."
                )
            try:
                transition, state.current, _ = await self._dispatch_phase(
                    dismiss,
                    self.port.dismiss_screen,
                    action.actor_id,
                    state.current,
                    context,
                    require_safe_actor=False,
                    retain_outer_authority=False,
                )
                state.receipts.append(transition.receipt)
            except Exception as exc:
                return False, (
                    f"Cleanup failed while closing {window!r}: {type(exc).__name__}: {exc}"
                )
        telemetry = state.current.telemetry
        confirmed = bool(
            telemetry is not None
            and telemetry.ui.open_inventory_windows == 0
            and telemetry.ui.modal_open is False
            and telemetry.ui.active_screen == "world"
        )
        return (
            confirmed,
            "Both controller-owned inventory windows are closed."
            if confirmed
            else "Inventory cleanup did not return to a clear world screen.",
        )

    def _terminal_result(
        self,
        bound: BoundOperation,
        action: HarvestResourceAction,
        actor_name: str,
        target_name: str,
        state: _HarvestState,
        context: OperationContext,
        *,
        cleanup_confirmed: bool,
        cleanup_reason: str,
    ) -> OperationResult:
        transferred = self._transferred_quantity(state.transfer)
        if state.failure_reason is None and cleanup_confirmed:
            status = ResourceHarvestStatus.HARVESTED
            release_reason = (
                " The controller-issued operating order was fully released."
                if state.production_task_released
                else ""
            )
            reason = (
                f"Conserved {transferred} {state.item_name!r} into {actor_name!r}; "
                f"{cleanup_reason}{release_reason}"
            )
        elif state.failure_reason is None:
            status = ResourceHarvestStatus.CLEANUP_FAILED
            reason = f"The resource transfer was conserved, but cleanup failed: {cleanup_reason}"
        else:
            status = ResourceHarvestStatus.NOT_HARVESTED
            reason = (
                f"{state.failure_reason} Cleanup: {cleanup_reason}"
                if not cleanup_confirmed
                else state.failure_reason
            )
        evidence = ResourceHarvestEvidence(
            status=status,
            target_id=action.target_id,
            selected_character_id=action.actor_id,
            requested_quantity=action.quantity,
            item_name=state.item_name,
            transferred_quantity=max(0, transferred),
            production_command_id=state.production_command_id,
            inventory_command_id=state.inventory_command_id,
            transfer=state.transfer,
            cleanup_confirmed=cleanup_confirmed,
            reason=reason[:1000],
        )
        boundary = next(
            (
                receipt.input_boundary
                for receipt in reversed(state.receipts)
                if receipt.input_boundary is not None
            ),
            None,
        )
        assert context.command is not None
        transition = Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=state.current.control_mode,
                command_id=context.command.command_id,
                started_after_revision=context.command.based_on_revision,
                completed_at_revision=state.current.world_revision,
                causal_revision_advanced=state.current.world_revision.is_later_than(
                    context.command.based_on_revision
                ),
                input_boundary=boundary,
                semantic=SemanticActionReceipt(
                    action_kind=action.kind,
                    contract_version=bound.definition.version,
                    target_id=action.target_id,
                    resolved_label=target_name,
                    source_revision=context.command.based_on_revision,
                    option_id=(
                        f"harvest-{context.scope.plan_id}-"
                        f"{context.scope.plan_version}-{context.scope.step_id}"
                    ),
                    revalidation=(
                        "Each private phase rebound the exact actor, source, "
                        "inventory layout, and current input lease."
                    ),
                    resource_harvest=evidence,
                ),
                accepted=True,
                executed=bool(state.receipts),
                dry_run=False,
                primitive_actions=sum(receipt.primitive_actions for receipt in state.receipts),
                message=reason[:1000],
            ),
            observation=state.current,
        )
        context.progress(
            "Accepted the controller-owned resource-harvest verdict.",
            state.current,
            evidence={
                "controller_verified": True,
                "status": status.value,
                "requested_quantity": action.quantity,
                "transferred_quantity": transferred,
                "cleanup_confirmed": cleanup_confirmed,
            },
        )
        return OperationResult(
            status=(
                OperationStatus.INTERRUPTED
                if state.interrupted and state.staged_patch is not None
                else OperationStatus.SUCCEEDED
                if status is ResourceHarvestStatus.HARVESTED
                else OperationStatus.FAILED
            ),
            observation=state.current,
            reason=reason,
            transition=transition,
            staged_patch=state.staged_patch,
            monitoring_started=True,
        )

    async def _set_speed(
        self,
        harvest: HarvestResourceAction,
        observation: Observation,
        context: OperationContext,
        *,
        speed: Literal[1, 2, 3],
        expected_multiplier: float,
        require_running: bool,
        require_safe_actor: bool,
        retain_outer_authority: bool,
    ) -> tuple[Observation, ActionReceipt | None]:
        telemetry = observation.telemetry
        if (
            telemetry is not None
            and telemetry.game.speed_multiplier == expected_multiplier
            and (not require_running or telemetry.game.paused is False)
        ):
            return observation, None
        transition, current, _ = await self._dispatch_phase(
            SetSpeedAction(speed=speed),
            self.port.set_speed,
            harvest.actor_id,
            observation,
            context,
            require_safe_actor=require_safe_actor,
            retain_outer_authority=retain_outer_authority,
        )
        error = self._speed_error(
            current,
            expected_multiplier=expected_multiplier,
            require_running=require_running,
        )
        if error is not None:
            raise SafetyViolation(error)
        return current, transition.receipt

    async def _dispatch_phase(
        self,
        action: Action,
        operation: ResourceOperation,
        actor_id: str,
        observation: Observation,
        context: OperationContext,
        *,
        require_safe_actor: bool = True,
        retain_outer_authority: bool = True,
    ) -> tuple[Transition, Observation, str]:
        phase_bound = self._require_phase_authority(
            action,
            actor_id,
            observation,
            require_safe_actor=require_safe_actor,
        )
        command, token = self._phase_authority(
            phase_bound,
            actor_id,
            observation,
            context,
            require_safe_actor=require_safe_actor,
            retain_outer_authority=retain_outer_authority,
        )
        transition = await operation(
            action,
            command=command,
            token=token,
        )
        current = self._publish_phase(
            observation,
            transition,
            command.command_id,
            context,
        )
        return transition, current, command.command_id

    def _phase_authority(
        self,
        bound: BoundOperation,
        actor_id: str,
        observation: Observation,
        context: OperationContext,
        *,
        require_safe_actor: bool,
        retain_outer_authority: bool,
    ) -> tuple[CommandDispatchContext, ExecutionToken]:
        parent = context.token
        if parent is None:
            raise RuntimeError("Harvest phase has no parent authority.")
        command = CommandDispatchContext(
            command_id=new_command_id(),
            based_on_revision=observation.world_revision,
            primitive_action_bound=(
                context.command.primitive_action_bound
                if context.command is not None
                else 0
            ),
            # A harvest phase is a sub-command of the harvest that authorized
            # it, so it inherits that authorization's recipients rather than
            # re-reading whoever is selected when the phase begins.
            authored_recipient_scope=(
                context.command.authored_recipient_scope
                if context.command is not None
                else None
            ),
            authored_primary=(
                context.command.authored_primary if context.command is not None else None
            ),
            authored_selection=(
                list(context.command.authored_selection)
                if context.command is not None
                else []
            ),
            authored_explicit_recipients=(
                list(context.command.authored_explicit_recipients)
                if context.command is not None
                else []
            ),
        )
        return command, ExecutionToken(
            plan_id=parent.plan_id,
            plan_version=parent.plan_version,
            step_id=parent.step_id,
            command_id=command.command_id,
            control_mode=parent.control_mode,
            validated_revision=command.based_on_revision,
            latest_observation=parent.latest_observation,
            max_telemetry_age_seconds=parent.max_telemetry_age_seconds,
            pointer_class=self.authority.pointer_class_for(bound),
            authority_validator=lambda current: self._phase_authorized(
                bound,
                actor_id,
                current,
                require_safe_actor=require_safe_actor,
            ),
            authorized_fingerprint=bound.identity.fingerprint,
            assumptions=parent.assumptions if retain_outer_authority else (),
            preconditions=(),
            failure_conditions=(parent.failure_conditions if retain_outer_authority else ()),
        )

    def _publish_phase(
        self,
        before: Observation,
        transition: Transition,
        command_id: str,
        context: OperationContext,
    ) -> Observation:
        if transition.receipt.command_id != command_id:
            raise CommandCausalityError(
                "Harvest phase receipt command ID does not match its exact subcommand."
            )
        after = transition.observation
        latest = context.world.latest
        if latest is not None and not after.world_revision.is_later_than(latest.world_revision):
            return latest
        if after.world_revision.is_later_than(before.world_revision):
            context.world.publish(after)
            latest = context.world.latest or after
        else:
            latest = latest or after
        context.progress(
            f"Completed controller-owned phase {transition.receipt.action.kind!r}.",
            latest,
            event_type="resource_harvest_phase",
            evidence={
                "phase_action": transition.receipt.action.kind,
                "phase_command_id": command_id,
                "primitive_actions": transition.receipt.primitive_actions,
            },
        )
        return latest

    def _require_phase_authority(
        self,
        action: Action,
        actor_id: str,
        observation: Observation,
        *,
        require_safe_actor: bool,
    ) -> BoundOperation:
        try:
            bound = self.binding.bind(action, observation, affordance=None)
        except ValueError as exc:
            raise SafetyViolation(str(exc)) from exc
        decision = self._phase_authorized(
            bound,
            actor_id,
            observation,
            require_safe_actor=require_safe_actor,
        )
        if not decision.allowed:
            raise SafetyViolation(str(decision.details.get("violation", decision.code.value)))
        assert decision.bound_operation is not None
        return decision.bound_operation

    def _phase_authorized(
        self,
        bound: BoundOperation,
        actor_id: str,
        observation: Observation,
        *,
        require_safe_actor: bool,
    ) -> AuthorizationDecision:
        """Answer a harvest phase with the same verdict every other step gets.

        A composite's internal phases hold host input exactly like an ordinary
        operation does, so they revalidate through the one authority rather than
        an inner check with its own shape.
        """

        decision = self.authority.evaluate(bound, observation)
        if not decision.allowed:
            return decision
        actor_refusal = self._actor_refusal(
            actor_id,
            observation,
            require_safe=require_safe_actor,
        )
        if actor_refusal is None:
            return decision
        code, actor_error = actor_refusal
        return AuthorizationDecision(
            allowed=False,
            code=code,
            based_on_revision=observation.world_revision,
            operation_fingerprint=decision.operation_fingerprint,
            details={"violation": actor_error, "operation_kind": bound.operation.kind},
        )

    @staticmethod
    def _actor_refusal(
        actor_id: str,
        observation: Observation,
        *,
        require_safe: bool,
    ) -> tuple[AuthorizationCode, str] | None:
        telemetry = observation.telemetry
        if telemetry is None or observation.telemetry_stale:
            return (
                AuthorizationCode.POLICY_DISALLOWED,
                "Fresh telemetry is required to retain harvest authority.",
            )
        selected = [
            character
            for character in telemetry.squad
            if character.selected and character.id == actor_id
        ]
        # Who, not how many. The identity comparison already says the actor is
        # the whole selection; a separate count was a second way to say it that
        # could drift from the contract.
        if not selected or telemetry.ui.selected_character_ids != [actor_id]:
            return (
                AuthorizationCode.SELECTION_INVALID,
                "The exact harvest actor is no longer solely selected.",
            )
        if require_safe and (
            selected[0].alive is not True
            or selected[0].conscious is not True
            or selected[0].down is not False
            or selected[0].in_combat is not False
            or selected[0].inventory_complete is not True
        ):
            return (
                AuthorizationCode.POLICY_DISALLOWED,
                "The harvest actor is no longer confirmed safe with complete inventory.",
            )
        return None

    @staticmethod
    def _speed_error(
        observation: Observation,
        *,
        expected_multiplier: float,
        require_running: bool,
    ) -> str | None:
        telemetry = observation.telemetry
        if telemetry is None or observation.telemetry_stale:
            return "Harvest speed control requires fresh telemetry."
        if telemetry.game.speed_multiplier != expected_multiplier:
            return (
                f"Expected {expected_multiplier:g}x harvest speed; observed "
                f"{telemetry.game.speed_multiplier!r}."
            )
        if require_running and telemetry.game.paused is not False:
            return "Fast harvest speed did not confirm a running world."
        return None

    @staticmethod
    def _inventory_window(observation: Observation, owner_name: str) -> str | None:
        telemetry = observation.telemetry
        if (
            telemetry is None
            or telemetry.ui.visible_controls_complete is not True
            or telemetry.ui.visible_controls is None
        ):
            return None
        wanted = normalize_control_label(owner_name)
        matches = {
            control.window
            for control in telemetry.ui.visible_controls
            if control.window and normalize_control_label(control.window) == wanted
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _collect_action(
        action: HarvestResourceAction,
        observation: Observation,
    ) -> CollectResourceOutputAction:
        telemetry = observation.telemetry
        if (
            telemetry is None
            or observation.telemetry_stale
            or telemetry.ui.open_inventory_windows != 2
            or telemetry.ui.context_inventory_target_id != action.target_id
            or telemetry.ui.visible_controls_complete is not True
            or telemetry.ui.visible_controls is None
        ):
            raise SafetyViolation("Harvest transfer requires one exact two-window layout.")
        targets = [
            target
            for target in telemetry.world_targets
            if target.id == action.target_id and target.kind == "natural_resource"
        ]
        if len(targets) != 1:
            raise SafetyViolation("The exact harvest source is absent or ambiguous.")
        wanted = normalize_control_label(targets[0].name)
        outputs = [
            control
            for control in telemetry.ui.visible_controls
            if control.role == "item"
            and control.section == "out"
            and control.item_name is not None
            and control.item_quantity is not None
            and 1 <= control.item_quantity <= 5
            and normalize_control_label(control.window) == wanted
        ]
        if len(outputs) != 1:
            raise SafetyViolation("The resource output stack is not exact and bounded.")
        output = outputs[0]
        assert output.item_name is not None
        assert output.item_quantity is not None
        return CollectResourceOutputAction(
            target_id=action.target_id,
            cell_label=output.label,
            item_name=output.item_name,
            source_quantity=output.item_quantity,
            window=output.window,
            section="out",
        )

    @staticmethod
    def _transfer_delta(
        evidence: ResourceTransferEvidence | None,
    ) -> tuple[int, int]:
        source_loss = (
            evidence.source_quantity_before - evidence.source_quantity_after
            if evidence is not None
            and evidence.source_quantity_before is not None
            and evidence.source_quantity_after is not None
            else 0
        )
        destination_gain = (
            evidence.destination_quantity_after - evidence.destination_quantity_before
            if evidence is not None
            and evidence.destination_quantity_before is not None
            and evidence.destination_quantity_after is not None
            else 0
        )
        return source_loss, destination_gain

    @staticmethod
    def _transferred_quantity(evidence: ResourceTransferEvidence | None) -> int:
        if (
            evidence is None
            or evidence.source_quantity_before is None
            or evidence.source_quantity_after is None
        ):
            return 0
        return evidence.source_quantity_before - evidence.source_quantity_after

    @staticmethod
    def _append_failure(state: _HarvestState, reason: str) -> None:
        state.failure_reason = (
            f"{state.failure_reason} {reason}" if state.failure_reason is not None else reason
        )


def resource_handlers(
    port: ResourceMechanicsPort,
    authority: OperationAuthority,
    binding: OperationBindingAuthority,
    planning_config: PlanningConfig,
) -> dict[str, OperationHandler]:
    return {
        "resources.perform_context_action": NativeMovementHandler(
            port.perform_context_action, planning_config
        ),
        "resources.produce_resource_output": NativeMovementHandler(
            port.produce_resource_output, planning_config
        ),
        "resources.open_context_inventory": AtomicMovementHandler(
            port.open_context_inventory,
            verify_native_terminal=True,
        ),
        "resources.harvest_resource": HarvestHandler(
            port,
            authority,
            binding,
            planning_config,
        ),
    }


RESOURCE_TRANSFER_OBSERVATION_TIMEOUT_SECONDS = 2.0


class KenshiResourceMechanics:
    """Production, context-action, and resource-transfer mechanics."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def finish_resource_transfer(
        self,
        action: CollectResourceOutputAction,
        transition: Transition,
    ) -> Transition:
        """Complete the collect handler's conservation terminal from later state."""

        semantic = transition.receipt.semantic
        if (
            semantic is None
            or semantic.resource_transfer is None
            or semantic.source_revision is None
        ):
            return transition
        observation = transition.observation
        deadline = time.monotonic() + RESOURCE_TRANSFER_OBSERVATION_TIMEOUT_SECONDS
        evidence = finalize_resource_transfer(
            action,
            baseline=semantic.resource_transfer,
            before_revision=semantic.source_revision,
            after=observation,
        )
        while (
            evidence.status is ResourceTransferStatus.UNVERIFIED
            and not observation.world_revision.is_later_than(semantic.source_revision)
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)
            observation = await self._surface.port.observe_without_capture()
            evidence = finalize_resource_transfer(
                action,
                baseline=semantic.resource_transfer,
                before_revision=semantic.source_revision,
                after=observation,
            )
        receipt = transition.receipt.model_copy(
            update={
                "semantic": semantic.model_copy(update={"resource_transfer": evidence}),
                "message": transition.receipt.message + " " + evidence.reason,
                "error_type": (
                    None
                    if evidence.status is ResourceTransferStatus.TRANSFERRED
                    else "ResourceTransferNotProven"
                ),
                "completed_at_revision": observation.world_revision,
            }
        )
        return transition.model_copy(
            update={
                "receipt": receipt,
                "observation": observation,
                "events": observation.events,
            }
        )

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

    async def open_context_inventory(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=self._execute_context_inventory_operation,
        )

    async def collect_resource_output(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        transition = await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_collect_operation(
                current, started, dispatch, token
            ),
        )
        return await self.finish_resource_transfer(
            cast(CollectResourceOutputAction, action), transition
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

    async def _execute_context_inventory_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_open_context_inventory(
            cast(OpenContextInventoryAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_collect_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_collect_resource_output(
            cast(CollectResourceOutputAction, action), started, token
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
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=native_commands.NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
            require_dialogue_target=False,
            minimum_output_quantity=action.minimum_output_quantity,
        )

    async def _execute_open_context_inventory(
        self,
        action: OpenContextInventoryAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Open the ordinary inventory window for one exact resource handle."""

        pulse_seconds = self._surface.controls_config.native_movement_pulse_seconds
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.OPEN_CONTEXT_INVENTORY_DEFINITION.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact natural-resource handle and required native "
                "terminal proof that its contextual inventory is open."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            require_vendor_role=False,
            semantic=semantic,
            wire_command=native_commands.NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
        )

    async def _execute_collect_resource_output(
        self,
        action: CollectResourceOutputAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Right-click exact output, retaining both inventory baselines."""

        del started
        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundResourceOutputCell,
        )
        bounds = binding.resolved_bounds
        assert bounds is not None
        baseline = begin_resource_transfer(action, observation)
        if (
            baseline.source_quantity_before is None
            or baseline.destination_quantity_before is None
            or baseline.selected_character_id is None
        ):
            raise RuntimeError(
                "No input was sent: complete source and destination baselines "
                "could not be retained."
            )
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        move_receipt = await self._surface.controller.execute(MoveCursorAction(x=x, y=y))
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
            contract_version=operations.COLLECT_RESOURCE_OUTPUT_DEFINITION.version,
            target_id=action.target_id,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved exact resource identity, output section, item, "
                f"quantity, bounds, and complete destination in-lease. {binding.reason}"
            ),
            resource_transfer=baseline,
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "primitive_actions": (
                    move_receipt.primitive_actions + primitive_receipt.primitive_actions
                ),
                "message": (
                    f"Sent the transfer gesture for {action.source_quantity} "
                    f"{action.item_name!r}; awaiting conserved source loss and "
                    "destination gain."
                ),
            }
        )
