"""Cross-cutting lifecycle for one already-bound private operation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..action_budget import ActionBudgetError, ActionBudgetLedger, ActionBudgetReservation
from ..condition_evaluation import evaluate_conditions
from ..core.authority import InputBoundaryDecision
from ..core.observation import Observation
from ..core.operation import (
    Action,
    ObservationPolicy,
    PointerActionClass,
)
from ..core.planning import (
    Condition,
    ConditionEvaluation,
    ConditionResult,
    PlanEnvelope,
    PlanStep,
)
from ..core.transport import (
    CommandDispatchContext,
    Transition,
)
from ..core.world import WorldStateRevision
from ..input_boundary import ExecutionToken
from ..operation_authority import AuthorizationDecision
from ..operation_definitions import BoundOperation, OperationTerminal, TerminalOwner
from ..planning import (
    PlanBudgetError,
    PlanBudgetLedger,
    PlanningClock,
    game_elapsed_seconds,
)
from ..session_log import SessionLogger
from ..world_state import CommandCausalityError, WorldStateStore
from .monitor_types import OperationMonitorPort
from .registry import HandlerRegistry
from .types import (
    ExecutionScope,
    OperationContext,
    OperationProgress,
    OperationResult,
    OperationStatus,
)

# The sentence the kernel used to emit for every transition-less failure,
# regardless of what the handler had already worked out.
_GENERIC_NO_TRANSITION = "World-command handler returned no causal transition."



def _authored_recipient_kwargs(bound: BoundOperation) -> dict[str, Any]:
    """Carry the authored recipient basis into the dispatch context.

    Authorization refuses a changed basis before the input lease releases. This
    puts the same facts where the request bytes are actually formed, so the two
    ends of the lease wait can be compared rather than assumed equal.
    """

    basis = bound.identity.recipient_basis
    if basis is None:
        return {}
    return {
        "authored_recipient_scope": basis.scope.value,
        "authored_primary": basis.primary,
        "authored_selection": list(basis.selection),
        "authored_explicit_recipients": list(basis.explicit_recipients),
    }


def _no_causal_transition_reason(
    observation: Observation,
    handler_reason: str = "",
) -> str:
    """Say why the world did not change, not merely that it did not.

    "World-command handler returned no causal transition" is true and useless:
    it names the check that failed rather than the condition that failed it. A
    live run spent every plan it had on world commands against a paused save,
    got this sentence three times, and aborted - while the actual cause,
    elapsed_minutes frozen because the game was paused, sat in every one of the
    158 observations in the bundle. When the world is stopped, no world command
    can ever produce a transition, and the message now says so.
    """

    # The handler usually knows exactly what was wrong and says so. Discarding
    # that for a generic sentence is how a precise diagnosis - "selection does
    # not satisfy the action's exact selection-cardinality contract" - reached
    # the bundle as "no causal transition", which sent a reader to the handler
    # instead of to the two selected characters that actually caused it.
    handler_reason = handler_reason.strip()
    if handler_reason and handler_reason != _GENERIC_NO_TRANSITION:
        return handler_reason
    telemetry = observation.telemetry
    if telemetry is not None and telemetry.game.paused:
        return (
            "The game is paused, so no world command can change the world. "
            "Resume play before ordering anything that depends on time passing."
        )
    return _GENERIC_NO_TRANSITION


class KernelEventReporter(Protocol):
    def __call__(
        self,
        event_type: str,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        step: PlanStep | None = None,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> None: ...


TransitionObserver = Callable[
    [
        PlanEnvelope,
        PlanStep,
        Observation,
        Transition,
        str | None,
        WorldStateRevision | None,
    ],
    Observation,
]
ActionStartedReporter = Callable[[int, Action], None]
OperationAuthorized = Callable[[BoundOperation, Observation], AuthorizationDecision]
OperationPointerClass = Callable[[BoundOperation], PointerActionClass]


@dataclass(frozen=True, slots=True)
class KernelHooks:
    event: KernelEventReporter
    observe_transition: TransitionObserver
    authorized: OperationAuthorized
    pointer_class: OperationPointerClass
    report_action_started: ActionStartedReporter | None = None


@dataclass(frozen=True, slots=True)
class KernelRequest:
    plan: PlanEnvelope
    step: PlanStep
    observation: Observation
    budget: PlanBudgetLedger
    plan_started_at: float
    plan_started_observation: Observation
    remaining_run_actions: int
    monitor: OperationMonitorPort | None = None


@dataclass(frozen=True, slots=True)
class KernelResult:
    observation: Observation
    succeeded: bool
    actions_completed: int
    reason: str
    terminated: bool = False
    success: bool | None = None
    staged_patch: object | None = None
    interrupted: bool = False
    pause_before_replan: bool = False


class ExecutionKernel:
    """Reserve, invoke, validate, and close one bound operation lifecycle."""

    def __init__(
        self,
        *,
        handlers: HandlerRegistry,
        action_budget: ActionBudgetLedger,
        logger: SessionLogger,
        clock: PlanningClock,
        state_store: WorldStateStore,
        hooks: KernelHooks,
        input_boundary_observation: Callable[[], Observation | None],
        input_boundary_max_telemetry_age_seconds: Callable[[], float | None],
    ) -> None:
        self.handlers = handlers
        self.action_budget = action_budget
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.hooks = hooks
        self.input_boundary_observation = input_boundary_observation
        self.input_boundary_max_telemetry_age_seconds = input_boundary_max_telemetry_age_seconds

    async def execute(
        self,
        bound: BoundOperation,
        request: KernelRequest,
    ) -> KernelResult:
        if request.remaining_run_actions <= 0:
            return KernelResult(
                observation=request.observation,
                succeeded=False,
                actions_completed=0,
                reason="Run action budget is exhausted.",
            )

        observation = request.observation
        authorization = self.hooks.authorized(bound, observation)
        if not authorization.allowed or authorization.bound_operation is None:
            return KernelResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=(
                    "Operation authority rejected the bound operation: "
                    + str(authorization.details.get("violation", authorization.code.value))
                ),
            )
        bound = authorization.bound_operation
        try:
            action_reservation = self.action_budget.reserve(bound, observation)
        except ActionBudgetError as exc:
            self.logger.write(
                "action_rejected",
                step_index=observation.step_index,
                payload={
                    "action": bound.operation.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                    "accepted": False,
                    "executed": False,
                    "dry_run": True,
                    "primitive_actions": 0,
                    "authorization_code": exc.code.value,
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return KernelResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=f"{exc.code.value}: {exc}",
            )

        action = bound.operation
        completion = bound.definition.resolve_terminal(
            action,
            observation,
            selected_affordance=bound.affordance is not None,
        )
        if completion.owner is TerminalOwner.RUNTIME_CONDITIONS and not completion.conditions:
            self.action_budget.release(action_reservation)
            return KernelResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=(
                    "Runtime-owned completion could not derive a causal baseline "
                    "from the immediate pre-dispatch observation; no input was sent."
                ),
            )

        try:
            reserved_risk = request.budget.reserve(action)
        except PlanBudgetError as exc:
            self.action_budget.release(action_reservation)
            return KernelResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=str(exc),
            )

        self.hooks.event(
            "plan_budget_reserved",
            request.plan,
            observation,
            step=request.step,
            reason="Reserved one action and its typed risk before dispatch.",
            evidence={
                "pointer_actions": reserved_risk[0],
                "purchase_actions": reserved_risk[1],
                "native_assisted_actions": reserved_risk[2],
            },
        )
        if self.hooks.report_action_started is not None:
            self.hooks.report_action_started(observation.step_index, action)

        command, context = self._start_operation(bound, request, completion)

        try:
            handled = await self.handlers.resolve(bound).execute(bound, context)
        except asyncio.CancelledError:
            request.budget.commit()
            self.action_budget.commit(action_reservation)
            reason = (
                "Independent safety supervision cancelled the in-flight operation; "
                "delivery is uncertain and the reservation remains spent."
            )
            if command is not None:
                self.state_store.fail_active_command(reason)
            self._reservation_event("plan_budget_committed", request, observation, reason)
            self.hooks.event(
                "plan_step_cancelled",
                request.plan,
                observation,
                step=request.step,
                reason=reason,
            )
            raise
        except Exception as exc:
            request.budget.commit()
            self.action_budget.commit(action_reservation)
            if command is not None:
                self.state_store.fail_active_command(f"{type(exc).__name__}: {exc}")
            reason = (
                "Operation delivery is uncertain after a handler error; the "
                "reservation remains spent."
            )
            self._reservation_event("plan_budget_committed", request, observation, reason)
            self.logger.write(
                "operation_handler_error",
                step_index=observation.step_index,
                payload={
                    "handler_key": bound.definition.handler_key,
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return KernelResult(
                observation=observation,
                succeeded=False,
                actions_completed=1,
                reason=f"Operation handler failed: {type(exc).__name__}: {exc}",
            )

        self._record_input_boundary(handled, request, command)
        closed = self._close_reservations(
            handled,
            request,
            action_reservation,
            reserved_risk,
            command,
        )
        if isinstance(closed, KernelResult):
            return closed
        latest, transition = closed

        if handled.status is not OperationStatus.SUCCEEDED:
            return KernelResult(
                observation=latest,
                succeeded=False,
                actions_completed=(0 if handled.status is OperationStatus.REJECTED else 1),
                reason=handled.reason,
                terminated=handled.terminated,
                success=handled.success,
                staged_patch=handled.staged_patch,
                interrupted=handled.status is OperationStatus.INTERRUPTED,
                pause_before_replan=handled.pause_before_replan,
            )

        if bound.definition.controller_verified or (
            completion.owner is TerminalOwner.CONTROLLER_TERMINAL
        ):
            return KernelResult(
                observation=latest,
                succeeded=True,
                actions_completed=1,
                reason=handled.reason,
                terminated=handled.terminated,
                success=handled.success,
                staged_patch=handled.staged_patch,
            )

        if completion.owner is TerminalOwner.AFFORDANCE_DELIVERY:
            accepted = bool(transition is not None and transition.receipt.accepted)
            executed = bool(transition is not None and transition.receipt.executed)
            causal_revision_advanced = latest.world_revision.is_later_than(bound.based_on_revision)
            succeeded = bool(
                transition is not None and accepted and executed and causal_revision_advanced
            )
            self.hooks.event(
                "plan_step_progress",
                request.plan,
                latest,
                step=request.step,
                reason="Checked the adapter's declared delivery terminal.",
                evidence={
                    "completion_owner": completion.owner.value,
                    "accepted": accepted,
                    "executed": executed,
                    "causal_revision_advanced": causal_revision_advanced,
                    "effect_verified": False,
                },
            )
            return KernelResult(
                observation=latest,
                succeeded=succeeded,
                actions_completed=1,
                reason=(
                    "Runtime delivered the exact affordance and received a later observation."
                    if succeeded
                    else "The affordance did not reach its bounded delivery terminal."
                ),
                terminated=handled.terminated,
                success=handled.success,
                staged_patch=handled.staged_patch if succeeded else None,
            )

        return await self._await_conditions(
            bound,
            request,
            handled,
            latest,
            transition,
            completion.owner,
            completion.conditions,
        )

    def _start_operation(
        self,
        bound: BoundOperation,
        request: KernelRequest,
        completion: OperationTerminal,
    ) -> tuple[CommandDispatchContext | None, OperationContext]:
        command, token = self._dispatch_authority(bound, request)
        context = OperationContext(
            world=self.state_store,
            logger=self.logger,
            clock=self.clock,
            scope=ExecutionScope(
                operation_id=(
                    command.command_id
                    if command is not None
                    else (
                        f"{request.plan.plan_id}:{request.plan.plan_version}:{request.step.step_id}"
                    )
                ),
                plan_id=request.plan.plan_id,
                plan_version=request.plan.plan_version,
                step_id=request.step.step_id,
            ),
            command=command,
            token=token,
            monitor=request.monitor,
            report_progress=lambda progress, current: self._progress(
                progress,
                current,
                request,
            ),
        )
        self.hooks.event(
            "plan_step_started",
            request.plan,
            request.observation,
            step=request.step,
            reason="Bound operation was authorized and reserved execution budgets.",
            evidence={
                "action_start_revision": request.observation.world_revision.model_dump(mode="json"),
                "operation_id": context.scope.operation_id,
                "handler_key": bound.definition.handler_key,
                "remaining_actions_before_commit": request.budget.remaining_actions,
                "completion_owner": completion.owner.value,
                "completion_conditions": [
                    condition.model_dump(mode="json") for condition in completion.conditions
                ],
            },
        )
        return command, context

    def _dispatch_authority(
        self,
        bound: BoundOperation,
        request: KernelRequest,
    ) -> tuple[CommandDispatchContext | None, ExecutionToken | None]:
        if not bound.definition.emits_world_command:
            return None, None
        revision = request.observation.world_revision
        command = self.state_store.begin_command(
            plan_id=request.plan.plan_id,
            plan_version=request.plan.plan_version,
            step_id=request.step.step_id,
            action_kind=bound.operation.kind,
            start_revision=revision,
        )
        dispatch = CommandDispatchContext(
            command_id=command.command_id,
            based_on_revision=revision,
            primitive_action_bound=bound.definition.primitive_action_bound_for(
                bound.operation
            ),
            **_authored_recipient_kwargs(bound),
        )
        token = ExecutionToken(
            plan_id=request.plan.plan_id,
            plan_version=request.plan.plan_version,
            step_id=request.step.step_id,
            command_id=command.command_id,
            control_mode=request.plan.control_mode,
            validated_revision=revision,
            latest_observation=self._latest_input_authority,
            max_telemetry_age_seconds=(self.input_boundary_max_telemetry_age_seconds()),
            pointer_class=self.hooks.pointer_class(bound),
            authority_validator=lambda current: self.hooks.authorized(
                bound,
                current,
            ),
            authorized_fingerprint=bound.identity.fingerprint,
            assumptions=tuple(request.plan.assumptions),
            preconditions=tuple(request.step.preconditions),
            failure_conditions=tuple(request.step.failure_conditions),
        )
        return dispatch, token

    def _latest_input_authority(self) -> Observation | None:
        return self.input_boundary_observation() or self.state_store.latest

    def _close_reservations(
        self,
        handled: OperationResult,
        request: KernelRequest,
        action_reservation: ActionBudgetReservation,
        reserved_risk: tuple[int, int, int],
        command: CommandDispatchContext | None,
    ) -> tuple[Observation, Transition | None] | KernelResult:
        transition = handled.transition
        receipt_matches = bool(
            transition is not None
            and (command is None or transition.receipt.command_id in {None, command.command_id})
        )
        definitely_rejected = receipt_matches and (
            handled.status is OperationStatus.REJECTED
            or bool(
                transition is not None
                and not transition.receipt.accepted
                and not transition.receipt.executed
            )
        )
        if definitely_rejected:
            request.budget.release(reserved_risk)
            self.action_budget.release(action_reservation)
            event = "plan_budget_released"
            reason = "The handler definitively rejected the operation without execution."
        else:
            request.budget.commit()
            self.action_budget.commit(action_reservation)
            event = "plan_budget_committed"
            reason = "The handler accepted or may have executed the bound operation."

        latest = handled.observation
        if command is not None:
            if transition is None:
                no_transition = _no_causal_transition_reason(latest, handled.reason)
                self.state_store.fail_active_command(no_transition)
                self._reservation_event(event, request, latest, reason)
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=no_transition,
                )
            receipt = transition.receipt
            if receipt.command_id not in {None, command.command_id}:
                self.state_store.fail_active_command(
                    "Handler acknowledgement command ID did not match active command."
                )
                self._reservation_event(event, request, latest, reason)
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason="Command causality validation failed: mismatched command ID.",
                )
            if receipt.command_id is None:
                receipt = receipt.model_copy(
                    update={
                        "command_id": command.command_id,
                        "started_after_revision": command.based_on_revision,
                        "completed_at_revision": transition.observation.world_revision,
                        "causal_revision_advanced": (
                            transition.observation.world_revision.is_later_than(
                                command.based_on_revision
                            )
                        ),
                    }
                )
                transition = transition.model_copy(update={"receipt": receipt})
            try:
                latest = self.hooks.observe_transition(
                    request.plan,
                    request.step,
                    request.observation,
                    transition,
                    command.command_id,
                    command.based_on_revision,
                )
                self.state_store.complete_command(
                    command.command_id,
                    latest.world_revision,
                )
            except CommandCausalityError as exc:
                self.state_store.fail_active_command(str(exc))
                self._reservation_event(event, request, latest, reason)
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=f"Command causality validation failed: {exc}",
                )
        elif transition is not None:
            latest = self.hooks.observe_transition(
                request.plan,
                request.step,
                request.observation,
                transition,
                None,
                None,
            )
        self._reservation_event(event, request, latest, reason)
        return latest, transition

    def _record_input_boundary(
        self,
        handled: OperationResult,
        request: KernelRequest,
        command: CommandDispatchContext | None,
    ) -> None:
        transition = handled.transition
        if transition is None or transition.receipt.input_boundary is None:
            return
        boundary = transition.receipt.input_boundary
        rejected = boundary.decision is InputBoundaryDecision.REJECTED
        self.hooks.event(
            "input_boundary_rejected" if rejected else "input_boundary_revalidated",
            request.plan,
            request.observation,
            step=request.step,
            reason=boundary.reason,
            evidence={
                "command_id": command.command_id if command is not None else None,
                "decision": boundary.decision.value,
                "lease_wait_seconds": boundary.lease_wait_seconds,
                "validated_revision": request.observation.world_revision.model_dump(mode="json"),
                "boundary_revision": (
                    boundary.boundary_revision.model_dump(mode="json")
                    if boundary.boundary_revision is not None
                    else None
                ),
            },
        )

    async def _await_conditions(
        self,
        bound: BoundOperation,
        request: KernelRequest,
        handled: OperationResult,
        latest: Observation,
        transition: Transition | None,
        owner: TerminalOwner,
        runtime_conditions: tuple[object, ...],
    ) -> KernelResult:
        typed_runtime_conditions = tuple(
            condition for condition in runtime_conditions if isinstance(condition, Condition)
        )
        runtime_paths = {
            condition.path for condition in typed_runtime_conditions if condition.path is not None
        }
        success_conditions = (
            (
                *typed_runtime_conditions,
                *(
                    condition
                    for condition in request.step.success_conditions
                    if condition.path not in runtime_paths
                ),
            )
            if owner is TerminalOwner.RUNTIME_CONDITIONS
            else tuple(request.step.success_conditions)
        )
        deadline = self.clock.monotonic() + request.step.timeout_seconds
        while True:
            success_evaluations = evaluate_conditions(
                list(success_conditions),
                latest,
                after_revision=bound.based_on_revision,
            )
            failure_evaluations = evaluate_conditions(
                request.step.failure_conditions,
                latest,
                after_revision=bound.based_on_revision,
            )
            self.hooks.event(
                "plan_step_progress",
                request.plan,
                latest,
                step=request.step,
                reason="Evaluated typed postconditions on the latest revision.",
                evidence={
                    "completion_owner": owner.value,
                    "success_conditions": _evaluations_json(success_evaluations),
                    "failure_conditions": _evaluations_json(failure_evaluations),
                },
            )
            failed = next(
                (
                    evaluation
                    for evaluation in failure_evaluations
                    if evaluation.result is ConditionResult.TRUE
                ),
                None,
            )
            if failed is not None:
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=f"A typed failure condition became true: {failed.reason}",
                    terminated=handled.terminated,
                    success=handled.success,
                )
            if success_evaluations and all(
                evaluation.result is ConditionResult.TRUE for evaluation in success_evaluations
            ):
                return KernelResult(
                    observation=latest,
                    succeeded=True,
                    actions_completed=1,
                    reason="All success conditions are true on a later world revision.",
                    terminated=handled.terminated,
                    success=handled.success,
                    staged_patch=handled.staged_patch,
                )
            if handled.terminated:
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason="The environment terminated before success was verified.",
                    terminated=True,
                    success=handled.success,
                )
            if reason := self._budget_stop_reason(request, latest):
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=reason,
                )
            if (
                request.step.observation_policy is ObservationPolicy.AFTER_ACTION
                or self.clock.monotonic() >= deadline
            ):
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=_unmet_postcondition_reason(
                        success_evaluations,
                        step_deadline_seconds=request.step.timeout_seconds,
                    ),
                )
            try:
                remaining_step = deadline - self.clock.monotonic()
                remaining_plan = request.plan.max_wall_seconds - (
                    self.clock.monotonic() - request.plan_started_at
                )
                latest = await self.state_store.wait_for(
                    lambda _: True,
                    after_revision=latest.world_revision,
                    timeout_seconds=min(remaining_step, remaining_plan),
                )
            except TimeoutError:
                return KernelResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=_unmet_postcondition_reason(
                        success_evaluations,
                        step_deadline_seconds=request.step.timeout_seconds,
                    ),
                )

    def _budget_stop_reason(
        self,
        request: KernelRequest,
        observation: Observation,
    ) -> str | None:
        if self.clock.monotonic() - request.plan_started_at >= request.plan.max_wall_seconds:
            return "Plan wall-clock budget is exhausted."
        elapsed = game_elapsed_seconds(request.plan_started_observation, observation)
        if elapsed is not None and elapsed >= request.plan.max_game_seconds:
            return "Plan in-game time budget is exhausted."
        if request.remaining_run_actions <= 1:
            return "Run action budget is exhausted."
        return None

    def _progress(
        self,
        progress: OperationProgress,
        observation: Observation,
        request: KernelRequest,
    ) -> None:
        self.hooks.event(
            progress.event_type,
            request.plan,
            observation,
            step=request.step,
            reason=progress.reason,
            evidence=progress.evidence,
        )

    def _reservation_event(
        self,
        event_type: str,
        request: KernelRequest,
        observation: Observation,
        reason: str,
    ) -> None:
        self.hooks.event(
            event_type,
            request.plan,
            observation,
            step=request.step,
            reason=reason,
        )


def _evaluations_json(
    evaluations: list[ConditionEvaluation],
) -> list[dict[str, object]]:
    return [evaluation.model_dump(mode="json") for evaluation in evaluations]


def _unmet_postcondition_reason(
    evaluations: list[ConditionEvaluation],
    *,
    step_deadline_seconds: float,
) -> str:
    stale = [evaluation for evaluation in evaluations if evaluation.result is ConditionResult.STALE]
    if stale:
        return (
            f"No causally later world revision arrived within {step_deadline_seconds:.1f}s; "
            + "; ".join(evaluation.reason for evaluation in stale[:3])
        )
    unmet = [
        evaluation for evaluation in evaluations if evaluation.result is not ConditionResult.TRUE
    ]
    if not unmet:
        return f"Step ran out of its {step_deadline_seconds:.1f}s budget."
    return (
        "The operation completed but did not have its intended effect within "
        f"{step_deadline_seconds:.1f}s: " + "; ".join(evaluation.reason for evaluation in unmet[:3])
    )
