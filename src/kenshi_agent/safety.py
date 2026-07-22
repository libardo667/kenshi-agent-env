from __future__ import annotations

import time
from collections import deque

from .config import SafetyConfig
from .models import (
    Action,
    ClickAction,
    CoordinateSpace,
    MoveCursorAction,
    Observation,
    PauseAction,
    SkillAction,
    WaitAction,
)
from .skills import MacroRegistry


class SafetyViolation(RuntimeError):
    pass


class ActionGuard:
    def __init__(self, config: SafetyConfig, macros: MacroRegistry) -> None:
        self.config = config
        self.macros = macros
        self._action_times: deque[float] = deque()

    def validate(self, action: Action, observation: Observation) -> Action:
        self._validate_action_constraints(action, observation)
        primitives: list[Action] | None = None
        if isinstance(action, SkillAction):
            if action.name not in self.config.allow_skills and observation.mode == "live":
                raise SafetyViolation(f"Skill {action.name!r} is not allowlisted for live use.")
            if observation.mode == "live" and not self.macros.has(action.name):
                raise SafetyViolation(f"Live skill {action.name!r} has no configured macro.")
            if observation.mode == "live":
                pulse_seconds = self.macros.movement_pulse_seconds(action.name)
                if pulse_seconds is not None and (
                    observation.telemetry is None or observation.telemetry.game.paused is not True
                ):
                    raise SafetyViolation(
                        f"Movement pulse {action.name!r} requires confirmed paused live state."
                    )
                try:
                    primitives = self.macros.expand(action)
                except (TypeError, ValueError) as exc:
                    raise SafetyViolation(
                        f"Live skill {action.name!r} could not be expanded safely: {exc}"
                    ) from exc
                pointer_bounds = self.macros.normalized_pointer_bounds(action.name)
                for primitive in primitives:
                    if primitive.kind not in {"key", "hotkey", "move_cursor", "click"}:
                        raise SafetyViolation(
                            f"Live skill {action.name!r} contains unsupported primitive "
                            f"{primitive.kind!r}."
                        )
                    self._validate_action_constraints(
                        primitive, observation, check_action_allowlist=False
                    )
                    if pointer_bounds is not None and isinstance(
                        primitive, (ClickAction, MoveCursorAction)
                    ):
                        if primitive.space != CoordinateSpace.NORMALIZED:
                            raise SafetyViolation(
                                f"Live skill {action.name!r} has a pointer safety envelope "
                                "but emitted non-normalized coordinates."
                            )
                        if not pointer_bounds.contains(primitive.x, primitive.y):
                            raise SafetyViolation(
                                f"Live skill {action.name!r} pointer target "
                                f"({primitive.x:.3f}, {primitive.y:.3f}) is outside its "
                                "calibrated safety envelope."
                            )
        primitive_count = self.macros.primitive_count(action) if primitives is not None else 1
        if primitive_count > self.config.max_primitive_actions_per_step:
            raise SafetyViolation(
                f"Action expands to {primitive_count} primitives; maximum is "
                f"{self.config.max_primitive_actions_per_step}."
            )
        self._consume_rate_budget(primitive_count)
        return action

    def _validate_action_constraints(
        self,
        action: Action,
        observation: Observation,
        *,
        check_action_allowlist: bool = True,
    ) -> None:
        if check_action_allowlist and action.kind not in self.config.allow_action_kinds:
            raise SafetyViolation(f"Action kind {action.kind!r} is not allowlisted.")
        if isinstance(action, WaitAction) and action.seconds > self.config.max_wait_seconds:
            raise SafetyViolation(
                f"Wait {action.seconds:.2f}s exceeds maximum {self.config.max_wait_seconds:.2f}s."
            )
        if isinstance(action, PauseAction) and observation.mode == "live":
            if observation.telemetry is None or observation.telemetry.game.paused is None:
                raise SafetyViolation(
                    "Pause action blocked because the current live pause state is unknown."
                )
            if not action.paused and not self.config.allow_live_unpause_actions:
                raise SafetyViolation(
                    "Direct live unpause is blocked; use a bounded movement pulse."
                )
        if isinstance(action, (ClickAction, MoveCursorAction)):
            self._validate_pointer_target(action, observation)

    def _validate_pointer_target(
        self,
        action: ClickAction | MoveCursorAction,
        observation: Observation,
    ) -> None:
        if (
            isinstance(action, ClickAction)
            and observation.mode == "live"
            and observation.telemetry_stale
            and self.config.block_clicks_when_telemetry_stale
        ):
            raise SafetyViolation("Click blocked because live telemetry is stale.")
        if observation.mode == "live" and action.space == CoordinateSpace.SCREEN:
            raise SafetyViolation(
                "Screen-space pointer actions are blocked in live mode; use Kenshi client "
                "or normalized coordinates."
            )
        if action.space == CoordinateSpace.NORMALIZED:
            if not (0.0 <= action.x <= 1.0 and 0.0 <= action.y <= 1.0):
                raise SafetyViolation("Normalized pointer coordinates must be within [0, 1].")
            return
        if action.x < 0 or action.y < 0:
            raise SafetyViolation("Pointer coordinates may not be negative.")
        if action.space == CoordinateSpace.CLIENT:
            ui = observation.telemetry.ui if observation.telemetry is not None else None
            if observation.mode == "live" and (
                ui is None or ui.client_width is None or ui.client_height is None
            ):
                raise SafetyViolation(
                    "Client-space pointer action blocked because Kenshi client dimensions "
                    "are unknown."
                )
            if ui is None:
                return
            if ui.client_width is not None and action.x >= ui.client_width:
                raise SafetyViolation("Pointer x-coordinate is outside the Kenshi window.")
            if ui.client_height is not None and action.y >= ui.client_height:
                raise SafetyViolation("Pointer y-coordinate is outside the Kenshi window.")

    def _consume_rate_budget(self, count: int) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._action_times and self._action_times[0] < cutoff:
            self._action_times.popleft()
        if len(self._action_times) + count > self.config.max_actions_per_minute:
            raise SafetyViolation("Per-minute primitive action rate limit would be exceeded.")
        self._action_times.extend([now] * count)
