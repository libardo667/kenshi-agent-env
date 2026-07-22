from __future__ import annotations

import time
from collections import deque

from .config import SafetyConfig
from .models import (
    Action,
    ClickAction,
    CoordinateSpace,
    Observation,
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
        if action.kind not in self.config.allow_action_kinds:
            raise SafetyViolation(f"Action kind {action.kind!r} is not allowlisted.")
        if isinstance(action, SkillAction):
            if action.name not in self.config.allow_skills and observation.mode == "live":
                raise SafetyViolation(f"Skill {action.name!r} is not allowlisted for live use.")
            if observation.mode == "live" and not self.macros.has(action.name):
                raise SafetyViolation(f"Live skill {action.name!r} has no configured macro.")
        if isinstance(action, WaitAction) and action.seconds > self.config.max_wait_seconds:
            raise SafetyViolation(
                f"Wait {action.seconds:.2f}s exceeds maximum {self.config.max_wait_seconds:.2f}s."
            )
        if isinstance(action, ClickAction):
            self._validate_click(action, observation)
        primitive_count = (
            self.macros.primitive_count(action)
            if isinstance(action, SkillAction) and observation.mode == "live"
            else 1
        )
        if primitive_count > self.config.max_primitive_actions_per_step:
            raise SafetyViolation(
                f"Action expands to {primitive_count} primitives; maximum is "
                f"{self.config.max_primitive_actions_per_step}."
            )
        self._consume_rate_budget(primitive_count)
        return action

    def _validate_click(self, action: ClickAction, observation: Observation) -> None:
        if (
            observation.mode == "live"
            and observation.telemetry_stale
            and self.config.block_clicks_when_telemetry_stale
        ):
            raise SafetyViolation("Click blocked because live telemetry is stale.")
        if action.space == CoordinateSpace.NORMALIZED:
            if not (0.0 <= action.x <= 1.0 and 0.0 <= action.y <= 1.0):
                raise SafetyViolation("Normalized click coordinates must be within [0, 1].")
            return
        if action.x < 0 or action.y < 0:
            raise SafetyViolation("Click coordinates may not be negative.")
        if action.space == CoordinateSpace.CLIENT and observation.telemetry is not None:
            ui = observation.telemetry.ui
            if ui.client_width is not None and action.x >= ui.client_width:
                raise SafetyViolation("Client click x-coordinate is outside the Kenshi window.")
            if ui.client_height is not None and action.y >= ui.client_height:
                raise SafetyViolation("Client click y-coordinate is outside the Kenshi window.")

    def _consume_rate_budget(self, count: int) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._action_times and self._action_times[0] < cutoff:
            self._action_times.popleft()
        if len(self._action_times) + count > self.config.max_actions_per_minute:
            raise SafetyViolation("Per-minute primitive action rate limit would be exceeded.")
        self._action_times.extend([now] * count)
