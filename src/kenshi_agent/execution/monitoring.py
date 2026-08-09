"""Reusable monitoring lifecycle invoked by operation-specific handlers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Protocol

from ..core.lifecycle import (
    LifecycleOutcome,
    MonitorDisposition,
    OrderDisposition,
    order_disposition_from_evidence,
)
from ..core.observation import Observation
from ..core.planning import (
    PlanEnvelope,
    PlanStep,
)
from ..core.transport import CommandDispatchContext
from ..future_planning import FuturePlanningPolicy, FuturePlanningSession
from ..input_boundary import ExecutionToken
from ..options import OptionStatus
from ..planning import PlanningClock
from ..world_state import WorldStateStore
from .monitor_types import (
    MonitoredOperation,
    MonitoredOperationResult,
    MonitorFinalizer,
    MonitorScope,
    StagedPatch,
)


def _order_disposition_now(
    observation: Observation | None,
    *,
    issued: bool,
) -> tuple[OrderDisposition, int | None]:
    """Read the order's fate from the freshest evidence, and say how fresh.

    Kept beside the monitor because this is the only place that knows both that
    Python stopped watching and what the last observation showed. Everything it
    concludes is qualified by the sequence it read.
    """

    telemetry = None if observation is None else observation.telemetry
    fresh = bool(
        telemetry is not None
        and observation is not None
        and not observation.telemetry_stale
    )
    observed_unattributed_ordinary_work = False
    if telemetry is not None:
        for character in telemetry.roster:
            work = character.work
            if work is None:
                continue
            # Controller world commands currently enter Kenshi through the
            # ordinary order path. Jobs, permanent Jobs, and current activity
            # are independent evidence and cannot prove that order was retained.
            if (
                work.has_player_orders
                or bool(work.ordinary_orders.items)
                or (
                    work.ordinary_orders.known_total is not None
                    and work.ordinary_orders.known_total > 0
                )
            ):
                observed_unattributed_ordinary_work = True
    disposition = order_disposition_from_evidence(
        issued=issued,
        telemetry_fresh=fresh,
        causally_retained=None,
        observed_unattributed_ordinary_work=observed_unattributed_ordinary_work,
    )
    sequence = telemetry.sequence if fresh and telemetry is not None else None
    return disposition, sequence


class MonitorEventReporter(Protocol):
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


class OperationMonitor:
    """Monitor one handler-owned option without knowing its operation type."""

    def __init__(
        self,
        *,
        scope: MonitorScope,
        future_planning: FuturePlanningPolicy | None,
        clock: PlanningClock,
        state_store: WorldStateStore,
        event: MonitorEventReporter,
    ) -> None:
        self.scope = scope
        self.future_planning = future_planning
        self.clock = clock
        self.state_store = state_store
        self.event = event

    async def run(
        self,
        option: MonitoredOperation,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
        allow_concurrent_planning: bool,
        finalize: MonitorFinalizer | None = None,
        observation: Observation | None = None,
    ) -> MonitoredOperationResult:
        option_task = option.start(command, token=token)
        scope = self.scope
        start_observation = observation or scope.observation
        self.event(
            "option_started",
            scope.plan,
            start_observation,
            step=scope.step,
            reason=option.reason,
            evidence={
                "option_id": option.option_id,
                "option_status": option.poll().status.value,
            },
        )
        subscription = self.state_store.subscribe()
        update_task: asyncio.Task[Any] | None = asyncio.create_task(subscription.get())
        planning_session: FuturePlanningSession | None = (
            self.future_planning.begin(
                scope,
                start_observation,
                option_id=option.option_id,
                enabled=allow_concurrent_planning,
            )
            if self.future_planning is not None
            else None
        )
        staged_patch: StagedPatch | None = None
        timed_out = False
        interrupted = False

        try:
            while option.poll().status is OptionStatus.RUNNING:
                remaining = scope.deadline - self.clock.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                waiting: set[asyncio.Task[Any]] = set()
                if update_task is not None:
                    waiting.add(update_task)
                if not option_task.done():
                    waiting.add(option_task)
                if planning_session is not None:
                    waiting.add(planning_session.task)
                if not waiting:
                    timed_out = True
                    break
                done, _ = await asyncio.wait(
                    waiting,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    timed_out = True
                    break

                if planning_session is not None and planning_session.task in done:
                    assert self.future_planning is not None
                    staged_patch = self.future_planning.consume(planning_session, scope)
                    planning_session = None

                if update_task is not None and update_task in done:
                    update = update_task.result()
                    progress = option.poll(update)
                    self.event(
                        "option_progress",
                        scope.plan,
                        update.observation,
                        step=scope.step,
                        reason=progress.reason,
                        evidence={
                            "option_id": option.option_id,
                            "option_status": progress.status.value,
                            "sequence_status": update.sequence_status.value,
                            "changed_paths": list(update.delta.changed_paths),
                        },
                    )
                    update_task = (
                        asyncio.create_task(subscription.get())
                        if progress.status is OptionStatus.RUNNING
                        else None
                    )
                elif option_task in done:
                    option.poll()

                if (
                    staged_patch is not None
                    and staged_patch.interrupts_active_step
                    and option.transition is not None
                    and option.poll().status is OptionStatus.RUNNING
                ):
                    interrupted = True
                    terminal = await option.cancel(
                        "The exact active option accepted a revision-bound strategic interruption."
                    )
                    self.event(
                        "option_interrupted",
                        scope.plan,
                        self.state_store.latest or scope.observation,
                        step=scope.step,
                        reason=terminal.reason,
                        evidence={
                            "option_id": option.option_id,
                            "option_status": terminal.status.value,
                            "interrupt_active_step_id": (
                                staged_patch.patch.interrupt_active_step_id
                            ),
                        },
                    )
                    break

            if timed_out:
                await option.cancel(
                    "The monitored option exceeded its step timeout before terminal success."
                )
            if finalize is not None:
                await finalize()

            terminal = option.poll()
            latest = self.state_store.latest or start_observation
            if terminal.status is OptionStatus.SUCCEEDED:
                event_type = "option_succeeded"
                reason = terminal.reason
            elif interrupted:
                event_type = "option_interrupted"
                reason = terminal.reason
            else:
                event_type = "option_failed"
                reason = (
                    "The monitored option timed out before terminal success."
                    if timed_out
                    else terminal.reason
                )
            if not interrupted or event_type != "option_interrupted":
                self.event(
                    event_type,
                    scope.plan,
                    latest,
                    step=scope.step,
                    reason=reason,
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            transition = (
                option.transition.model_copy(deep=True)
                if option.transition is not None
                else option.result()
            )
            # Two answers, from two different pieces of evidence. The monitor's
            # is known exactly; the order's is read from the last observation
            # and carries the sequence it was read at.
            if terminal.status is OptionStatus.SUCCEEDED:
                monitor_disposition = MonitorDisposition.OBSERVED_TERMINAL
            elif timed_out:
                monitor_disposition = MonitorDisposition.DETACHED_AFTER_TIMEOUT
            elif interrupted:
                monitor_disposition = MonitorDisposition.DETACHED_FOR_REPLAN
            elif latest is not None and latest.telemetry_stale:
                monitor_disposition = MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS
            else:
                monitor_disposition = MonitorDisposition.OBSERVED_TERMINAL
            order_disposition, at_sequence = _order_disposition_now(
                latest,
                issued=bool(transition.receipt.executed or transition.receipt.accepted),
            )
            lifecycle = LifecycleOutcome(
                monitor=monitor_disposition,
                order=order_disposition,
                detail=reason,
                observed_at_sequence=at_sequence,
            )
            # Append-only. The receipt for this operation is already written and
            # says what was true when the operation terminated; what Kenshi did
            # with the order afterwards is a later fact about the same command,
            # not a correction to that receipt. Editing the receipt would make
            # the record of the terminal depend on how long anyone kept
            # watching afterwards.
            self.event(
                "order_disposition_observed",
                scope.plan,
                latest,
                step=scope.step,
                reason=lifecycle.describe(),
                evidence={
                    "option_id": option.option_id,
                    "command_id": transition.receipt.command_id,
                    "monitor_disposition": lifecycle.monitor.value,
                    "order_disposition": lifecycle.order.value,
                    "observed_at_sequence": lifecycle.observed_at_sequence,
                    "evidence_semantics_version": lifecycle.evidence_semantics_version,
                    "order_may_still_be_running": lifecycle.order.order_may_still_be_running,
                },
            )
            return MonitoredOperationResult(
                transition=transition,
                terminal=terminal,
                staged_patch=staged_patch,
                interrupted=interrupted,
                lifecycle=lifecycle,
            )
        except asyncio.CancelledError:
            # Detaching, not cancelling the order. `option.cancel` cancels an
            # asyncio task; it sends nothing to Kenshi, so the character keeps
            # whatever it was told to do.
            cancelled = await option.cancel(
                "Independent safety supervision detached the monitor. No order "
                "was sent to Kenshi, so any order the character holds remains."
            )
            self.event(
                (
                    "option_cancelled"
                    if cancelled.status is OptionStatus.CANCELLED
                    else "option_failed"
                ),
                scope.plan,
                self.state_store.latest or start_observation,
                step=scope.step,
                reason=cancelled.reason,
                evidence={
                    "option_id": option.option_id,
                    "option_status": cancelled.status.value,
                },
            )
            raise
        finally:
            subscription.close()
            if update_task is not None and not update_task.done():
                update_task.cancel()
                with suppress(asyncio.CancelledError):
                    await update_task
            if planning_session is not None:
                assert self.future_planning is not None
                await self.future_planning.discard(planning_session, scope, option)
