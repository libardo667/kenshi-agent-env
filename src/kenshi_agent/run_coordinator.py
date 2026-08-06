"""One state machine for a whole run: observe, plan, execute, record, repeat.

The coordinator owns sequencing and nothing else. It decides *when* to observe,
plan, bind, execute, record, hand off, preempt, and finalize; it delegates *what*
each of those means to the services it holds. Single-cycle and continuous play
are one scheduling policy argument over this same machine, not two runtimes.

It contains no operation-family knowledge. Which operation is running is the
operation definition's business and its handler's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol, TypeVar

from .advisor_service import AdvisorService
from .affordances import terminal_affordance_receipt
from .config import PlanningConfig, SafetyConfig
from .continuity import ContinuityLedger
from .continuity_service import ContinuityService
from .continuous_executor import (
    ContinuousPlanExecutor,
)
from .control_ownership import (
    ControlOwnershipEvent,
    ControlOwnershipMachine,
    ControlOwnershipState,
)
from .core.affordance import (
    AffordanceExecution,
    AffordanceLifecycleStatus,
)
from .core.continuity import MemoryRetrievalPolicy
from .core.evidence import PlanDisposition
from .core.lifecycle import (
    EVIDENCE_SEMANTICS_VERSION,
    MonitorDisposition,
    monitor_disposition_for_safety_cause,
)
from .core.observation import Observation
from .core.operation import (
    ControlMode,
    IdempotencyPolicy,
    PauseAction,
    PlanningMode,
    StopAction,
)
from .core.planner_context import AuthoredPlannerContext
from .core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    PlanEnvelope,
    PlannerDecision,
    PlanPatch,
    PlanStep,
    RiskBudget,
)
from .core.scenario import ScenarioAttestation
from .core.telemetry import ScenarioIdentity
from .core.transport import (
    CommandDispatchContext,
    Transition,
)
from .core.world import WorldStateRevision
from .env.base import AgentEnvironment
from .final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
)
from .live_plan_policy import live_plan_rebase_errors, with_covering_risk_budget
from .operation_execution import OperationExecutionFactory
from .outcome_recorder import OutcomeRecorder
from .plan_events import PlanEventRecorder
from .planner_context import PlannerContextAssembler
from .planner_service import (
    PLANNER_ERROR_RATIONALE_MAX_CHARS,
    PlannerService,
    bounded_text,
)
from .planners.base import (
    HostedPlannerResponseError,
)
from .planning import PlanningClock, PlanValidationError, validate_plan
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
from .safety import SafetyViolation
from .safety_supervisor import SafetyCause, SafetyPreemption, SafetySupervisor
from .session_log import SessionLogger
from .world_state import (
    CommandCausalityError,
    ObservationPump,
    StoreUpdate,
    WorldEvent,
    WorldStateClosedError,
    WorldStateError,
    WorldStateStore,
)

_WorkResult = TypeVar("_WorkResult")
SafetyPauseValidator = Callable[[PauseAction, Observation], PauseAction]


class ControlPauseExecutor(Protocol):
    async def __call__(
        self,
        action: PauseAction,
        *,
        command: CommandDispatchContext,
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    control_mode: ControlMode
    steps_completed: int
    terminated: bool
    success: bool | None
    stop_reason: str
    started_at: datetime
    finished_at: datetime
    final_observation: Observation | None


@dataclass(frozen=True, slots=True)
class _RunSession:
    """Everything one run opens and must close, whatever schedules its steps."""

    observation: Observation
    state_store: WorldStateStore
    safety_supervisor: SafetySupervisor | None
    observation_pump: ObservationPump | None



def _retained_work_at_exit(observation: Observation | None) -> dict[str, object]:
    """Orders Kenshi still holds as the run ends, and how current that is."""

    telemetry = None if observation is None else observation.telemetry
    if telemetry is None:
        return {
            "orders_at_exit": None,
            "orders_at_exit_note": (
                "No final observation, so what Kenshi holds is unknown. Nothing "
                "was cleared by ending the run."
            ),
        }
    held = [
        {
            "character": character.name,
            "orders": character.task_state.orders_count,
            "jobs": character.task_state.jobs_count,
            "permajobs": character.task_state.permajobs_count,
            "current_activity": (
                character.task_state.current_activity.task_name
                if character.task_state.current_activity is not None
                else None
            ),
        }
        for character in telemetry.squad
        if character.task_state is not None and character.task_state.has_retained_work
    ]
    stale = observation is not None and observation.telemetry_stale
    return {
        "orders_at_exit": held,
        "orders_at_exit_observed_sequence": None if stale else telemetry.sequence,
        "orders_at_exit_note": (
            "Read from stale telemetry; treat as a last-known state."
            if stale
            else "Ending the run sent no order-clearing input; these remain with "
            "the characters."
        ),
    }


class RunCoordinator:
    """Sequence one run, delegating every responsibility it does not own."""

    _IDENTICAL_REPLAN_FAILURE_LIMIT = 3

    def __init__(
        self,
        *,
        run_id: str,
        environment: AgentEnvironment,
        execute_control_pause: ControlPauseExecutor,
        safety_config: SafetyConfig,
        validate_safety_pause: SafetyPauseValidator,
        reflexes: ReflexEngine,
        logger: SessionLogger,
        control_mode: ControlMode,
        reporter: ConsoleDecisionReporter | None,
        planning_config: PlanningConfig,
        planning_clock: PlanningClock,
        observation_clock: PlanningClock,
        log_full_observations: bool,
        scenario: ScenarioIdentity | None,
        scenario_attestation: ScenarioAttestation | None,
        memory_retrieval_policy: MemoryRetrievalPolicy,
        ledger: ContinuityLedger,
        continuity: ContinuityService,
        planner_service: PlannerService,
        planner_context: PlannerContextAssembler,
        advisor_service: AdvisorService,
        outcomes: OutcomeRecorder,
        operation_execution: OperationExecutionFactory,
        plan_events: PlanEventRecorder,
    ) -> None:
        self.run_id = run_id
        self.environment = environment
        self.execute_control_pause = execute_control_pause
        self.safety_config = safety_config
        self.validate_safety_pause = validate_safety_pause
        self.reflexes = reflexes
        self.logger = logger
        self.control_mode = control_mode
        self.reporter = reporter
        self.planning_config = planning_config
        self.planning_clock = planning_clock
        self.observation_clock = observation_clock
        self.log_full_observations = log_full_observations
        self.scenario = scenario
        self.scenario_attestation = scenario_attestation
        self.memory_retrieval_policy = memory_retrieval_policy
        self._ledger = ledger
        self.continuity = continuity
        self.planner_service = planner_service
        self.planner_context = planner_context
        self.advisor_service = advisor_service
        self.outcomes = outcomes
        self.operation_execution = operation_execution
        self.plan_events = plan_events
        self._state_store: WorldStateStore | None = None
        self.final_safe_state: FinalSafeStateOutcome | None = None

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
        return await self._run_scheduled(
            max_steps=max_steps,
            seed=seed,
            accepts_plans=self.planning_config.mode == PlanningMode.CONTINUOUS,
        )

    def _log_observation(self, observation: Observation) -> None:
        """Record an observation at the configured level of detail."""

        payload: Observation | dict[str, Any] = (
            observation if self.log_full_observations else observation.log_digest()
        )
        self.logger.write(
            "observation",
            step_index=observation.step_index,
            payload=payload,
        )

    async def _begin_run(
        self,
        *,
        max_steps: int,
        seed: int | None,
        supervised: bool,
    ) -> _RunSession:
        """Open one run's session: campaign state, world store, and observers.

        Both scheduling policies open a run identically. Whether an independent
        supervisor and observation pump are attached is the only difference, and
        that is a policy of the schedule, not a different runtime.
        """

        self._ledger.reset()
        self.advisor_service.reset()
        self.continuity.reset()
        observation = self.planner_context.decorate(await self.environment.reset(seed=seed))
        self.logger.write(
            "run_started",
            payload={
                "max_steps": max_steps,
                "seed": seed,
                "control_mode": self.control_mode.value,
                "planning_mode": self.planning_config.mode.value,
                "memory_retrieval_policy": (self.memory_retrieval_policy.value),
                "scenario": (
                    self.scenario.model_dump(mode="json") if self.scenario is not None else None
                ),
                "scenario_attestation": (
                    self.scenario_attestation.model_dump(mode="json")
                    if self.scenario_attestation is not None
                    else None
                ),
            },
        )
        if self.reporter is not None:
            self.reporter.run_started(max_steps)
        state_store = self._new_world_state_store()
        self._state_store = state_store
        initial_update = state_store.publish(observation)
        self._log_world_state_update(initial_update)
        safety_supervisor: SafetySupervisor | None = None
        observation_pump: ObservationPump | None = None
        if supervised:
            if self.safety_config.supervisor_enabled:
                safety_supervisor = self._new_safety_supervisor(state_store)
                await safety_supervisor.start()
            if self.planning_config.observation_pump_enabled:
                observation_pump = ObservationPump(
                    self.environment,
                    state_store,
                    interval_seconds=(self.planning_config.observation_pump_seconds),
                    clock=self.observation_clock,
                    transform=self.planner_context.decorate,
                    on_update=self._log_world_state_update,
                )
                await observation_pump.start()
        return _RunSession(
            observation=initial_update.observation,
            state_store=state_store,
            safety_supervisor=safety_supervisor,
            observation_pump=observation_pump,
        )

    async def _finish_run(self, session: _RunSession | None) -> None:
        """Close one run's session. Runs on every exit path, including failure."""

        if session is not None and session.safety_supervisor is not None:
            await self._finish_safety_supervisor(session.safety_supervisor)
        if session is not None and session.observation_pump is not None:
            await session.observation_pump.stop()
        await self.advisor_service.finish()
        if session is not None:
            session.state_store.shutdown()
            self.logger.write(
                "world_state_finished",
                payload=asdict(session.state_store.metrics),
            )
        self._state_store = None
        await self._close_environment()

    async def _run_scheduled(
        self,
        *,
        max_steps: int,
        seed: int | None = None,
        accepts_plans: bool,
    ) -> RunSummary:
        """Observe, plan, execute, record, repeat, under one scheduling policy.

        `accepts_plans` is the whole difference between the continuous and
        single-step schedules: which planner output shape this run will act on,
        and therefore whether an independent supervisor and observation pump are
        worth attaching. Every other responsibility is shared.
        """

        started = datetime.now(UTC)
        cycles = 0
        steps_completed = 0
        terminated = False
        success: bool | None = None
        stop_reason = "Maximum action count reached."
        observation: Observation | None = None
        consecutive_replans = 0
        planner_feedback: str | None = None
        pending_reflex: PlannerDecision | None = None
        last_replan_failure: str | None = None
        identical_replan_failures = 0
        safety_supervisor: SafetySupervisor | None = None
        state_store: WorldStateStore | None = None

        def identical_failure_limit_reached(reason: str) -> bool:
            nonlocal last_replan_failure, identical_replan_failures
            signature = bounded_text(reason, 1_500)
            if signature == last_replan_failure:
                identical_replan_failures += 1
            else:
                last_replan_failure = signature
                identical_replan_failures = 1
            if identical_replan_failures < self._IDENTICAL_REPLAN_FAILURE_LIMIT:
                return False
            self.logger.write(
                "replan_stalled",
                step_index=observation.step_index if observation is not None else None,
                payload={
                    "reason": signature,
                    "identical_failures": identical_replan_failures,
                    "limit": self._IDENTICAL_REPLAN_FAILURE_LIMIT,
                },
            )
            return True

        def reset_replan_failure() -> None:
            nonlocal last_replan_failure, identical_replan_failures
            last_replan_failure = None
            identical_replan_failures = 0

        session: _RunSession | None = None
        try:
            session = await self._begin_run(
                max_steps=max_steps, seed=seed, supervised=accepts_plans
            )
            state_store = session.state_store
            observation = session.observation
            safety_supervisor = session.safety_supervisor
            if not accepts_plans:
                self._log_observation(observation)

            while not terminated and (
                cycles < max_steps if not accepts_plans else steps_completed < max_steps
            ):
                cycles += 1
                observation = state_store.latest or observation
                if safety_supervisor is not None and safety_supervisor.preempted:
                    pending_preemption = await safety_supervisor.wait_for_preemption()
                    (
                        observation,
                        completed,
                        terminated,
                        success,
                        stop_reason,
                        safety_supervisor,
                        preemption_feedback,
                    ) = await self._handle_preemption_and_maybe_handoff(
                        pending_preemption,
                        state_store,
                        safety_supervisor,
                        remaining_run_actions=max_steps - steps_completed,
                    )
                    steps_completed += completed
                    if preemption_feedback is not None:
                        planner_feedback = preemption_feedback
                        consecutive_replans = 0
                        reset_replan_failure()
                    continue
                reflex = pending_reflex or self.reflexes.decide(observation)
                pending_reflex = None
                if reflex is not None:
                    (
                        observation,
                        completed,
                        terminated,
                        success,
                        stop_reason,
                    ) = await self._execute_continuous_decision(
                        reflex,
                        observation,
                        source="reflex",
                        planner_latency_seconds=0.0,
                    )
                    steps_completed += completed
                    continue

                planning_started = monotonic()
                # Redecorated here rather than relying on whichever publish
                # happened last: a plan outcome recorded after the executor
                # returned must reach the planner that replaces that plan, with
                # or without an observation pump running.
                planner_observation = self.planner_context.decorate(observation)
                if planner_feedback is not None:
                    planner_observation = planner_observation.model_copy(
                        update={"planner_feedback": planner_feedback}
                    )
                if self.reporter is not None:
                    self.reporter.planning_started(observation.step_index)
                planner_source = "planner"
                try:
                    authored_output, preemption = await self._race_with_safety_supervisor(
                        self.planner_service.decide(planner_observation),
                        safety_supervisor,
                    )
                    if preemption is not None:
                        planner_latency_seconds = monotonic() - planning_started
                        self.logger.write(
                            "strategic_planner_call",
                            step_index=observation.step_index,
                            payload={
                                "source": "safety_supervisor_cancelled",
                                "planner_latency_seconds": planner_latency_seconds,
                                "world_revision": (
                                    observation.world_revision.model_dump(mode="json")
                                ),
                                "control_mode": observation.control_mode.value,
                                "output_type": "cancelled",
                            },
                        )
                        self.logger.write(
                            "strategic_planner_cancelled",
                            step_index=observation.step_index,
                            payload={
                                "cause": preemption.cause.value,
                                "reason": preemption.reason,
                                "world_revision": (
                                    preemption.observation.world_revision.model_dump(mode="json")
                                ),
                                "control_mode": observation.control_mode.value,
                            },
                        )
                        (
                            observation,
                            completed,
                            terminated,
                            success,
                            stop_reason,
                            safety_supervisor,
                            preemption_feedback,
                        ) = await self._handle_preemption_and_maybe_handoff(
                            preemption,
                            state_store,
                            safety_supervisor,
                            remaining_run_actions=max_steps - steps_completed,
                        )
                        steps_completed += completed
                        if preemption_feedback is not None:
                            planner_feedback = preemption_feedback
                            consecutive_replans = 0
                            reset_replan_failure()
                        continue
                    assert authored_output is not None
                    output = authored_output.output
                    authored_context = authored_output.context
                except Exception as exc:
                    # A malformed planner response is one bad answer, not a
                    # reason to end a session meant to run continuously. Ask
                    # again and let the replan limit bound a planner that cannot
                    # produce a well-formed one.
                    planner_source = "planner_error"
                    # Tell the next attempt what was wrong with this one. Without
                    # it a deterministic mistake is remade every retry until the
                    # replan limit ends the run.
                    planner_feedback = self.planner_service.retry_feedback(exc)
                    failure_signature = self.planner_service.failure_signature(exc)
                    self.planner_service.failure_decision(
                        exc,
                        step_index=observation.step_index,
                    )
                    # A failed call is still a call: keep it in the replay record
                    # so latency and failure rate stay measurable.
                    self.logger.write(
                        "strategic_planner_call",
                        step_index=observation.step_index,
                        payload={
                            "source": planner_source,
                            "planner_latency_seconds": monotonic() - planning_started,
                            "world_revision": observation.world_revision.model_dump(mode="json"),
                            "control_mode": observation.control_mode.value,
                            "output_type": type(exc).__name__,
                            **(
                                {"failure_category": exc.category}
                                if isinstance(exc, HostedPlannerResponseError)
                                else {}
                            ),
                        },
                    )
                    consecutive_replans += 1
                    if identical_failure_limit_reached(failure_signature):
                        stop_reason = (
                            "Stopped: the planner returned "
                            f"{identical_replan_failures} unusable responses in a "
                            "row because the same failure repeated: "
                            + bounded_text(str(exc), PLANNER_ERROR_RATIONALE_MAX_CHARS)
                        )
                        terminated = True
                    elif consecutive_replans > self.planning_config.max_consecutive_replans:
                        stop_reason = (
                            f"Stopped: the planner returned {consecutive_replans} "
                            "unusable responses in a row. The last was: "
                            + bounded_text(str(exc), PLANNER_ERROR_RATIONALE_MAX_CHARS)
                        )
                        terminated = True
                    continue
                planner_latency_seconds = monotonic() - planning_started
                self.logger.write(
                    "strategic_planner_call",
                    step_index=observation.step_index,
                    payload={
                        "source": planner_source,
                        "planner_latency_seconds": planner_latency_seconds,
                        "world_revision": observation.world_revision.model_dump(mode="json"),
                        "control_mode": observation.control_mode.value,
                        "output_type": type(output).__name__,
                    },
                )
                observation = state_store.latest or observation

                if not accepts_plans:
                    # This schedule acts on one authored decision per turn; a
                    # plan is the wrong shape here, so stop rather than run it.
                    if isinstance(output, PlannerDecision):
                        decision = output
                        decision_source = planner_source
                    else:
                        decision = PlannerDecision(
                            intent="Stop after incompatible planner output.",
                            rationale=(
                                "Single-step mode requires PlannerDecision, but "
                                f"received {type(output).__name__}."
                            ),
                            action=StopAction(
                                reason="Planner output did not match single-step mode."
                            ),
                            confidence=1.0,
                        )
                        decision_source = "planner_error"
                    (
                        observation,
                        completed,
                        terminated,
                        success,
                        stop_reason,
                    ) = await self._execute_continuous_decision(
                        decision,
                        observation,
                        source=decision_source,
                        planner_latency_seconds=planner_latency_seconds,
                        authored_context=authored_context,
                    )
                    steps_completed += completed
                    observation = self.planner_context.decorate(observation)
                    self._log_observation(observation)
                    continue

                if isinstance(output, PlannerDecision):
                    if not isinstance(output.action, StopAction):
                        stop_reason = (
                            "The planner returned a single action where continuous mode "
                            "needs a PlanEnvelope or a StopAction."
                        )
                        self.logger.write(
                            "planner_output_rejected",
                            step_index=observation.step_index,
                            payload={
                                "reason": stop_reason,
                                "output": output.model_dump(mode="json"),
                                "world_revision": (
                                    observation.world_revision.model_dump(mode="json")
                                ),
                                "control_mode": observation.control_mode.value,
                            },
                        )
                        # A wrongly shaped response is a bad answer, not a reason
                        # to end the session; ask again.
                        planner_feedback = (
                            "Your previous response had the wrong shape. Continuous "
                            "mode needs a PlanEnvelope or StopAction."
                        )
                        consecutive_replans += 1
                        if identical_failure_limit_reached(stop_reason):
                            stop_reason = (
                                "Stopped after the same wrong planner output shape "
                                f"repeated {identical_replan_failures} times."
                            )
                            terminated = True
                        elif consecutive_replans > self.planning_config.max_consecutive_replans:
                            terminated = True
                        continue
                    (
                        observation,
                        completed,
                        terminated,
                        success,
                        stop_reason,
                    ) = await self._execute_continuous_decision(
                        output,
                        observation,
                        source=planner_source,
                        planner_latency_seconds=planner_latency_seconds,
                        authored_context=authored_context,
                    )
                    steps_completed += completed
                    continue

                if isinstance(output, PlanPatch):
                    rejection_reason = (
                        "Plan patch rejected: no matching active plan was available to revise."
                    )
                    planner_feedback = (
                        "Your previous PlanPatch could not be applied because there is "
                        "no active plan to patch. Return a fresh PlanEnvelope or "
                        "StopAction grounded in the current observation; do not return "
                        "another PlanPatch unless active_plan is present."
                    )
                    self._plan_event(
                        "plan_rejected",
                        plan_id=output.plan_id,
                        plan_version=output.based_on_plan_version,
                        observation=observation,
                        reason=rejection_reason,
                        evidence={"patch": output.model_dump(mode="json")},
                    )
                    # An out-of-context patch has no authority and spends no
                    # action. Treat it like every neighboring unusable planner
                    # answer: reject it, explain the correct output shape, and
                    # let the common replan limits bound recurrence.
                    consecutive_replans += 1
                    if identical_failure_limit_reached("plan_patch_without_active_plan"):
                        stop_reason = (
                            "Stopped after the same orphaned plan patch repeated "
                            f"{identical_replan_failures} times."
                        )
                        terminated = True
                    elif consecutive_replans > self.planning_config.max_consecutive_replans:
                        stop_reason = (
                            "Stopped: the planner produced "
                            f"{consecutive_replans} unusable plan patches in a row."
                        )
                        terminated = True
                    continue

                plan = output
                self._plan_event(
                    "plan_proposed",
                    plan_id=plan.plan_id,
                    plan_version=plan.plan_version,
                    observation=observation,
                    reason="Strategic planner returned a bounded typed plan.",
                    evidence={
                        "plan": plan.model_dump(mode="json"),
                        "planner_latency_seconds": planner_latency_seconds,
                    },
                )
                if observation.mode == "live" and not plan.based_on_revision.same_snapshot_as(
                    observation.world_revision
                ):
                    rebase_errors = live_plan_rebase_errors(
                        plan,
                        planner_observation,
                        observation,
                    )
                    if rebase_errors:
                        stop_reason = (
                            "Plan rejected before execution: the world moved on while "
                            "the plan was being written, so what it points at is no "
                            "longer there: " + "; ".join(rebase_errors)
                        )
                        planner_feedback = (
                            "Your previous plan was rejected because its references "
                            "no longer matched the world by the time it arrived. "
                            "Re-read the observation and bind to what is there now: "
                            + bounded_text("; ".join(rebase_errors), 900)
                        )
                        self._plan_event(
                            "plan_rejected",
                            plan_id=plan.plan_id,
                            plan_version=plan.plan_version,
                            observation=observation,
                            reason=stop_reason,
                            evidence={
                                "plan_basis": plan.based_on_revision.model_dump(mode="json"),
                                "current_revision": (
                                    observation.world_revision.model_dump(mode="json")
                                ),
                            },
                        )
                        # Same policy as an outright rejected plan: one plan that
                        # aged badly is not a reason to end a session meant to run
                        # continuously. Ask for another, and let the replan limit
                        # bound a planner that cannot produce a usable one.
                        consecutive_replans += 1
                        if identical_failure_limit_reached("; ".join(rebase_errors)):
                            stop_reason = (
                                "Stopped after the same stale-reference plan failure "
                                f"repeated {identical_replan_failures} times: "
                                + "; ".join(rebase_errors)
                            )
                            terminated = True
                        elif consecutive_replans > self.planning_config.max_consecutive_replans:
                            stop_reason = (
                                f"Stopped: the planner produced {consecutive_replans} "
                                "unusable plans in a row. The last reason was: "
                                + "; ".join(rebase_errors)
                            )
                            terminated = True
                        continue
                    old_basis = plan.based_on_revision
                    plan = plan.model_copy(
                        update={"based_on_revision": observation.world_revision},
                        deep=True,
                    )
                    self._plan_event(
                        "plan_rebased",
                        plan_id=plan.plan_id,
                        plan_version=plan.plan_version,
                        observation=observation,
                        reason=(
                            "The plan basis and its own assumptions still hold on the "
                            "newer revision; operation eligibility is checked at scheduling."
                        ),
                        evidence={
                            "old_basis": old_basis.model_dump(mode="json"),
                            "new_basis": plan.based_on_revision.model_dump(mode="json"),
                        },
                    )
                # The steps are the declaration of what the plan will spend, so
                # derive the budget from them rather than rejecting a plan for
                # failing to also state a number we compute anyway. Raised only,
                # so a planner asking for more headroom keeps it.
                plan = with_covering_risk_budget(plan)
                try:
                    assumption_evidence = validate_plan(
                        plan,
                        observation,
                        self.planning_config,
                    )
                except PlanValidationError as exc:
                    # A rejected plan is one bad plan, not a reason to end the
                    # session. An agent meant to run continuously should ask for
                    # another plan; `max_consecutive_replans` is what bounds a
                    # planner that cannot produce an acceptable one.
                    stop_reason = f"Plan rejected before execution: {exc}"
                    planner_feedback = (
                        "Your previous plan was rejected. Fix exactly this and "
                        "return the schema again: " + bounded_text(str(exc), 900)
                    )
                    self._plan_event(
                        "plan_rejected",
                        plan_id=plan.plan_id,
                        plan_version=plan.plan_version,
                        observation=observation,
                        reason=stop_reason,
                        evidence={"plan_basis": plan.based_on_revision.model_dump(mode="json")},
                    )
                    consecutive_replans += 1
                    if identical_failure_limit_reached(str(exc)):
                        stop_reason = (
                            "Stopped after the same rejected plan repeated "
                            f"{identical_replan_failures} times. The reason was: {exc}"
                        )
                        terminated = True
                    elif consecutive_replans > self.planning_config.max_consecutive_replans:
                        stop_reason = (
                            "Stopped: the planner produced "
                            f"{consecutive_replans} unusable plans in a row. The last "
                            f"reason was: {exc}"
                        )
                        terminated = True
                    continue

                # Only now: after schema, causal basis, assumptions, control
                # mode, graph, and budget validation have all passed. A plan
                # that never became executable contributes no continuity.
                self.continuity.apply_plan(
                    plan,
                    observation,
                    authored_context=authored_context,
                )
                observation = state_store.decorate_latest(
                    self.planner_context.decorate(observation)
                )
                plan_started_at = datetime.now(UTC)
                self._plan_event(
                    "plan_accepted",
                    plan_id=plan.plan_id,
                    plan_version=plan.plan_version,
                    observation=observation,
                    reason=(
                        "Schema, causal basis, assumptions, control mode, graph, "
                        "and budgets passed validation."
                    ),
                    evidence={
                        "assumptions": [
                            result.model_dump(mode="json") for result in assumption_evidence
                        ]
                    },
                )
                if self.reporter is not None:
                    self.reporter.plan_accepted(
                        step_index=observation.step_index,
                        objective=plan.objective,
                        latency_seconds=planner_latency_seconds,
                    )
                executor = self._new_plan_executor(
                    state_store,
                    concurrent_planning=True,
                )
                result, preemption = await self._race_with_safety_supervisor(
                    executor.execute(
                        plan,
                        observation,
                        remaining_run_actions=max_steps - steps_completed,
                    ),
                    safety_supervisor,
                )
                if preemption is not None:
                    # "plan_execution_cancelled" names what happened to the
                    # plan. What happened to any order Kenshi is holding is a
                    # different question, and preemption answers none of it: no
                    # order-clearing input is sent here either. A human taking
                    # the keyboard and telemetry going quiet end monitoring for
                    # different reasons and leave the order in different states
                    # of knownness, so the cause is translated rather than
                    # flattened to "the supervisor stopped it".
                    monitor_disposition = monitor_disposition_for_safety_cause(
                        preemption.cause.value
                    )
                    self.logger.write(
                        "plan_execution_cancelled",
                        step_index=observation.step_index,
                        payload={
                            "plan_id": plan.plan_id,
                            "plan_version": plan.plan_version,
                            "cause": preemption.cause.value,
                            "reason": preemption.reason,
                            "monitor_disposition": monitor_disposition.value,
                            "order_disposition_note": (
                                "Preemption sent no order-clearing input; any order "
                                "the characters hold remains with them."
                            ),
                            **_retained_work_at_exit(preemption.observation),
                            "world_revision": (
                                preemption.observation.world_revision.model_dump(mode="json")
                            ),
                            "control_mode": observation.control_mode.value,
                        },
                    )
                    self.outcomes.record_plan_outcome(
                        plan,
                        disposition=PlanDisposition.ABANDONED,
                        reason=(
                            f"Safety preempted the plan ({preemption.cause.value}): "
                            f"{preemption.reason}"
                        ),
                        completed_step_ids=(),
                        actions_completed=0,
                        observation=preemption.observation,
                        started_at=plan_started_at,
                    )
                    (
                        observation,
                        completed,
                        terminated,
                        success,
                        stop_reason,
                        safety_supervisor,
                        preemption_feedback,
                    ) = await self._handle_preemption_and_maybe_handoff(
                        preemption,
                        state_store,
                        safety_supervisor,
                        remaining_run_actions=max_steps - steps_completed,
                    )
                    steps_completed += completed
                    if preemption_feedback is not None:
                        planner_feedback = preemption_feedback
                        consecutive_replans = 0
                        reset_replan_failure()
                    continue
                assert result is not None
                observation = result.observation
                steps_completed += result.actions_completed
                stop_reason = result.reason
                self.outcomes.record_plan_outcome(
                    plan,
                    disposition=(
                        PlanDisposition.TERMINATED
                        if result.terminated
                        else PlanDisposition.COMPLETED
                        if result.completed
                        else PlanDisposition.FAILED
                    ),
                    reason=result.reason,
                    completed_step_ids=result.completed_step_ids,
                    actions_completed=result.actions_completed,
                    observation=observation,
                    started_at=plan_started_at,
                )
                if result.terminated:
                    terminated = True
                    success = result.success
                    continue
                if result.completed:
                    consecutive_replans = 0
                    reset_replan_failure()
                    # The advice was taken; stop repeating it.
                    planner_feedback = None
                    if steps_completed >= max_steps:
                        stop_reason = "Maximum action count reached after plan completion."
                    continue

                consecutive_replans += 1
                planner_feedback = (
                    "Your previous accepted plan could not make progress. Do not "
                    "repeat the same action shape; fix exactly this: "
                    + bounded_text(result.reason, 900)
                )
                if result.reflex_decision is not None:
                    # The next scheduler pass executes the deterministic reflex
                    # through the ordinary authority/handler path before replanning.
                    pending_reflex = result.reflex_decision
                    continue
                if identical_failure_limit_reached(result.reason):
                    stop_reason = (
                        "Continuous planning stopped after the same plan failure "
                        f"repeated {identical_replan_failures} times: {result.reason}"
                    )
                    terminated = True
                elif consecutive_replans > self.planning_config.max_consecutive_replans:
                    stop_reason = (
                        "Continuous planning stopped after exceeding the bounded "
                        "consecutive replan limit."
                    )
                    terminated = True

            return self._finish_run_summary(
                started=started,
                steps_completed=steps_completed,
                terminated=terminated,
                success=success,
                stop_reason=stop_reason,
                observation=observation,
            )
        finally:
            await self._finish_run(session)

    async def _close_environment(self) -> None:
        try:
            outcome = await self.environment.close()
        except Exception as exc:
            outcome = FinalSafeStateOutcome(
                status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
                reason=(f"Environment final-state cleanup failed ({type(exc).__name__}: {exc})."),
            )
        self.final_safe_state = outcome
        if outcome is not None:
            self.logger.write(
                "run_finished_safety",
                payload=outcome,
            )

    async def _race_with_safety_supervisor(
        self,
        work: Coroutine[Any, Any, _WorkResult],
        supervisor: SafetySupervisor | None,
    ) -> tuple[_WorkResult | None, SafetyPreemption | None]:
        if supervisor is None:
            return await work, None
        work_task = asyncio.create_task(work)
        preemption_task = asyncio.create_task(supervisor.wait_for_preemption())
        try:
            done, _ = await asyncio.wait(
                {work_task, preemption_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work_task in done:
                return work_task.result(), None
            if preemption_task in done:
                preemption = preemption_task.result()
                if not work_task.done():
                    work_task.cancel()
                with suppress(asyncio.CancelledError):
                    await work_task
                return None, preemption
            return work_task.result(), None
        finally:
            for task in (work_task, preemption_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    def _new_safety_supervisor(
        self,
        state_store: WorldStateStore,
    ) -> SafetySupervisor:
        return SafetySupervisor(
            store=state_store,
            reflexes=self.reflexes,
            max_sequence_stalls=self.safety_config.supervisor_max_sequence_stalls,
            minimum_live_stall_age_seconds=(
                self.safety_config.supervisor_sequence_stall_min_age_seconds
            ),
            require_paused_between_actions=(self.safety_config.require_paused_between_actions),
        )

    async def _finish_safety_supervisor(
        self,
        supervisor: SafetySupervisor,
    ) -> None:
        await supervisor.stop()
        self.logger.write(
            "safety_supervisor_finished",
            payload=asdict(supervisor.metrics),
        )

    async def _handle_preemption_and_maybe_handoff(
        self,
        preemption: SafetyPreemption,
        state_store: WorldStateStore,
        supervisor: SafetySupervisor | None,
        *,
        remaining_run_actions: int,
    ) -> tuple[
        Observation,
        int,
        bool,
        bool | None,
        str,
        SafetySupervisor | None,
        str | None,
    ]:
        (
            observation,
            completed,
            terminated,
            success,
            stop_reason,
            pause_confirmed,
        ) = await self._handle_safety_preemption(preemption, state_store)
        if not pause_confirmed:
            return (
                observation,
                completed,
                terminated,
                success,
                stop_reason,
                supervisor,
                None,
            )

        can_offer_takeover = (
            preemption.cause is SafetyCause.HUMAN_INPUT
            and self.safety_config.automatic_takeover_enabled
            and remaining_run_actions > completed
            and observation.telemetry is not None
            and not observation.telemetry_stale
            and observation.telemetry.game.loaded
            and observation.telemetry.game.paused is True
            and "game.pause" in observation.telemetry.capabilities
        )
        if preemption.cause is SafetyCause.HUMAN_INPUT and not can_offer_takeover:
            self._log_safety_terminal(
                preemption,
                observation,
                status="safe_paused",
                reason=stop_reason,
            )
            return (
                observation,
                completed,
                True,
                success,
                stop_reason,
                supervisor,
                None,
            )

        if can_offer_takeover:
            if supervisor is not None:
                await self._finish_safety_supervisor(supervisor)
                supervisor = None
            resumed, observation, stop_reason = await self._await_control_takeover(
                state_store,
                preemption,
            )
            if not resumed:
                self._log_safety_terminal(
                    preemption,
                    observation,
                    status="human_control",
                    reason=stop_reason,
                )
                return observation, completed, True, None, stop_reason, None, None

            if self.safety_config.supervisor_enabled:
                supervisor = self._new_safety_supervisor(state_store)
                await supervisor.start()
            feedback = (
                "Human input cancelled the previous work. Control returned after "
                "fresh paused-state revalidation; author a new plan from the current "
                "revision."
            )
            return (
                observation,
                completed,
                False,
                None,
                stop_reason,
                supervisor,
                feedback,
            )

        if preemption.cause is SafetyCause.EMERGENCY_STOP:
            stop_reason = (
                "Emergency stop ended continuous execution after Kenshi reached "
                "a confirmed safe pause."
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status="safe_paused",
                reason=stop_reason,
            )
            return (
                observation,
                completed,
                True,
                None,
                stop_reason,
                supervisor,
                None,
            )

        if remaining_run_actions <= completed:
            stop_reason = (
                "The automated safety pause was confirmed, but the run action budget is exhausted."
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status="safe_paused",
                reason=stop_reason,
            )
            return (
                observation,
                completed,
                True,
                None,
                stop_reason,
                supervisor,
                None,
            )

        replan_errors = self._automated_pause_replan_errors(
            observation,
            preemption,
            state_store,
        )
        if replan_errors:
            stop_reason = (
                "The automated safety pause was confirmed, but strategic work "
                "cannot safely resume: " + "; ".join(replan_errors)
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status="replan_unavailable",
                reason=stop_reason,
            )
            return (
                observation,
                completed,
                True,
                None,
                stop_reason,
                supervisor,
                None,
            )

        if supervisor is not None:
            await self._finish_safety_supervisor(supervisor)
            supervisor = None
        if self.safety_config.supervisor_enabled:
            supervisor = self._new_safety_supervisor(state_store)
            await supervisor.start()
        planner_feedback = (
            "An automated safety intervention cancelled the previous work and "
            f"paused Kenshi ({preemption.cause.value}): "
            f"{bounded_text(preemption.reason, 700)}. The paused state is "
            "fresh and confirmed. Reassess the current observation and author a "
            "new plan; do not resume the cancelled plan."
        )
        self.logger.write(
            "safety_supervisor_replan_requested",
            step_index=observation.step_index,
            payload={
                "cause": preemption.cause.value,
                "reason": preemption.reason,
                "planner_feedback": planner_feedback,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
            },
        )
        return (
            observation,
            completed,
            False,
            None,
            stop_reason,
            supervisor,
            planner_feedback,
        )

    def _automated_pause_replan_errors(
        self,
        observation: Observation,
        preemption: SafetyPreemption,
        state_store: WorldStateStore,
    ) -> list[str]:
        errors: list[str] = []
        telemetry = observation.telemetry
        if preemption.cause in {
            SafetyCause.HUMAN_INPUT,
            SafetyCause.EMERGENCY_STOP,
        }:
            errors.append("the intervention belongs to a human-control boundary")
        if observation.control_mode is not self.control_mode:
            errors.append("control mode changed")
        if observation.telemetry_stale:
            errors.append("telemetry is stale")
        if telemetry is None:
            errors.append("telemetry is unavailable")
            return errors
        if not telemetry.game.loaded:
            errors.append("game is not loaded")
        if telemetry.game.paused is not True:
            errors.append("game is not confirmed paused")
        if "game.pause" not in telemetry.capabilities:
            errors.append("game.pause capability is unavailable")
        if not observation.world_revision.is_later_than(preemption.observation.world_revision):
            errors.append("world revision did not advance after safety preemption")
        if state_store.active_command is not None:
            errors.append("a command is still active")
        return errors

    async def _await_control_takeover(
        self,
        state_store: WorldStateStore,
        preemption: SafetyPreemption,
    ) -> tuple[bool, Observation, str]:
        machine = ControlOwnershipMachine(
            quiet_seconds=self.safety_config.human_control_quiet_seconds,
            countdown_seconds=self.safety_config.takeover_countdown_seconds,
        )
        observation = state_store.latest or preemption.observation
        # The takeover pauses the game for the human's benefit, so handing
        # control back should leave the world as it was found. Otherwise the
        # agent resumes into a stopped world it never stopped, and every
        # movement it orders sits there going nowhere.
        was_running = (
            observation.telemetry is not None and observation.telemetry.game.paused is False
        )
        self._emit_control_ownership_events(
            machine.yield_to_human(
                self.planning_clock.monotonic(),
                reason=(
                    "Human input preempted agent work; the game is confirmed "
                    "paused and all remaining plan work was cancelled."
                ),
            ),
            observation,
        )
        subscription = state_store.subscribe()
        try:
            while machine.state not in {
                ControlOwnershipState.AGENT_ACTIVE,
                ControlOwnershipState.DISARMED,
            }:
                update_task = asyncio.create_task(
                    subscription.get(),
                    name="kenshi-agent-handoff-observation",
                )
                timer_task = asyncio.create_task(
                    self.planning_clock.sleep(self.safety_config.takeover_poll_seconds),
                    name="kenshi-agent-handoff-timer",
                )
                done, pending = await asyncio.wait(
                    {update_task, timer_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                human_input = False
                emergency_stop = False
                if update_task in done:
                    update = update_task.result()
                    observation = update.observation
                    messages = {
                        event.payload.get("message")
                        for event in update.events
                        if event.event_type == "observation_event"
                    }
                    human_input = "human_input_detected" in messages
                    emergency_stop = "emergency_stop_detected" in messages
                for task in pending:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                events = machine.advance(
                    self.planning_clock.monotonic(),
                    human_input=human_input,
                    emergency_stop=emergency_stop,
                )
                self._emit_control_ownership_events(events, observation)
        except WorldStateClosedError:
            reason = "Stopped: the telemetry stream closed while you had control."
            self._emit_control_ownership_events(
                machine.disarm(reason=reason),
                observation,
            )
            return False, observation, reason
        finally:
            subscription.close()

        if machine.state is ControlOwnershipState.DISARMED:
            return (
                False,
                observation,
                "Automatic takeover was disarmed; human control remains active.",
            )

        observation = state_store.latest or observation
        errors = self._takeover_revalidation_errors(
            observation,
            preemption,
            state_store,
        )
        if errors:
            reason = (
                "The agent did not resume after the handback countdown because the "
                "current state no longer checks out: " + "; ".join(errors)
            )
            self._emit_control_ownership_events(
                machine.disarm(reason=reason),
                observation,
            )
            return False, observation, reason
        if was_running:
            observation = await self._restore_running_world(state_store, observation)
        reason = (
            "Agent takeover completed after a visible countdown and fresh "
            "paused-state revalidation; strategic work will replan from the "
            "current revision."
        )
        return True, observation, reason

    async def _restore_running_world(
        self,
        state_store: WorldStateStore,
        observation: Observation,
    ) -> Observation:
        """Unpause after a handback, if the human interrupted a running world.

        Failure here is not fatal: the agent can pause and unpause for itself,
        and a takeover that ends with the world stopped is recoverable. It is
        just slow and confusing, which is what this avoids.
        """

        telemetry = observation.telemetry
        if telemetry is None or telemetry.game.paused is not True:
            return observation
        command = state_store.begin_command(
            plan_id="control-handback",
            plan_version=1,
            step_id="restore_running_world",
            action_kind="pause",
            start_revision=observation.world_revision,
        )
        try:
            transition = await self.execute_control_pause(
                PauseAction(paused=False),
                command=CommandDispatchContext(
                    command_id=command.command_id,
                    based_on_revision=observation.world_revision,
                ),
            )
        except Exception as exc:
            state_store.fail_active_command(f"{type(exc).__name__}: {exc}")
            self.logger.write(
                "control_handback_resume_failed",
                step_index=observation.step_index,
                payload={"reason": f"{type(exc).__name__}: {exc}"},
            )
            return observation
        resumed = transition.observation or observation
        state_store.complete_command(command.command_id, resumed.world_revision)
        self.logger.write(
            "control_handback_resumed",
            step_index=observation.step_index,
            payload={
                "reason": (
                    "The human interrupted a running world, so the world was set "
                    "running again when they handed control back."
                )
            },
        )
        return resumed

    def _takeover_revalidation_errors(
        self,
        observation: Observation,
        preemption: SafetyPreemption,
        state_store: WorldStateStore,
    ) -> list[str]:
        errors: list[str] = []
        telemetry = observation.telemetry
        if observation.control_mode is not self.control_mode:
            errors.append("control mode changed")
        if observation.telemetry_stale:
            errors.append("telemetry is stale")
        if telemetry is None:
            errors.append("telemetry is unavailable")
            return errors
        if not telemetry.game.loaded:
            errors.append("game is not loaded")
        if telemetry.game.paused is not True:
            errors.append("game is not confirmed paused")
        if "game.pause" not in telemetry.capabilities:
            errors.append("game.pause capability is unavailable")
        if not observation.world_revision.is_later_than(preemption.observation.world_revision):
            errors.append("world revision did not advance after human preemption")
        if state_store.active_command is not None:
            errors.append("a command is still active")
        return errors

    def _emit_control_ownership_events(
        self,
        events: tuple[ControlOwnershipEvent, ...],
        observation: Observation,
    ) -> None:
        for event in events:
            self.logger.write(
                event.event_type.value,
                step_index=observation.step_index,
                payload={
                    "state": event.state.value,
                    "reason": event.reason,
                    "seconds_remaining": event.seconds_remaining,
                    "world_revision": observation.world_revision.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                },
            )
            if self.reporter is not None:
                self.reporter.control_ownership(event)

    async def _handle_safety_preemption(
        self,
        preemption: SafetyPreemption,
        state_store: WorldStateStore,
    ) -> tuple[Observation, int, bool, bool | None, str, bool]:
        observation = state_store.latest or preemption.observation
        self.logger.write(
            "safety_supervisor_preempted",
            step_index=observation.step_index,
            payload={
                "cause": preemption.cause.value,
                "reason": preemption.reason,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
                "decision": preemption.decision.model_dump(mode="json"),
            },
        )
        self.logger.write(
            "decision",
            step_index=observation.step_index,
            payload={
                "source": "safety_supervisor",
                "planner_latency_seconds": 0.0,
                "decision": preemption.decision.model_dump(mode="json"),
            },
        )
        if self.reporter is not None:
            if preemption.cause is SafetyCause.HOST_TERMINAL:
                self.reporter.safety_failure(
                    step_index=observation.step_index,
                    cause=preemption.cause.value,
                    reason=preemption.reason,
                )
            self.reporter.decision(
                step_index=observation.step_index,
                source="safety_supervisor",
                decision=preemption.decision,
                latency_seconds=0.0,
            )

        if (
            preemption.cause is SafetyCause.HUMAN_INPUT
            and isinstance(preemption.decision.action, PauseAction)
            and self._is_usable_paused_observation(observation)
        ):
            if state_store.active_command is not None:
                state_store.fail_active_command(
                    "Human input preempted work after a safe pause was confirmed."
                )
            reason = (
                "Human input yielded agent work while Kenshi was already confirmed "
                "paused on fresh telemetry; no redundant cleanup input was emitted."
            )
            self.logger.write(
                "safety_pause_already_confirmed",
                step_index=observation.step_index,
                payload={
                    "cause": preemption.cause.value,
                    "reason": reason,
                    "world_revision": observation.world_revision.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                },
            )
            return observation, 0, False, None, reason, True

        if not isinstance(preemption.decision.action, PauseAction):
            paused = (
                observation.telemetry.game.paused if observation.telemetry is not None else None
            )
            status = (
                "terminal_failure"
                if preemption.cause is SafetyCause.HOST_TERMINAL
                else "safe_paused"
                if paused is True
                else "stopped_unverified"
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status=status,
                reason=preemption.reason,
            )
            return observation, 0, True, None, preemption.reason, False

        action = preemption.decision.action
        try:
            guarded_action = self.validate_safety_pause(action, observation)
        except SafetyViolation as exc:
            reason = f"Safety cleanup policy rejected pause: {exc}"
            self.logger.write(
                "safety_cleanup_failed",
                step_index=observation.step_index,
                payload={
                    "cause": preemption.cause.value,
                    "reason": reason,
                    "world_revision": observation.world_revision.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                },
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status="cleanup_failed",
                reason=reason,
            )
            return observation, 0, True, None, reason, False

        if state_store.active_command is not None:
            state_store.fail_active_command(
                "Independent safety supervisor preempted an in-flight command."
            )
        start_revision = observation.world_revision
        command = state_store.begin_command(
            plan_id="safety-supervisor",
            plan_version=1,
            step_id=preemption.cause.value,
            action_kind=guarded_action.kind,
            start_revision=start_revision,
        )
        self.logger.write(
            "safety_cleanup_started",
            step_index=observation.step_index,
            payload={
                "cause": preemption.cause.value,
                "command_id": command.command_id,
                "world_revision": start_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
            },
        )
        try:
            transition = await self.execute_control_pause(
                guarded_action,
                command=CommandDispatchContext(
                    command_id=command.command_id,
                    based_on_revision=start_revision,
                ),
            )
        except Exception as exc:
            state_store.fail_active_command(f"{type(exc).__name__}: {exc}")
            reason = f"Safety pause execution failed: {type(exc).__name__}: {exc}"
            self.logger.write(
                "safety_cleanup_failed",
                step_index=observation.step_index,
                payload={
                    "cause": preemption.cause.value,
                    "command_id": command.command_id,
                    "reason": reason,
                    "world_revision": start_revision.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                },
            )
            self._log_safety_terminal(
                preemption,
                observation,
                status="cleanup_failed",
                reason=reason,
            )
            return observation, 1, True, None, reason, False

        latest = self.outcomes.record_transition(
            preemption.decision,
            observation,
            transition,
            command_id=command.command_id,
            action_start_revision=start_revision,
        )
        try:
            state_store.complete_command(
                command.command_id,
                latest.world_revision,
            )
        except CommandCausalityError as exc:
            reason = f"Safety pause command causality failed: {exc}"
            self.logger.write(
                "safety_cleanup_failed",
                step_index=latest.step_index,
                payload={
                    "cause": preemption.cause.value,
                    "command_id": command.command_id,
                    "reason": reason,
                    "world_revision": latest.world_revision.model_dump(mode="json"),
                    "control_mode": latest.control_mode.value,
                },
            )
            self._log_safety_terminal(
                preemption,
                latest,
                status="cleanup_failed",
                reason=reason,
            )
            return latest, 1, True, None, reason, False

        verified = self._is_causally_paused(latest, start_revision)
        if not verified:
            try:
                latest = await state_store.wait_for(
                    self._is_usable_paused_observation,
                    after_revision=start_revision,
                    timeout_seconds=(self.safety_config.supervisor_pause_timeout_seconds),
                )
            except TimeoutError:
                pass
            verified = self._is_causally_paused(latest, start_revision)

        if not verified:
            reason = (
                "Safety pause did not reach a causally later confirmed paused "
                "revision before its timeout."
            )
            self.logger.write(
                "safety_cleanup_failed",
                step_index=latest.step_index,
                payload={
                    "cause": preemption.cause.value,
                    "command_id": command.command_id,
                    "reason": reason,
                    "world_revision": latest.world_revision.model_dump(mode="json"),
                    "control_mode": latest.control_mode.value,
                },
            )
            self._log_safety_terminal(
                preemption,
                latest,
                status="cleanup_failed",
                reason=reason,
            )
            return latest, 1, True, None, reason, False

        reason = (
            "Safety supervisor paused Kenshi and confirmed the result "
            "(verified on a fresh telemetry reading, not a stale one)."
        )
        self.logger.write(
            "safety_cleanup_completed",
            step_index=latest.step_index,
            payload={
                "cause": preemption.cause.value,
                "command_id": command.command_id,
                "reason": reason,
                "world_revision": latest.world_revision.model_dump(mode="json"),
                "control_mode": latest.control_mode.value,
            },
        )
        return latest, 1, False, None, reason, True

    def _log_safety_terminal(
        self,
        preemption: SafetyPreemption,
        observation: Observation,
        *,
        status: str,
        reason: str,
    ) -> None:
        self.logger.write(
            "safety_supervisor_terminal",
            step_index=observation.step_index,
            payload={
                "cause": preemption.cause.value,
                "status": status,
                "reason": reason,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
            },
        )

    @staticmethod
    def _is_causally_paused(
        observation: Observation,
        after_revision: WorldStateRevision,
    ) -> bool:
        return bool(
            observation.world_revision.is_later_than(after_revision)
            and RunCoordinator._is_usable_paused_observation(observation)
        )

    @staticmethod
    def _is_usable_paused_observation(observation: Observation) -> bool:
        return bool(
            not observation.telemetry_stale
            and observation.telemetry is not None
            and observation.telemetry.game.loaded
            and observation.telemetry.game.paused is True
            and "game.pause" in observation.telemetry.capabilities
        )

    async def _execute_continuous_decision(
        self,
        decision: PlannerDecision,
        observation: Observation,
        *,
        source: str,
        planner_latency_seconds: float,
        authored_context: AuthoredPlannerContext | None = None,
    ) -> tuple[Observation, int, bool, bool | None, str]:
        self.logger.write(
            "decision",
            step_index=observation.step_index,
            payload={
                "source": source,
                "planner_latency_seconds": planner_latency_seconds,
                "decision": decision.model_dump(mode="json"),
            },
        )
        if self.reporter is not None:
            self.reporter.decision(
                step_index=observation.step_index,
                source=source,
                decision=decision,
                latency_seconds=planner_latency_seconds,
            )
        state_store = self._state_store
        if state_store is None:
            raise RuntimeError("Deterministic execution has no world-state owner.")
        step_id = f"step-{observation.step_index}"
        plan = PlanEnvelope(
            schema_version="1.0",
            plan_id=f"runtime-{observation.step_index}",
            plan_version=1,
            objective=decision.intent,
            control_mode=observation.control_mode,
            based_on_revision=observation.world_revision,
            assumptions=[self._current_condition(observation)],
            steps=[
                PlanStep(
                    step_id=step_id,
                    action=decision.action,
                    affordance=decision.affordance,
                    preconditions=[self._current_condition(observation)],
                    success_conditions=[],
                    failure_conditions=[],
                    timeout_seconds=30.0,
                    retry_budget=0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                )
            ],
            entry_step_id=step_id,
            max_actions=1,
            max_wall_seconds=60.0,
            max_game_seconds=300.0,
            risk_budget=RiskBudget(
                max_pointer_actions=1,
                max_purchase_actions=1,
                max_native_assisted_actions=1,
                max_spend=1_000_000,
            ),
        )
        executor = self._new_plan_executor(
            state_store,
            planning_config=(
                self.planning_config
                if observation.telemetry is not None
                and self.planning_config.mode is not PlanningMode.SINGLE_STEP
                else self.planning_config.model_copy(
                    update={"require_paused_between_actions": False}
                )
            ),
        )
        result = await executor.execute(
            plan,
            observation,
            remaining_run_actions=1,
        )
        latest = result.observation
        latest = self._apply_decision_sidecars(
            decision,
            latest,
            authored_context=authored_context,
            plan_id="single-step",
            step_id=step_id,
        )
        latest = state_store.decorate_latest(latest)
        return (
            latest,
            result.actions_completed,
            result.terminated or isinstance(decision.action, StopAction),
            result.success,
            result.reason,
        )

    @staticmethod
    def _current_condition(observation: Observation) -> Condition:
        if observation.telemetry is None or observation.telemetry_age_seconds is None:
            return Condition(
                kind=ConditionKind.FIELD,
                path="telemetry_stale",
                operator=ConditionOperator.EQUALS,
                expected=observation.telemetry_stale,
                max_age_seconds=300.0,
            )
        return Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=300.0,
        )

    def _new_plan_executor(
        self,
        state_store: WorldStateStore,
        *,
        planning_config: PlanningConfig | None = None,
        concurrent_planning: bool = False,
    ) -> ContinuousPlanExecutor:
        config = planning_config or self.planning_config
        operations = self.operation_execution.create(
            state_store=state_store,
            planning_config=config,
            event=self.plan_events,
            concurrent_planning=concurrent_planning,
        )
        return ContinuousPlanExecutor(
            operations=operations,
            reflexes=self.reflexes,
            logger=self.logger,
            clock=self.planning_clock,
            state_store=state_store,
            planning_config=config,
            event=self.plan_events,
        )

    def _new_world_state_store(self) -> WorldStateStore:
        return WorldStateStore(
            history_limit=self.planning_config.state_history_limit,
            delta_limit=self.planning_config.state_delta_limit,
            event_limit=self.planning_config.event_journal_limit,
            subscriber_queue_limit=self.planning_config.subscriber_queue_limit,
            max_delta_paths=self.planning_config.max_delta_paths,
            clock=self.planning_clock,
            event_sink=self._log_world_event,
        )

    def _record_decision_affordance_receipt(
        self,
        decision: PlannerDecision,
        observation: Observation,
        *,
        status: AffordanceLifecycleStatus,
        reason: str,
        execution_started: bool = False,
    ) -> None:
        if decision.affordance is None:
            return
        telemetry_sequence = (
            observation.telemetry.sequence if observation.telemetry is not None else None
        )
        receipt = terminal_affordance_receipt(
            decision.affordance,
            status=status,
            message=reason,
            telemetry_sequence=telemetry_sequence,
            execution_started=execution_started,
            monitoring_started=(
                execution_started
                and decision.affordance.execution is not AffordanceExecution.IMMEDIATE
            ),
        )
        self.logger.write(
            "affordance_receipt",
            step_index=observation.step_index,
            payload={
                "plan_id": "single-step",
                "plan_version": 1,
                "step_id": f"step-{observation.step_index}",
                "receipt": receipt.model_dump(mode="json"),
            },
        )

    def _log_world_state_update(
        self,
        update: StoreUpdate,
        *,
        log_observation: bool = True,
    ) -> None:
        observation = update.observation
        world_metrics = self._state_store.metrics if self._state_store is not None else None
        self.logger.write(
            "world_state_update",
            step_index=observation.step_index,
            payload={
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "sequence_status": update.sequence_status.value,
                "changed_paths": list(update.delta.changed_paths),
                "delta_truncated": update.delta.truncated,
                "transient_events_lost": (
                    world_metrics.transient_events_lost if world_metrics is not None else 0
                ),
                "subscriber_update_drops": (
                    world_metrics.subscriber_drops if world_metrics is not None else 0
                ),
                "observation_pump_errors": (
                    world_metrics.pump_errors if world_metrics is not None else 0
                ),
            },
        )
        if log_observation:
            self._log_observation(observation)

    def _log_world_event(self, event: WorldEvent) -> None:
        self.logger.write(
            "world_state_event",
            payload={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "world_revision": (
                    event.revision.model_dump(mode="json") if event.revision is not None else None
                ),
                "observed_at_monotonic": event.observed_at_monotonic,
                "evidence": event.payload,
            },
        )

    def _finish_run_summary(
        self,
        *,
        started: datetime,
        steps_completed: int,
        terminated: bool,
        success: bool | None,
        stop_reason: str,
        observation: Observation | None,
    ) -> RunSummary:
        finished = datetime.now(UTC)
        summary = RunSummary(
            run_id=self.run_id,
            control_mode=self.control_mode,
            steps_completed=steps_completed,
            terminated=terminated,
            success=success,
            stop_reason=stop_reason,
            started_at=started,
            finished_at=finished,
            final_observation=observation,
        )
        self.logger.write(
            "run_finished",
            step_index=observation.step_index if observation else None,
            payload={
                "steps_completed": summary.steps_completed,
                "control_mode": summary.control_mode.value,
                "planning_mode": self.planning_config.mode.value,
                "terminated": summary.terminated,
                "success": summary.success,
                "stop_reason": summary.stop_reason,
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                # What Kenshi is still holding as the process leaves. The run
                # ending is not an instruction to anybody: no order-clearing
                # input is sent on the way out, so a character mid-job keeps
                # that job. Saying so is the difference between a report that
                # records the agent stopped and one that implies the world did.
                #
                # Run end and process shutdown are distinguished because they
                # are different facts: one means the agent finished what it set
                # out to do, the other that it was stopped mid-thought. Both
                # leave the order alone, and a reader deciding whether to resume
                # wants to know which happened.
                "monitor_disposition": (
                    MonitorDisposition.DETACHED_AT_RUN_END.value
                    if summary.terminated
                    else MonitorDisposition.DETACHED_AT_SHUTDOWN.value
                ),
                "evidence_semantics_version": EVIDENCE_SEMANTICS_VERSION,
                **_retained_work_at_exit(observation),
            },
        )
        if self.reporter is not None:
            self.reporter.run_finished(
                steps_completed=summary.steps_completed,
                stop_reason=summary.stop_reason,
            )
        return summary

    def _plan_event(
        self,
        event_type: str,
        *,
        plan_id: str,
        plan_version: int,
        observation: Observation,
        reason: str,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self.logger.write(
            event_type,
            step_index=observation.step_index,
            payload={
                "plan_id": plan_id,
                "plan_version": plan_version,
                "step_id": None,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
                "reason": reason,
                "evidence": evidence or {},
            },
        )
        if self.reporter is not None and event_type == "plan_rejected":
            self.reporter.plan_failure(
                event_type=event_type,
                step_index=observation.step_index,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=None,
                reason=reason,
            )

    def _advisor_context_observation(
        self,
        observation: Observation,
    ) -> Observation:
        current = self._state_store.latest if self._state_store is not None else None
        latest = self.planner_context.decorate(current or observation)
        if self._state_store is None:
            return latest
        try:
            return self._state_store.decorate_latest(latest)
        except WorldStateError:
            # The observation pump can publish between reading and decorating.
            # The next planner call always re-decorates the latest revision, so
            # advice is retained by AdvisorSession even if this opportunistic
            # context refresh loses that race.
            current = self._state_store.latest
            return self.planner_context.decorate(current) if current is not None else latest

    def _apply_decision_sidecars(
        self,
        decision: PlannerDecision,
        observation: Observation,
        *,
        authored_context: AuthoredPlannerContext | None,
        plan_id: str,
        step_id: str,
    ) -> Observation:
        """Commit planner sidecars after the action and expose them immediately."""

        if (
            decision.continuity_operations or decision.fieldbook_operations
        ) and authored_context is None:
            raise RuntimeError(
                "Planner-authored durable operations have no authored planner context."
            )
        self.continuity.apply_decision(
            decision,
            observation,
            authored_context=authored_context,
            plan_id=plan_id,
            step_id=step_id,
        )
        latest = self.planner_context.decorate(observation)
        if self._state_store is not None:
            latest = self._state_store.decorate_latest(latest)
        return latest
