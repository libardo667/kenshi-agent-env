"""The one producer of typed outcome records for everything the run does.

An outcome is not what a receipt said; it is what the world showed afterward.
This compares the state an operation started from with causally later evidence,
classifies the difference into decision-relevant change and mechanical noise,
and files one typed record per action and per plan.

Handlers hand up typed terminal evidence. Nothing here reverse-engineers what
happened from prose, and nothing else in the system writes an outcome.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import dist

from PIL import Image, ImageChops

from .continuity import ContinuityLedger
from .core.evidence import (
    ActionOutcome,
    ActionOutcomeAssessment,
    CameraRecoveryStatus,
    PlanDisposition,
    PurchaseStatus,
    ResourceHarvestStatus,
    SaleStatus,
)
from .core.observation import Observation
from .core.operation import (
    HarvestResourceAction,
    PurchaseItemAction,
    RecoverCameraViewAction,
    SellItemAction,
)
from .core.planning import (
    PlanEnvelope,
    PlannerDecision,
    PlanStep,
)
from .core.telemetry import (
    CharacterState,
    NearbyEntity,
    TelemetrySnapshot,
)
from .core.transport import (
    ActionReceipt,
    Transition,
)
from .core.world import WorldStateRevision
from .non_progress import retry_state_fingerprint
from .nutrition import nutrition_reserve_change
from .operation_definitions import definition_for
from .planner_service import bounded_text
from .reporting import ConsoleDecisionReporter
from .session_log import SessionLogger
from .world_state import StoreUpdate, WorldStateError, WorldStateStore


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


@dataclass(frozen=True, slots=True)
class _OutcomeIntent:
    """A plan step's intent, standing in for a planner decision's."""

    intent: str
    rationale: str = ""


class OutcomeRecorder:
    """Compare before and after, and file what actually changed."""

    # Below this fraction of changed pixels, a frame difference is noise rather
    # than evidence that the world became different to decide in.
    _MATERIAL_VISUAL_CHANGE_FRACTION = 0.01

    def __init__(
        self,
        *,
        ledger: ContinuityLedger,
        logger: SessionLogger,
        reporter: ConsoleDecisionReporter | None,
        run_id: str,
        decorate: Callable[[Observation], Observation],
        log_observation: Callable[[Observation], None],
        log_world_state_update: Callable[[StoreUpdate], None],
        state_store: Callable[[], WorldStateStore | None],
    ) -> None:
        self._ledger = ledger
        self._logger = logger
        self._reporter = reporter
        self._run_id = run_id
        self._decorate = decorate
        self._log_observation = log_observation
        self._log_world_state_update = log_world_state_update
        # Read through a provider rather than held: a run swaps its store, and
        # a recorder holding a stale one silently stops publishing.
        self._store = state_store

    @staticmethod
    def _vendor_candidates(
        snapshot: TelemetrySnapshot,
    ) -> dict[tuple[str, str | None], NearbyEntity]:
        return {
            (entity.name, entity.faction): entity
            for entity in snapshot.nearby_entities
            if entity.is_confirmed_vendor()
        }

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

    def record_action_outcome(
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
            run_id=self._run_id,
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
        self._logger.write("action_outcome", step_index=before.step_index, payload=outcome)

    def record_transition(
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
        store = self._store()
        candidate = self._decorate(transition.observation)
        update: StoreUpdate | None = None
        if store is None:
            latest = candidate
        elif (
            store.latest is not None
            and candidate.world_revision == store.latest.world_revision
        ):
            latest = store.latest
        else:
            try:
                update = store.publish(candidate)
            except WorldStateError as exc:
                self._logger.write(
                    "observation_rejected",
                    step_index=candidate.step_index,
                    payload={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "world_revision": candidate.world_revision.model_dump(mode="json"),
                    },
                )
                latest = store.latest or before
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
        self._logger.write(
            "action_receipt",
            step_index=before.step_index,
            payload=receipt,
        )
        if self._reporter is not None:
            self._reporter.action_receipt(
                step_index=before.step_index,
                receipt=receipt,
            )
        self.record_action_outcome(
            decision,
            receipt,
            before,
            latest,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id or f"step-{before.step_index}",
            command_id=command_id,
        )
        latest = self._decorate(latest)
        if store is None:
            self._log_observation(latest)
        else:
            latest = store.decorate_latest(latest)
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

    def record_plan_outcome(
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
        self._logger.write(
            "plan_outcome",
            step_index=observation.step_index,
            payload=outcome,
        )

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
        changed(
            "selected characters",
            sorted(before.ui.selected_character_ids),
            sorted(after.ui.selected_character_ids),
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

    @classmethod
    def _telemetry_changes(
        cls,
        before: TelemetrySnapshot | None,
        after: TelemetrySnapshot | None,
    ) -> list[str]:
        return [change.label for change in cls._telemetry_changes_detailed(before, after)]

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

    def observe_plan_transition(
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
        return self.record_transition(
            decision,
            before,
            transition,
            command_id=command_id,
            action_start_revision=action_start_revision,
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            step_id=step.step_id,
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
