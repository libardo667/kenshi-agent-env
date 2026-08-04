"""Plan-local event recording shared by traversal and operation execution."""

from __future__ import annotations

from typing import Any, Protocol

from .models import Observation, PlanEnvelope, PlanStep
from .session_log import SessionLogger


class PlanFailureReporter(Protocol):
    def __call__(
        self,
        *,
        event_type: str,
        step_index: int,
        plan_id: str,
        plan_version: int,
        step_id: str | None,
        reason: str,
    ) -> None: ...


class PlanEventReporter(Protocol):
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


class PlanEventRecorder:
    """Write one plan event shape and route its bounded failure summary."""

    def __init__(
        self,
        logger: SessionLogger,
        failure_reporter: PlanFailureReporter | None = None,
    ) -> None:
        self.logger = logger
        self.failure_reporter = failure_reporter

    def __call__(
        self,
        event_type: str,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        step: PlanStep | None = None,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.logger.write(
            event_type,
            step_index=observation.step_index,
            payload={
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "step_id": step.step_id if step is not None else None,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
                "reason": reason,
                "evidence": evidence or {},
            },
        )
        if self.failure_reporter is not None and event_type in {
            "plan_patch_rejected",
            "concurrent_planner_discarded",
            "plan_aborted",
        }:
            self.failure_reporter(
                event_type=event_type,
                step_index=observation.step_index,
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                step_id=step.step_id if step is not None else None,
                reason=reason,
            )
