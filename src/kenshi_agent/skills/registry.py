from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..config import MacroConfig
from ..models import Action, SkillAction, parse_action


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

    def description(self, name: str) -> str:
        try:
            return self._macros[name].description
        except KeyError as exc:
            raise UnknownSkillError(name) from exc

    def expand(self, action: SkillAction) -> list[Action]:
        try:
            macro = self._macros[action.name]
        except KeyError as exc:
            raise UnknownSkillError(action.name) from exc
        rendered = [self._render(deepcopy(item), action.args) for item in macro.actions]
        actions = [parse_action(item) for item in rendered]
        if any(isinstance(item, SkillAction) for item in actions):
            raise ValueError("Macros may not recursively invoke other skills.")
        return actions

    def primitive_count(self, action: Action) -> int:
        if isinstance(action, SkillAction):
            return len(self.expand(action))
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
