from __future__ import annotations

import re
from collections.abc import Iterable

from .affordances import OPERATION_BINDING_AUTHORITY, OperationBindingError
from .authorization import AuthorizationCode
from .config import SafetyConfig
from .models import (
    Action,
    ClickAction,
    ConsultAdvisorAction,
    ControlMode,
    CoordinateSpace,
    Disposition,
    MoveCursorAction,
    NativeCommandStatus,
    Observation,
    PauseAction,
    PointerActionClass,
    PurchaseItemAction,
    ReadFieldbookAction,
    RecallMemoryAction,
    ScrollAction,
    SetSpeedAction,
    SkillAction,
    WaitAction,
    is_controller_primitive,
    normalize_control_label,
)
from .operation_definitions import (
    BoundOperation,
    SelectionRequirement,
)
from .skills import MacroRegistry


class SafetyViolation(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: AuthorizationCode = AuthorizationCode.POLICY_DISALLOWED,
    ) -> None:
        super().__init__(message)
        self.code = code


def require_exact_target_id(value: object) -> str:
    """Return one usable stable reference or fail before any state lookup."""

    if not isinstance(value, str) or not value:
        raise SafetyViolation(  # mutation: reason
            "Action requires an exact target_id."  # mutation: reason
        )
    return value


class OperationPolicy:
    def __init__(
        self,
        config: SafetyConfig,
        macros: MacroRegistry,
        *,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
        semantic_pointer_skills: Iterable[str] = (),
    ) -> None:
        self.config = config
        self.macros = macros
        self.control_mode = control_mode
        self.semantic_pointer_skills = frozenset(semantic_pointer_skills)

    def validate(self, action: Action, observation: Observation) -> Action:
        """Bind and apply pure policy for a direct diagnostic caller."""

        if is_controller_primitive(action):
            self._validate_control_mode(observation)
            self._validate_action_constraints(action, observation)
            return action
        try:
            bound = OPERATION_BINDING_AUTHORITY.bind(action, observation, affordance=None)
        except OperationBindingError as exc:
            raise SafetyViolation(str(exc), code=exc.code) from exc
        return self.revalidate_bound(bound, observation)

    def revalidate(self, action: Action, observation: Observation) -> Action:
        """Re-check current authority without spending the same budget twice."""

        return self.validate(action, observation)

    def revalidate_bound(
        self,
        bound: BoundOperation,
        observation: Observation,
    ) -> Action:
        """Apply pure policy to a binding established by the binding authority."""

        action = bound.operation
        self._validate_control_mode(observation)
        self._validate_action_constraints(action, observation)
        definition = bound.definition
        if not isinstance(action, SkillAction):
            primitive_actions = definition.primitive_action_bound_for(action)
            self._validate_bound_operation_definition(
                bound,
                observation,
                primitive_actions=primitive_actions,
            )
            if isinstance(action, PurchaseItemAction) and observation.mode == "live":
                self._validate_generic_purchase(
                    action,
                    observation,
                )
            return action
        return self._validate_compatibility_skill(action, observation)

    def pointer_class_for(self, bound: BoundOperation) -> PointerActionClass:
        """Resolve calibration policy without teaching the external adapter semantics."""

        action = bound.operation
        if not isinstance(action, SkillAction):
            return bound.definition.pointer_class
        if action.name in self.semantic_pointer_skills:
            return PointerActionClass.SEMANTIC_CURRENT
        if not self.macros.has(action.name):
            return PointerActionClass.UNSUPPORTED
        pointer = any(
            isinstance(primitive, (ClickAction, MoveCursorAction, ScrollAction))
            for primitive in self.macros.expand(action)
        )
        return (
            PointerActionClass.PROFILE_CALIBRATED
            if pointer
            else PointerActionClass.COORDINATE_INDEPENDENT
        )

    def _validate_bound_operation_definition(
        self,
        bound: BoundOperation,
        observation: Observation,
        *,
        primitive_actions: int,
    ) -> None:
        definition = bound.definition
        if not definition.allows_control_mode(self.control_mode):
            raise SafetyViolation(
                f"Action {definition.kind!r} is not permitted in "
                f"control mode {self.control_mode.value!r}."
            )
        if definition.native_assisted and self.control_mode != ControlMode.NATIVE_ASSISTED:
            raise SafetyViolation(
                f"Action {definition.kind!r} requires native_assisted control mode."
            )
        primitive_limit = (
            self.config.max_controller_verified_primitive_actions_per_step
            if definition.controller_verified
            else self.config.max_primitive_actions_per_step
        )
        if primitive_actions > primitive_limit:
            raise SafetyViolation(
                f"Action {definition.kind!r} may emit {primitive_actions} primitives; "
                f"maximum is {primitive_limit} for this operation class."
            )
        if observation.mode != "live":
            return
        if definition.requires_fresh_telemetry and (
            observation.telemetry_stale or observation.telemetry is None
        ):
            raise SafetyViolation(
                f"Action {definition.kind!r} requires fresh authoritative telemetry."
            )
        capabilities = set(
            observation.telemetry.capabilities if observation.telemetry is not None else ()
        )
        missing = definition.missing_capabilities(capabilities)
        if missing:
            raise SafetyViolation(
                f"Action {definition.kind!r} lacks required capabilities: " + ", ".join(missing),
                code=AuthorizationCode.CAPABILITY_UNAVAILABLE,
            )
        if definition.selection_requirement is SelectionRequirement.EXACTLY_ONE:
            try:
                self._validate_exact_selection(observation)
            except SafetyViolation as exc:
                raise SafetyViolation(str(exc), code=AuthorizationCode.SELECTION_INVALID) from exc
        elif definition.selection_requirement is SelectionRequirement.ONE_OR_MORE:
            try:
                self._validate_squad_selection(observation)
            except SafetyViolation as exc:
                raise SafetyViolation(str(exc), code=AuthorizationCode.SELECTION_INVALID) from exc

    def _validate_compatibility_skill(
        self,
        action: SkillAction,
        observation: Observation,
    ) -> Action:
        self._validate_control_mode(observation)
        self._validate_action_constraints(action, observation)
        # Configured compatibility skills retain their macro-expanded policy
        # until the macro surface is retired in its own reconstruction stage.
        # They still bind and execute through the operation kernel.
        primitives: list[Action] | None = None
        if (
            self.macros.has(action.name)
            and self.macros.requires_native_assisted(action.name)
            and self.control_mode != ControlMode.NATIVE_ASSISTED
        ):
            raise SafetyViolation(f"Skill {action.name!r} requires native_assisted control mode.")
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
            if (
                pulse_seconds is not None
                and self.config.require_paused_between_actions
                and (observation.telemetry is None or observation.telemetry.game.paused is not True)
            ):
                raise SafetyViolation(
                    f"Movement pulse {action.name!r} requires confirmed paused live state."
                )
            if action.name == "approach_confirmed_vendor":
                self._validate_native_vendor_target(action, observation)
            if action.name == "continue_confirmed_vendor_approach":
                self._validate_native_vendor_continuation(action, observation)
            if action.name == "buy_inspected_shop_item":
                self._validate_purchase(action, observation)
            pointer_bounds = self.macros.normalized_pointer_bounds(action.name)
            for primitive in primitives:
                if primitive.kind not in {"key", "hotkey", "move_cursor", "click", "scroll"}:
                    raise SafetyViolation(
                        f"Live skill {action.name!r} contains unsupported "
                        f"primitive {primitive.kind!r}."
                    )
                self._validate_intrinsic_action_constraints(primitive, observation)
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
        primitive_count = (
            self.macros.primitive_count(action)
            if primitives is not None
            else 0
            if isinstance(
                action,
                (
                    ConsultAdvisorAction,
                    RecallMemoryAction,
                    ReadFieldbookAction,
                ),
            )
            else 1
        )
        if primitive_count > self.config.max_primitive_actions_per_step:
            raise SafetyViolation(  # mutation: reason
                f"Action expands to {primitive_count} primitives; "  # mutation: reason
                f"maximum is {self.config.max_primitive_actions_per_step}."  # mutation: reason
            )
        return action

    def _validate_generic_purchase(
        self,
        action: PurchaseItemAction,
        observation: Observation,
    ) -> None:
        """Spending limits for the generic purchase.

        The contract already proved the cell, its tooltip, the item name, the
        price and the seller. What is left is what no amount of evidence can
        settle - how much of the operator's money this run may spend, and how
        often - so those stay configuration, enforced here.
        """

        assert observation.telemetry is not None
        telemetry = observation.telemetry
        if self.config.require_paused_between_actions and telemetry.game.paused is not True:
            # Only when the profile actually asks for it. A stream agent has to
            # unpause to walk anywhere, so an unconditional check here refuses
            # every purchase it could ever reach a shop to make. What protects
            # the purchase is the cell binding, the verified seller, the exact
            # player-window owner and the open trade screen - all already
            # enforced by the contract, independent of whether the world is
            # moving.
            raise SafetyViolation(  # mutation: reason
                "Purchase requires a confirmed paused game "  # mutation: reason
                "because the live configuration sets "  # mutation: reason
                "require_paused_between_actions."  # mutation: reason
            )
        if not observation.trade_screen_open():
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because no trade is open: a shop's own "  # mutation: reason
                "inventory window must be open beside ours."  # mutation: reason
            )
        # Price and remaining-money constraints are pure policy. The mutable
        # per-run purchase count is owned by ActionBudgetLedger.
        if (
            self.config.max_purchase_price is not None
            and action.expected_price > self.config.max_purchase_price
        ):
            raise SafetyViolation(  # mutation: reason
                f"Expected price {action.expected_price} exceeds "  # mutation: reason
                f"maximum {self.config.max_purchase_price}."  # mutation: reason
            )
        money = telemetry.game.money
        if money is None:
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because current money is unknown."  # mutation: reason
            )
        if (
            self.config.min_money_after_purchase is not None
            and money - action.expected_price * action.quantity
            < self.config.min_money_after_purchase
        ):
            estimated_total = action.expected_price * action.quantity
            raise SafetyViolation(  # mutation: reason
                "Expected bounded purchase would leave "  # mutation: reason
                f"{money - estimated_total} cats; "  # mutation: reason
                f"minimum is {self.config.min_money_after_purchase}."  # mutation: reason
            )
        tooltip_text = telemetry.ui.tooltip_text
        for marker in self.config.required_purchase_tooltip_markers:
            if tooltip_text is None or marker not in tooltip_text:
                raise SafetyViolation(  # mutation: reason
                    "Purchase blocked because the tooltip lacks "  # mutation: reason
                    f"the required marker {marker!r}."  # mutation: reason
                )

    @staticmethod
    def _validate_exact_selection(observation: Observation) -> None:
        assert observation.telemetry is not None
        telemetry = observation.telemetry
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise SafetyViolation(  # mutation: reason
                "Action requires one exact primary "  # mutation: reason
                "selected character."  # mutation: reason
            )

    @staticmethod
    def _validate_squad_selection(observation: Observation) -> None:
        assert observation.telemetry is not None
        telemetry = observation.telemetry
        selected_ids = telemetry.ui.selected_character_ids
        if not selected_ids or telemetry.ui.selected_character_id not in selected_ids:
            raise SafetyViolation("Action requires one or more exact selected squad members.")

    @staticmethod
    def _validate_native_vendor_target(
        action: SkillAction,
        observation: Observation,
    ) -> None:
        if observation.telemetry_stale or observation.telemetry is None:
            raise SafetyViolation(  # mutation: reason
                "Native vendor approach requires fresh "  # mutation: reason
                "authoritative telemetry."  # mutation: reason
            )
        telemetry = observation.telemetry
        required_capabilities = {
            "control.approach_vendor",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        missing = required_capabilities - set(telemetry.capabilities)
        if missing:
            raise SafetyViolation(  # mutation: reason
                "Native vendor approach lacks required capabilities: "  # mutation: reason
                + ", ".join(sorted(missing))  # mutation: reason
            )
        selected_ids = telemetry.ui.selected_character_ids
        if not selected_ids or telemetry.ui.selected_character_id not in selected_ids:
            raise SafetyViolation(  # mutation: reason
                "Native vendor approach requires an exact nonempty selection "  # mutation: reason
                "with its primary selected character identified."  # mutation: reason
            )
        target_id = require_exact_target_id(action.argument_map().get("target_id"))
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if target is None:
            raise SafetyViolation(  # mutation: reason
                "Native vendor target is absent from the "  # mutation: reason
                "current bounded nearby set."  # mutation: reason
            )
        if (
            target.is_animal is not False
            or target.has_vendor_list is not True
            or target.is_squad_leader is not True
            or target.has_dialogue is not True
            or target.conscious is not True
            or target.disposition not in {Disposition.FRIENDLY, Disposition.NEUTRAL}
        ):
            raise SafetyViolation(  # mutation: reason
                "Native vendor target lacks exact current role, "  # mutation: reason
                "consciousness, or non-hostile evidence."  # mutation: reason
            )

    @classmethod
    def _validate_native_vendor_continuation(
        cls,
        action: SkillAction,
        observation: Observation,
    ) -> None:
        cls._validate_native_vendor_target(action, observation)
        assert observation.telemetry is not None
        telemetry = observation.telemetry
        active_id = telemetry.native_control.active_command_id
        acknowledgement = (
            telemetry.native_control.acknowledgement_for(active_id)
            if active_id is not None
            else None
        )
        target_id = action.argument_map().get("target_id")
        if (
            acknowledgement is None
            or acknowledgement.status != NativeCommandStatus.ACCEPTED
            or acknowledgement.target_id != target_id
            or acknowledgement.selected_character_ids != telemetry.ui.selected_character_ids
        ):
            raise SafetyViolation(  # mutation: reason
                "Native vendor continuation requires the exact "  # mutation: reason
                "active accepted command, target, and selection."  # mutation: reason
            )

    def validate_safety_pause(
        self,
        action: PauseAction,
        observation: Observation,
    ) -> PauseAction:
        """Validate the narrow safe-pause path without consuming rate budget."""

        if action.paused is not True:
            raise SafetyViolation(  # mutation: reason
                "Safety override only permits requesting paused=true."  # mutation: reason
            )
        self._validate_control_mode(observation)
        self._validate_action_constraints(action, observation)
        return action

    def _validate_control_mode(self, observation: Observation) -> None:
        if observation.mode == "live" and observation.control_mode != self.control_mode:
            raise SafetyViolation(  # mutation: reason
                "Observation control mode "  # mutation: reason
                f"{observation.control_mode.value!r} does not match "  # mutation: reason
                f"operation-policy control mode {self.control_mode.value!r}."  # mutation: reason
            )

    def _validate_purchase(
        self,
        action: SkillAction,
        observation: Observation,
    ) -> None:
        if observation.telemetry_stale or observation.telemetry is None:
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because live telemetry "  # mutation: reason
                "is stale or absent."  # mutation: reason
            )
        telemetry = observation.telemetry
        required_capabilities = {
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.basic",
            "ui.inventory",
            "ui.tooltip",
            "ui.visible_controls",
        }
        missing = required_capabilities - set(telemetry.capabilities)
        if missing:
            raise SafetyViolation(  # mutation: reason
                "Purchase lacks required authoritative capabilities: "  # mutation: reason
                + ", ".join(sorted(missing))  # mutation: reason
            )
        if self.config.require_paused_between_actions and telemetry.game.paused is not True:
            # Only when the profile actually asks for it. A stream agent has to
            # unpause to walk anywhere, so an unconditional check here refuses
            # every purchase it could ever reach a shop to make. What protects
            # the purchase is the cell binding, the verified seller, the exact
            # selection and the open trade screen - all still enforced below,
            # all independent of whether the world is moving.
            raise SafetyViolation(  # mutation: reason
                "Purchase requires a confirmed paused game "  # mutation: reason
                "because the live configuration sets "  # mutation: reason
                "require_paused_between_actions."  # mutation: reason
            )
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise SafetyViolation(  # mutation: reason
                "Purchase requires one exact primary selected character."  # mutation: reason
            )
        if not observation.trade_screen_open():
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because no exact trade is open: a "  # mutation: reason
                "shop's own inventory window must be open beside ours."  # mutation: reason
            )
        arguments = action.argument_map()
        target_id = require_exact_target_id(arguments.get("target_id"))
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if (
            target is None
            or not target.name
            or target.shop_inventory_owner is not True
            or target.disposition not in {Disposition.FRIENDLY, Disposition.NEUTRAL}
        ):
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because the exact target is "  # mutation: reason
                "not a verified non-hostile shop owner."  # mutation: reason
            )
        expected_price = arguments.get("expected_price")
        if (
            isinstance(expected_price, bool)
            or not isinstance(expected_price, int)
            or expected_price <= 0
        ):
            raise SafetyViolation(  # mutation: reason
                "Purchase requires a positive integer expected_price."  # mutation: reason
            )
        if (
            self.config.max_purchase_price is not None
            and expected_price > self.config.max_purchase_price
        ):
            raise SafetyViolation(  # mutation: reason
                f"Expected price {expected_price} exceeds maximum "  # mutation: reason
                f"{self.config.max_purchase_price}."  # mutation: reason
            )
        money = telemetry.game.money
        if money is None:
            raise SafetyViolation(  # mutation: reason
                "Purchase blocked because current money is unknown."  # mutation: reason
            )
        if (
            self.config.min_money_after_purchase is not None
            and money - expected_price < self.config.min_money_after_purchase
        ):
            raise SafetyViolation(  # mutation: reason
                "Expected purchase would leave "  # mutation: reason
                f"{money - expected_price} cats; minimum is "  # mutation: reason
                f"{self.config.min_money_after_purchase}."  # mutation: reason
            )

        item_name = arguments.get("item_name")
        if not isinstance(item_name, str) or not item_name.strip():
            raise SafetyViolation(  # mutation: reason
                "Purchase requires the exact current tooltip item_name."  # mutation: reason
            )
        tooltip_text = telemetry.ui.tooltip_text
        tooltip_bounds = telemetry.ui.tooltip_source_bounds
        if telemetry.ui.tooltip_visible is not True or not tooltip_text or tooltip_bounds is None:
            raise SafetyViolation(  # mutation: reason
                "Purchase requires a visible authoritative tooltip "  # mutation: reason
                "and its source bounds."  # mutation: reason
            )
        price_pattern = rf"(?<![A-Za-z0-9])c\.{expected_price}(?![0-9])"
        if (
            item_name not in tooltip_text
            or "[Food]" not in tooltip_text
            or re.search(price_pattern, tooltip_text) is None
        ):
            raise SafetyViolation(  # mutation: reason
                "Purchase arguments do not match the "  # mutation: reason
                "current food tooltip."  # mutation: reason
            )
        controls = telemetry.ui.visible_controls
        if telemetry.ui.visible_controls_complete is not True or controls is None:
            raise SafetyViolation(  # mutation: reason
                "Purchase requires a complete current inventory-control export."  # mutation: reason
            )
        seller_window = normalize_control_label(target.name)
        tooltip_cells = [
            control
            for control in controls
            if control.role == "item"
            and control.bounds == tooltip_bounds
            and normalize_control_label(control.window) == seller_window
        ]
        if len(tooltip_cells) != 1:
            raise SafetyViolation(  # mutation: reason
                "The inspected cell is not uniquely owned by the exact seller's "
                "current inventory window."  # mutation: reason
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
            raise SafetyViolation(  # mutation: reason
                "Purchase coordinates are outside the "  # mutation: reason
                "current tooltip source."  # mutation: reason
            )

    def _validate_action_constraints(
        self,
        action: Action,
        observation: Observation,
    ) -> None:
        if action.kind not in self.config.allow_action_kinds:
            raise SafetyViolation(  # mutation: reason
                f"Action kind {action.kind!r} is not allowlisted."  # mutation: reason
            )
        self._validate_intrinsic_action_constraints(action, observation)

    def _validate_intrinsic_action_constraints(
        self,
        action: Action,
        observation: Observation,
    ) -> None:
        if isinstance(action, WaitAction) and action.seconds > self.config.max_wait_seconds:
            raise SafetyViolation(  # mutation: reason
                f"Wait {action.seconds:.2f}s exceeds maximum "  # mutation: reason
                f"{self.config.max_wait_seconds:.2f}s."  # mutation: reason
            )
        if isinstance(action, PauseAction) and observation.mode == "live":
            if observation.telemetry is None or observation.telemetry.game.paused is None:
                raise SafetyViolation(  # mutation: reason
                    "Pause action blocked because the current "  # mutation: reason
                    "live pause state is unknown."  # mutation: reason
                )
            if not action.paused and not self.config.allow_live_unpause_actions:
                raise SafetyViolation(  # mutation: reason
                    "Direct live unpause is blocked; "  # mutation: reason
                    "use a bounded movement pulse."  # mutation: reason
                )
        if isinstance(action, SetSpeedAction) and observation.mode == "live":
            telemetry = observation.telemetry
            if (
                telemetry is None
                or telemetry.game.paused is None
                or telemetry.game.speed_multiplier is None
                or "game.pause" not in telemetry.capabilities
                or "game.speed" not in telemetry.capabilities
            ):
                raise SafetyViolation(  # mutation: reason
                    "Set-speed action requires fresh authoritative pause and "
                    "speed state."  # mutation: reason
                )
            if telemetry.game.paused and not self.config.allow_live_unpause_actions:
                raise SafetyViolation(  # mutation: reason
                    "Direct live unpause through set_speed is blocked; "
                    "use a bounded movement option."  # mutation: reason
                )
        if isinstance(action, ReadFieldbookAction) and action.project_id is not None:
            available_project_ids = {
                project.project_id for project in observation.fieldbook_projects
            }
            if observation.active_fieldbook_project is not None:
                available_project_ids.add(observation.active_fieldbook_project.project_id)
            if observation.fieldbook_read is not None:
                available_project_ids.update(observation.fieldbook_read.project_ids)
            available_project_ids.update(
                receipt.project_id
                for receipt in observation.recent_fieldbook_receipts
                if receipt.project_id is not None
            )
            if action.project_id not in available_project_ids:
                raise SafetyViolation(  # mutation: reason
                    f"Fieldbook project {action.project_id!r} is not present "
                    "in the current planner-visible fieldbook context."
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
            raise SafetyViolation(  # mutation: reason
                "Click blocked because live telemetry is stale."  # mutation: reason
            )
        if observation.mode == "live" and action.space == CoordinateSpace.SCREEN:
            raise SafetyViolation(  # mutation: reason
                "Screen-space pointer actions are blocked in "  # mutation: reason
                "live mode; use Kenshi client or normalized "  # mutation: reason
                "coordinates."  # mutation: reason
            )
        if action.space == CoordinateSpace.NORMALIZED:
            if not (0.0 <= action.x <= 1.0 and 0.0 <= action.y <= 1.0):
                raise SafetyViolation(  # mutation: reason
                    "Normalized pointer coordinates must be within [0, 1]."  # mutation: reason
                )
            return
        if action.x < 0 or action.y < 0:
            raise SafetyViolation(  # mutation: reason
                "Pointer coordinates may not be negative."  # mutation: reason
            )
        if action.space == CoordinateSpace.CLIENT:
            ui = observation.telemetry.ui if observation.telemetry is not None else None
            if observation.mode == "live" and (
                ui is None or ui.client_width is None or ui.client_height is None
            ):
                raise SafetyViolation(  # mutation: reason
                    "Client-space pointer action blocked because "  # mutation: reason
                    "Kenshi client dimensions are unknown."  # mutation: reason
                )
            if ui is None:
                return
            if ui.client_width is not None and action.x >= ui.client_width:
                raise SafetyViolation(  # mutation: reason
                    "Pointer x-coordinate is outside the Kenshi window."  # mutation: reason
                )
            if ui.client_height is not None and action.y >= ui.client_height:
                raise SafetyViolation(  # mutation: reason
                    "Pointer y-coordinate is outside the Kenshi window."  # mutation: reason
                )
