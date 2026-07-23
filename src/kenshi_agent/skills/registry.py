from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..config import MacroConfig, NormalizedPointerBoundsConfig
from ..models import (
    Action,
    ControlMode,
    NormalizedPointerBounds,
    SkillAction,
    SkillSpec,
    parse_action,
)


class UnknownSkillError(KeyError):
    pass


class MacroRegistry:
    """Expands a named skill into a bounded sequence of ordinary UI actions."""

    def __init__(self, macros: dict[str, MacroConfig]) -> None:
        self._macros = dict(macros)

    def names(self) -> list[str]:
        return sorted(self._macros)

    def has(self, name: str) -> bool:
        return name in self._macros

    def available_names(
        self,
        names: list[str],
        *,
        control_mode: ControlMode,
    ) -> list[str]:
        return sorted(
            name
            for name in set(names)
            if self.has(name)
            and (
                control_mode == ControlMode.NATIVE_ASSISTED
                or not self.requires_native_assisted(name)
            )
        )

    def requires_native_assisted(self, name: str) -> bool:
        try:
            return self._macros[name].requires_native_assisted
        except KeyError as exc:
            raise UnknownSkillError(name) from exc

    def description(self, name: str) -> str:
        try:
            return self._macros[name].description
        except KeyError as exc:
            raise UnknownSkillError(name) from exc

    def spec(self, name: str) -> SkillSpec:
        try:
            macro = self._macros[name]
        except KeyError as exc:
            raise UnknownSkillError(name) from exc
        return SkillSpec(
            name=name,
            description=macro.description,
            arguments=macro.arguments,
            visual_precondition=macro.visual_precondition,
            normalized_pointer_bounds=(
                NormalizedPointerBounds.model_validate(
                    macro.normalized_pointer_bounds.model_dump()
                )
                if macro.normalized_pointer_bounds is not None
                else None
            ),
            movement_pulse_seconds=macro.movement_pulse_seconds,
            movement_pulse_min_seconds=macro.movement_pulse_min_seconds,
            movement_pulse_max_seconds=macro.movement_pulse_max_seconds,
            requires_native_assisted=macro.requires_native_assisted,
        )

    def specs(self) -> list[SkillSpec]:
        return [self.spec(name) for name in self.names()]

    def normalized_pointer_bounds(self, name: str) -> NormalizedPointerBoundsConfig | None:
        try:
            return self._macros[name].normalized_pointer_bounds
        except KeyError as exc:
            raise UnknownSkillError(name) from exc

    def movement_pulse_seconds(self, name: str) -> float | None:
        try:
            return self._macros[name].movement_pulse_seconds
        except KeyError as exc:
            raise UnknownSkillError(name) from exc

    def is_stateful_movement(self, action: Action) -> bool:
        return bool(
            isinstance(action, SkillAction)
            and self.has(action.name)
            and self.movement_pulse_seconds(action.name) is not None
        )

    def resolve_movement_pulse_seconds(self, action: SkillAction) -> float | None:
        try:
            macro = self._macros[action.name]
        except KeyError as exc:
            raise UnknownSkillError(action.name) from exc
        default = macro.movement_pulse_seconds
        if default is None:
            return None
        minimum = macro.movement_pulse_min_seconds or default
        maximum = macro.movement_pulse_max_seconds or default
        requested = action.argument_map().get("duration_seconds", default)
        if isinstance(requested, bool) or not isinstance(requested, (int, float)):
            raise ValueError("duration_seconds must be a number")
        duration = float(requested)
        if not minimum <= duration <= maximum:
            raise ValueError(
                f"duration_seconds={duration:.2f} is outside the calibrated "
                f"range [{minimum:.2f}, {maximum:.2f}]"
            )
        return duration

    def expand(self, action: SkillAction) -> list[Action]:
        try:
            macro = self._macros[action.name]
        except KeyError as exc:
            raise UnknownSkillError(action.name) from exc
        arguments = action.argument_map()
        rendered = [self._render(deepcopy(item), arguments) for item in macro.actions]
        actions = [parse_action(item) for item in rendered]
        if any(isinstance(item, SkillAction) for item in actions):
            raise ValueError("Macros may not recursively invoke other skills.")
        return actions

    def primitive_count(self, action: Action) -> int:
        if isinstance(action, SkillAction):
            pulse_primitives = 2 if self.movement_pulse_seconds(action.name) is not None else 0
            return len(self.expand(action)) + pulse_primitives
        return 1

    @classmethod
    def _render(cls, value: Any, args: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            key = value[2:-2].strip()
            if key not in args:
                raise ValueError(f"Missing skill argument: {key}")
            return args[key]
        if isinstance(value, list):
            return [cls._render(item, args) for item in value]
        if isinstance(value, dict):
            return {key: cls._render(item, args) for key, item in value.items()}
        return value
