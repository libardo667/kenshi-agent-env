from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Coroutine, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import dist
from time import monotonic
from typing import Any, TypeVar, cast

from PIL import Image, ImageChops

from .advisor import (
    AdvisorSession,
    advisor_state_fingerprint,
    disabled_advisor_availability,
)
from .affordances import terminal_affordance_receipt
from .config import PlanningConfig
from .continuity import ContinuityLedger
from .continuity_service import ContinuityService
from .continuous_executor import (
    AdvisorActionResult,
    ContinuousPlanExecutor,
)
from .control_ownership import (
    ControlOwnershipEvent,
    ControlOwnershipMachine,
    ControlOwnershipState,
)
from .env import AgentEnvironment
from .execution.ports import OperationMechanicsPort
from .final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
)
from .memory import MemoryStore, RecallBudget
from .models import (
    Action,
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionReceipt,
    AdvisorConsultEvidence,
    AdvisorConsultStatus,
    AffordanceExecution,
    AffordanceLifecycleStatus,
    AuthoredPlannerContext,
    CameraRecoveryStatus,
    CharacterState,
    CommandDispatchContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConsultAdvisorAction,
    ControlMode,
    HarvestResourceAction,
    IdempotencyPolicy,
    MemoryRetrievalPolicy,
    NearbyEntity,
    Observation,
    PauseAction,
    PlanDisposition,
    PlanEnvelope,
    PlannerDecision,
    PlanningMode,
    PlanPatch,
    PlanStep,
    PurchaseItemAction,
    PurchaseStatus,
    RecoverCameraViewAction,
    ResourceHarvestStatus,
    RiskBudget,
    SaleStatus,
    ScenarioIdentity,
    SellItemAction,
    SkillAction,
    StopAction,
    TelemetrySnapshot,
    Transition,
    WorldStateRevision,
)
from .non_progress import retry_state_fingerprint
from .nutrition import nutrition_reserve_change
from .operation_authority import AuthorizationDecision, OperationAuthority
from .operation_definitions import definition_for
from .planner_service import (
    PLANNER_ERROR_RATIONALE_MAX_CHARS,
    PlannerService,
    bounded_text,
)
from .planners import Planner
from .planners.base import (
    HostedPlannerResponseError,
)
from .planning import PlanningClock, PlanValidationError, SystemPlanningClock, validate_plan
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
from .runtime_continuity import (
    recall_for_observation,
)
from .safety import ActionGuard, SafetyViolation
from .safety_supervisor import SafetyCause, SafetyPreemption, SafetySupervisor
from .scenario_fixtures import ScenarioAttestation
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


@dataclass(frozen=True, slots=True)
class _OutcomeIntent:
    """Minimal executor-authored context for recording a continuous step."""

    intent: str


@dataclass(frozen=True, slots=True)
class TelemetryChange:
    """One observed telemetry delta and whether it can count as progress.

    Actor displacement and the pause/speed transitions a monitored option
    performs to do its own work are *mechanical*: every movement produces them
    whether or not the world became any different to decide in. Treating them
    as change is what let live run `live-trade-surface-20260729-r1` report five
    blind directional hops as five successful world changes while the choice
    set never moved.

    The producer declares this, rather than a consumer re-deriving it by
    parsing the rendered label, so the two cannot drift apart.
    """

    label: str
    decision_relevant: bool = True


# How many continuity receipts a planner sees. Enough to stop repeating one
# deterministic mistake; not enough to become a second, rival history.
MAX_SURFACED_CONTINUITY_RECEIPTS = 4
MAX_SURFACED_FIELDBOOK_RECEIPTS = 4


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


class AgentRuntime:
    _MATERIAL_VISUAL_CHANGE_FRACTION = 0.01
    _IDENTICAL_REPLAN_FAILURE_LIMIT = 3

    def __init__(
        self,
        *,
        run_id: str,
        environment: AgentEnvironment,
        operation_port: OperationMechanicsPort | None = None,
        planner: Planner,
        advisor: AdvisorSession | None = None,
        guard: ActionGuard,
        reflexes: ReflexEngine,
        logger: SessionLogger,
        memory: MemoryStore | None,
        memory_limit: int,
        minimum_memory_salience: float,
        entity_memory_limit: int = 8,
        commitment_memory_limit: int = 4,
        hypothesis_memory_limit: int = 2,
        fieldbook_project_limit: int = 8,
        memory_retrieval_policy: MemoryRetrievalPolicy = (MemoryRetrievalPolicy.DETERMINISTIC),
        action_outcome_limit: int = 12,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
        reporter: ConsoleDecisionReporter | None = None,
        planning_config: PlanningConfig | None = None,
        planning_clock: PlanningClock | None = None,
        observation_clock: PlanningClock | None = None,
        log_full_observations: bool = False,
        scenario: ScenarioIdentity | None = None,
        scenario_attestation: ScenarioAttestation | None = None,
    ) -> None:
        self.run_id = run_id
        self.environment = environment
        resolved_operation_port = operation_port or getattr(
            environment,
            "operation_mechanics",
            None,
        )
        if resolved_operation_port is None:
            raise TypeError("Runtime requires an exact operation mechanics port.")
        self.operation_port = cast(OperationMechanicsPort, resolved_operation_port)
        self.planner = planner
        self.advisor = advisor
        self.guard = guard
        # One cross-cutting authority, asked before scheduling and again
        # inside the input lease, so both moments share one policy.
        self.authority = OperationAuthority(guard)
        self.reflexes = reflexes
        self.logger = logger
        self.memory = memory
        self.memory_limit = memory_limit
        self.entity_memory_limit = entity_memory_limit
        self.minimum_memory_salience = minimum_memory_salience
        self.fieldbook_project_limit = fieldbook_project_limit
        self.memory_retrieval_policy = memory_retrieval_policy
        self.action_outcome_limit = action_outcome_limit
        self.control_mode = control_mode
        self._ledger = ContinuityLedger(
            run_id=run_id,
            action_outcome_limit=action_outcome_limit,
        )
        self._recall_budget = RecallBudget(
            commitments=commitment_memory_limit,
            current_target=entity_memory_limit,
            open_hypotheses=hypothesis_memory_limit,
            general=memory_limit,
            minimum_salience=minimum_memory_salience,
        )
        # Bounded and short: these exist so a deterministic invalid update is
        # not repeated, not as a second history.
        self._advisor_brief_ids: set[str] = set()
        self._advisor_task: asyncio.Task[None] | None = None
        self._advisor_result_ready = False
        self.continuity = ContinuityService(
            run_id=run_id,
            store=memory,
            ledger=self._ledger,
            logger=logger,
            control_mode=control_mode,
            advisor_brief_ids=lambda: self._advisor_brief_ids,
        )
        self.planner_service = PlannerService(
            planner=planner,
            logger=logger,
            continuity=self.continuity,
            control_mode_value=control_mode.value,
        )
        self.reporter = reporter
        self.planning_config = planning_config or PlanningConfig()
        self.planning_clock = planning_clock or SystemPlanningClock()
        self.observation_clock = observation_clock or SystemPlanningClock()
        self.log_full_observations = log_full_observations
        self.scenario = scenario
        if scenario_attestation is not None and scenario_attestation.scenario != scenario:
            raise ValueError("Scenario attestation must match the runtime scenario identity.")
        self.scenario_attestation = scenario_attestation
        self._state_store: WorldStateStore | None = None
        # Numbering only. Membership is answered by the record list itself, so a
        # request evicted by MAX_RETAINED_AFFORDANCE_REQUESTS can be raised again
        # instead of being suppressed as a duplicate of something invisible.
        self.final_safe_state: FinalSafeStateOutcome | None = None

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

    def _action_authority(
        self,
        action: Action,
        observation: Observation,
    ) -> AuthorizationDecision:
        """Re-ask the one authority, without spending the same budget twice."""

        return self.authority.evaluate(action, observation)

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
        return await self._run_scheduled(
            max_steps=max_steps,
            seed=seed,
            accepts_plans=self.planning_config.mode == PlanningMode.CONTINUOUS,
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
        self._advisor_brief_ids.clear()
        self.continuity.reset()
        observation = self._with_memories(await self.environment.reset(seed=seed))
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
            if self.guard.config.supervisor_enabled:
                safety_supervisor = self._new_safety_supervisor(state_store)
                await safety_supervisor.start()
            if self.planning_config.observation_pump_enabled:
                observation_pump = ObservationPump(
                    self.environment,
                    state_store,
                    interval_seconds=(self.planning_config.observation_pump_seconds),
                    clock=self.observation_clock,
                    transform=self._with_memories,
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
        await self._finish_advisor_task()
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
                planner_observation = self._with_memories(observation)
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
                    observation = self._with_memories(observation)
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
                    from .live_plan_policy import live_plan_rebase_errors

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
                            "Every action still binds to the same current reference and "
                            "all assumptions still hold on the newer revision."
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
                from .live_plan_policy import (
                    with_covering_risk_budget,
                )

                plan = with_covering_risk_budget(plan)
                try:
                    assumption_evidence = validate_plan(
                        plan,
                        observation,
                        self.planning_config,
                        self.guard.macros,
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
                observation = state_store.decorate_latest(self._with_memories(observation))
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
                executor = ContinuousPlanExecutor(
                    environment=self.environment,
                    operation_port=self.operation_port,
                    guard=self.guard,
                    reflexes=self.reflexes,
                    logger=self.logger,
                    clock=self.planning_clock,
                    state_store=state_store,
                    observe_transition=self._observe_plan_transition,
                    planning_config=self.planning_config,
                    concurrent_planner=self.planner_service.decide,
                    consult_advisor=self._execute_advisor_action,
                    apply_patch_continuity=self.continuity.apply_patch,
                    read_memory=self.continuity.read_memory,
                    read_fieldbook=self.continuity.read_fieldbook,
                    report_action_started=(
                        self.reporter.action_started if self.reporter is not None else None
                    ),
                    report_plan_failure=(
                        self.reporter.plan_failure if self.reporter is not None else None
                    ),
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
                    self.logger.write(
                        "plan_execution_cancelled",
                        step_index=observation.step_index,
                        payload={
                            "plan_id": plan.plan_id,
                            "plan_version": plan.plan_version,
                            "cause": preemption.cause.value,
                            "reason": preemption.reason,
                            "world_revision": (
                                preemption.observation.world_revision.model_dump(mode="json")
                            ),
                            "control_mode": observation.control_mode.value,
                        },
                    )
                    self._record_plan_outcome(
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
                self._record_plan_outcome(
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
                    # through the ordinary guard/environment path before replanning.
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
            max_sequence_stalls=self.guard.config.supervisor_max_sequence_stalls,
            minimum_live_stall_age_seconds=(
                self.guard.config.supervisor_sequence_stall_min_age_seconds
            ),
            require_paused_between_actions=(self.guard.config.require_paused_between_actions),
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
            and self.guard.config.automatic_takeover_enabled
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

            if self.guard.config.supervisor_enabled:
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
        if self.guard.config.supervisor_enabled:
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
            quiet_seconds=self.guard.config.human_control_quiet_seconds,
            countdown_seconds=self.guard.config.takeover_countdown_seconds,
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
                    self.planning_clock.sleep(self.guard.config.takeover_poll_seconds),
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
            transition = await self.operation_port.pause(
                PauseAction(paused=False),
                command=CommandDispatchContext(
                    command_id=command.command_id,
                    based_on_revision=observation.world_revision,
                ),
                token=None,
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
            guarded_action = self.guard.validate_safety_pause(action, observation)
        except SafetyViolation as exc:
            reason = f"Safety cleanup guard rejected pause: {exc}"
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
            transition = await self.operation_port.pause(
                guarded_action,
                command=CommandDispatchContext(
                    command_id=command.command_id,
                    based_on_revision=start_revision,
                ),
                token=None,
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

        latest = self._record_transition(
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
                    timeout_seconds=(self.guard.config.supervisor_pause_timeout_seconds),
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
            and AgentRuntime._is_usable_paused_observation(observation)
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
    ) -> ContinuousPlanExecutor:
        return ContinuousPlanExecutor(
            environment=self.environment,
            operation_port=self.operation_port,
            guard=self.guard,
            reflexes=self.reflexes,
            logger=self.logger,
            clock=self.planning_clock,
            state_store=state_store,
            observe_transition=self._observe_plan_transition,
            planning_config=planning_config or self.planning_config,
            consult_advisor=self._execute_advisor_action,
            apply_patch_continuity=self.continuity.apply_patch,
            read_memory=self.continuity.read_memory,
            read_fieldbook=self.continuity.read_fieldbook,
            report_action_started=(
                self.reporter.action_started if self.reporter is not None else None
            ),
            report_plan_failure=(self.reporter.plan_failure if self.reporter is not None else None),
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

    def _observe_plan_transition(
        self,
        plan: PlanEnvelope,
        step: PlanStep,
        before: Observation,
        transition: Transition,
        command_id: str | None,
        action_start_revision: WorldStateRevision | None,
    ) -> Observation:
        decision = _OutcomeIntent(
            intent=f"Execute plan {plan.plan_id} step {step.step_id}.",
        )
        return self._record_transition(
            decision,
            before,
            transition,
            command_id=command_id,
            action_start_revision=action_start_revision,
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            step_id=step.step_id,
        )

    def _record_transition(
        self,
        decision: PlannerDecision | _OutcomeIntent,
        before: Observation,
        transition: Transition,
        *,
        command_id: str | None = None,
        action_start_revision: WorldStateRevision | None = None,
        plan_id: str = "single-step",
        plan_version: int = 1,
        step_id: str | None = None,
    ) -> Observation:
        candidate = self._with_memories(transition.observation)
        update: StoreUpdate | None = None
        if self._state_store is None:
            latest = candidate
        elif (
            self._state_store.latest is not None
            and candidate.world_revision == self._state_store.latest.world_revision
        ):
            latest = self._state_store.latest
        else:
            try:
                update = self._state_store.publish(candidate)
            except WorldStateError as exc:
                self.logger.write(
                    "observation_rejected",
                    step_index=candidate.step_index,
                    payload={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "world_revision": candidate.world_revision.model_dump(mode="json"),
                    },
                )
                latest = self._state_store.latest or before
            else:
                latest = update.observation

        receipt = transition.receipt
        if command_id is not None and action_start_revision is not None:
            receipt = receipt.model_copy(
                update={
                    "command_id": command_id,
                    "started_after_revision": action_start_revision,
                    "completed_at_revision": latest.world_revision,
                    "causal_revision_advanced": (
                        latest.world_revision.is_later_than(action_start_revision)
                    ),
                }
            )
        self.logger.write(
            "action_receipt",
            step_index=before.step_index,
            payload=receipt,
        )
        if self.reporter is not None:
            self.reporter.action_receipt(
                step_index=before.step_index,
                receipt=receipt,
            )
        self._record_action_outcome(
            decision,
            receipt,
            before,
            latest,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id or f"step-{before.step_index}",
            command_id=command_id,
        )
        latest = self._with_memories(latest)
        if self._state_store is None:
            self._log_observation(latest)
        else:
            latest = self._state_store.decorate_latest(latest)
            if update is not None:
                self._log_world_state_update(
                    StoreUpdate(
                        observation=latest,
                        sequence_status=update.sequence_status,
                        delta=update.delta,
                        events=update.events,
                        active_plan=update.active_plan,
                        active_command=update.active_command,
                    )
                )
        return latest

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

    def _with_memories(self, observation: Observation) -> Observation:
        advisor_availability = (
            self.advisor.availability(observation)
            if self.advisor is not None
            else disabled_advisor_availability()
        )
        advisor_task = getattr(self, "_advisor_task", None)
        if (
            advisor_task is not None
            and not advisor_task.done()
            and not getattr(self, "_advisor_result_ready", False)
            and not advisor_availability.request_pending
        ):
            # create_task schedules the provider coroutine for the next event-loop
            # turn. Expose the reservation immediately so a faster planner cannot
            # launch a duplicate in that small gap.
            advisor_availability = advisor_availability.model_copy(
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
        updates: dict[str, object] = {
            "planning_mode": self.planning_config.mode,
            "recent_action_outcomes": self._ledger.recent_action_outcomes,
            "recent_plan_outcomes": self._ledger.recent_plan_outcomes,
            "recent_continuity_receipts": self.continuity.recent_receipts,
            "recent_fieldbook_receipts": self.continuity.recent_fieldbook_receipts,
            "continuity_writes_degraded_reason": (self.continuity.authority.writes_degraded_reason),
            "continuity_reads_degraded_reason": (self.continuity.authority.reads_degraded_reason),
            "advisor": advisor_availability,
            "memory_search": self.continuity.pending_memory_search,
            "fieldbook_read": self.continuity.pending_fieldbook_read,
            "fieldbook_projects": [],
            "active_fieldbook_project": None,
        }
        if self.memory is not None:
            recalled = recall_for_observation(
                self.memory,
                self.continuity.authority,
                budget=self._recall_budget,
                target_ids=observation.current_memory_target_ids(),
            )
            updates["memories"] = recalled.records
            updates["memory_recall"] = recalled.summary
            updates["continuity_reads_degraded_reason"] = recalled.reads_degraded_reason
            updates["continuity_writes_degraded_reason"] = recalled.writes_degraded_reason
            if recalled.failure is not None:
                self.logger.write(
                    "continuity_store_failed",
                    step_index=observation.step_index,
                    payload={
                        "boundary": recalled.failure.boundary,
                        "reason": recalled.failure.reason,
                    },
                )
            if self.continuity.authority.reads_degraded_reason is None:
                try:
                    updates["fieldbook_projects"] = self.memory.fieldbook.list_projects(
                        limit=getattr(self, "fieldbook_project_limit", 8)
                    )
                    updates["active_fieldbook_project"] = (
                        self.memory.fieldbook.active_project_summary()
                    )
                except sqlite3.Error as exc:
                    reason = self.continuity.authority.quarantine_reads_after_store_failure(exc)
                    updates["continuity_reads_degraded_reason"] = reason
                    updates["continuity_writes_degraded_reason"] = (
                        self.continuity.authority.writes_degraded_reason
                    )
                    self.logger.write(
                        "continuity_store_failed",
                        step_index=observation.step_index,
                        payload={
                            "boundary": "automatic_fieldbook_index",
                            "reason": reason,
                        },
                    )
        return observation.model_copy(update=updates)

    async def _execute_advisor_action(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> AdvisorActionResult:
        """Queue a cognitive request without holding up foreground play."""

        if self.advisor is None:
            evidence = AdvisorConsultEvidence(
                status=AdvisorConsultStatus.DISABLED,
                reason="The strategic advisor is disabled for this run.",
                calls_used=0,
                max_calls=0,
                state_fingerprint=advisor_state_fingerprint(observation),
            )
            return self._finish_immediate_advisor_action(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            )

        self._reap_finished_advisor_task()
        if self._advisor_task is not None and not self._advisor_task.done():
            evidence = AdvisorConsultEvidence(
                status=AdvisorConsultStatus.PENDING,
                reason=(
                    "An advisor request is already pending; the duplicate request was not launched."
                ),
                calls_used=self.advisor.calls_used,
                max_calls=self.advisor.config.max_calls_per_run,
                state_fingerprint=advisor_state_fingerprint(observation),
            )
            return self._finish_immediate_advisor_action(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            )

        availability = self.advisor.availability(observation)
        if not availability.may_request:
            # Suppression paths do not reach a provider and complete immediately.
            evidence = await self.advisor.consult(action, observation)
            return self._finish_immediate_advisor_action(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            )

        started_at = datetime.now(UTC)
        evidence = AdvisorConsultEvidence(
            status=AdvisorConsultStatus.PENDING,
            reason=(
                "The advisor request was queued in the background; foreground "
                "play may continue while it is thinking."
            ),
            calls_used=min(
                self.advisor.calls_used + 1,
                self.advisor.config.max_calls_per_run,
            ),
            max_calls=self.advisor.config.max_calls_per_run,
            state_fingerprint=advisor_state_fingerprint(observation),
        )
        receipt = ActionReceipt(
            action=action,
            control_mode=self.control_mode,
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
        self.logger.write(
            "action_receipt",
            step_index=observation.step_index,
            payload=receipt,
        )
        self.logger.write(
            "advisor_request_queued",
            step_index=observation.step_index,
            payload=self._advisor_event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        if self.reporter is not None:
            self.reporter.action_receipt(
                step_index=observation.step_index,
                receipt=receipt,
            )
        self._advisor_task = asyncio.create_task(
            self._complete_advisor_action(
                action,
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
            ),
            name=f"advisor-{self.run_id}-{plan_id}-{step_id}",
        )
        self._advisor_result_ready = False
        latest = self._advisor_context_observation(observation)
        return AdvisorActionResult(observation=latest, receipt=receipt)

    async def _complete_advisor_action(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> None:
        """Finish one single-flight provider call and publish only its advice."""

        assert self.advisor is not None
        try:
            evidence = await self.advisor.consult(action, observation)
        except asyncio.CancelledError:
            self.logger.write(
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
                    f"{self.advisor.config.timeout_seconds:.2f} seconds."
                ),
                calls_used=self.advisor.calls_used,
                max_calls=self.advisor.config.max_calls_per_run,
                state_fingerprint=advisor_state_fingerprint(observation),
            )
        self._advisor_result_ready = True
        if evidence.brief is not None:
            # Only a brief this run actually issued may later be cited as the
            # source of a memory, and only ever as advice.
            self._advisor_brief_ids.add(evidence.brief.brief_id)
        self.logger.write(
            "advisor_result",
            step_index=observation.step_index,
            payload=self._advisor_event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        self._advisor_context_observation(observation)

    def _finish_immediate_advisor_action(
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
            control_mode=self.control_mode,
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
        self.logger.write(
            "action_receipt",
            step_index=observation.step_index,
            payload=receipt,
        )
        self.logger.write(
            "advisor_result",
            step_index=observation.step_index,
            payload=self._advisor_event_payload(
                observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                evidence=evidence,
            ),
        )
        if self.reporter is not None:
            self.reporter.action_receipt(
                step_index=observation.step_index,
                receipt=receipt,
            )
        return AdvisorActionResult(
            observation=self._advisor_context_observation(observation),
            receipt=receipt,
        )

    @staticmethod
    def _advisor_event_payload(
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

    def _advisor_context_observation(
        self,
        observation: Observation,
    ) -> Observation:
        current = self._state_store.latest if self._state_store is not None else None
        latest = self._with_memories(current or observation)
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
            return self._with_memories(current) if current is not None else latest

    async def _finish_advisor_task(self) -> None:
        task = self._advisor_task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.write(
                "advisor_task_failed",
                payload={
                    "task": task.get_name(),
                    "error_type": type(exc).__name__,
                    "reason": bounded_text(str(exc), 1_000),
                },
            )
        finally:
            if self._advisor_task is task:
                self._advisor_task = None
            self._advisor_result_ready = False

    def _reap_finished_advisor_task(self) -> None:
        task = self._advisor_task
        if task is None or not task.done():
            return
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.write(
                "advisor_task_failed",
                payload={
                    "task": task.get_name(),
                    "error_type": type(exc).__name__,
                    "reason": bounded_text(str(exc), 1_000),
                },
            )
        finally:
            self._advisor_task = None
            self._advisor_result_ready = False

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
        latest = self._with_memories(observation)
        if self._state_store is not None:
            latest = self._state_store.decorate_latest(latest)
        return latest

    def _record_plan_outcome(
        self,
        plan: PlanEnvelope,
        *,
        disposition: PlanDisposition,
        reason: str,
        completed_step_ids: Sequence[str],
        actions_completed: int,
        observation: Observation,
        started_at: datetime,
    ) -> None:
        outcome = self._ledger.record_plan_outcome(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            objective=plan.objective,
            disposition=disposition,
            reason=bounded_text(reason, 1000),
            completed_step_ids=completed_step_ids,
            actions_completed=actions_completed,
            terminal_revision=observation.world_revision,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        self.logger.write(
            "plan_outcome",
            step_index=observation.step_index,
            payload=outcome,
        )

    def _record_action_outcome(
        self,
        decision: PlannerDecision | _OutcomeIntent,
        receipt: ActionReceipt,
        before: Observation,
        after: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
        command_id: str | None = None,
    ) -> None:
        visual_change = self._visual_change_fraction(before, after)
        telemetry_changes = self._telemetry_changes_detailed(before.telemetry, after.telemetry)
        selected_before = self._selected_character(before.telemetry)
        selected_after = self._selected_character(after.telemetry)
        movement_distance = self._movement_distance(selected_before, selected_after)
        assessment, feedback = self._assess_outcome(
            receipt,
            after.telemetry,
            visual_change=visual_change,
            telemetry_changes=telemetry_changes,
            movement_distance=movement_distance,
        )
        contract = definition_for(receipt.action)
        controller_verified = bool(
            contract is not None
            and contract.controller_verified
            and assessment is ActionOutcomeAssessment.CHANGED
        )
        semantic_status: str | None = None
        target_id: str | None = None
        if receipt.semantic is not None:
            target_id = receipt.semantic.target_id
            if receipt.semantic.purchase is not None:
                semantic_status = receipt.semantic.purchase.status.value
            elif receipt.semantic.sale is not None:
                semantic_status = receipt.semantic.sale.status.value
            elif receipt.semantic.resource_harvest is not None:
                semantic_status = receipt.semantic.resource_harvest.status.value
            elif receipt.semantic.resource_transfer is not None:
                semantic_status = receipt.semantic.resource_transfer.status.value
            elif receipt.semantic.camera_recovery is not None:
                semantic_status = receipt.semantic.camera_recovery.status.value
        if receipt.native_acknowledgement is not None:
            target_id = receipt.native_acknowledgement.target_id or target_id
            semantic_status = receipt.native_acknowledgement.status.value
        if target_id is None:
            candidate_target = getattr(receipt.action, "target_id", None)
            target_id = candidate_target if isinstance(candidate_target, str) else None
        outcome = ActionOutcome(
            outcome_id=self._ledger.next_action_outcome_id(),
            run_id=self.run_id,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            command_id=command_id or receipt.command_id,
            step_index=before.step_index,
            intent=decision.intent,
            action=receipt.action,
            executed=receipt.executed,
            receipt_message=receipt.message,
            assessment=assessment,
            causal_revision_advanced=receipt.causal_revision_advanced,
            controller_verified=controller_verified,
            semantic_status=semantic_status,
            target_id=target_id,
            feedback=feedback,
            started_after_revision=receipt.started_after_revision,
            completed_at_revision=receipt.completed_at_revision,
            visual_change_fraction=visual_change,
            telemetry_changes=[change.label for change in telemetry_changes],
            selected_character_name=(
                selected_after.name
                if selected_after is not None
                else selected_before.name
                if selected_before is not None
                else None
            ),
            position_before=(selected_before.position if selected_before is not None else None),
            position_after=(selected_after.position if selected_after is not None else None),
            identity_session_id=(
                after.telemetry.identity_session_id if after.telemetry is not None else None
            ),
            retry_state_fingerprint=retry_state_fingerprint(
                receipt.action,
                after,
            ),
        )
        self._ledger.record_action_outcome(outcome)
        self.logger.write("action_outcome", step_index=before.step_index, payload=outcome)

    @classmethod
    def _assess_outcome(
        cls,
        receipt: ActionReceipt,
        after: TelemetrySnapshot | None,
        *,
        visual_change: float | None,
        telemetry_changes: Sequence[TelemetryChange],
        movement_distance: float | None,
    ) -> tuple[ActionOutcomeAssessment, str]:
        labels = [change.label for change in telemetry_changes]
        # Displacement and world-time transitions are what an option does to
        # itself, not what it did to the world. An action that produced only
        # those left every choice exactly where it found it.
        decision_relevant = [
            change.label for change in telemetry_changes if change.decision_relevant
        ]
        mechanical_only = bool(labels) and not decision_relevant
        if not receipt.executed:
            return (
                ActionOutcomeAssessment.NOT_EXECUTED,
                "The executor did not perform this action. Do not treat it as progress.",
            )
        if receipt.causal_revision_advanced is False:
            return (
                ActionOutcomeAssessment.UNKNOWN,
                "The action has no causally later validated world revision. "
                "Do not treat raw or pre-command state as progress.",
            )

        if isinstance(receipt.action, PurchaseItemAction):
            purchase = receipt.semantic.purchase if receipt.semantic is not None else None
            if purchase is None:
                return (
                    ActionOutcomeAssessment.UNKNOWN,
                    "Purchase returned no typed controller evidence.",
                )
            if purchase.status is PurchaseStatus.PURCHASED:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    f"The controller conserved all {purchase.purchased_quantity} "
                    f"requested {purchase.item_name!r} purchases through matching "
                    "quoted charge and exact window-owner inventory gain.",
                )
            if purchase.status is PurchaseStatus.PARTIALLY_PURCHASED:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    f"The controller conserved {purchase.purchased_quantity}/"
                    f"{purchase.requested_quantity} {purchase.item_name!r} "
                    f"purchases before stopping: {purchase.reason}",
                )
            if purchase.status is PurchaseStatus.NOT_PURCHASED:
                return (
                    ActionOutcomeAssessment.NO_OP,
                    f"Purchase made no verified transfer: {purchase.reason}",
                )
            return (
                ActionOutcomeAssessment.UNKNOWN,
                f"Purchase delivery is ambiguous and must not be retried as a "
                f"whole: {purchase.reason}",
            )

        if isinstance(receipt.action, SellItemAction):
            sale = receipt.semantic.sale if receipt.semantic is not None else None
            if sale is None:
                return (
                    ActionOutcomeAssessment.UNKNOWN,
                    "Sale returned no typed controller evidence.",
                )
            if sale.status is SaleStatus.SOLD:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    f"The controller conserved all {sale.sold_quantity} requested "
                    f"{sale.item_name!r} sales through matching purse gain and "
                    "exact window-owner inventory loss.",
                )
            if sale.status is SaleStatus.PARTIALLY_SOLD:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    f"The controller conserved {sale.sold_quantity}/"
                    f"{sale.requested_quantity} {sale.item_name!r} sales before "
                    f"stopping: {sale.reason}",
                )
            if sale.status is SaleStatus.NOT_SOLD:
                return (
                    ActionOutcomeAssessment.NO_OP,
                    f"Sale made no verified transfer: {sale.reason}",
                )
            return (
                ActionOutcomeAssessment.UNKNOWN,
                f"Sale delivery is ambiguous and must not be retried as a whole: {sale.reason}",
            )

        if isinstance(receipt.action, HarvestResourceAction):
            harvest = receipt.semantic.resource_harvest if receipt.semantic is not None else None
            if harvest is None:
                return (
                    ActionOutcomeAssessment.UNKNOWN,
                    "Resource harvest returned no typed controller evidence.",
                )
            if harvest.status is ResourceHarvestStatus.HARVESTED:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    f"The controller conserved {harvest.transferred_quantity} "
                    f"{harvest.item_name!r} into the exact actor and closed its "
                    "owned inventory windows.",
                )
            return (
                ActionOutcomeAssessment.NO_OP,
                f"Resource harvest ended as {harvest.status.value!r}: {harvest.reason}",
            )

        if isinstance(receipt.action, RecoverCameraViewAction):
            recovery = receipt.semantic.camera_recovery if receipt.semantic is not None else None
            if recovery is None:
                return (
                    ActionOutcomeAssessment.UNKNOWN,
                    "Camera recovery returned no typed controller evidence. Do not "
                    "assume the view is usable.",
                )
            if recovery.status is CameraRecoveryStatus.ALREADY_CLEAR:
                # The controller looked and found nothing to do. The view is
                # usable, but this action did not make it so, and asking again
                # on the same evidence will keep returning already_clear.
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "The view was already a usable selected-character-following "
                    f"view on floor {recovery.final_floor}, so recovery changed "
                    "nothing. Do not repeat it without evidence the view broke.",
                )
            if recovery.status is CameraRecoveryStatus.RECOVERED:
                return (
                    ActionOutcomeAssessment.CHANGED,
                    "The controller restored a usable selected-character-following "
                    f"view on floor {recovery.final_floor}; camera recovery does "
                    "not need model-authored follow-up gestures.",
                )
            return (
                ActionOutcomeAssessment.NO_OP,
                "The fixed camera transaction exhausted its bounded candidates "
                "without a clear anchored frame. Do not finagle camera primitives "
                "or repeat recovery on the same evidence.",
            )

        if isinstance(receipt.action, SkillAction):
            name = receipt.action.name
            if name in {"move_visible_terrain", "move_on_map"}:
                if movement_distance is not None and movement_distance >= 0.5:
                    if mechanical_only:
                        return (
                            ActionOutcomeAssessment.NO_OP,
                            cls._blind_movement_feedback(movement_distance),
                        )
                    return (
                        ActionOutcomeAssessment.CHANGED,
                        f"The selected character moved {movement_distance:.2f} world units; "
                        "use the new position "
                        "and view to judge route progress.",
                    )
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "This movement skill did not move the selected character by a measurable "
                    "amount. Treat the "
                    "destination as failed or blocked and choose a different grounded route.",
                )
            if name in {
                "interact_visible_person",
                "approach_confirmed_vendor",
                "continue_confirmed_vendor_approach",
            }:
                active_screen = after.ui.active_screen if after is not None else None
                interaction_opened = after is not None and (
                    after.ui.dialogue_open is True or active_screen in {"dialogue", "trade"}
                )
                if interaction_opened:
                    return (
                        ActionOutcomeAssessment.CHANGED,
                        "The interaction opened dialogue or trade. Inspect that UI before any "
                        "further click.",
                    )
                if movement_distance is not None and movement_distance >= 0.5:
                    return (
                        ActionOutcomeAssessment.CHANGED,
                        "The interaction approach moved the selected character "
                        f"{movement_distance:.2f} world "
                        "units but opened no dialogue or trade yet.",
                    )
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "The interaction opened no dialogue or trade and did not move the "
                    "selected character. The "
                    "click failed to make progress; do not repeat it on the same evidence.",
                )
            if name == "buy_inspected_shop_item":
                money_changed = any(label.startswith("money: ") for label in labels)
                food_changed = any(label.startswith("food items: ") for label in labels)
                if money_changed and food_changed:
                    return (
                        ActionOutcomeAssessment.CHANGED,
                        "Purchase verified: money decreased and the selected character's "
                        "food-item count increased.",
                    )
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "Purchase was not verified by both a money decrease and food-item increase. "
                    "Do not click another item.",
                )

        if mechanical_only:
            # The screenshot cannot outvote this: walking repaints the frame
            # whether or not the walk was worth anything.
            if movement_distance is not None and movement_distance >= 0.5:
                return (
                    ActionOutcomeAssessment.NO_OP,
                    cls._blind_movement_feedback(movement_distance),
                )
            return (
                ActionOutcomeAssessment.NO_OP,
                "This action only moved world time; nothing else the runtime "
                "tracks changed. Pausing or resuming is not progress on its own, "
                "so do not repeat it without new evidence.",
            )
        if decision_relevant or (
            visual_change is not None and visual_change >= cls._MATERIAL_VISUAL_CHANGE_FRACTION
        ):
            return (
                ActionOutcomeAssessment.CHANGED,
                "The action produced an observed change. Use the listed telemetry deltas and "
                "current screenshot to judge whether it advanced the objective.",
            )
        if visual_change is not None:
            return (
                ActionOutcomeAssessment.NO_OP,
                "No material visual or tracked telemetry change followed this action. Treat it "
                "as a no-op in the observed state and do not repeat it without new evidence.",
            )
        return (
            ActionOutcomeAssessment.UNKNOWN,
            "The runtime could not verify a visual or telemetry outcome. Do not assume the "
            "action succeeded.",
        )

    @staticmethod
    def _blind_movement_feedback(movement_distance: float) -> str:
        return (
            f"The selected character moved {movement_distance:.2f} world units "
            "and nothing else changed: no character, target, interface, or "
            "resource became available or unavailable. Distance is not progress. "
            "Do not repeat a bearing on the same evidence; either name an "
            "observed destination or change approach."
        )

    @staticmethod
    def _visual_change_fraction(before: Observation, after: Observation) -> float | None:
        if before.screenshot_path is None or after.screenshot_path is None:
            return None
        try:
            with Image.open(before.screenshot_path) as before_image:
                before_gray = before_image.convert("L").resize((96, 54), Image.Resampling.BILINEAR)
            with Image.open(after.screenshot_path) as after_image:
                after_gray = after_image.convert("L").resize((96, 54), Image.Resampling.BILINEAR)
        except (OSError, ValueError):
            return None
        histogram = ImageChops.difference(before_gray, after_gray).histogram()
        changed_pixels = sum(histogram[8:])
        return changed_pixels / (96 * 54)

    @classmethod
    def _telemetry_changes(
        cls,
        before: TelemetrySnapshot | None,
        after: TelemetrySnapshot | None,
    ) -> list[str]:
        return [change.label for change in cls._telemetry_changes_detailed(before, after)]

    @classmethod
    def _telemetry_changes_detailed(
        cls,
        before: TelemetrySnapshot | None,
        after: TelemetrySnapshot | None,
    ) -> list[TelemetryChange]:
        if before is None or after is None:
            return []

        changes: list[TelemetryChange] = []

        def changed(
            label: str,
            old: object,
            new: object,
            *,
            decision_relevant: bool = True,
        ) -> None:
            if old != new:
                changes.append(
                    TelemetryChange(
                        f"{label}: {old!r} -> {new!r}",
                        decision_relevant=decision_relevant,
                    )
                )

        # World time is the controller's to move: options unpause to walk and
        # repause to finish. A pause transition on its own leaves every choice
        # exactly where it was.
        changed("paused", before.game.paused, after.game.paused, decision_relevant=False)
        changed(
            "speed",
            before.game.speed_multiplier,
            after.game.speed_multiplier,
            decision_relevant=False,
        )
        changed("money", before.game.money, after.game.money)
        changed("location", before.game.location_name, after.game.location_name)
        changed("active screen", before.ui.active_screen, after.ui.active_screen)
        changed("modal open", before.ui.modal_open, after.ui.modal_open)
        changed("dialogue open", before.ui.dialogue_open, after.ui.dialogue_open)
        changed("dialogue options", before.ui.dialogue_options, after.ui.dialogue_options)
        changed("context menu open", before.ui.context_menu_open, after.ui.context_menu_open)
        changed(
            "selected character",
            before.ui.selected_character_id,
            after.ui.selected_character_id,
        )

        selected_before = cls._selected_character(before)
        selected_after = cls._selected_character(after)
        if selected_before is not None and selected_after is not None:
            changed("food items", selected_before.food_items, selected_after.food_items)
            changed("current goal", selected_before.current_goal, selected_after.current_goal)
            changed("alive", selected_before.alive, selected_after.alive)
            changed("conscious", selected_before.conscious, selected_after.conscious)
            changed("in combat", selected_before.in_combat, selected_after.in_combat)
            reserve_change = nutrition_reserve_change(
                selected_before.hunger,
                selected_after.hunger,
            )
            if reserve_change is not None:
                changes.append(TelemetryChange(reserve_change))
            if selected_before.position is not None and selected_after.position is not None:
                distance = dist(
                    (
                        selected_before.position.x,
                        selected_before.position.y,
                        selected_before.position.z,
                    ),
                    (
                        selected_after.position.x,
                        selected_after.position.y,
                        selected_after.position.z,
                    ),
                )
                if distance >= 0.5:
                    changes.append(
                        TelemetryChange(
                            f"{selected_after.name} moved {distance:.2f} world units",
                            decision_relevant=False,
                        )
                    )

        visible_before = {
            entity.name for entity in before.nearby_entities if entity.visible is True
        }
        visible_after = {entity.name for entity in after.nearby_entities if entity.visible is True}
        appeared = sorted(visible_after - visible_before)
        disappeared = sorted(visible_before - visible_after)
        if appeared:
            changes.append(TelemetryChange(f"visible entities appeared: {', '.join(appeared)}"))
        if disappeared:
            changes.append(
                TelemetryChange(f"visible entities disappeared: {', '.join(disappeared)}")
            )

        candidate_before = cls._vendor_candidates(before)
        candidate_after = cls._vendor_candidates(after)
        for key in sorted(candidate_before.keys() & candidate_after.keys()):
            old = candidate_before[key]
            new = candidate_after[key]
            if old.distance is not None and new.distance is not None:
                delta = new.distance - old.distance
                if abs(delta) >= 0.5:
                    direction = "farther" if delta > 0 else "closer"
                    # Closing on a named vendor is route progress the planner
                    # chose, not incidental drift.
                    changes.append(
                        TelemetryChange(
                            f"distance to {new.name}: {old.distance:.2f} -> "
                            f"{new.distance:.2f} ({abs(delta):.2f} {direction})"
                        )
                    )
            if old.camera_bearing_degrees is not None and new.camera_bearing_degrees is not None:
                bearing_delta = (
                    new.camera_bearing_degrees - old.camera_bearing_degrees + 180.0
                ) % 360.0 - 180.0
                if abs(bearing_delta) >= 3.0:
                    # Where the camera points is a view detail, not a change in
                    # what the agent can choose to do next.
                    changes.append(
                        TelemetryChange(
                            f"camera bearing to {new.name}: "
                            f"{old.camera_bearing_degrees:.1f} -> "
                            f"{new.camera_bearing_degrees:.1f} degrees",
                            decision_relevant=False,
                        )
                    )
        return changes

    @staticmethod
    def _vendor_candidates(
        snapshot: TelemetrySnapshot,
    ) -> dict[tuple[str, str | None], NearbyEntity]:
        return {
            (entity.name, entity.faction): entity
            for entity in snapshot.nearby_entities
            if entity.is_confirmed_vendor()
        }

    @staticmethod
    def _selected_character(snapshot: TelemetrySnapshot | None) -> CharacterState | None:
        if snapshot is None:
            return None
        selected_id = snapshot.ui.selected_character_id
        if selected_id is not None:
            selected = next(
                (character for character in snapshot.squad if character.id == selected_id),
                None,
            )
            if selected is not None:
                return selected
        return next(
            (character for character in snapshot.squad if character.selected),
            snapshot.squad[0] if snapshot.squad else None,
        )

    @staticmethod
    def _movement_distance(
        before: CharacterState | None,
        after: CharacterState | None,
    ) -> float | None:
        if before is None or after is None or before.position is None or after.position is None:
            return None
        return dist(
            (before.position.x, before.position.y, before.position.z),
            (after.position.x, after.position.y, after.position.z),
        )
