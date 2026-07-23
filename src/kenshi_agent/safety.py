from __future__ import annotations

import re
import time
from collections import deque

from .config import SafetyConfig
from .models import (
    Action,
    ClickAction,
    ControlMode,
    CoordinateSpace,
    MoveCursorAction,
    Observation,
    PauseAction,
    ScrollAction,
    SkillAction,
    WaitAction,
)
from .skills import MacroRegistry


class SafetyViolation(RuntimeError):
    pass


class ActionGuard:
    def __init__(
        self,
        config: SafetyConfig,
        macros: MacroRegistry,
        *,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    ) -> None:
        self.config = config
        self.macros = macros
        self.control_mode = control_mode
        self._action_times: deque[float] = deque()
        self._purchase_count = 0

    def validate(self, action: Action, observation: Observation) -> Action:
        self._validate_control_mode(observation)
        self._validate_action_constraints(action, observation)
        primitives: list[Action] | None = None
        if isinstance(action, SkillAction):
            if (
                self.macros.has(action.name)
                and self.macros.requires_native_assisted(action.name)
                and self.control_mode != ControlMode.NATIVE_ASSISTED
            ):
                raise SafetyViolation(
                    f"Skill {action.name!r} requires native_assisted control mode."
                )
            if action.name not in self.config.allow_skills and observation.mode == "live":
                raise SafetyViolation(f"Skill {action.name!r} is not allowlisted for live use.")
            if observation.mode == "live" and not self.macros.has(action.name):
                raise SafetyViolation(f"Live skill {action.name!r} has no configured macro.")
            if observation.mode == "live":
                try:
                    pulse_seconds = self.macros.resolve_movement_pulse_seconds(action)
                    primitives = self.macros.expand(action)
                except (TypeError, ValueError) as exc:
                    raise SafetyViolation(
                        f"Live skill {action.name!r} could not be expanded safely: {exc}"
                    ) from exc
                if pulse_seconds is not None and (
                    observation.telemetry is None or observation.telemetry.game.paused is not True
                ):
                    raise SafetyViolation(
                        f"Movement pulse {action.name!r} requires confirmed paused live state."
                    )
                if action.name == "approach_confirmed_vendor":
                    self._validate_native_vendor_target(action, observation)
                if action.name == "buy_inspected_shop_item":
                    self._validate_purchase(action, observation)
                pointer_bounds = self.macros.normalized_pointer_bounds(action.name)
                for primitive in primitives:
                    if primitive.kind not in {
                        "key",
                        "hotkey",
                        "move_cursor",
                        "click",
                        "scroll",
                    }:
                        raise SafetyViolation(
                            f"Live skill {action.name!r} contains unsupported primitive "
                            f"{primitive.kind!r}."
                        )
                    self._validate_action_constraints(
                        primitive, observation, check_action_allowlist=False
                    )
                    if pointer_bounds is not None and isinstance(
                        primitive, (ClickAction, MoveCursorAction, ScrollAction)
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
        if (
            isinstance(action, SkillAction)
            and action.name == "buy_inspected_shop_item"
            and observation.mode == "live"
        ):
            self._purchase_count += 1
        return action

    @staticmethod
    def _validate_native_vendor_target(
        action: SkillAction,
        observation: Observation,
    ) -> None:
        if observation.telemetry_stale or observation.telemetry is None:
            raise SafetyViolation("Native vendor approach requires fresh authoritative telemetry.")
        telemetry = observation.telemetry
        required_capabilities = {
            "control.approach_vendor",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        missing = required_capabilities - set(telemetry.capabilities)
        if missing:
            raise SafetyViolation(
                "Native vendor approach lacks required capabilities: " + ", ".join(sorted(missing))
            )
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise SafetyViolation(
                "Native vendor approach requires one exact primary selected character."
            )
        target_id = action.argument_map().get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise SafetyViolation("Native vendor approach requires an exact target_id.")
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if target is None:
            raise SafetyViolation(
                "Native vendor target is absent from the current bounded nearby set."
            )
        if (
            target.is_animal is not False
            or target.has_vendor_list is not True
            or target.is_squad_leader is not True
            or target.has_dialogue is not True
            or target.conscious is not True
            or target.disposition.value not in {"friendly", "neutral"}
        ):
            raise SafetyViolation(
                "Native vendor target lacks exact current role, consciousness, "
                "or non-hostile evidence."
            )

    def validate_safety_pause(
        self,
        action: PauseAction,
        observation: Observation,
    ) -> PauseAction:
        """Validate the narrow safe-pause path without consuming rate budget."""

        if action.paused is not True:
            raise SafetyViolation("Safety override only permits requesting paused=true.")
        self._validate_control_mode(observation)
        self._validate_action_constraints(action, observation)
        return action

    def _validate_control_mode(self, observation: Observation) -> None:
        if observation.mode == "live" and observation.control_mode != self.control_mode:
            raise SafetyViolation(
                f"Observation control mode {observation.control_mode.value!r} does not match "
                f"guard control mode {self.control_mode.value!r}."
            )

    def _validate_purchase(self, action: SkillAction, observation: Observation) -> None:
        if observation.telemetry_stale or observation.telemetry is None:
            raise SafetyViolation("Purchase blocked because live telemetry is stale or absent.")
        telemetry = observation.telemetry
        required_capabilities = {
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.basic",
            "squad.hunger",
            "ui.inventory",
            "ui.tooltip",
        }
        missing = required_capabilities - set(telemetry.capabilities)
        if missing:
            raise SafetyViolation(
                "Purchase lacks required authoritative capabilities: "
                + ", ".join(sorted(missing))
            )
        if telemetry.game.paused is not True:
            raise SafetyViolation("Purchase requires a confirmed paused game.")
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise SafetyViolation("Purchase requires one exact primary selected character.")
        if telemetry.ui.active_screen != "trade":
            raise SafetyViolation("Purchase blocked because the exact trade screen is not open.")
        arguments = action.argument_map()
        target_id = arguments.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise SafetyViolation("Purchase requires an exact target_id.")
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if (
            telemetry.active_shop_trader_count != 1
            or target is None
            or target.shop_inventory_owner is not True
            or target.disposition.value not in {"friendly", "neutral"}
        ):
            raise SafetyViolation(
                "Purchase blocked because the exact target is not the one verified "
                "non-hostile shop owner."
            )
        if self._purchase_count >= self.config.max_purchases_per_run:
            raise SafetyViolation("Per-run purchase limit has already been reached.")

        expected_price = arguments.get("expected_price")
        if (
            isinstance(expected_price, bool)
            or not isinstance(expected_price, int)
            or expected_price <= 0
        ):
            raise SafetyViolation("Purchase requires a positive integer expected_price.")
        if expected_price > self.config.max_purchase_price:
            raise SafetyViolation(
                f"Expected price {expected_price} exceeds maximum {self.config.max_purchase_price}."
            )
        money = telemetry.game.money
        if money is None:
            raise SafetyViolation("Purchase blocked because current money is unknown.")
        if money - expected_price < self.config.min_money_after_purchase:
            raise SafetyViolation(
                f"Expected purchase would leave {money - expected_price} cats; minimum is "
                f"{self.config.min_money_after_purchase}."
            )

        item_name = arguments.get("item_name")
        if not isinstance(item_name, str) or not item_name.strip():
            raise SafetyViolation("Purchase requires the exact current tooltip item_name.")
        tooltip_text = telemetry.ui.tooltip_text
        tooltip_bounds = telemetry.ui.tooltip_source_bounds
        if (
            telemetry.ui.tooltip_visible is not True
            or not tooltip_text
            or tooltip_bounds is None
        ):
            raise SafetyViolation(
                "Purchase requires a visible authoritative tooltip and its source bounds."
            )
        price_pattern = rf"(?<![A-Za-z0-9])c\.{expected_price}(?![0-9])"
        if (
            item_name not in tooltip_text
            or "[Food]" not in tooltip_text
            or re.search(price_pattern, tooltip_text) is None
        ):
            raise SafetyViolation(
                "Purchase arguments do not match the current food tooltip."
            )
        x = arguments.get("x")
        y = arguments.get("y")
        if (
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or isinstance(y, bool)
            or not isinstance(y, (int, float))
            or not tooltip_bounds.contains(float(x), float(y))
        ):
            raise SafetyViolation(
                "Purchase coordinates are outside the current tooltip source."
            )

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
        if isinstance(action, (ClickAction, MoveCursorAction, ScrollAction)):
            self._validate_pointer_target(action, observation)

    def _validate_pointer_target(
        self,
        action: ClickAction | MoveCursorAction | ScrollAction,
        observation: Observation,
    ) -> None:
        if (
            isinstance(action, (ClickAction, ScrollAction))
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
