from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import dist
from time import monotonic

from PIL import Image, ImageChops

from .env import AgentEnvironment
from .memory import MemoryStore
from .models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionReceipt,
    CharacterState,
    NearbyEntity,
    Observation,
    PlannerDecision,
    SkillAction,
    StopAction,
    TelemetrySnapshot,
)
from .planners import Planner
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
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
        reporter: ConsoleDecisionReporter | None = None,
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
        self._action_outcomes: list[ActionOutcome] = []
        self.reporter = reporter

    async def run(self, *, max_steps: int, seed: int | None = None) -> RunSummary:
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
            self.logger.write("run_started", payload={"max_steps": max_steps, "seed": seed})
            if self.reporter is not None:
                self.reporter.run_started(max_steps)
            self.logger.write(
                "observation", step_index=observation.step_index, payload=observation
            )

            for _ in range(max_steps):
                planning_started = monotonic()
                if self.reporter is not None:
                    self.reporter.planning_started(observation.step_index)
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
                            transition.receipt.message
                            or "Environment terminated the episode."
                        )
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
            if self.reporter is not None:
                self.reporter.run_finished(
                    steps_completed=summary.steps_completed,
                    stop_reason=summary.stop_reason,
                )
            return summary
        finally:
            await self.environment.close()

    def _with_memories(self, observation: Observation) -> Observation:
        updates: dict[str, object] = {
            "recent_action_outcomes": self._action_outcomes[-self.action_outcome_limit :]
            if self.action_outcome_limit > 0
            else []
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
                        f"Lekko moved {movement_distance:.2f} world units; use the new position "
                        "and view to judge route progress.",
                    )
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "This movement skill did not move Lekko by a measurable amount. Treat the "
                    "destination as failed or blocked and choose a different grounded route.",
                )
            if name in {"interact_visible_person", "approach_confirmed_vendor"}:
                active_screen = after.ui.active_screen if after is not None else None
                interaction_opened = after is not None and (
                    after.ui.dialogue_open is True
                    or active_screen in {"dialogue", "trade"}
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
                        f"The interaction approach moved Lekko {movement_distance:.2f} world "
                        "units but opened no dialogue or trade yet.",
                    )
                return (
                    ActionOutcomeAssessment.NO_OP,
                    "The interaction opened no dialogue or trade and did not move Lekko. The "
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
            visual_change is not None
            and visual_change >= cls._MATERIAL_VISUAL_CHANGE_FRACTION
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
                before_gray = before_image.convert("L").resize(
                    (96, 54), Image.Resampling.BILINEAR
                )
            with Image.open(after.screenshot_path) as after_image:
                after_gray = after_image.convert("L").resize(
                    (96, 54), Image.Resampling.BILINEAR
                )
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
            if (
                old.camera_bearing_degrees is not None
                and new.camera_bearing_degrees is not None
            ):
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
