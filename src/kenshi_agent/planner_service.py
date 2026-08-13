"""One lifecycle for every strategic planner call.

Assembling the payload, issuing the call, recording what the call carried and
what came back, and turning a bad response into something the run can act on all
belong together: they are the same boundary seen at four moments. Keeping them
here means a caller asks for a decision and receives either an authored output
or a typed refusal, without knowing how the planner is reached.

A planner failure is a bad answer, not a broken run. This produces a decision
that stops cleanly and carries enough detail to diagnose why.
"""

from __future__ import annotations

from typing import Any

from .continuity_service import ContinuityService
from .core.observation import Observation
from .core.operation import StopAction
from .core.planner_context import AuthoredPlannerOutput
from .core.planning import PlannerDecision
from .planners import Planner
from .planners.base import (
    HostedPlannerCallDiagnostics,
    HostedPlannerResponseError,
)
from .session_log import SessionLogger

# A failed planner response is diagnostic evidence, so the log keeps far more of
# it than the rationale a stop decision carries into the run's own record.
PLANNER_ERROR_LOG_MAX_CHARS = 8_000
PLANNER_ERROR_RATIONALE_MAX_CHARS = 1_500


def bounded_text(value: str, max_chars: int) -> str:
    """Trim to a budget while saying plainly that trimming happened."""

    suffix = " ... [truncated]"
    if len(value) <= max_chars:
        return value
    return value[: max_chars - len(suffix)] + suffix


class PlannerService:
    """Prepare, issue, and record one strategic planner call."""

    def __init__(
        self,
        *,
        planner: Planner,
        logger: SessionLogger,
        continuity: ContinuityService,
        control_mode_value: str,
    ) -> None:
        self._planner = planner
        self._logger = logger
        self._continuity = continuity
        self._control_mode_value = control_mode_value
        self._contexts_issued = 0

    @property
    def contexts_issued(self) -> int:
        return self._contexts_issued

    def reset(self) -> None:
        self._contexts_issued = 0

    async def decide(self, observation: Observation) -> AuthoredPlannerOutput:
        """Assemble a planner payload, marking exactly what it carried.

        Delivery is recorded here and only here: this is the one place a memory
        actually reaches a planner.

        A requested read is consumed here too. It answers exactly the planner
        call that asked for it, and is not left lying around to be re-read as if
        it were fresh.
        """

        self._contexts_issued += 1
        prepared = self._planner.prepare_input(
            observation,
            context_id=f"pc-{self._contexts_issued}",
        )
        manifest = prepared.context.manifest
        self._logger.write(
            "planner_context_prepared",
            step_index=observation.step_index,
            payload=manifest.model_dump(mode="json"),
        )
        self._continuity.record_delivery(
            memory_ids=manifest.memory_ids,
            observation=observation,
        )
        self._continuity.clear_pending_reads()
        # This is the last durable boundary before the planner receives the
        # prepared context.  The payload is the same immutable enumeration that
        # built the planner input, not a prompt parse or a second source walk.
        self._logger.write(
            "affordance_set",
            step_index=observation.step_index,
            payload=prepared.affordance_set.model_dump(mode="json"),
        )
        try:
            output = await self._planner.decide_prepared(prepared)
        except Exception:
            self._record_transport(observation, structured_output_accepted=False)
            raise
        self._record_transport(observation, structured_output_accepted=True)
        return AuthoredPlannerOutput(output=output, context=prepared.context)

    def _record_transport(
        self,
        observation: Observation,
        *,
        structured_output_accepted: bool,
    ) -> None:
        diagnostics: HostedPlannerCallDiagnostics | None = self._planner.take_call_diagnostics()
        if diagnostics is None:
            return
        self._logger.write(
            "planner_transport",
            step_index=observation.step_index,
            payload={
                **diagnostics.event_payload(),
                "structured_output_accepted": structured_output_accepted,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
            },
        )

    @staticmethod
    def failure_signature(exc: Exception) -> str:
        """Identify repeats of the same failure, so a loop can be broken."""

        # Duck-typed: any planner failure that can name its own repeat signature
        # owns that answer. Keying on one concrete class meant a new failure kind
        # silently fell back to its own message text, so two occurrences with
        # different details never counted as the same failure and the loop
        # breaker never fired.
        signature = getattr(exc, "failure_signature", None)
        if isinstance(signature, str) and signature:
            return signature
        return str(exc)

    def retry_feedback(self, exc: Exception) -> str:
        """What to tell the planner so its next attempt can be different."""

        feedback = getattr(exc, "retry_feedback", None)
        if isinstance(feedback, str) and feedback:
            return bounded_text(feedback, 1_200)
        return (
            "Your previous response could not be used. Fix exactly this and "
            "return the schema again: " + bounded_text(str(exc), 900)
        )

    def failure_decision(self, exc: Exception, *, step_index: int) -> PlannerDecision:
        """Turn an unusable planner response into a clean stop with evidence."""

        message = f"Planner raised {type(exc).__name__}: {exc}"
        payload: dict[str, Any] = {
            "control_mode": self._control_mode_value,
            "error_type": type(exc).__name__,
            "message": bounded_text(message, PLANNER_ERROR_LOG_MAX_CHARS),
            "message_characters": len(message),
            "message_truncated": len(message) > PLANNER_ERROR_LOG_MAX_CHARS,
        }
        if isinstance(exc, HostedPlannerResponseError):
            payload.update(
                {
                    "failure_category": exc.category,
                    "failure_detail": exc.detail,
                    "response_excerpt": exc.response_excerpt,
                    "failure_signature": exc.failure_signature,
                    "finish_reason": exc.diagnostics.finish_reason,
                }
            )
        self._logger.write("planner_error", step_index=step_index, payload=payload)
        return PlannerDecision(
            intent="Stop after planner failure.",
            rationale=bounded_text(message, PLANNER_ERROR_RATIONALE_MAX_CHARS),
            action=StopAction(reason="Planner failure."),
            confidence=1.0,
        )
