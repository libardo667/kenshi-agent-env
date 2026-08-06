from __future__ import annotations

from .affordances import OPERATION_BINDING_AUTHORITY, OperationBindingError
from .config import SafetyConfig
from .core.authority import AuthorizationCode
from .core.observation import Observation
from .core.operation import (
    Action,
    ClickAction,
    ControlMode,
    CoordinateSpace,
    MoveCursorAction,
    PauseAction,
    PointerActionClass,
    PurchaseItemAction,
    ReadFieldbookAction,
    ScrollAction,
    SetSpeedAction,
    WaitAction,
    is_controller_primitive,
)
from .operation_definitions import BoundOperation


class SafetyViolation(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: AuthorizationCode = AuthorizationCode.POLICY_DISALLOWED,
    ) -> None:
        super().__init__(message)
        self.code = code


class OperationPolicy:
    def __init__(
        self,
        config: SafetyConfig,
        *,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    ) -> None:
        self.config = config
        self.control_mode = control_mode

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
        primitive_actions = definition.primitive_action_bound_for(action)
        self._validate_bound_operation_definition(
            bound,
            observation,
            primitive_actions=primitive_actions,
        )
        if isinstance(action, PurchaseItemAction) and observation.mode == "live":
            self._validate_generic_purchase(action, observation)
        return action

    def pointer_class_for(self, bound: BoundOperation) -> PointerActionClass:
        """Resolve calibration policy without teaching the external adapter semantics."""

        return bound.definition.pointer_class

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
        # Safety enforces the resolved contract; it does not keep its own model
        # of what each scope means. It used to: PRIMARY was read here as "exactly
        # one selected character", while the registry's own rule is that Kenshi's
        # exported primary exists and is among the selected. A primary-scoped
        # order was therefore refused whenever a second character happened to be
        # selected alongside it - the registry and safety disagreeing about the
        # same scope, with safety winning silently.
        if not definition.satisfies_recipient_scope(observation, bound.operation):
            scope = definition.recipient_scope_for(bound.operation, observation)
            raise SafetyViolation(
                f"Action {definition.kind!r} addresses {scope.value!r}, which the "
                "current selection cannot supply.",
                code=AuthorizationCode.SELECTION_INVALID,
            )

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
