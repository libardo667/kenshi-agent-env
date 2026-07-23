from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import dist
from time import monotonic

from PIL import Image, ImageChops

from .config import PlanningConfig
from .continuous_executor import ContinuousPlanExecutor
from .env import AgentEnvironment
from .memory import MemoryStore
from .models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionReceipt,
    CharacterState,
    ControlMode,
    NearbyEntity,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlanningMode,
    PlanPatch,
    PlanStep,
    SkillAction,
    StopAction,
    TelemetrySnapshot,
    Transition,
)
from .planners import Planner
from .planning import PlanningClock, PlanValidationError, SystemPlanningClock, validate_plan
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
from .safety import ActionGuard, SafetyViolation
from .session_log import SessionLogger


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


class AgentRuntime:
    _MATERIAL_VISUAL_CHANGE_FRACTION = 0.01

    def __init__(
        self,
        *,
        run_id: str,
        environment: AgentEnvironment,
        planner: Planner,
        guard: ActionGuard,
        reflexes: ReflexEngine,
        logger: SessionLogger,
        memory: MemoryStore | None,
        memory_limit: int,
        minimum_memory_salience: float,
        action_outcome_limit: int = 12,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
        reporter: ConsoleDecisionReporter | None = None,
        planning_config: PlanningConfig | None = None,
        planning_clock: PlanningClock | None = None,
    ) -> None:
        self.run_id = run_id
        self.environment = environment
        self.planner = planner
        self.guard = guard
        self.reflexes = reflexes
        self.logger = logger
        self.memory = memory
        self.memory_limit = memory_limit
        self.minimum_memory_salience = minimum_memory_salience
        self.action_outcome_limit = action_outcome_limit
        self.control_mode = control_mode
        self._action_outcomes: list[ActionOutcome] = []
        self.reporter = reporter
        self.planning_config = planning_config or PlanningConfig()
        self.planning_clock = planning_clock or SystemPlanningClock()

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
        if self.planning_config.mode == PlanningMode.CONTINUOUS:
            return await self._run_continuous(max_steps=max_steps, seed=seed)
        return await self._run_single_step(max_steps=max_steps, seed=seed)

    async def _run_single_step(
        self,
        *,
        max_steps: int,
        seed: int | None = None,
    ) -> RunSummary:
        started = datetime.now(UTC)
        steps_completed = 0
        terminated = False
        success: bool | None = None
        stop_reason = "Maximum step count reached."
        observation: Observation | None = None
        try:
            self._action_outcomes.clear()
            observation = await self.environment.reset(seed=seed)
            observation = self._with_memories(observation)
            self.logger.write(
                "run_started",
                payload={
                    "max_steps": max_steps,
                    "seed": seed,
                    "control_mode": self.control_mode.value,
                },
            )
            if self.reporter is not None:
                self.reporter.run_started(max_steps)
            self.logger.write("observation", step_index=observation.step_index, payload=observation)

            for _ in range(max_steps):
                planning_started = monotonic()
                if self.reporter is not None:
                    self.reporter.planning_started(observation.step_index)
                decision_source = "planner"
                reflex_decision = self.reflexes.decide(observation)
                if reflex_decision is not None:
                    decision = reflex_decision
                    decision_source = "reflex"
                else:
                    try:
                        planner_output = await self.planner.decide(observation)
                        if isinstance(planner_output, PlannerDecision):
                            decision = planner_output
                        else:
                            decision = PlannerDecision(
                                intent="Stop after incompatible planner output.",
                                rationale=(
                                    "Single-step mode requires PlannerDecision, but "
                                    f"received {type(planner_output).__name__}."
                                ),
                                action=StopAction(
                                    reason="Planner output did not match single-step mode."
                                ),
                                confidence=1.0,
                            )
                            decision_source = "planner_error"
                    except Exception as exc:
                        decision = PlannerDecision(
                            intent="Stop after planner failure.",
                            rationale=f"Planner raised {type(exc).__name__}: {exc}",
                            action=StopAction(reason="Planner failure."),
                            confidence=1.0,
                        )
                        decision_source = "planner_error"

                planner_latency_seconds = monotonic() - planning_started

                self.logger.write(
                    "decision",
                    step_index=observation.step_index,
                    payload={
                        "source": decision_source,
                        "planner_latency_seconds": planner_latency_seconds,
                        "decision": decision.model_dump(mode="json"),
                    },
                )
                if self.reporter is not None:
                    self.reporter.decision(
                        step_index=observation.step_index,
                        source=decision_source,
                        decision=decision,
                        latency_seconds=planner_latency_seconds,
                    )

                try:
                    action = self.guard.validate(decision.action, observation)
                except SafetyViolation as exc:
                    now = datetime.now(UTC)
                    rejected = ActionReceipt(
                        action=decision.action,
                        control_mode=self.control_mode,
                        accepted=False,
                        executed=False,
                        dry_run=True,
                        started_at=now,
                        finished_at=now,
                        primitive_actions=0,
                        message=str(exc),
                        error_type=type(exc).__name__,
                    )
                    self.logger.write(
                        "action_rejected",
                        step_index=observation.step_index,
                        payload=rejected,
                    )
                    if self.reporter is not None:
                        self.reporter.error(
                            step_index=observation.step_index,
                            label="REJECT",
                            message=str(exc),
                        )
                    stop_reason = f"Safety policy rejected action: {exc}"
                    terminated = True
                    break

                try:
                    transition = await self.environment.step(action)
                except Exception as exc:
                    self.logger.write(
                        "environment_error",
                        step_index=observation.step_index,
                        payload={"type": type(exc).__name__, "message": str(exc)},
                    )
                    if self.reporter is not None:
                        self.reporter.error(
                            step_index=observation.step_index,
                            label="ERROR",
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    stop_reason = f"Environment error: {type(exc).__name__}: {exc}"
                    terminated = True
                    break

                steps_completed += 1
                self.logger.write(
                    "action_receipt",
                    step_index=observation.step_index,
                    payload=transition.receipt,
                )
                if self.reporter is not None:
                    self.reporter.action_receipt(
                        step_index=observation.step_index,
                        receipt=transition.receipt,
                    )
                self._record_action_outcome(
                    decision,
                    transition.receipt,
                    observation,
                    transition.observation,
                )
                self._store_memories(decision)
                observation = self._with_memories(transition.observation)
                self.logger.write(
                    "observation", step_index=observation.step_index, payload=observation
                )

                if transition.terminated:
                    terminated = True
                    success = transition.success
                    if transition.events:
                        stop_reason = transition.events[-1]
                    else:
                        stop_reason = (
                            transition.receipt.message or "Environment terminated the episode."
                        )
                    break
                if isinstance(action, StopAction):
                    terminated = True
                    stop_reason = action.reason
                    break

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
        finally:
            await self.environment.close()

    async def _run_continuous(
        self,
        *,
        max_steps: int,
        seed: int | None = None,
    ) -> RunSummary:
        started = datetime.now(UTC)
        steps_completed = 0
        terminated = False
        success: bool | None = None
        stop_reason = "Maximum action count reached."
        observation: Observation | None = None
        consecutive_replans = 0

        try:
            self._action_outcomes.clear()
            observation = self._with_memories(await self.environment.reset(seed=seed))
            self.logger.write(
                "run_started",
                payload={
                    "max_steps": max_steps,
                    "seed": seed,
                    "control_mode": self.control_mode.value,
                    "planning_mode": self.planning_config.mode.value,
                },
            )
            if self.reporter is not None:
                self.reporter.run_started(max_steps)
            self.logger.write(
                "observation",
                step_index=observation.step_index,
                payload=observation,
            )
            if observation.mode == "live":
                return self._finish_continuous_summary(
                    started=started,
                    steps_completed=0,
                    terminated=True,
                    success=None,
                    stop_reason=(
                        "Continuous execution is currently restricted to mock and "
                        "fake event-driven environments; live validation is deferred."
                    ),
                    observation=observation,
                )

            while steps_completed < max_steps and not terminated:
                reflex = self.reflexes.decide(observation)
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
                if self.reporter is not None:
                    self.reporter.planning_started(observation.step_index)
                planner_source = "planner"
                try:
                    output = await self.planner.decide(observation)
                except Exception as exc:
                    planner_source = "planner_error"
                    output = PlannerDecision(
                        intent="Stop after planner failure.",
                        rationale=f"Planner raised {type(exc).__name__}: {exc}",
                        action=StopAction(reason="Planner failure."),
                        confidence=1.0,
                    )
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

                if isinstance(output, PlannerDecision):
                    if not isinstance(output.action, StopAction):
                        stop_reason = (
                            "Continuous mode rejected a single-action planner output; "
                            "a PlanEnvelope or safe StopAction is required."
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
                    )
                    steps_completed += completed
                    continue

                if isinstance(output, PlanPatch):
                    stop_reason = "A PlanPatch cannot be applied without an active matching plan."
                    self._plan_event(
                        "plan_rejected",
                        plan_id=output.plan_id,
                        plan_version=output.based_on_plan_version,
                        observation=observation,
                        reason=stop_reason,
                        evidence={"patch": output.model_dump(mode="json")},
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
                try:
                    assumption_evidence = validate_plan(
                        plan,
                        observation,
                        self.planning_config,
                        self.guard.macros,
                    )
                except PlanValidationError as exc:
                    stop_reason = f"Plan rejected before execution: {exc}"
                    self._plan_event(
                        "plan_rejected",
                        plan_id=plan.plan_id,
                        plan_version=plan.plan_version,
                        observation=observation,
                        reason=stop_reason,
                        evidence={"plan_basis": plan.based_on_revision.model_dump(mode="json")},
                    )
                    terminated = True
                    continue

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
                executor = ContinuousPlanExecutor(
                    environment=self.environment,
                    guard=self.guard,
                    reflexes=self.reflexes,
                    logger=self.logger,
                    config=self.planning_config,
                    clock=self.planning_clock,
                    observe_transition=self._observe_plan_transition,
                )
                result = await executor.execute(
                    plan,
                    observation,
                    remaining_run_actions=max_steps - steps_completed,
                )
                observation = result.observation
                steps_completed += result.actions_completed
                stop_reason = result.reason
                if result.terminated:
                    terminated = True
                    success = result.success
                    continue
                if result.completed:
                    consecutive_replans = 0
                    if steps_completed >= max_steps:
                        stop_reason = "Maximum action count reached after plan completion."
                    continue

                consecutive_replans += 1
                if result.reflex_decision is not None:
                    # The next scheduler pass executes the deterministic reflex
                    # through the ordinary guard/environment path before replanning.
                    continue
                if consecutive_replans > self.planning_config.max_consecutive_replans:
                    stop_reason = (
                        "Continuous planning stopped after exceeding the bounded "
                        "consecutive replan limit."
                    )
                    terminated = True

            return self._finish_continuous_summary(
                started=started,
                steps_completed=steps_completed,
                terminated=terminated,
                success=success,
                stop_reason=stop_reason,
                observation=observation,
            )
        finally:
            await self.environment.close()

    async def _execute_continuous_decision(
        self,
        decision: PlannerDecision,
        observation: Observation,
        *,
        source: str,
        planner_latency_seconds: float,
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
        try:
            action = self.guard.validate(decision.action, observation)
        except SafetyViolation as exc:
            now = datetime.now(UTC)
            rejected = ActionReceipt(
                action=decision.action,
                control_mode=self.control_mode,
                accepted=False,
                executed=False,
                dry_run=True,
                started_at=now,
                finished_at=now,
                primitive_actions=0,
                message=str(exc),
                error_type=type(exc).__name__,
            )
            self.logger.write(
                "action_rejected",
                step_index=observation.step_index,
                payload=rejected,
            )
            return (
                observation,
                0,
                True,
                None,
                f"Safety policy rejected action: {exc}",
            )
        try:
            transition = await self.environment.step(action)
        except Exception as exc:
            self.logger.write(
                "environment_error",
                step_index=observation.step_index,
                payload={"type": type(exc).__name__, "message": str(exc)},
            )
            return (
                observation,
                0,
                True,
                None,
                f"Environment error: {type(exc).__name__}: {exc}",
            )

        latest = self._record_transition(decision, observation, transition)
        is_terminated = transition.terminated or isinstance(action, StopAction)
        reason = (
            transition.events[-1]
            if transition.events
            else action.reason
            if isinstance(action, StopAction)
            else transition.receipt.message or "Deterministic decision completed."
        )
        return latest, 1, is_terminated, transition.success, reason

    def _observe_plan_transition(
        self,
        plan: PlanEnvelope,
        step: PlanStep,
        before: Observation,
        transition: Transition,
    ) -> Observation:
        decision = PlannerDecision(
            intent=f"Execute plan {plan.plan_id} step {step.step_id}.",
            rationale=(
                "The executor revalidated this typed step against the latest "
                "revision and remaining budgets."
            ),
            action=step.action,
            confidence=1.0,
        )
        return self._record_transition(decision, before, transition)

    def _record_transition(
        self,
        decision: PlannerDecision,
        before: Observation,
        transition: Transition,
    ) -> Observation:
        self.logger.write(
            "action_receipt",
            step_index=before.step_index,
            payload=transition.receipt,
        )
        if self.reporter is not None:
            self.reporter.action_receipt(
                step_index=before.step_index,
                receipt=transition.receipt,
            )
        self._record_action_outcome(
            decision,
            transition.receipt,
            before,
            transition.observation,
        )
        self._store_memories(decision)
        latest = self._with_memories(transition.observation)
        self.logger.write(
            "observation",
            step_index=latest.step_index,
            payload=latest,
        )
        return latest

    def _finish_continuous_summary(
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

    def _with_memories(self, observation: Observation) -> Observation:
        updates: dict[str, object] = {
            "planning_mode": self.planning_config.mode,
            "recent_action_outcomes": self._action_outcomes[-self.action_outcome_limit :]
            if self.action_outcome_limit > 0
            else [],
        }
        if self.memory is not None and self.memory_limit > 0:
            updates["memories"] = self.memory.recall(
                limit=self.memory_limit,
                minimum_salience=self.minimum_memory_salience,
            )
        return observation.model_copy(update=updates)

    def _store_memories(self, decision: PlannerDecision) -> None:
        if self.memory is None:
            return
        for write in decision.memory_writes:
            memory_id = self.memory.add(self.run_id, write)
            self.logger.write(
                "memory_written",
                payload={"memory_id": memory_id, "memory": write.model_dump(mode="json")},
            )

    def _record_action_outcome(
        self,
        decision: PlannerDecision,
        receipt: ActionReceipt,
        before: Observation,
        after: Observation,
    ) -> None:
        visual_change = self._visual_change_fraction(before, after)
        telemetry_changes = self._telemetry_changes(before.telemetry, after.telemetry)
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
        outcome = ActionOutcome(
            step_index=before.step_index,
            intent=decision.intent,
            action=receipt.action,
            executed=receipt.executed,
            receipt_message=receipt.message,
            assessment=assessment,
            feedback=feedback,
            visual_change_fraction=visual_change,
            telemetry_changes=telemetry_changes,
            selected_character_name=(
                selected_after.name
                if selected_after is not None
                else selected_before.name
                if selected_before is not None
                else None
            ),
            position_before=(selected_before.position if selected_before is not None else None),
            position_after=(selected_after.position if selected_after is not None else None),
        )
        self._action_outcomes.append(outcome)
        self.logger.write("action_outcome", step_index=before.step_index, payload=outcome)

    @classmethod
    def _assess_outcome(
        cls,
        receipt: ActionReceipt,
        after: TelemetrySnapshot | None,
        *,
        visual_change: float | None,
        telemetry_changes: list[str],
        movement_distance: float | None,
    ) -> tuple[ActionOutcomeAssessment, str]:
        if not receipt.executed:
            return (
                ActionOutcomeAssessment.NOT_EXECUTED,
                "The executor did not perform this action. Do not treat it as progress.",
            )

        if isinstance(receipt.action, SkillAction):
            name = receipt.action.name
            if name in {"move_visible_terrain", "move_on_map"}:
                if movement_distance is not None and movement_distance >= 0.5:
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
            if name in {"interact_visible_person", "approach_confirmed_vendor"}:
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
                money_changed = any(change.startswith("money: ") for change in telemetry_changes)
                food_changed = any(
                    change.startswith("food items: ") for change in telemetry_changes
                )
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

        if telemetry_changes or (
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
        if before is None or after is None:
            return []

        changes: list[str] = []

        def changed(label: str, old: object, new: object) -> None:
            if old != new:
                changes.append(f"{label}: {old!r} -> {new!r}")

        changed("paused", before.game.paused, after.game.paused)
        changed("speed", before.game.speed_multiplier, after.game.speed_multiplier)
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
            if (
                selected_before.hunger is not None
                and selected_after.hunger is not None
                and abs(selected_before.hunger - selected_after.hunger) >= 0.1
            ):
                changes.append(
                    f"hunger: {selected_before.hunger:.2f} -> {selected_after.hunger:.2f}"
                )
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
                    changes.append(f"{selected_after.name} moved {distance:.2f} world units")

        visible_before = {
            entity.name for entity in before.nearby_entities if entity.visible is True
        }
        visible_after = {entity.name for entity in after.nearby_entities if entity.visible is True}
        appeared = sorted(visible_after - visible_before)
        disappeared = sorted(visible_before - visible_after)
        if appeared:
            changes.append(f"visible entities appeared: {', '.join(appeared)}")
        if disappeared:
            changes.append(f"visible entities disappeared: {', '.join(disappeared)}")

        candidate_before = cls._vendor_candidates(before)
        candidate_after = cls._vendor_candidates(after)
        for key in sorted(candidate_before.keys() & candidate_after.keys()):
            old = candidate_before[key]
            new = candidate_after[key]
            if old.distance is not None and new.distance is not None:
                delta = new.distance - old.distance
                if abs(delta) >= 0.5:
                    direction = "farther" if delta > 0 else "closer"
                    changes.append(
                        f"distance to {new.name}: {old.distance:.2f} -> "
                        f"{new.distance:.2f} ({abs(delta):.2f} {direction})"
                    )
            if old.camera_bearing_degrees is not None and new.camera_bearing_degrees is not None:
                bearing_delta = (
                    new.camera_bearing_degrees - old.camera_bearing_degrees + 180.0
                ) % 360.0 - 180.0
                if abs(bearing_delta) >= 3.0:
                    changes.append(
                        f"camera bearing to {new.name}: "
                        f"{old.camera_bearing_degrees:.1f} -> "
                        f"{new.camera_bearing_degrees:.1f} degrees"
                    )
        return changes

    @staticmethod
    def _vendor_candidates(
        snapshot: TelemetrySnapshot,
    ) -> dict[tuple[str, str | None], NearbyEntity]:
        return {
            (entity.name, entity.faction): entity
            for entity in snapshot.nearby_entities
            if entity.is_animal is False
            and entity.has_vendor_list is True
            and entity.is_squad_leader is True
            and entity.has_dialogue is True
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
