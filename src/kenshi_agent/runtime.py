from __future__ import annotations

from typing import TypeVar, cast

from .action_budget import ActionBudgetLedger
from .advisor import (
    AdvisorSession,
)
from .advisor_service import AdvisorService
from .affordances import OPERATION_BINDING_AUTHORITY
from .config import PlanningConfig
from .continuity import ContinuityLedger
from .continuity_service import ContinuityService
from .core.continuity import MemoryRetrievalPolicy
from .core.operation import ControlMode
from .core.scenario import ScenarioAttestation
from .core.telemetry import ScenarioIdentity
from .env.base import AgentEnvironment
from .execution.ports import OperationMechanicsPort
from .final_safe_state import (
    FinalSafeStateOutcome,
)
from .memory import MemoryStore, RecallBudget
from .operation_authority import OperationAuthority
from .operation_execution import OperationExecutionFactory
from .outcome_recorder import OutcomeRecorder
from .plan_events import PlanEventRecorder
from .planner_context import PlannerContextAssembler
from .planner_service import (
    PlannerService,
)
from .planners import Planner
from .planning import PlanningClock, SystemPlanningClock
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
from .run_coordinator import RunCoordinator, RunSummary
from .safety import OperationPolicy
from .session_log import SessionLogger

_WorkResult = TypeVar("_WorkResult")


# How many continuity receipts a planner sees. Enough to stop repeating one
# deterministic mistake; not enough to become a second, rival history.
MAX_SURFACED_CONTINUITY_RECEIPTS = 4
MAX_SURFACED_FIELDBOOK_RECEIPTS = 4


class AgentRuntime:
    def __init__(
        self,
        *,
        run_id: str,
        environment: AgentEnvironment,
        operation_port: OperationMechanicsPort | None = None,
        planner: Planner,
        advisor: AdvisorSession | None = None,
        policy: OperationPolicy,
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
        self.policy = policy
        self.action_budget = ActionBudgetLedger(policy.config)
        # One cross-cutting authority, asked before scheduling and again
        # inside the input lease, so both moments share one policy.
        self.authority = OperationAuthority(policy, OPERATION_BINDING_AUTHORITY)
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
        self.continuity = ContinuityService(
            run_id=run_id,
            store=memory,
            ledger=self._ledger,
            logger=logger,
            control_mode=control_mode,
            recall_budget=self._recall_budget,
            fieldbook_project_limit=fieldbook_project_limit,
            advisor_brief_ids=lambda: self.advisor_service.brief_ids,
        )
        self.outcomes = OutcomeRecorder(
            ledger=self._ledger,
            logger=logger,
            reporter=reporter,
            run_id=run_id,
            decorate=lambda observation: self.planner_context.decorate(observation),
            log_observation=lambda observation: self._coordinator._log_observation(observation),
            log_world_state_update=(
                lambda update: self._coordinator._log_world_state_update(update)
            ),
            state_store=lambda: self._coordinator._state_store,
        )
        self.advisor_service = AdvisorService(
            advisor=advisor,
            logger=logger,
            reporter=reporter,
            control_mode=control_mode,
            run_id=run_id,
            refresh_context=(
                lambda observation: self._coordinator._advisor_context_observation(observation)
            ),
        )
        self.planner_service = PlannerService(
            planner=planner,
            logger=logger,
            continuity=self.continuity,
            control_mode_value=control_mode.value,
        )
        self.reporter = reporter
        self.planning_config = planning_config or PlanningConfig()
        self.planner_context = PlannerContextAssembler(
            continuity=self.continuity,
            ledger=self._ledger,
            planning_config=self.planning_config,
            advisor_availability=self.advisor_service.availability,
        )
        self.planning_clock = planning_clock or SystemPlanningClock()
        self.observation_clock = observation_clock or SystemPlanningClock()
        self.plan_events = PlanEventRecorder(
            logger,
            reporter.plan_failure if reporter is not None else None,
        )
        self.operation_execution = OperationExecutionFactory(
            environment=environment,
            operation_port=self.operation_port,
            action_budget=self.action_budget,
            authority=self.authority,
            logger=logger,
            clock=self.planning_clock,
            observe_transition=self.outcomes.observe_plan_transition,
            concurrent_planner=self.planner_service.decide,
            consult_advisor=self.advisor_service.consult,
            apply_patch_continuity=self.continuity.apply_patch,
            read_memory=self.continuity.read_memory,
            read_fieldbook=self.continuity.read_fieldbook,
            report_action_started=(reporter.action_started if reporter is not None else None),
        )
        self.log_full_observations = log_full_observations
        self.scenario = scenario
        if scenario_attestation is not None and scenario_attestation.scenario != scenario:
            raise ValueError("Scenario attestation must match the runtime scenario identity.")
        self.scenario_attestation = scenario_attestation
        self._coordinator = RunCoordinator(
            run_id=run_id,
            environment=environment,
            execute_control_pause=self.operation_port.control_pause,
            safety_config=policy.config,
            validate_safety_pause=policy.validate_safety_pause,
            reflexes=reflexes,
            logger=logger,
            control_mode=control_mode,
            reporter=reporter,
            planning_config=self.planning_config,
            planning_clock=self.planning_clock,
            observation_clock=self.observation_clock,
            log_full_observations=log_full_observations,
            scenario=scenario,
            scenario_attestation=scenario_attestation,
            memory_retrieval_policy=memory_retrieval_policy,
            ledger=self._ledger,
            continuity=self.continuity,
            planner_service=self.planner_service,
            planner_context=self.planner_context,
            advisor_service=self.advisor_service,
            outcomes=self.outcomes,
            operation_execution=self.operation_execution,
            plan_events=self.plan_events,
        )

    @property
    def coordinator(self) -> RunCoordinator:
        """The state machine this runtime composed and delegates its run to."""

        return self._coordinator

    @property
    def final_safe_state(self) -> FinalSafeStateOutcome | None:
        """The one final-state verdict, owned by the coordinator that produced it."""

        return self._coordinator.final_safe_state

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
        return await self._coordinator.run(max_steps=max_steps, seed=seed)
