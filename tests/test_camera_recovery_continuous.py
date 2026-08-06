from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from operation_test_support import operation_port, plan_executor

from kenshi_agent.config import PlanningConfig, SafetyConfig
from kenshi_agent.core.evidence import (
    CameraFrameScore,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    ResourceTransferEvidence,
    ResourceTransferStatus,
    SemanticActionReceipt,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    CollectResourceOutputAction,
    ControlMode,
    IdempotencyPolicy,
    OpenContextInventoryAction,
    RecoverCameraViewAction,
)
from kenshi_agent.core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)
from kenshi_agent.core.telemetry import (
    CameraState,
    CharacterState,
    GameState,
    InventoryItem,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.env.base import AgentEnvironment
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.live_plan_policy import live_plan_policy_errors
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety import OperationPolicy
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.world_state import WorldStateStore


class FakeClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class CameraVerdictEnvironment(AgentEnvironment):
    def __init__(self, status: CameraRecoveryStatus, tmp_path: Path) -> None:
        self.status = status
        self.tmp_path = tmp_path
        self.sequence = 1
        self.step_index = 0
        self.control_mode = ControlMode.INTERFACE_ONLY

    def observation(self) -> Observation:
        bounds = NormalizedPointerBounds(
            min_x=0.30, min_y=0.84, max_x=0.38, max_y=0.95
        )
        return Observation(
            run_id="camera-continuous",
            step_index=self.step_index,
            mode="live",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=self.sequence,
                frame_sequence=self.sequence,
                capability_epoch=1,
                observed_at_monotonic=float(self.sequence),
            ),
            telemetry=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                capabilities=[
                    "camera.position",
                    "camera.recovery",
                    "game.pause",
                    "game.time",
                    "squad.basic",
                    "ui.visible_controls",
                ],
                game=GameState(loaded=True, paused=True, elapsed_minutes=0.0),
                camera=CameraState(
                    center=Vec3(x=0.0, y=0.0, z=0.0),
                    position=Vec3(x=0.0, y=20.0, z=0.0),
                ),
                ui=UIState(
                    active_screen="world",
                    modal_open=False,
                    dialogue_open=False,
                    selected_character_id="char-hep",
                    selected_character_ids=["char-hep"],
                    visible_controls=[
                        VisibleUIControl(label="Hep", role="text", bounds=bounds),
                        VisibleUIControl(
                            label="[Hep]",
                            role="text",
                            bounds=NormalizedPointerBounds(
                                min_x=0.45, min_y=0.34, max_x=0.50, max_y=0.38
                            ),
                        ),
                        VisibleUIControl(
                            label="Floor 0",
                            role="text",
                            bounds=NormalizedPointerBounds(
                                min_x=0.70, min_y=0.69, max_x=0.74, max_y=0.72
                            ),
                        ),
                        VisibleUIControl(
                            label="hud_FloorArrowUp",
                            role="button",
                            bounds=NormalizedPointerBounds(
                                min_x=0.70, min_y=0.66, max_x=0.73, max_y=0.69
                            ),
                        ),
                        VisibleUIControl(
                            label="hud_FloorArrowDown",
                            role="button",
                            bounds=NormalizedPointerBounds(
                                min_x=0.70, min_y=0.72, max_x=0.73, max_y=0.75
                            ),
                        ),
                    ],
                ),
                squad=[
                    CharacterState(
                        id="char-hep",
                        name="Hep",
                        selected=True,
                        position=Vec3(x=0.0, y=0.0, z=0.0),
                    )
                ],
            ),
            telemetry_stale=False,
            telemetry_age_seconds=0.0,
        )

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return self.observation()

    async def observe(self) -> Observation:
        return self.observation()

    async def step(self, action: Action) -> Transition:
        return await self.dispatch(
            action,
            command=CommandDispatchContext(
                command_id="cmd-" + ("0" * 32),
                based_on_revision=self.observation().world_revision,
            ),
        )

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        del token
        assert isinstance(action, RecoverCameraViewAction)
        before = self.observation().world_revision
        self.sequence += 1
        self.step_index += 1
        clear = self.status is not CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS
        candidate = CameraFrameScore(
            candidate="controller_candidate",
            screenshot_path=self.tmp_path / "candidate.png",
            screenshot_sha256="0" * 64,
            telemetry_sequence=self.sequence,
            frame_sequence=self.sequence,
            floor=0,
            score=0.90 if clear else 0.25,
            edge_density=0.9 if clear else 0.1,
            contrast=0.9 if clear else 0.1,
            color_diversity=0.9 if clear else 0.1,
            nonflat_fraction=0.9 if clear else 0.1,
            inverse_dominant_color=0.9 if clear else 0.1,
            selected_world_label_visible=True,
            anchor_distance=0.0,
            clear=clear,
        )
        evidence = CameraRecoveryEvidence(
            status=self.status,
            selected_character_id="char-hep",
            selected_character_name="Hep",
            initial_floor=0,
            final_floor=0,
            clear_score_threshold=0.72,
            anchor_max_distance=30.0,
            paused_for_recovery=False,
            primitive_actions=0 if self.status is CameraRecoveryStatus.ALREADY_CLEAR else 4,
            follow_method=(
                "already_anchored"
                if self.status is CameraRecoveryStatus.ALREADY_CLEAR
                else "portrait_double_click"
            ),
            chosen_candidate=candidate.candidate,
            candidates=[candidate],
        )
        after = self.observation()
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=self.control_mode,
                command_id=command.command_id,
                started_after_revision=before,
                completed_at_revision=after.world_revision,
                causal_revision_advanced=True,
                semantic=SemanticActionReceipt(
                    action_kind=action.kind,
                    contract_version="1.0",
                    target_id="char-hep",
                    resolved_label="Hep",
                    source_revision=before,
                    revalidation="Controller test receipt.",
                    camera_recovery=evidence,
                ),
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=evidence.primitive_actions,
            ),
            observation=after,
        )

    async def close(self) -> None:
        return None


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def camera_plan(observation: Observation) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="camera-controller-verdict",
        plan_version=1,
        objective="Recover the camera without model-authored gestures.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="recover",
                action=RecoverCameraViewAction(),
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=30.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="recover",
        max_actions=1,
        max_wall_seconds=60.0,
        max_game_seconds=60.0,
        risk_budget=RiskBudget(
            max_pointer_actions=1,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


class ResourceVerdictEnvironment(CameraVerdictEnvironment):
    def __init__(
        self,
        action: OpenContextInventoryAction | CollectResourceOutputAction,
        *,
        native_reason: str = "exact_context_inventory_open",
        transfer_status: ResourceTransferStatus = ResourceTransferStatus.TRANSFERRED,
        tmp_path: Path,
    ) -> None:
        super().__init__(CameraRecoveryStatus.ALREADY_CLEAR, tmp_path)
        self.action = action
        self.native_reason = native_reason
        self.transfer_status = transfer_status
        self.control_mode = ControlMode.NATIVE_ASSISTED

    def observation(self) -> Observation:
        observation = super().observation()
        assert observation.telemetry is not None
        collecting = isinstance(self.action, CollectResourceOutputAction)
        ui = observation.telemetry.ui.model_copy(
            update={
                "active_screen": "trade" if collecting else "world",
                "modal_open": collecting,
                "open_inventory_windows": 2 if collecting else 0,
                "context_inventory_target_id": (
                    "entity-copper" if collecting else None
                ),
                "visible_controls_complete": True,
                "visible_controls": (
                    [
                        VisibleUIControl(
                            label="Raw Iron 0",
                            window="COPPER RESOURCE",
                            role="item",
                            item_name="Raw Iron",
                            item_quantity=2,
                            section="out",
                            bounds=NormalizedPointerBounds(
                                min_x=0.30,
                                max_x=0.36,
                                min_y=0.40,
                                max_y=0.48,
                            ),
                        ),
                        VisibleUIControl(
                            label="HEP",
                            window="HEP",
                            role="text",
                            bounds=NormalizedPointerBounds(
                                min_x=0.60,
                                max_x=0.90,
                                min_y=0.20,
                                max_y=0.80,
                            ),
                        ),
                    ]
                    if collecting
                    else []
                ),
            }
        )
        telemetry = observation.telemetry.model_copy(
            update={
                "capabilities": [
                    "control.open_context_inventory",
                    "identity.stable_handles",
                    "squad.inventory",
                    "ui.context_inventory_target",
                    "ui.inventory",
                    "ui.visible_controls",
                    "world.context_targets",
                ],
                "active_shop_trader_count": 0,
                "ui": ui,
                "squad": [
                    CharacterState(
                        id="char-hep",
                        name="Hep",
                        selected=True,
                        inventory_complete=True,
                        inventory=[
                            InventoryItem(
                                name="Raw Iron",
                                item_name="Raw Iron",
                                item_quantity=2,
                                section="main",
                            )
                        ],
                    )
                ],
                "world_targets": [
                    WorldTarget(
                        id="entity-copper",
                        name="Copper Resource",
                        kind="natural_resource",
                        position=Vec3(x=10.0, y=0.0, z=20.0),
                        distance=30.0,
                        context_actions=["operate"],
                        default_task="operate_machinery",
                    )
                ],
            }
        )
        return observation.model_copy(
            update={
                "control_mode": self.control_mode,
                "telemetry": telemetry,
            },
            deep=True,
        )

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        del token
        assert action == self.action
        before = self.observation()
        self.sequence += 1
        after = self.observation()
        acknowledgement = None
        transfer = None
        if isinstance(action, OpenContextInventoryAction):
            acknowledgement = NativeCommandAcknowledgement(
                command_id=command.command_id,
                command="open_context_inventory",
                status=NativeCommandStatus.COMPLETED,
                reason=self.native_reason,
                target_id=action.target_id,
                selected_character_ids=["char-hep"],
                based_on_telemetry_sequence=before.world_revision.telemetry_sequence,
                acknowledged_at_telemetry_sequence=after.world_revision.telemetry_sequence
                or 0,
                accepted_at_telemetry_sequence=after.world_revision.telemetry_sequence,
                terminal_at_telemetry_sequence=after.world_revision.telemetry_sequence,
            )
        else:
            transfer = ResourceTransferEvidence(
                status=self.transfer_status,
                target_id=action.target_id,
                selected_character_id="char-hep",
                item_name=action.item_name,
                source_quantity_before=2,
                source_quantity_after=0,
                destination_quantity_before=0,
                destination_quantity_after=(
                    2 if self.transfer_status is ResourceTransferStatus.TRANSFERRED else 0
                ),
                observed_after_sequence=after.world_revision.telemetry_sequence,
                reason="Test controller verdict.",
            )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=self.control_mode,
                command_id=command.command_id,
                started_after_revision=before.world_revision,
                completed_at_revision=after.world_revision,
                causal_revision_advanced=True,
                semantic=SemanticActionReceipt(
                    action_kind=action.kind,
                    contract_version="1.0",
                    target_id=action.target_id,
                    revalidation="Test receipt.",
                    resource_transfer=transfer,
                ),
                native_acknowledgement=acknowledgement,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=1,
            ),
            observation=after,
        )


def resource_plan(
    observation: Observation,
    action: OpenContextInventoryAction | CollectResourceOutputAction,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="resource-controller-verdict",
        plan_version=1,
        objective="Accept only typed resource terminals.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="resource",
                action=action,
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=30.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="resource",
        max_actions=1,
        max_wall_seconds=60.0,
        max_game_seconds=60.0,
        risk_budget=RiskBudget(
            max_pointer_actions=1,
            max_purchase_actions=0,
            max_native_assisted_actions=1,
        ),
    )


@pytest.mark.parametrize(
    ("status", "expected_completed"),
    [
        (CameraRecoveryStatus.ALREADY_CLEAR, True),
        (CameraRecoveryStatus.RECOVERED, True),
        (CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS, False),
    ],
)
def test_continuous_executor_uses_controller_verdict_without_postconditions(
    tmp_path: Path,
    status: CameraRecoveryStatus,
    expected_completed: bool,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = CameraVerdictEnvironment(status, tmp_path)
        observation = await environment.reset()
        plan = camera_plan(observation)
        assert live_plan_policy_errors(plan) == []

        store = WorldStateStore(clock=clock)
        store.publish(observation)
        logger = SessionLogger(tmp_path / f"{status.value}.jsonl", "camera-continuous")

        def observe_transition(
            plan: PlanEnvelope,
            step: PlanStep,
            before: Observation,
            transition: Transition,
            command_id: str,
            action_start_revision: WorldStateRevision,
        ) -> Observation:
            del plan, step, before, command_id, action_start_revision
            store.publish(transition.observation)
            return transition.observation

        executor = plan_executor(
            environment=environment,
            operation_port=operation_port(environment),
            policy=OperationPolicy(
                SafetyConfig(
                    allow_action_kinds=["recover_camera_view"],
                    max_actions_per_minute=100,
                ),
                control_mode=ControlMode.INTERFACE_ONLY,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            clock=clock,
            state_store=store,
            observe_transition=observe_transition,
            planning_config=PlanningConfig(),
        )
        try:
            result = await executor.execute(
                plan,
                observation,
                remaining_run_actions=1,
            )
        finally:
            logger.close()

        assert result.actions_completed == 1
        assert result.completed is expected_completed
        if expected_completed:
            assert result.reason == "Plan completed."
            assert status.value in (tmp_path / f"{status.value}.jsonl").read_text(
                encoding="utf-8"
            )
        else:
            assert status.value in result.reason

    asyncio.run(scenario())
