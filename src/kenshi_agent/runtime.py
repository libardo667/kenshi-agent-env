from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .env import AgentEnvironment
from .memory import MemoryStore
from .models import ActionReceipt, Observation, PlannerDecision, StopAction, Transition
from .planners import Planner
from .reflexes import ReflexEngine
from .safety import ActionGuard, SafetyViolation
from .session_log import SessionLogger


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    steps_completed: int
    terminated: bool
    success: bool | None
    stop_reason: str
    started_at: datetime
    finished_at: datetime
    final_observation: Observation | None


class AgentRuntime:
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

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
        started = datetime.now(UTC)
        steps_completed = 0
        terminated = False
        success: bool | None = None
        stop_reason = "Maximum step count reached."
        observation: Observation | None = None
        try:
            observation = await self.environment.reset(seed=seed)
            observation = self._with_memories(observation)
            self.logger.write("run_started", payload={"max_steps": max_steps, "seed": seed})
            self.logger.write(
                "observation", step_index=observation.step_index, payload=observation
            )

            for _ in range(max_steps):
                decision_source = "planner"
                decision = self.reflexes.decide(observation)
                if decision is not None:
                    decision_source = "reflex"
                else:
                    try:
                        decision = await self.planner.decide(observation)
                    except Exception as exc:
                        decision = PlannerDecision(
                            intent="Stop after planner failure.",
                            rationale=f"Planner raised {type(exc).__name__}: {exc}",
                            action=StopAction(reason="Planner failure."),
                            confidence=1.0,
                        )
                        decision_source = "planner_error"

                self.logger.write(
                    "decision",
                    step_index=observation.step_index,
                    payload={
                        "source": decision_source,
                        "decision": decision.model_dump(mode="json"),
                    },
                )

                try:
                    action = self.guard.validate(decision.action, observation)
                except SafetyViolation as exc:
                    now = datetime.now(UTC)
                    rejected = ActionReceipt(
                        action=decision.action,
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
                    stop_reason = f"Environment error: {type(exc).__name__}: {exc}"
                    terminated = True
                    break

                steps_completed += 1
                self.logger.write(
                    "action_receipt",
                    step_index=observation.step_index,
                    payload=transition.receipt,
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
                        stop_reason = transition.receipt.message or "Environment terminated the episode."
                    break
                if isinstance(action, StopAction):
                    terminated = True
                    stop_reason = action.reason
                    break

            finished = datetime.now(UTC)
            summary = RunSummary(
                run_id=self.run_id,
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
                    "terminated": summary.terminated,
                    "success": summary.success,
                    "stop_reason": summary.stop_reason,
                    "started_at": summary.started_at.isoformat(),
                    "finished_at": summary.finished_at.isoformat(),
                },
            )
            return summary
        finally:
            await self.environment.close()

    def _with_memories(self, observation: Observation) -> Observation:
        if self.memory is None or self.memory_limit <= 0:
            return observation
        memories = self.memory.recall(
            limit=self.memory_limit,
            minimum_salience=self.minimum_memory_salience,
        )
        return observation.model_copy(update={"memories": memories})

    def _store_memories(self, decision: PlannerDecision) -> None:
        if self.memory is None:
            return
        for write in decision.memory_writes:
            memory_id = self.memory.add(self.run_id, write)
            self.logger.write(
                "memory_written",
                payload={"memory_id": memory_id, "memory": write.model_dump(mode="json")},
            )
