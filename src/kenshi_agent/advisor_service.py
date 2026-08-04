"""Read-only strategic advice, requested without stalling foreground play.

An advisor call reaches a provider and can take seconds. Play does not wait for
it: the request is queued, the run keeps acting, and the brief is published when
it arrives. Exactly one request may be in flight, so a planner that asks twice
gets told a request is already pending rather than launching a duplicate.

This owns cadence, single-flight, and delivery. It never gains operation or
environment authority: nothing here decides what the run may do, only what it
has been told.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .advisor import AdvisorSession, advisor_state_fingerprint, disabled_advisor_availability
from .models import (
    ActionReceipt,
    AdvisorAvailability,
    AdvisorConsultEvidence,
    AdvisorConsultStatus,
    ConsultAdvisorAction,
    ControlMode,
    Observation,
)
from .planner_service import bounded_text
from .reporting import ConsoleDecisionReporter
from .session_log import SessionLogger


@dataclass(frozen=True, slots=True)
class AdvisorActionResult:
    """What one consultation returns: fresh context, and the receipt for it."""

    observation: Observation
    receipt: ActionReceipt


class AdvisorService:
    """Queue, complete, and publish one read-only strategic consultation."""

    def __init__(
        self,
        *,
        advisor: AdvisorSession | None,
        logger: SessionLogger,
        reporter: ConsoleDecisionReporter | None,
        control_mode: ControlMode,
        run_id: str,
        refresh_context: Callable[[Observation], Observation],
    ) -> None:
        self._advisor = advisor
        self._logger = logger
        self._reporter = reporter
        self._control_mode = control_mode
        self._run_id = run_id
        self._refresh_context = refresh_context
        self._task: asyncio.Task[None] | None = None
        self._result_ready = False
        self._brief_ids: set[str] = set()

    @property
    def brief_ids(self) -> set[str]:
        """Briefs this run actually issued, and may therefore cite as advice."""

        return self._brief_ids

    def reset(self) -> None:
        self._brief_ids.clear()
        self._result_ready = False

    def availability(self, observation: Observation) -> AdvisorAvailability:
        """Whether a brief may be requested right now, reservation included.

        create_task schedules the provider coroutine for the next event-loop
        turn. Expose the reservation immediately so a faster planner cannot
        launch a duplicate in that small gap.
        """

        availability = (
            self._advisor.availability(observation)
            if self._advisor is not None
            else disabled_advisor_availability()
        )
        if (
            self._task is not None
            and not self._task.done()
            and not self._result_ready
            and not availability.request_pending
        ):
            availability = availability.model_copy(
                update={
                    "may_request": False,
                    "suggested": False,
                    "request_pending": True,
                    "reason": (
                        "An advisor request is already pending; keep playing and "
                        "use the brief after it arrives."
                    ),
                }
            )
        return availability

    async def consult(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> AdvisorActionResult:
        """Queue a cognitive request without holding up foreground play."""

        if self._advisor is None:
            return self._finish_immediately(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=AdvisorConsultEvidence(
                    status=AdvisorConsultStatus.DISABLED,
                    reason="The strategic advisor is disabled for this run.",
                    calls_used=0,
                    max_calls=0,
                    state_fingerprint=advisor_state_fingerprint(observation),
                ),
            )

        self.reap_finished()
        if self._task is not None and not self._task.done():
            return self._finish_immediately(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=AdvisorConsultEvidence(
                    status=AdvisorConsultStatus.PENDING,
                    reason=(
                        "An advisor request is already pending; the duplicate "
                        "request was not launched."
                    ),
                    calls_used=self._advisor.calls_used,
                    max_calls=self._advisor.config.max_calls_per_run,
                    state_fingerprint=advisor_state_fingerprint(observation),
                ),
            )

        if not self._advisor.availability(observation).may_request:
            # Suppression paths do not reach a provider and complete immediately.
            return self._finish_immediately(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=await self._advisor.consult(action, observation),
            )

        started_at = datetime.now(UTC)
        evidence = AdvisorConsultEvidence(
            status=AdvisorConsultStatus.PENDING,
            reason=(
                "The advisor request was queued in the background; foreground "
                "play may continue while it is thinking."
            ),
            calls_used=min(
                self._advisor.calls_used + 1,
                self._advisor.config.max_calls_per_run,
            ),
            max_calls=self._advisor.config.max_calls_per_run,
            state_fingerprint=advisor_state_fingerprint(observation),
        )
        receipt = ActionReceipt(
            action=action,
            control_mode=self._control_mode,
            advisor=evidence,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            primitive_actions=0,
            message=evidence.reason,
            error_type=None,
        )
        self._logger.write("action_receipt", step_index=observation.step_index, payload=receipt)
        self._logger.write(
            "advisor_request_queued",
            step_index=observation.step_index,
            payload=self._event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        if self._reporter is not None:
            self._reporter.action_receipt(step_index=observation.step_index, receipt=receipt)
        self._task = asyncio.create_task(
            self._complete(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
            ),
            name=f"advisor-{self._run_id}-{plan_id}-{step_id}",
        )
        self._result_ready = False
        return AdvisorActionResult(
            observation=self._refresh_context(observation),
            receipt=receipt,
        )

    async def _complete(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> None:
        """Finish one single-flight provider call and publish only its advice."""

        assert self._advisor is not None
        try:
            evidence = await self._advisor.consult(action, observation)
        except asyncio.CancelledError:
            self._logger.write(
                "advisor_cancelled",
                step_index=observation.step_index,
                payload={
                    "plan_id": plan_id,
                    "plan_version": plan_version,
                    "step_id": step_id,
                    "world_revision": observation.world_revision.model_dump(mode="json"),
                    "controller_primitives": 0,
                    "world_command_created": False,
                    "reason": "The run ended while the read-only advisory was pending.",
                },
            )
            raise
        except TimeoutError:
            evidence = AdvisorConsultEvidence(
                status=AdvisorConsultStatus.FAILED,
                reason=(
                    "Advisor call exceeded its configured provider timeout of "
                    f"{self._advisor.config.timeout_seconds:.2f} seconds."
                ),
                calls_used=self._advisor.calls_used,
                max_calls=self._advisor.config.max_calls_per_run,
                state_fingerprint=advisor_state_fingerprint(observation),
            )
        self._result_ready = True
        if evidence.brief is not None:
            # Only a brief this run actually issued may later be cited as the
            # source of a memory, and only ever as advice.
            self._brief_ids.add(evidence.brief.brief_id)
        self._logger.write(
            "advisor_result",
            step_index=observation.step_index,
            payload=self._event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        self._refresh_context(observation)

    def _finish_immediately(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
        evidence: AdvisorConsultEvidence,
    ) -> AdvisorActionResult:
        answered = evidence.status is AdvisorConsultStatus.ANSWERED
        attempted = evidence.status in {
            AdvisorConsultStatus.ANSWERED,
            AdvisorConsultStatus.FAILED,
        }
        now = datetime.now(UTC)
        receipt = ActionReceipt(
            action=action,
            control_mode=self._control_mode,
            advisor=evidence,
            accepted=answered,
            executed=attempted,
            dry_run=False,
            started_at=now,
            finished_at=now,
            primitive_actions=0,
            message=evidence.reason,
            error_type=None if answered else evidence.status.value,
        )
        self._logger.write("action_receipt", step_index=observation.step_index, payload=receipt)
        self._logger.write(
            "advisor_result",
            step_index=observation.step_index,
            payload=self._event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        if self._reporter is not None:
            self._reporter.action_receipt(step_index=observation.step_index, receipt=receipt)
        return AdvisorActionResult(
            observation=self._refresh_context(observation),
            receipt=receipt,
        )

    def reap_finished(self) -> None:
        """Clear a completed task so the next request may launch."""

        task = self._task
        if task is None or not task.done():
            return
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_task_failure(task, exc)
        finally:
            self._task = None
            self._result_ready = False

    async def finish(self) -> None:
        """Cancel and await any pending request as the run ends."""

        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_task_failure(task, exc)
        finally:
            if self._task is task:
                self._task = None
            self._result_ready = False

    def _log_task_failure(self, task: asyncio.Task[None], exc: Exception) -> None:
        self._logger.write(
            "advisor_task_failed",
            payload={
                "task": task.get_name(),
                "error_type": type(exc).__name__,
                "reason": bounded_text(str(exc), 1_000),
            },
        )

    @staticmethod
    def _event_payload(
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
        evidence: AdvisorConsultEvidence,
    ) -> dict[str, object]:
        return {
            "plan_id": plan_id,
            "plan_version": plan_version,
            "step_id": step_id,
            "world_revision": observation.world_revision.model_dump(mode="json"),
            "controller_primitives": 0,
            "world_command_created": False,
            "evidence": evidence.model_dump(mode="json"),
        }
