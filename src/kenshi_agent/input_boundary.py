"""Final revalidation at the real input boundary.

The continuous executor validates plan assumptions and step preconditions
immediately before `environment.dispatch()`. A live dispatch may then wait an
unbounded polite interval for a quiet input turn, so the evidence that
authorized the action can be obsolete by the time the first primitive would be
emitted. `ExecutionToken` carries that authorization into the environment and
re-checks it after the lease is acquired, using the same typed condition
machinery rather than a parallel ad hoc boolean path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .control.calibration import calibration_allows_input
from .models import (
    CalibrationReport,
    Condition,
    ConditionEvaluation,
    ConditionResult,
    ControlMode,
    InputBoundaryDecision,
    InputBoundaryReport,
    Observation,
    WorldStateRevision,
)
from .planning import evaluate_conditions
from .terminal_state import TERMINAL_WINDOW_EVENT_PREFIX

_MAX_REPORTED_EVALUATIONS = 24

# Observation events that independently withdraw input authority. These are the
# same deterministic signals the reflex and supervisor paths latch on; the
# boundary re-checks them because the lease wait is exactly when a human is
# most likely to have taken the keyboard.
_BLOCKING_EVENTS = ("human_input_detected", "emergency_stop_detected")


@dataclass(frozen=True, slots=True)
class _ExecutionTokenState:
    plan_id: str
    plan_version: int
    step_id: str
    command_id: str
    control_mode: ControlMode
    validated_revision: WorldStateRevision
    latest_observation: Callable[[], Observation | None]
    max_telemetry_age_seconds: float | None
    authority_validator: Callable[[Observation], str | None] | None = None
    assumptions: tuple[Condition, ...] = ()
    preconditions: tuple[Condition, ...] = ()
    failure_conditions: tuple[Condition, ...] = ()
    _reports: list[InputBoundaryReport] = field(default_factory=list, compare=False)


class ExecutionToken(_ExecutionTokenState):
    """Bounded authorization carried from executor validation into dispatch."""

    __slots__ = ()

    def _get_reports(self) -> tuple[InputBoundaryReport, ...]:
        """Every boundary decision this token has produced, in order."""

        return tuple(self._reports)

    reports = property(_get_reports)

    def revalidate(
        self,
        *,
        lease_wait_seconds: float = 0.0,
        calibration: CalibrationReport | None = None,
    ) -> InputBoundaryReport:
        report = self._decide(
            lease_wait_seconds=lease_wait_seconds,
            calibration=calibration,
        )
        self._reports.append(report)
        return report

    def _reject(
        self,
        reason: str,
        *,
        lease_wait_seconds: float,
        boundary_revision: WorldStateRevision | None = None,
        evaluations: list[ConditionEvaluation] | None = None,
    ) -> InputBoundaryReport:
        return self._report(
            InputBoundaryDecision.REJECTED,
            reason,
            lease_wait_seconds=lease_wait_seconds,
            boundary_revision=boundary_revision,
            evaluations=evaluations,
        )

    def _report(
        self,
        decision: InputBoundaryDecision,
        reason: str,
        *,
        lease_wait_seconds: float,
        boundary_revision: WorldStateRevision | None = None,
        evaluations: list[ConditionEvaluation] | None = None,
    ) -> InputBoundaryReport:
        return InputBoundaryReport(
            decision=decision,
            reason=reason,
            lease_wait_seconds=lease_wait_seconds,
            plan_id=self.plan_id,
            plan_version=self.plan_version,
            step_id=self.step_id,
            validated_revision=self.validated_revision,
            boundary_revision=boundary_revision,
            evaluations=(evaluations or [])[:_MAX_REPORTED_EVALUATIONS],
        )

    def _decide(
        self,
        *,
        lease_wait_seconds: float,
        calibration: CalibrationReport | None = None,
    ) -> InputBoundaryReport:
        # Calibration is resolved inside the lease by the caller. A profile that
        # drifted during the wait must block here even when every typed
        # condition still holds, because the coordinates no longer mean anything.
        if calibration is not None and not calibration_allows_input(calibration):
            return self._reject(
                "Calibration identity is no longer usable "  # mutation: diagnostic-only
                "at the input boundary "  # mutation: diagnostic-only
                f"({calibration.status.value}): {calibration.reason}",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
            )

        observation = self.latest_observation()
        if observation is None:
            return self._reject(
                "No canonical observation is available "  # mutation: diagnostic-only
                "at the input boundary, so "  # mutation: diagnostic-only
                "current state cannot be proven.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
            )

        boundary_revision = observation.world_revision
        if observation.telemetry_stale:
            return self._reject(
                "The canonical telemetry is stale "  # mutation: diagnostic-only
                "at the input boundary.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )
        if self.max_telemetry_age_seconds is None:
            return self._reject(
                "The telemetry age ceiling is unknown "  # mutation: diagnostic-only
                "at the input boundary, so "  # mutation: diagnostic-only
                "fresh authority cannot be proven.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )
        if observation.telemetry_age_seconds is None:
            return self._reject(
                "The canonical telemetry age is unknown "  # mutation: diagnostic-only
                "at the input boundary, so "  # mutation: diagnostic-only
                "fresh authority cannot be proven.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )
        if observation.telemetry_age_seconds > self.max_telemetry_age_seconds:
            return self._reject(
                "The canonical telemetry age at the input boundary "  # mutation: diagnostic-only
                f"({observation.telemetry_age_seconds:.3f}s) "  # mutation: diagnostic-only
                "exceeds the configured "  # mutation: diagnostic-only
                f"maximum ({self.max_telemetry_age_seconds:.3f}s).",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )
        if self.validated_revision.is_later_than(boundary_revision):
            return self._reject(
                "The canonical revision regressed "  # mutation: diagnostic-only
                "while the input lease was pending.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )

        if observation.control_mode != self.control_mode:
            return self._reject(
                "Control mode changed from "  # mutation: diagnostic-only
                f"{self.control_mode.value!r} "  # mutation: diagnostic-only
                f"to {observation.control_mode.value!r} "  # mutation: diagnostic-only
                "while the input lease was pending.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )

        blocking = [
            event
            for event in observation.events
            if event in _BLOCKING_EVENTS
            or event.startswith(TERMINAL_WINDOW_EVENT_PREFIX)
        ]
        if blocking:
            return self._reject(
                "Input authority was withdrawn at the boundary "  # mutation: diagnostic-only
                f"by {blocking[0]!r}.",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
            )

        if self.authority_validator is not None:
            authority_error = self.authority_validator(observation)
            if authority_error is not None:
                return self._reject(
                    "The action no longer passes its safety "  # mutation: diagnostic-only
                    "and reference checks "  # mutation: diagnostic-only
                    f"at the input boundary: {authority_error}",  # mutation: diagnostic-only
                    lease_wait_seconds=lease_wait_seconds,
                    boundary_revision=boundary_revision,
                )

        evaluations = evaluate_conditions(
            [*self.assumptions, *self.preconditions],
            observation,
        )
        blocked = next(
            (
                evaluation
                for evaluation in evaluations
                if evaluation.result != ConditionResult.TRUE
            ),
            None,
        )
        if blocked is not None:
            return self._reject(
                "A plan assumption or step precondition "  # mutation: diagnostic-only
                "is no longer true at the input boundary: "  # mutation: diagnostic-only
                f"{blocked.result.value}: {blocked.reason}",  # mutation: diagnostic-only
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
                evaluations=evaluations,
            )

        failure_evaluations = evaluate_conditions(
            list(self.failure_conditions),
            observation,
        )
        active_failure = next(
            (
                evaluation
                for evaluation in failure_evaluations
                if evaluation.result is not ConditionResult.FALSE
            ),
            None,
        )
        if active_failure is not None:
            reason = (
                "A step failure condition became true at the input boundary: "
                f"{active_failure.reason}"
                if active_failure.result is ConditionResult.TRUE
                else (
                    "A step failure condition is not observable at the input "
                    f"boundary: {active_failure.reason}"
                )
            )
            return self._reject(
                reason,
                lease_wait_seconds=lease_wait_seconds,
                boundary_revision=boundary_revision,
                evaluations=[*evaluations, *failure_evaluations],
            )

        return self._report(
            InputBoundaryDecision.REVALIDATED,
            "Assumptions, preconditions, failure conditions, control mode, "
            "and input authority still hold on the latest canonical revision "
            "inside the input lease.",
            lease_wait_seconds=lease_wait_seconds,
            boundary_revision=boundary_revision,
            evaluations=[*evaluations, *failure_evaluations],
        )
