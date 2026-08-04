from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PIL import Image, ImageDraw, ImageFont

from ..affordances import OPERATION_BINDING_AUTHORITY
from ..camera_recovery import score_camera_observation
from ..config import MockConfig
from ..models import (
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ActionReceipt,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    CameraState,
    CharacterState,
    CommandDispatchContext,
    ControlMode,
    Disposition,
    GameState,
    InventoryItem,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PauseAction,
    RecoverCameraViewAction,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    TelemetrySnapshot,
    Transition,
    UIState,
    Vec3,
    VisibleUIControl,
    WaitAction,
    WorldStateRevision,
)
from ..operation_definitions import (
    BindingFailure,
    BoundActor,
    BoundCameraRecovery,
    BoundVisibleControl,
    definition_for,
)
from .base import AgentEnvironment

if TYPE_CHECKING:
    from ..input_boundary import ExecutionToken


@dataclass(slots=True)
class MockWorld:
    location: str
    cats: int
    hunger: float
    food_items: int
    first_aid_kits: int
    health: float = 100.0
    bleeding_rate: float = 0.0
    elapsed_minutes: float = 0.0
    paused: bool = True
    speed: float = 1.0
    alive: bool = True
    conscious: bool = True
    hostile_nearby: bool = False
    distance_to_hostile: float | None = None
    treated_injuries: int = 0
    travel_progress: float = 0.0


class MockOperationPort:
    """Exact operation surface for the deterministic mock adapter."""

    def __init__(self, environment: MockEnvironment) -> None:
        self._environment = environment

    async def pause(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        typed = cast(PauseAction, action)
        self._environment.world.paused = typed.paused
        return await self._finish(
            typed,
            command,
            message=f"Mock game paused={typed.paused}.",
        )

    async def control_pause(
        self,
        action: PauseAction,
        *,
        command: CommandDispatchContext,
    ) -> Transition:
        return await self.pause(action, command=command, token=None)

    async def set_speed(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        typed = cast(SetSpeedAction, action)
        self._environment.world.paused = False
        self._environment.world.speed = GAME_SPEED_MULTIPLIER_BY_GEAR[typed.speed]
        return await self._finish(
            typed,
            command,
            message=(
                f"Mock playback started at speed gear {typed.speed} "
                f"({self._environment.world.speed:g}x)."
            ),
        )

    async def wait(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        typed = cast(WaitAction, action)
        await asyncio.sleep(0)
        self._environment._advance_time(
            typed.seconds * self._environment.config.minutes_per_wait_second
        )
        return await self._finish(
            typed,
            command,
            message=f"Advanced mock time by {typed.seconds:.2f} minutes.",
        )

    async def skill(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        typed = cast(SkillAction, action)
        return await self._finish(
            typed,
            command,
            message=self._environment._apply_skill(typed),
        )

    async def approach_dialogue_target(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        return await self._semantic(action, command=command, token=token)

    async def activate_visible_control(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        return await self._semantic(action, command=command, token=token)

    async def recover_camera_view(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        return await self._semantic(action, command=command, token=token)

    async def _semantic(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        typed = cast(
            ApproachDialogueTargetAction
            | ActivateVisibleControlAction
            | RecoverCameraViewAction,
            action,
        )
        message, semantic = self._environment._apply_semantic_action(typed)
        return await self._finish(
            typed,
            command,
            message=message,
            primitive_actions=0 if isinstance(typed, RecoverCameraViewAction) else 1,
            semantic=semantic,
        )

    async def _record(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        return await self._finish(
            action,
            command,
            message=f"Recorded UI primitive {action.kind}; mock world state did not change.",
        )

    async def _finish(
        self,
        action: Action,
        command: CommandDispatchContext,
        *,
        message: str,
        primitive_actions: int = 1,
        semantic: SemanticActionReceipt | None = None,
    ) -> Transition:
        started = datetime.now(UTC)
        environment = self._environment
        environment._step_index += 1
        terminated = False
        success: bool | None = None
        if not environment.world.alive:
            terminated = True
            success = False
            environment._events.append("The mock character died.")
        elif environment.world.elapsed_minutes >= 1440:
            terminated = True
            success = True
            environment._events.append("The mock character survived one in-game day.")
        observation = await environment.observe()
        receipt = ActionReceipt(
            action=action,
            control_mode=environment.control_mode,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_actions,
            message=message,
            semantic=semantic,
            command_id=command.command_id,
            started_after_revision=command.based_on_revision,
            completed_at_revision=observation.world_revision,
            causal_revision_advanced=observation.world_revision.is_later_than(
                command.based_on_revision
            ),
        )
        return Transition(
            receipt=receipt,
            observation=observation,
            terminated=terminated,
            success=success,
            events=observation.events,
        )

    collect_resource_output = _record
    command_world_target = _record
    dismiss_screen = _record
    equip_item = _record
    exit_current_building = _record
    move_in_direction = _record
    move_to_character = _record
    open_context_inventory = _record
    open_screen = _record
    perform_context_action = _record
    produce_resource_output = _record
    purchase_item = _record
    regroup_with_squad_member = _record
    respond_to_immediate_threat = _record
    rotate_camera = _record
    scroll_screen = _record
    select_squad_member = _record
    select_squad_member_exact = _record
    sell_item = _record
    travel_to_map_destination = _record
    use_game_binding = _record


class MockEnvironment(AgentEnvironment):
    """Small deterministic world for testing the full agent loop off-game."""

    def __init__(
        self,
        config: MockConfig,
        run_dir: Path,
        run_id: str,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_id = run_id
        self.control_mode = control_mode
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._rng = random.Random(config.seed)
        self._step_index = 0
        self._sequence = 0
        self._frame_sequence = 0
        self._events: list[str] = []
        self._last_observation: Observation | None = None
        self.world = self._new_world()
        self._mechanics = MockOperationPort(self)

    @property
    def operation_mechanics(self) -> MockOperationPort:
        return self._mechanics

    def _new_world(self) -> MockWorld:
        return MockWorld(
            location=self.config.start_location,
            cats=self.config.start_cats,
            hunger=self.config.start_hunger,
            food_items=self.config.start_food_items,
            first_aid_kits=self.config.start_first_aid_kits,
        )

    async def reset(self, *, seed: int | None = None) -> Observation:
        if seed is not None:
            self._rng.seed(seed)
        else:
            self._rng.seed(self.config.seed)
        self._step_index = 0
        self._sequence = 0
        self._frame_sequence = 0
        self._events = ["Episode reset in the mock world."]
        self.world = self._new_world()
        return await self.observe()

    async def observe(self) -> Observation:
        return await self._observe(capture=True)

    async def observe_without_capture(self) -> Observation:
        return await self._observe(capture=False)

    async def _observe(self, *, capture: bool) -> Observation:
        self._sequence += 1
        screenshot = None
        screenshot_hash = None
        if capture:
            self._frame_sequence += 1
            screenshot = self._render_screenshot()
            screenshot_hash = hashlib.sha256(screenshot.read_bytes()).hexdigest()
        nearby: list[NearbyEntity] = []
        if self.world.hostile_nearby:
            nearby.append(
                NearbyEntity(
                    id="mock:hungry-bandit",
                    name="Hungry Bandit",
                    kind="character",
                    faction="Hungry Bandits",
                    disposition=Disposition.HOSTILE,
                    distance=self.world.distance_to_hostile,
                    visible=True,
                    conscious=True,
                )
            )
        day = int(self.world.elapsed_minutes // 1440) + 1
        minute_of_day = int(self.world.elapsed_minutes % 1440)
        telemetry = TelemetrySnapshot(
            sequence=self._sequence,
            captured_at=datetime.now(UTC),
            source="mock",
            capabilities=[
                "camera.position",
                "camera.recovery",
                "game.pause",
                "game.speed",
                "game.time",
                "game.money",
                "game.location",
                "squad.basic",
                "squad.current_goal",
                "squad.hunger",
                "squad.health",
                "nearby.visible_entities",
                "ui.visible_controls",
            ],
            game=GameState(
                loaded=True,
                paused=self.world.paused,
                speed_multiplier=float(self.world.speed),
                day=day,
                hour=minute_of_day // 60,
                minute=minute_of_day % 60,
                elapsed_minutes=self.world.elapsed_minutes,
                money=self.world.cats,
                location_name=self.world.location,
            ),
            camera=CameraState(
                position=Vec3(x=0.0, y=22.0, z=0.0),
                center=Vec3(x=self.world.travel_progress, y=0.0, z=0.0),
            ),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="mock:wanderer",
                selected_character_ids=["mock:wanderer"],
                client_width=1280,
                client_height=720,
                visible_controls=[
                    VisibleUIControl(
                        label="Wanderer",
                        role="text",
                        bounds=NormalizedPointerBounds(
                            min_x=0.30, min_y=0.84, max_x=0.38, max_y=0.95
                        ),
                    ),
                    VisibleUIControl(
                        label="[Wanderer]",
                        role="text",
                        bounds=NormalizedPointerBounds(
                            min_x=0.47, min_y=0.45, max_x=0.55, max_y=0.49
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
                        label="mock_FloorArrowUp",
                        role="button",
                        bounds=NormalizedPointerBounds(
                            min_x=0.70, min_y=0.66, max_x=0.73, max_y=0.69
                        ),
                    ),
                    VisibleUIControl(
                        label="mock_FloorArrowDown",
                        role="button",
                        bounds=NormalizedPointerBounds(
                            min_x=0.70, min_y=0.72, max_x=0.73, max_y=0.75
                        ),
                    ),
                ],
            ),
            squad=[
                CharacterState(
                    id="mock:wanderer",
                    name="Wanderer",
                    selected=True,
                    alive=self.world.alive,
                    conscious=self.world.conscious,
                    down=not self.world.conscious,
                    in_combat=self.world.hostile_nearby,
                    position=Vec3(x=self.world.travel_progress, y=0.0, z=0.0),
                    movement_speed=18.0 if self.world.conscious else 0.0,
                    hunger=self.world.hunger,
                    bleeding_rate=self.world.bleeding_rate,
                    food_items=self.world.food_items,
                    first_aid_kits=self.world.first_aid_kits,
                    current_goal="Survive one day",
                    inventory=[
                        *(
                            [InventoryItem(name="Dried Meat", quantity=self.world.food_items)]
                            if self.world.food_items
                            else []
                        ),
                        *(
                            [
                                InventoryItem(
                                    name="Basic First Aid Kit",
                                    quantity=self.world.first_aid_kits,
                                )
                            ]
                            if self.world.first_aid_kits
                            else []
                        ),
                    ],
                )
            ],
            nearby_entities=nearby,
        )
        events = list(self._events)
        self._events.clear()
        observation = Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="mock",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=self._sequence,
                frame_sequence=self._frame_sequence if capture else None,
                capability_epoch=1,
            ),
            telemetry=telemetry,
            telemetry_stale=False,
            telemetry_age_seconds=0.0,
            screenshot_path=screenshot,
            screenshot_sha256=screenshot_hash,
            events=events,
            available_skills=[
                "buy_food",
                "eat_food",
                "first_aid",
                "work_for_cats",
                "seek_safety",
                "travel_toward_squin",
            ],
        )
        self._last_observation = observation
        return observation

    def _apply_semantic_action(
        self,
        action: (
            ApproachDialogueTargetAction | ActivateVisibleControlAction | RecoverCameraViewAction
        ),
    ) -> tuple[str, SemanticActionReceipt | None]:
        """Bind a semantic action against mock state and record what it resolved to.

        The mock world has no dialogue partners of its own, so this exists to
        keep the contract path honest off-game: the same binding that gates live
        execution decides the outcome here, and an unbindable reference is a
        recorded no-op rather than a silent success.
        """

        observation = self._last_observation
        if observation is None:
            return (f"Mock {action.kind} had no current observation to bind against.", None)
        definition = definition_for(action)
        if definition is None:
            return (f"Unknown semantic action {action.kind}.", None)
        try:
            bound = OPERATION_BINDING_AUTHORITY.bind(
                action,
                observation,
                affordance=None,
            )
        except ValueError as exc:
            return (
                f"Mock {action.kind} bound to nothing and changed no state: {exc}",
                SemanticActionReceipt(
                    action_kind=action.kind,
                    contract_version=definition.version,
                    source_revision=observation.world_revision,
                    revalidation=str(exc),
                ),
            )
        binding = bound.binding
        assert not isinstance(binding, BindingFailure)
        if isinstance(action, RecoverCameraViewAction):
            assert isinstance(binding, BoundCameraRecovery)
            score = score_camera_observation(
                observation,
                candidate="mock_initial",
                floor=binding.floor,
                clear_score_threshold=0.72,
                anchor_max_distance=30.0,
            )
            status = (
                CameraRecoveryStatus.ALREADY_CLEAR
                if score.clear
                else CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS
            )
            semantic = SemanticActionReceipt(
                action_kind=action.kind,
                contract_version=definition.version,
                target_id=binding.target_id,
                resolved_label=binding.resolved_label,
                resolved_role=binding.resolved_role,
                resolved_bounds=binding.resolved_bounds,
                source_revision=binding.source_revision,
                revalidation=binding.reason,
                camera_recovery=CameraRecoveryEvidence(
                    status=status,
                    selected_character_id=binding.target_id,
                    selected_character_name=binding.selected_character_name,
                    initial_floor=binding.floor,
                    final_floor=binding.floor,
                    clear_score_threshold=0.72,
                    anchor_max_distance=30.0,
                    paused_for_recovery=False,
                    primitive_actions=0,
                    follow_method="already_anchored",
                    chosen_candidate=score.candidate,
                    candidates=[score],
                ),
            )
            return (
                f"Mock camera recovery returned {status.value}: {binding.reason}",
                semantic,
            )
        target_id: str | None = None
        resolved_label: str | None = None
        resolved_role: str | None = None
        resolved_bounds: NormalizedPointerBounds | None = None
        if isinstance(binding, BoundActor):
            target_id = binding.target_id
        elif isinstance(binding, BoundVisibleControl):
            resolved_label = binding.resolved_label
            resolved_role = binding.resolved_role
            resolved_bounds = binding.resolved_bounds
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=definition.version,
            target_id=target_id,
            resolved_label=resolved_label,
            resolved_role=resolved_role,
            resolved_bounds=resolved_bounds,
            source_revision=binding.source_revision,
            revalidation=binding.reason,
        )
        return (
            f"Mock {action.kind} resolved its reference: {binding.reason}",
            semantic,
        )

    def _apply_skill(self, action: SkillAction) -> str:
        name = action.name
        if name == "buy_food":
            if self.world.location not in {"The Hub", "Squin"}:
                return "No shop is available at the current mock location."
            if self.world.cats < 50:
                return "Not enough cats to buy food."
            self.world.cats -= 50
            self.world.food_items += 1
            self._advance_time(10)
            self._events.append("Bought one food item for 50 cats.")
            return "Bought food."
        if name == "eat_food":
            if self.world.food_items <= 0:
                return "No food item was available."
            self.world.food_items -= 1
            self.world.hunger = min(3.0, self.world.hunger + 0.95)
            self._events.append("Ate one food item.")
            return "Ate food."
        if name == "first_aid":
            if self.world.first_aid_kits <= 0:
                return "No first-aid kit was available."
            if self.world.bleeding_rate <= 0:
                return "No bleeding required treatment."
            self.world.bleeding_rate = 0.0
            self.world.treated_injuries += 1
            self._advance_time(5)
            self._events.append("Treated the mock injury.")
            return "Applied first aid."
        if name == "work_for_cats":
            if self.world.hostile_nearby:
                return "Working is unsafe while a hostile is nearby."
            self._advance_time(60)
            self.world.cats += 120
            self._events.append("Completed one hour of mock labor for 120 cats.")
            return "Earned 120 cats."
        if name == "seek_safety":
            self.world.hostile_nearby = False
            self.world.distance_to_hostile = None
            self.world.location = "The Hub"
            self._advance_time(20)
            self._events.append("Reached the mock safety of The Hub.")
            return "Returned to safety."
        if name == "travel_toward_squin":
            if self.world.hostile_nearby:
                return "Travel cannot proceed safely while a hostile is nearby."
            self.world.location = "Road to Squin"
            self.world.travel_progress += 20.0
            self._advance_time(90)
            if self.world.travel_progress >= 100.0:
                self.world.location = "Squin"
                self._events.append("Arrived at mock Squin.")
            else:
                self._events.append("Made progress toward mock Squin.")
            return "Travelled toward Squin."
        return f"Unknown mock skill: {name}."

    def _advance_time(self, minutes: float) -> None:
        if self.world.paused:
            self._events.append("Time did not advance because the mock game is paused.")
            return
        scaled = max(0.0, minutes * self.world.speed)
        self.world.elapsed_minutes += scaled
        self.world.hunger = max(0.0, self.world.hunger - scaled * 0.00075)
        if self.world.bleeding_rate > 0:
            self.world.health -= self.world.bleeding_rate * scaled * 0.08
        if self.world.hunger <= 0:
            self.world.health -= scaled * 0.2
        if self.config.random_events and not self.world.hostile_nearby:
            event_probability = min(0.35, scaled / 600.0)
            if self._rng.random() < event_probability:
                self.world.hostile_nearby = True
                self.world.distance_to_hostile = self._rng.uniform(18.0, 45.0)
                self._events.append("A mock Hungry Bandit appeared nearby.")
        if self.world.hostile_nearby and scaled > 0:
            distance = self.world.distance_to_hostile or 30.0
            distance -= scaled * 0.08
            self.world.distance_to_hostile = distance
            if distance <= 0:
                damage = self._rng.uniform(8.0, 22.0)
                self.world.health -= damage
                self.world.bleeding_rate = max(self.world.bleeding_rate, 0.8)
                self.world.hostile_nearby = False
                self.world.distance_to_hostile = None
                self._events.append(f"The bandit struck for {damage:.1f} mock damage.")
        if self.world.health <= 0:
            self.world.alive = False
            self.world.conscious = False

    def _render_screenshot(self) -> Path:
        path = self.run_dir / f"mock_frame_{self._sequence:05d}.png"
        image = Image.new("RGB", (1280, 720), (205, 190, 154))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        draw.rectangle((0, 0, 1280, 78), fill=(44, 42, 36))
        draw.text((24, 20), "KENSHI AGENT MOCK", fill=(235, 235, 225), font=font)
        draw.text(
            (24, 44),
            f"{self.world.location} | cats {self.world.cats} | hunger {self.world.hunger:.1f}",
            fill=(235, 235, 225),
            font=font,
        )
        draw.rectangle((55, 110, 1225, 600), outline=(70, 65, 54), width=3)
        draw.ellipse((600, 340, 622, 362), fill=(45, 68, 78))
        draw.text((570, 370), "Wanderer", fill=(30, 30, 30), font=font)
        if self.world.hostile_nearby:
            draw.ellipse((820, 300, 845, 325), fill=(105, 35, 30))
            draw.text((790, 330), "Hungry Bandit", fill=(90, 20, 20), font=font)
        draw.rectangle((0, 625, 1280, 720), fill=(55, 53, 47))
        draw.text(
            (24, 645),
            f"Day {int(self.world.elapsed_minutes // 1440) + 1}  "
            f"{int(self.world.elapsed_minutes % 1440) // 60:02d}:"
            f"{int(self.world.elapsed_minutes % 60):02d}  "
            f"HP {self.world.health:.1f}  Food {self.world.food_items}  "
            f"Aid {self.world.first_aid_kits}",
            fill=(240, 240, 230),
            font=font,
        )
        image.save(path)
        return path

    async def close(self) -> None:
        return None
