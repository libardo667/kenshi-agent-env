from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from operation_test_support import operation_port, plan_executor

from kenshi_agent.config import PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.live_plan_policy import live_plan_policy_errors
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    CharacterState,
    CollectResourceOutputAction,
    CommandDispatchContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ContextActionKind,
    ControlMode,
    DismissScreenAction,
    GameBinding,
    GameState,
    HarvestResourceAction,
    IdempotencyPolicy,
    InventoryItem,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NormalizedPointerBounds,
    Observation,
    OpenContextInventoryAction,
    PlanEnvelope,
    PlanningMode,
    PlanStep,
    ProduceResourceOutputAction,
    ResourceHarvestStatus,
    ResourceTransferEvidence,
    ResourceTransferStatus,
    RiskBudget,
    SemanticActionReceipt,
    SetSpeedAction,
    TelemetrySnapshot,
    Transition,
    UIState,
    UseGameBindingAction,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
)
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.world_state import WorldStateStore

RUN_ID = "resource-harvest-option"
ACTOR_ID = "entity-bark"
TARGET_ID = "entity-iron"
ITEM_NAME = "Raw Iron"
TARGET_WINDOW = "IRON RESOURCE"
ACTOR_WINDOW = "BARK"


class FakeClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


def _revision(sequence: int) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        capability_epoch=1,
        observed_at_monotonic=float(sequence),
    )


def _bounds(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
    )


class HarvestEnvironment(AgentEnvironment):
    """A deterministic controller fixture for one complete semantic harvest."""

    def __init__(self) -> None:
        self.sequence = 1
        self.phase = "world"
        self.paused = True
        self.speed_multiplier = 0.0
        self.source_quantity = 5
        self.destination_quantity = 0
        self.actions: list[Action] = []
        self.control_mode = ControlMode.NATIVE_ASSISTED
        self.store: WorldStateStore | None = None

    def observation(self) -> Observation:
        source_open = self.phase in {"source", "both", "transferred"}
        actor_open = self.phase in {"both", "transferred", "actor"}
        open_windows = int(source_open) + int(actor_open)
        controls: list[VisibleUIControl] = []
        if source_open:
            controls.extend(
                [
                    VisibleUIControl(
                        label=TARGET_WINDOW,
                        role="text",
                        window=TARGET_WINDOW,
                        bounds=_bounds(0.05, 0.10, 0.45, 0.80),
                    ),
                    VisibleUIControl(
                        label="Raw Iron 0",
                        role="item",
                        window=TARGET_WINDOW,
                        section="out",
                        item_name=ITEM_NAME,
                        item_quantity=self.source_quantity,
                        selected_inventory_accepts_item=True,
                        bounds=_bounds(0.10, 0.20, 0.18, 0.28),
                    ),
                ]
            )
        if actor_open:
            controls.append(
                VisibleUIControl(
                    label=ACTOR_WINDOW,
                    role="text",
                    window=ACTOR_WINDOW,
                    bounds=_bounds(0.55, 0.10, 0.95, 0.80),
                )
            )
        active_screen = (
            "world"
            if open_windows == 0
            else "trade"
            if open_windows == 2
            else "inventory"
        )
        return Observation(
            run_id=RUN_ID,
            step_index=self.sequence,
            mode="live",
            control_mode=self.control_mode,
            planning_mode=PlanningMode.CONTINUOUS,
            world_revision=_revision(self.sequence),
            telemetry_age_seconds=0.0,
            telemetry=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                protocol_version="1.2.0",
                identity_session_id="session-harvest",
                capabilities=[
                    "game.pause",
                    "game.speed",
                    "squad.basic",
                    "squad.health",
                    "squad.inventory",
                    "ui.inventory",
                    "ui.visible_controls",
                    "ui.context_inventory_target",
                    "world.context_targets",
                    "control.produce_resource_output",
                    "control.open_context_inventory",
                    "identity.stable_handles",
                ],
                active_shop_trader_count=0,
                game=GameState(
                    loaded=True,
                    paused=self.paused,
                    speed_multiplier=self.speed_multiplier,
                    elapsed_minutes=1.0,
                ),
                ui=UIState(
                    active_screen=active_screen,
                    modal_open=open_windows > 0,
                    dialogue_open=False,
                    open_inventory_windows=open_windows,
                    selected_character_id=ACTOR_ID,
                    selected_character_ids=[ACTOR_ID],
                    context_inventory_target_id=TARGET_ID if source_open else None,
                    visible_controls_complete=True,
                    visible_controls=controls,
                ),
                squad=[
                    CharacterState(
                        id=ACTOR_ID,
                        name="Bark",
                        selected=True,
                        alive=True,
                        conscious=True,
                        down=False,
                        in_combat=False,
                        inventory_complete=True,
                        inventory=(
                            [
                                InventoryItem(
                                    name=ITEM_NAME,
                                    quantity=self.destination_quantity,
                                )
                            ]
                            if self.destination_quantity
                            else []
                        ),
                    )
                ],
                world_targets=[
                    WorldTarget(
                        id=TARGET_ID,
                        name="Iron Resource",
                        kind="natural_resource",
                        position=Vec3(x=1.0, y=0.0, z=1.0),
                        distance=10.0,
                        context_actions=[ContextActionKind.OPERATE],
                        default_task="operate_machinery",
                        mining_resource_level=1.0,
                    )
                ],
            ),
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
                command_id="cmd-" + ("f" * 32),
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
        before = self.observation()
        self.actions.append(action)
        native: NativeCommandAcknowledgement | None = None
        transfer: ResourceTransferEvidence | None = None

        if isinstance(action, SetSpeedAction) and action.speed == 3:
            assert self.phase == "world"
            self.paused = False
            self.speed_multiplier = 5.0
        elif isinstance(action, SetSpeedAction) and action.speed == 1:
            assert self.phase == "world"
            self.speed_multiplier = 1.0
        elif isinstance(action, ProduceResourceOutputAction):
            assert self.phase == "world"
            assert self.paused is False
            assert self.speed_multiplier == 5.0
            assert action.target_id == TARGET_ID
            assert action.minimum_output_quantity == 5
            native = NativeCommandAcknowledgement(
                command_id=command.command_id,
                command=action.kind,
                status=NativeCommandStatus.COMPLETED,
                reason="resource_output_ready",
                target_id=TARGET_ID,
                selected_character_ids=[ACTOR_ID],
                based_on_telemetry_sequence=self.sequence,
                acknowledged_at_telemetry_sequence=self.sequence + 1,
                accepted_at_telemetry_sequence=self.sequence + 1,
                terminal_at_telemetry_sequence=self.sequence + 1,
                minimum_output_quantity=5,
            )
        elif isinstance(action, OpenContextInventoryAction):
            assert self.phase == "world"
            assert action.target_id == TARGET_ID
            self.phase = "source"
            native = NativeCommandAcknowledgement(
                command_id=command.command_id,
                command=action.kind,
                status=NativeCommandStatus.COMPLETED,
                reason="exact_context_inventory_open",
                target_id=TARGET_ID,
                selected_character_ids=[ACTOR_ID],
                based_on_telemetry_sequence=self.sequence,
                acknowledged_at_telemetry_sequence=self.sequence + 1,
                accepted_at_telemetry_sequence=self.sequence + 1,
                terminal_at_telemetry_sequence=self.sequence + 1,
            )
        elif isinstance(action, UseGameBindingAction):
            assert self.phase == "source"
            assert action.binding is GameBinding.TOGGLE_INVENTORY
            self.phase = "both"
        elif isinstance(action, CollectResourceOutputAction):
            assert self.phase in {"both", "transferred"}
            assert (
                action.target_id,
                action.cell_label,
                action.item_name,
                action.source_quantity,
                action.window,
                action.section,
            ) == (
                TARGET_ID,
                "Raw Iron 0",
                ITEM_NAME,
                self.source_quantity,
                TARGET_WINDOW,
                "out",
            )
            source_before = self.source_quantity
            destination_before = self.destination_quantity
            self.source_quantity -= 1
            self.destination_quantity += 1
            if self.destination_quantity == 5:
                self.phase = "transferred"
            transfer = ResourceTransferEvidence(
                status=ResourceTransferStatus.TRANSFERRED,
                target_id=TARGET_ID,
                selected_character_id=ACTOR_ID,
                item_name=ITEM_NAME,
                source_quantity_before=source_before,
                source_quantity_after=self.source_quantity,
                destination_quantity_before=destination_before,
                destination_quantity_after=self.destination_quantity,
                observed_after_sequence=self.sequence + 1,
                reason="Conserved one unit into Bark.",
            )
        elif isinstance(action, DismissScreenAction):
            if self.phase == "transferred":
                assert action.window == TARGET_WINDOW
                self.phase = "actor"
            elif self.phase == "actor":
                assert action.window == ACTOR_WINDOW
                self.phase = "world_done"
            else:
                raise AssertionError(f"unexpected dismissal in {self.phase}")
        else:
            raise AssertionError(f"planner motor primitive leaked into fixture: {action}")

        self.sequence += 1
        after = self.observation()
        if isinstance(action, ProduceResourceOutputAction) and self.store is not None:
            # The live observation pump advances while the native option works.
            # Its current state owns truth; the older dispatch transition must
            # never be republished over it when production becomes terminal.
            self.sequence += 1
            self.store.publish(self.observation())
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version="fixture",
            target_id=TARGET_ID,
            source_revision=before.world_revision,
            revalidation="Fixture revalidated the exact phase.",
            resource_transfer=transfer,
        )
        primitive_actions = (
            2
            if isinstance(action, SetSpeedAction) and action.speed == 3
            else 6
            if isinstance(
                action,
                (ProduceResourceOutputAction, OpenContextInventoryAction),
            )
            else 4
            if isinstance(action, CollectResourceOutputAction)
            else 3
            if isinstance(action, DismissScreenAction)
            else 1
        )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=self.control_mode,
                command_id=command.command_id,
                started_after_revision=before.world_revision,
                completed_at_revision=after.world_revision,
                causal_revision_advanced=True,
                native_acknowledgement=native,
                semantic=semantic,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=primitive_actions,
            ),
            observation=after,
        )

    async def close(self) -> None:
        return None


def _fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def test_harvest_is_one_planner_action_with_controller_owned_transfer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = HarvestEnvironment()
        observation = await environment.reset()
        action = HarvestResourceAction(
            actor_id=ACTOR_ID,
            target_id=TARGET_ID,
            quantity=5,
        )
        plan = PlanEnvelope(
            schema_version="1.0",
            plan_id="harvest-iron",
            objective="Harvest five iron into Bark's inventory.",
            control_mode=ControlMode.NATIVE_ASSISTED,
            based_on_revision=observation.world_revision,
            assumptions=[_fresh()],
            steps=[
                PlanStep(
                    step_id="harvest",
                    action=action,
                    preconditions=[_fresh()],
                    success_conditions=[],
                    timeout_seconds=300.0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                )
            ],
            entry_step_id="harvest",
            max_actions=1,
            max_wall_seconds=360.0,
            max_game_seconds=3600.0,
            risk_budget=RiskBudget(
                max_pointer_actions=12,
                max_purchase_actions=0,
                max_native_assisted_actions=2,
            ),
        )
        assert live_plan_policy_errors(
            plan,
            observation,
            max_steps=1,
        ) == []

        store = WorldStateStore(clock=clock)
        store.publish(observation)
        environment.store = store
        log_path = tmp_path / "harvest.jsonl"
        logger = SessionLogger(log_path, RUN_ID)
        observed_transitions: list[Transition] = []

        def observe_transition(
            plan: PlanEnvelope,
            step: PlanStep,
            before: Observation,
            transition: Transition,
            command_id: str,
            action_start_revision: WorldStateRevision,
        ) -> Observation:
            del plan, step, before, command_id, action_start_revision
            observed_transitions.append(transition.model_copy(deep=True))
            store.publish(transition.observation)
            return transition.observation

        executor = plan_executor(
            environment=environment,
            operation_port=operation_port(environment),
            guard=ActionGuard(
                SafetyConfig(
                    allow_action_kinds=[
                        "harvest_resource",
                        "produce_resource_output",
                        "open_context_inventory",
                        "set_speed",
                        "use_game_binding",
                        "collect_resource_output",
                        "dismiss_screen",
                    ],
                    allow_live_unpause_actions=True,
                    max_actions_per_minute=100,
                    max_controller_verified_primitive_actions_per_step=45,
                ),
                MacroRegistry({}),
                control_mode=ControlMode.NATIVE_ASSISTED,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            clock=clock,
            state_store=store,
            observe_transition=observe_transition,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                max_plan_wall_seconds=360.0,
                max_native_assisted_actions_per_plan=2,
                require_paused_between_actions=False,
            ),
        )
        try:
            result = await executor.execute(
                plan,
                observation,
                remaining_run_actions=1,
            )
        finally:
            logger.close()

        assert result.completed, (
            result.reason,
            [item.model_dump(mode="json") for item in environment.actions],
        )
        assert result.actions_completed == 1
        assert result.observation.telemetry is not None
        assert result.observation.telemetry.ui.open_inventory_windows == 0
        assert result.observation.telemetry.squad[0].inventory == [
            InventoryItem(name=ITEM_NAME, quantity=5)
        ]
        assert [item.kind for item in environment.actions] == [
            "set_speed",
            "produce_resource_output",
            "set_speed",
            "open_context_inventory",
            "use_game_binding",
            "collect_resource_output",
            "collect_resource_output",
            "collect_resource_output",
            "collect_resource_output",
            "collect_resource_output",
            "dismiss_screen",
            "dismiss_screen",
        ]
        assert [
            item.binding
            for item in environment.actions
            if isinstance(item, UseGameBindingAction)
        ] == [
            GameBinding.TOGGLE_INVENTORY,
        ]
        assert environment.speed_multiplier == 1.0
        assert len(observed_transitions) == 1
        outer_receipt = observed_transitions[0].receipt
        assert outer_receipt.primitive_actions == 42
        assert outer_receipt.action == action
        assert outer_receipt.semantic is not None
        assert outer_receipt.semantic.resource_harvest is not None
        assert (
            outer_receipt.semantic.resource_harvest.status
            is ResourceHarvestStatus.HARVESTED
        )

    asyncio.run(scenario())
