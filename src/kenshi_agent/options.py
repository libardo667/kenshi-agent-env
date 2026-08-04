from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Any, TypeAlias, TypeGuard

from .approach import ApproachMonitor, ApproachStatus
from .input_boundary import ExecutionToken
from .models import (
    Action,
    ApproachDialogueTargetAction,
    CommandDispatchContext,
    ExitCurrentBuildingAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    Observation,
    PerformContextAction,
    ProduceResourceOutputAction,
    RegroupWithSquadMemberAction,
    RespondToImmediateThreatAction,
    SemanticActionReceipt,
    SkillAction,
    ThreatResponseStrategy,
    Transition,
    TravelToMapDestinationAction,
    WorldStateRevision,
)
from .movement_ownership import has_keyed_native_movement_terminal
from .operation_definitions import definition_for
from .threat_response import (
    threat_response_authority_error,
    threat_response_health_error,
    threat_response_terminal_reached,
    withdrawal_move,
)
from .world_state import StoreUpdate

NativeMovementAction: TypeAlias = (
    MoveInDirectionAction
    | MoveToCharacterAction
    | RegroupWithSquadMemberAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
    | PerformContextAction
    | ProduceResourceOutputAction
)
TransitionOperation: TypeAlias = Callable[..., Coroutine[Any, Any, Transition]]


class OptionLifecycleError(RuntimeError):
    pass


class OptionStatus(StrEnum):
    CREATED = "created"
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OptionPoll:
    option_id: str
    status: OptionStatus
    reason: str
    revision: WorldStateRevision


class StatefulMovementOption:
    """Lifecycle adapter for one existing bounded movement-pulse skill."""

    def __init__(
        self,
        *,
        option_id: str,
        action: SkillAction,
        operation: TransitionOperation,
        require_paused_start: bool = True,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.operation = operation
        # Same reason the approach option carries this: an agent playing
        # continuously moves from a running world, and an unconditional demand
        # for a paused start means the move can never begin.
        self.require_paused_start = require_paused_start
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.reason = "Option has not been prepared."

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Movement option can only be prepared once.")
        telemetry = observation.telemetry
        if telemetry is None or "game.pause" not in telemetry.capabilities:
            raise OptionLifecycleError("Movement option requires a capable start state.")
        if self.require_paused_start and telemetry.game.paused is not True:
            raise OptionLifecycleError(
                "Movement option requires a capable, confirmed paused start state."
            )
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        self.status = OptionStatus.PREPARED
        self.reason = "Movement start state is capable and confirmed paused."
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Movement option must be prepared before start.")
        self.status = OptionStatus.RUNNING
        self.reason = "Movement action is running through the environment."
        work = self.operation(command=command, token=token)
        self.task = asyncio.create_task(work, name=f"kenshi-agent-{self.option_id}")
        return self.task

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll:
        if update is not None:
            self.latest_observation = update.observation.model_copy(deep=True)
        task = self.task
        if self.status is OptionStatus.RUNNING and task is not None and task.done():
            if task.cancelled():
                self.status = OptionStatus.CANCELLED
                self.reason = "Movement option task was cancelled."
            else:
                error = task.exception()
                if error is not None:
                    self.status = OptionStatus.FAILED
                    self.reason = f"Movement option failed: {type(error).__name__}: {error}"
                else:
                    self.transition = task.result()
                    self.latest_observation = self.transition.observation.model_copy(deep=True)
                    self.status = OptionStatus.SUCCEEDED
                    self.reason = "Movement environment transition completed."
        return self._poll_result()

    async def cancel(self, reason: str) -> OptionPoll:
        if self.status in {
            OptionStatus.SUCCEEDED,
            OptionStatus.FAILED,
            OptionStatus.CANCELLED,
        }:
            return self._poll_result()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.status = OptionStatus.FAILED
                self.reason = (
                    f"Movement option cancellation cleanup failed: {type(exc).__name__}: {exc}"
                )
                return self._poll_result()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return self._poll_result()

    def result(self) -> Transition:
        self.poll()
        if self.status is OptionStatus.FAILED and self.task is not None:
            error = self.task.exception()
            if error is not None:
                raise error
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                f"Movement option has no successful transition in state {self.status.value!r}."
            )
        return self.transition.model_copy(deep=True)

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        if observation is None:
            revision = WorldStateRevision()
        else:
            revision = observation.world_revision.model_copy(deep=True)
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )


class StatefulThreatResponseOption:
    """Own engage/withdraw mechanics through a confirmed terminal pause."""

    def __init__(
        self,
        *,
        option_id: str,
        action: RespondToImmediateThreatAction,
        operation: TransitionOperation,
        pause_operation: TransitionOperation,
        withdrawal_operation: TransitionOperation | None = None,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.operation = operation
        self.withdrawal_operation = withdrawal_operation
        self.pause_operation = pause_operation
        self.command: CommandDispatchContext | None = None
        self.token: ExecutionToken | None = None
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.movement_option: StatefulNativeMovementOption | None = None
        self.reason = "Threat-response option has not been prepared."
        self._finished = False

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Threat-response option can only be prepared once.")
        if reason := threat_response_authority_error(self.action, observation):
            raise OptionLifecycleError(reason)
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        if self.action.strategy is ThreatResponseStrategy.WITHDRAW:
            movement = withdrawal_move(self.action, observation)
            if movement is None:
                raise OptionLifecycleError(
                    "Withdrawal geometry could not be derived from current positions."
                )
            if self.withdrawal_operation is None:
                raise OptionLifecycleError(
                    "Withdrawal has no exact native movement operation."
                )
            self.movement_option = StatefulNativeMovementOption(
                option_id=f"{self.option_id}-withdrawal",
                action=movement,
                operation=partial(self.withdrawal_operation, movement),
                require_paused_start=True,
            )
            self.movement_option.prepare(observation)
        self.status = OptionStatus.PREPARED
        self.reason = f"Paused immediate threat is grounded for {self.action.strategy.value}."
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Threat-response option must be prepared before start.")
        if command is None:
            raise OptionLifecycleError("Threat-response option requires a keyed command context.")
        self.status = OptionStatus.RUNNING
        self.command = command.model_copy(deep=True)
        self.token = token
        if self.movement_option is not None:
            self.reason = "Runtime issued a grounded withdrawal away from the nearest hostile."
            self.task = self.movement_option.start(command, token=token)
        else:
            self.reason = "Runtime is establishing normal-speed playback for the chosen engagement."
            self.task = asyncio.create_task(
                self.operation(command=command, token=token),
                name=f"kenshi-agent-{self.option_id}",
            )
        return self.task

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll:
        if update is not None:
            self.latest_observation = update.observation.model_copy(deep=True)
        if self.status is not OptionStatus.RUNNING:
            return self._poll_result()

        movement_status: OptionStatus | None = None
        if self.movement_option is not None:
            movement_poll = self.movement_option.poll(update)
            movement_status = movement_poll.status
            self.transition = self.movement_option.transition
            if movement_status in {OptionStatus.FAILED, OptionStatus.CANCELLED}:
                self.status = movement_status
                self.reason = movement_poll.reason
                return self._poll_result()
        else:
            task = self.task
            if task is not None and task.done():
                if task.cancelled():
                    self.status = OptionStatus.CANCELLED
                    self.reason = "Threat-response playback task was cancelled."
                    return self._poll_result()
                if error := task.exception():
                    self.status = OptionStatus.FAILED
                    self.reason = (
                        f"Threat-response playback failed: {type(error).__name__}: {error}"
                    )
                    return self._poll_result()
                if self.transition is None:
                    self.transition = task.result()
                    if update is None:
                        self.latest_observation = self.transition.observation.model_copy(deep=True)
                    if not self.transition.receipt.accepted or not self.transition.receipt.executed:
                        self.status = OptionStatus.FAILED
                        self.reason = (
                            "Threat-response playback was not executed: "
                            f"{self.transition.receipt.message}"
                        )
                        return self._poll_result()

        if self.transition is None:
            return self._poll_result()
        latest = self.latest_observation
        if latest is None:
            return self._poll_result()
        if reason := threat_response_health_error(latest):
            self.status = OptionStatus.FAILED
            self.reason = (
                f"Threat response reached its deterministic disengagement boundary: {reason}"
            )
        elif threat_response_terminal_reached(self.action, latest):
            self.status = OptionStatus.SUCCEEDED
            self.reason = "The immediate hostile and selected actor's combat state are clear."
        elif movement_status is OptionStatus.SUCCEEDED:
            self.status = OptionStatus.FAILED
            self.reason = "The bounded withdrawal finished, but the immediate threat remains."
        else:
            self.reason = f"The chosen {self.action.strategy.value} response remains in progress."
        return self._poll_result()

    async def finish(self) -> OptionPoll:
        """Pause and confirm the world without changing the terminal verdict."""

        if self._finished:
            return self._poll_result()
        self._finished = True
        try:
            if self.pause_operation is not None:
                pause_transition = await self.pause_operation(
                    command=self.command,
                    token=self.token,
                )
            elif self.start_observation is not None:
                raise RuntimeError("Threat response has no terminal pause operation.")
            else:
                raise RuntimeError("Threat response has no start observation.")
        except Exception as exc:
            self.status = OptionStatus.FAILED
            self.reason = f"Threat-response terminal pause failed: {type(exc).__name__}: {exc}"
            return self._poll_result()

        paused = pause_transition.observation.telemetry
        if (
            not pause_transition.receipt.accepted
            or not pause_transition.receipt.executed
            or pause_transition.observation.telemetry_stale
            or paused is None
            or paused.game.paused is not True
        ):
            self.status = OptionStatus.FAILED
            self.reason = "Threat-response cleanup did not confirm a fresh terminal pause."
            return self._poll_result()

        primary = self.transition
        self.latest_observation = pause_transition.observation.model_copy(deep=True)
        if primary is not None:
            receipt = primary.receipt.model_copy(
                update={
                    "action": self.action,
                    "completed_at_revision": pause_transition.observation.world_revision,
                    "finished_at": pause_transition.receipt.finished_at,
                    "primitive_actions": (
                        primary.receipt.primitive_actions
                        + pause_transition.receipt.primitive_actions
                    ),
                    "semantic": SemanticActionReceipt(
                        action_kind=self.action.kind,
                        contract_version="1.0",
                        target_id=self.action.actor_id,
                        source_revision=(
                            self.start_observation.world_revision
                            if self.start_observation is not None
                            else None
                        ),
                        option_id=self.option_id,
                        revalidation=(
                            "The runtime rebound the exact selected actor and "
                            "immediate threat before dispatch, then monitored "
                            "threat and squad health through a confirmed pause."
                        ),
                    ),
                    "message": (
                        f"{primary.receipt.message} {self.reason} "
                        "Runtime confirmed the terminal pause."
                    ),
                }
            )
            self.transition = primary.model_copy(
                update={
                    "receipt": receipt,
                    "observation": pause_transition.observation,
                    "events": pause_transition.events,
                },
                deep=True,
            )
        self.reason += " Runtime confirmed the terminal pause."
        return self._poll_result()

    async def cancel(self, reason: str) -> OptionPoll:
        if (
            self.status
            in {
                OptionStatus.SUCCEEDED,
                OptionStatus.FAILED,
                OptionStatus.CANCELLED,
            }
            and self._finished
        ):
            return self._poll_result()
        if self.movement_option is not None:
            await self.movement_option.cancel(reason)
            self.transition = self.movement_option.transition
        else:
            task = self.task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.status = OptionStatus.FAILED
                    self.reason = (
                        f"Threat-response cancellation failed: {type(exc).__name__}: {exc}"
                    )
                    return await self.finish()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return await self.finish()

    def result(self) -> Transition:
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                "Threat-response option has no successful transition in state "
                f"{self.status.value!r}."
            )
        if not self._finished:
            raise OptionLifecycleError(
                "Threat-response option has not confirmed its terminal pause."
            )
        return self.transition.model_copy(deep=True)

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        revision = (
            observation.world_revision.model_copy(deep=True)
            if observation is not None
            else WorldStateRevision()
        )
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )



def _required_native_terminal_reasons(action: Action) -> frozenset[str]:
    """The exact native reasons this operation's definition accepts as complete.

    Empty means the definition names no proof, so any terminal is accepted. This
    keeps "what proves this operation finished" the definition's answer rather
    than a type switch at the monitor.
    """

    definition = definition_for(action)
    if definition is None:
        return frozenset()
    return definition.native_terminal_success_reasons


class StatefulNativeMovementOption:
    """Own one native command until its exact acknowledgement becomes terminal.

    Directional, exact-character, and building-exit movement finish after
    native pathing proves their command-specific terminal.
    Context actions finish when native code proves that Kenshi's AI accepted
    the exact reviewed task/subject pair. In every case the acknowledgement is
    keyed by command ID and fenced by the complete command identity.
    """

    @classmethod
    def supports(cls, action: Action) -> TypeGuard[NativeMovementAction]:
        """Whether this lifecycle owns the action's native terminal."""

        return has_keyed_native_movement_terminal(action)

    def __init__(
        self,
        *,
        option_id: str,
        action: NativeMovementAction,
        operation: TransitionOperation,
        require_paused_start: bool = True,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.operation = operation
        self.require_paused_start = require_paused_start
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.command: CommandDispatchContext | None = None
        # Usually this is the caller's new command ID. If the environment
        # safely adopts an exact already-active direction, it is the original
        # native command ID instead; the executor's logical command remains
        # separate for plan causality.
        self.native_command_id: str | None = None
        self.selected_character_ids: list[str] = []
        self.reason = "Native movement option has not been prepared."

    @property
    def _wire_command(self) -> str:
        if isinstance(self.action, MoveToCharacterAction):
            return "move_to_character"
        if isinstance(self.action, RegroupWithSquadMemberAction):
            return "regroup_with_squad_member"
        if isinstance(self.action, TravelToMapDestinationAction):
            return "travel_to_map_destination"
        if isinstance(self.action, ProduceResourceOutputAction):
            return "produce_resource_output"
        if isinstance(self.action, PerformContextAction):
            return "perform_context_action"
        if isinstance(self.action, ExitCurrentBuildingAction):
            return "exit_current_building"
        return "move_in_direction"

    @property
    def _required_capability(self) -> str:
        if isinstance(self.action, MoveToCharacterAction):
            return "control.move_to_character"
        if isinstance(self.action, RegroupWithSquadMemberAction):
            return "control.regroup_with_squad_member"
        if isinstance(self.action, TravelToMapDestinationAction):
            return "control.travel_to_map_destination"
        if isinstance(self.action, ProduceResourceOutputAction):
            return "control.produce_resource_output"
        if isinstance(self.action, PerformContextAction):
            return "control.perform_context_action"
        if isinstance(self.action, ExitCurrentBuildingAction):
            return "control.exit_current_building"
        return "control.move_in_direction"

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Native movement option can only be prepared once.")
        telemetry = observation.telemetry
        if (
            telemetry is None
            or "game.pause" not in telemetry.capabilities
            or self._required_capability not in telemetry.capabilities
        ):
            raise OptionLifecycleError("Native movement option requires a capable start state.")
        if self.require_paused_start and telemetry.game.paused is not True:
            raise OptionLifecycleError(
                "Native movement option requires a capable, confirmed paused start state."
            )
        selected_ids = telemetry.ui.selected_character_ids
        if (
            not selected_ids
            or telemetry.ui.selected_character_id not in selected_ids
            or (
                not isinstance(
                    self.action,
                    (MoveToCharacterAction, TravelToMapDestinationAction),
                )
                and len(selected_ids) != 1
            )
        ):
            raise OptionLifecycleError(
                "Native movement option selection does not satisfy the action's "
                "exact selection-cardinality contract."
            )
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        self.selected_character_ids = list(selected_ids)
        if isinstance(self.action, MoveToCharacterAction):
            character_targets = [
                entity for entity in telemetry.nearby_entities if entity.id == self.action.target_id
            ]
            if len(character_targets) != 1:
                raise OptionLifecycleError(
                    "Character-movement option requires one exact currently nearby target."
                )
        if isinstance(self.action, RegroupWithSquadMemberAction):
            actors = [
                member
                for member in telemetry.squad
                if member.id == self.action.actor_id and member.selected
            ]
            squad_targets = [
                member for member in telemetry.squad if member.id == self.action.target_id
            ]
            if (
                len(actors) != 1
                or telemetry.ui.selected_character_ids != [self.action.actor_id]
                or len(squad_targets) != 1
                or self.action.target_id == self.action.actor_id
            ):
                raise OptionLifecycleError(
                    "Squad-regroup option requires one exact selected actor and "
                    "one distinct current squadmate."
                )
        if isinstance(self.action, TravelToMapDestinationAction):
            destinations = [
                destination
                for destination in telemetry.known_map_destinations
                if destination.id == self.action.destination_id
            ]
            if len(destinations) != 1:
                raise OptionLifecycleError(
                    "Map-travel option requires one exact currently known destination."
                )
        if isinstance(self.action, ExitCurrentBuildingAction):
            selected = [character for character in telemetry.squad if character.selected]
            if len(selected) != 1 or selected[0].indoors is not True:
                raise OptionLifecycleError(
                    "Building-exit option requires one selected character confirmed indoors."
                )
        if isinstance(
            self.action,
            (PerformContextAction, ProduceResourceOutputAction),
        ):
            context_targets = [
                target
                for target in telemetry.world_targets
                if target.id == self.action.target_id
                and (
                    (
                        isinstance(self.action, PerformContextAction)
                        and self.action.context_action in target.context_actions
                    )
                    or (
                        isinstance(self.action, ProduceResourceOutputAction)
                        and "operate" in target.context_actions
                    )
                )
            ]
            if len(context_targets) != 1:
                raise OptionLifecycleError(
                    "Context-action option requires one exact currently actionable world target."
                )
        active_id = telemetry.native_control.active_command_id
        active = (
            telemetry.native_control.acknowledgement_for(active_id)
            if active_id is not None
            else None
        )
        if (
            active is not None
            and active.status is NativeCommandStatus.ACCEPTED
            and self._matches_identity(active)
        ):
            self.native_command_id = active.command_id
        self.status = OptionStatus.PREPARED
        self.reason = "Native movement start state is capable and the selection is exact."
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Native movement option must be prepared before start.")
        if command is None:
            raise OptionLifecycleError("Native movement option requires a keyed command context.")
        self.command = command.model_copy(deep=True)
        if self.native_command_id is None:
            self.native_command_id = command.command_id
        self.status = OptionStatus.RUNNING
        if isinstance(self.action, ProduceResourceOutputAction):
            self.reason = (
                "Resource production dispatched; awaiting actual stock in the "
                "exact target's output inventory."
            )
        elif isinstance(self.action, PerformContextAction):
            self.reason = (
                "Contextual task dispatched; awaiting native proof of the exact task and target."
            )
        elif isinstance(self.action, ExitCurrentBuildingAction):
            self.reason = (
                "Building-exit order dispatched; awaiting its terminal native acknowledgement."
            )
        elif isinstance(self.action, TravelToMapDestinationAction):
            self.reason = (
                "Map-travel order dispatched; awaiting arrival at the exact known destination."
            )
        elif isinstance(self.action, MoveToCharacterAction):
            self.reason = (
                "Exact-character walk dispatched; awaiting its keyed native arrival terminal."
            )
        elif isinstance(self.action, RegroupWithSquadMemberAction):
            self.reason = (
                "Squad-regroup order dispatched; awaiting the exact squadmate arrival terminal."
            )
        else:
            self.reason = (
                "Directional movement order dispatched; awaiting its terminal "
                "native acknowledgement."
            )
        work = self.operation(command=command, token=token)
        self.task = asyncio.create_task(work, name=f"kenshi-agent-{self.option_id}")
        return self.task

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll:
        if update is not None:
            self.latest_observation = update.observation.model_copy(deep=True)
        if self.status is not OptionStatus.RUNNING:
            return self._poll_result()

        task = self.task
        if task is not None and task.done():
            if task.cancelled():
                self.status = OptionStatus.CANCELLED
                self.reason = "Native movement option task was cancelled."
                return self._poll_result()
            error = task.exception()
            if error is not None:
                self.status = OptionStatus.FAILED
                self.reason = f"Native movement dispatch failed: {type(error).__name__}: {error}"
                return self._poll_result()
            if self.transition is None:
                self.transition = task.result()
                # A store update passed to this poll may already be later than
                # the observation bundled with the quick dispatch receipt. Do
                # not roll the monitor backward to that older acceptance.
                if update is None:
                    self.latest_observation = self.transition.observation.model_copy(deep=True)
                if not self.transition.receipt.accepted and not self.transition.receipt.executed:
                    self.status = OptionStatus.FAILED
                    self.reason = (
                        "Native movement order was rejected without execution: "
                        f"{self.transition.receipt.message}"
                    )
                    return self._poll_result()
                receipt_acknowledgement = self.transition.receipt.native_acknowledgement
                if (
                    receipt_acknowledgement is not None
                    and receipt_acknowledgement.status
                    in {
                        NativeCommandStatus.ACCEPTED,
                        NativeCommandStatus.COMPLETED,
                    }
                    and self._matches_identity(receipt_acknowledgement)
                ):
                    # LiveEnvironment may have adopted an exact active order
                    # instead of issuing the executor's fresh logical command.
                    # Continue monitoring the native ID it actually returned.
                    self.native_command_id = receipt_acknowledgement.command_id

        acknowledgement = self._current_acknowledgement()
        if acknowledgement is None:
            self.reason = "Native movement was dispatched; awaiting a matching acknowledgement."
            return self._poll_result()
        if not self._matches(acknowledgement):
            self.status = OptionStatus.FAILED
            self.reason = (
                "Native movement acknowledgement identity did not match the "
                "dispatched command vector and selection."
            )
            return self._poll_result()
        if acknowledgement.status is NativeCommandStatus.ACCEPTED:
            if isinstance(self.action, ProduceResourceOutputAction):
                self.reason = (
                    "Kenshi retained the exact resource job; task or approach is "
                    "progress, and output is not ready yet."
                )
            else:
                self.reason = (
                    "Kenshi accepted the exact native movement order; the character "
                    "is still walking."
                )
        elif acknowledgement.status is NativeCommandStatus.COMPLETED:
            required = _required_native_terminal_reasons(self.action)
            if required and acknowledgement.reason not in required:
                self.status = OptionStatus.FAILED
                self.reason = (
                    "Native operation claimed terminal completion without its "
                    f"required proof: reported {acknowledgement.reason!r}, "
                    f"expected one of {', '.join(sorted(required))}."
                )
            elif self.transition is None:
                self.reason = (
                    "Kenshi completed the exact native movement order; "
                    "awaiting the dispatch transition."
                )
            else:
                self.status = OptionStatus.SUCCEEDED
                self.reason = (
                    f"Kenshi completed the exact native movement order: {acknowledgement.reason}."
                )
        else:
            self.status = OptionStatus.FAILED
            self.reason = (
                f"Kenshi ended the native movement as "
                f"{acknowledgement.status.value!r}: {acknowledgement.reason}."
            )
        return self._poll_result()

    async def cancel(self, reason: str) -> OptionPoll:
        if self.status in {
            OptionStatus.SUCCEEDED,
            OptionStatus.FAILED,
            OptionStatus.CANCELLED,
        }:
            return self._poll_result()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.status = OptionStatus.FAILED
                self.reason = (
                    f"Native movement cancellation cleanup failed: {type(exc).__name__}: {exc}"
                )
                return self._poll_result()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return self._poll_result()

    def result(self) -> Transition:
        if self.status is OptionStatus.FAILED and self.task is not None:
            done_cleanly = self.task.done() and not self.task.cancelled()
            error = self.task.exception() if done_cleanly else None
            if error is not None:
                raise error
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                "Native movement option has no successful transition in state "
                f"{self.status.value!r}."
            )
        return self.transition.model_copy(deep=True)

    def _current_acknowledgement(
        self,
    ) -> NativeCommandAcknowledgement | None:
        if self.native_command_id is None:
            return None
        observation = self.latest_observation
        if observation is not None and observation.telemetry is not None:
            acknowledgement = observation.telemetry.native_control.acknowledgement_for(
                self.native_command_id
            )
            if acknowledgement is not None:
                return acknowledgement
        if self.transition is not None:
            return self.transition.receipt.native_acknowledgement
        return None

    def _matches(self, acknowledgement: NativeCommandAcknowledgement) -> bool:
        if self.native_command_id is None:
            return False
        return bool(
            acknowledgement.command_id == self.native_command_id
            and self._matches_identity(acknowledgement)
        )

    def _matches_identity(
        self,
        acknowledgement: NativeCommandAcknowledgement,
    ) -> bool:
        if acknowledgement.command != self._wire_command:
            return False
        if len(acknowledgement.selected_character_ids) != len(self.selected_character_ids) or set(
            acknowledgement.selected_character_ids
        ) != set(self.selected_character_ids):
            return False
        if isinstance(
            self.action,
            (PerformContextAction, ProduceResourceOutputAction),
        ):
            minimum_matches = (
                not isinstance(self.action, ProduceResourceOutputAction)
                or acknowledgement.minimum_output_quantity == self.action.minimum_output_quantity
            )
            return bool(
                acknowledgement.target_id == self.action.target_id
                and acknowledgement.bearing_degrees == 0.0
                and acknowledgement.distance_units == 0.0
                and minimum_matches
            )
        if isinstance(self.action, TravelToMapDestinationAction):
            return bool(
                acknowledgement.target_id == self.action.destination_id
                and acknowledgement.bearing_degrees == 0.0
                and acknowledgement.distance_units == 0.0
            )
        if isinstance(self.action, MoveToCharacterAction):
            return bool(
                acknowledgement.target_id == self.action.target_id
                and acknowledgement.bearing_degrees == 0.0
                and acknowledgement.distance_units == 0.0
            )
        if isinstance(self.action, RegroupWithSquadMemberAction):
            return bool(
                acknowledgement.target_id == self.action.target_id
                and acknowledgement.bearing_degrees == 0.0
                and acknowledgement.distance_units == 0.0
            )
        if acknowledgement.target_id != "":
            return False
        if isinstance(self.action, ExitCurrentBuildingAction):
            return bool(
                acknowledgement.bearing_degrees == 0.0 and acknowledgement.distance_units == 0.0
            )
        return bool(
            acknowledgement.bearing_degrees == self.action.bearing_degrees
            and acknowledgement.distance_units == self.action.distance_units
        )

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        revision = (
            observation.world_revision.model_copy(deep=True)
            if observation is not None
            else WorldStateRevision()
        )
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )


class StatefulApproachOption:
    """A long approach toward a deterministic target, monitored by world state.

    Unlike a movement pulse, the approach dispatch (a native move-then-talk
    order) is acknowledged quickly while the character keeps walking for tens of
    seconds. So the dispatch task completing is not success: the option issues
    the action, then drives an `ApproachMonitor` from world-state updates and
    reaches SUCCEEDED only when dialogue opens with the exact target (or it
    closes inside the arrival radius), and FAILED when the monitor says the
    target vanished or a hostile entered threat range. This is the long,
    interruptible window in which strategic planning can overlap execution.
    """

    def __init__(
        self,
        *,
        option_id: str,
        action: Action,
        operation: TransitionOperation,
        target_id: str,
        arrival_distance: float = 5.0,
        threat_distance: float = 15.0,
        require_paused_start: bool = True,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.operation = operation
        # An agent playing continuously starts its walk from a running world;
        # demanding a paused start there means the approach can never begin.
        self.require_paused_start = require_paused_start
        self.monitor = ApproachMonitor(
            target_id=target_id,
            arrival_distance=arrival_distance,
            threat_distance=threat_distance,
        )
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.latest_status: ApproachStatus | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.reason = "Approach option has not been prepared."

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Approach option can only be prepared once.")
        telemetry = observation.telemetry
        if telemetry is None or "game.pause" not in telemetry.capabilities:
            raise OptionLifecycleError("Approach option requires a capable start state.")
        if self.require_paused_start and telemetry.game.paused is not True:
            raise OptionLifecycleError(
                "Approach option requires a capable, confirmed paused start state."
            )
        begin = self.monitor.begin(observation)
        if not begin.target_present:
            raise OptionLifecycleError(
                "Approach option requires the target to be present at the start."
            )
        native_talk = isinstance(self.action, ApproachDialogueTargetAction)
        if begin.arrived and not native_talk:
            raise OptionLifecycleError(
                "Approach option must not dispatch after the target has already been reached."
            )
        if begin.should_abort:
            raise OptionLifecycleError(f"Approach option start state is blocked: {begin.reason}")
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        self.latest_status = begin
        self.status = OptionStatus.PREPARED
        self.reason = (
            "Target is already close; issue one native talk-to order and verify "
            "that exact dialogue opens."
            if begin.arrived
            else "Approach start state is capable, paused, and the target is present."
        )
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Approach option must be prepared before start.")
        self.status = OptionStatus.RUNNING
        self.reason = (
            "Dialogue interaction dispatched; monitoring the exact target."
            if isinstance(self.action, ApproachDialogueTargetAction)
            else "Approach order dispatched; walking toward the target."
        )
        work = self.operation(command=command, token=token)
        self.task = asyncio.create_task(work, name=f"kenshi-agent-{self.option_id}")
        return self.task

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll:
        if update is not None:
            self.latest_observation = update.observation.model_copy(deep=True)
        if self.status is not OptionStatus.RUNNING:
            return self._poll_result()

        # A definitively rejected or failed dispatch means the approach never
        # started; it is not a case of "still walking."
        task = self.task
        if task is not None and task.done():
            if task.cancelled():
                self.status = OptionStatus.CANCELLED
                self.reason = "Approach option task was cancelled."
                return self._poll_result()
            error = task.exception()
            if error is not None:
                self.status = OptionStatus.FAILED
                self.reason = f"Approach dispatch failed: {type(error).__name__}: {error}"
                return self._poll_result()
            if self.transition is None:
                self.transition = task.result()
                if not self.transition.receipt.accepted and not self.transition.receipt.executed:
                    self.status = OptionStatus.FAILED
                    self.reason = (
                        "Approach order was rejected without execution: "
                        f"{self.transition.receipt.message}"
                    )
                    return self._poll_result()

        # Drive the deterministic monitor from the latest world state.
        if update is not None:
            status = self.monitor.assess(update.observation)
            self.latest_status = status
            exact_dialogue_required = isinstance(self.action, ApproachDialogueTargetAction)
            if status.arrived and (not exact_dialogue_required or status.dialogue_open_with_target):
                # Success requires the order to have been accepted, so we do not
                # claim arrival from a dispatch that never issued.
                if self.transition is not None:
                    self.status = OptionStatus.SUCCEEDED
                    self.reason = status.reason
                else:
                    self.reason = (
                        f"Target reached; awaiting dispatch acknowledgement. {status.reason}"
                    )
            elif status.should_abort:
                self.status = OptionStatus.FAILED
                self.reason = status.reason
            elif status.arrived and exact_dialogue_required:
                self.reason = (
                    "Within talk range; waiting for Kenshi to open dialogue with "
                    "the exact native target."
                )
            else:
                self.reason = status.reason
        return self._poll_result()

    async def cancel(self, reason: str) -> OptionPoll:
        if self.status in {
            OptionStatus.SUCCEEDED,
            OptionStatus.FAILED,
            OptionStatus.CANCELLED,
        }:
            return self._poll_result()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.status = OptionStatus.FAILED
                self.reason = (
                    f"Approach option cancellation cleanup failed: {type(exc).__name__}: {exc}"
                )
                return self._poll_result()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return self._poll_result()

    def result(self) -> Transition:
        if self.status is OptionStatus.FAILED and self.task is not None:
            done_cleanly = self.task.done() and not self.task.cancelled()
            error = self.task.exception() if done_cleanly else None
            if error is not None:
                raise error
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                f"Approach option has no successful transition in state {self.status.value!r}."
            )
        return self.transition.model_copy(deep=True)

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        revision = (
            observation.world_revision.model_copy(deep=True)
            if observation is not None
            else WorldStateRevision()
        )
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )
