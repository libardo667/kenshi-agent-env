"""Screen, visible-control, scroll, and named-binding handlers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from ... import operation_definitions as operations
from ...core.evidence import (
    QuicksaveEvidence,
    QuicksaveStatus,
    SemanticActionReceipt,
)
from ...core.operation import (
    Action,
    ActivateVisibleControlAction,
    ClickAction,
    DismissScreenAction,
    GameBinding,
    GameScreen,
    HotkeyAction,
    KeyAction,
    OpenScreenAction,
    ScrollAction,
    ScrollScreenAction,
    UseGameBindingAction,
    game_binding_primitive,
)
from ...core.planning import (
    SCREEN_BINDINGS,
    screen_is_open,
)
from ...core.telemetry import window_close_point
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from ...input_boundary import ExecutionToken
from ...operation_definitions import BoundOperation
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .input_binding import authorized_input_binding
from .kenshi_surface import KenshiControlSurface

ScreenOperation = Callable[
    [Action],
    Awaitable[Transition],
]


class ScreenMechanicsPort(Protocol):
    """Exact screen mechanics; no family dispatcher is exposed."""

    async def open_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def dismiss_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def activate_visible_control(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def scroll_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def use_game_binding(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class ScreenHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        transition = await _execute(self.operation, bound.operation, context)
        return _delivery_result(transition)

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


@dataclass(frozen=True, slots=True)
class GameBindingHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(UseGameBindingAction, bound.operation)
        transition = await _execute(self.operation, action, context)
        if action.binding is not GameBinding.QUICKSAVE:
            return _delivery_result(transition)
        quicksave = (
            transition.receipt.semantic.quicksave
            if transition.receipt.semantic is not None
            else None
        )
        succeeded = quicksave is not None and quicksave.status is QuicksaveStatus.SAVED
        return OperationResult(
            status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
            observation=transition.observation,
            reason=(
                quicksave.reason
                if quicksave is not None
                else "Controller returned no typed quicksave evidence."
            ),
            transition=transition,
            terminated=transition.terminated,
            success=transition.success,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


async def _execute(
    operation: Callable[..., Awaitable[Transition]],
    action: Action,
    context: OperationContext,
) -> Transition:
    command = context.command
    if command is None:
        raise RuntimeError("Screen operation has no command authority.")
    return await operation(action, command=command, token=context.token)


def _delivery_result(transition: Transition) -> OperationResult:
    accepted = transition.receipt.accepted or transition.receipt.executed
    return OperationResult(
        status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
        observation=transition.observation,
        reason=transition.receipt.message,
        transition=transition,
        terminated=transition.terminated,
        success=transition.success,
    )


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason="Screen operation was cancelled.",
    )


def screen_handlers(port: ScreenMechanicsPort) -> dict[str, OperationHandler]:
    return {
        "screens.open_screen": ScreenHandler(port.open_screen),
        "screens.dismiss_screen": ScreenHandler(port.dismiss_screen),
        "screens.activate_visible_control": ScreenHandler(port.activate_visible_control),
        "screens.scroll_screen": ScreenHandler(port.scroll_screen),
        "screens.use_game_binding": GameBindingHandler(port.use_game_binding),
    }


@dataclass(frozen=True, slots=True)
class _QuicksaveTreeState:
    files: tuple[tuple[str, int, int], ...]
    quick_save_size_bytes: int | None


def _quicksave_tree_state(path: Path) -> _QuicksaveTreeState:
    """Read one exact save slot without following links or opening its contents."""

    if not path.exists():
        return _QuicksaveTreeState(files=(), quick_save_size_bytes=None)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"Quicksave slot is not a real directory: {path}")
    files: list[tuple[str, int, int]] = []
    quick_save_size: int | None = None
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise RuntimeError(f"Quicksave completion refuses symbolic links: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise RuntimeError(f"Quicksave completion found an unsupported entry: {candidate}")
        stat = candidate.stat()
        relative = candidate.relative_to(path).as_posix()
        files.append((relative, stat.st_size, stat.st_mtime_ns))
        if relative == "quick.save" and stat.st_size > 0:
            quick_save_size = stat.st_size
    return _QuicksaveTreeState(
        files=tuple(files),
        quick_save_size_bytes=quick_save_size,
    )


def _changed_quicksave_files(
    before: _QuicksaveTreeState,
    after: _QuicksaveTreeState,
) -> int:
    before_by_path = {path: (size, modified) for path, size, modified in before.files}
    after_by_path = {path: (size, modified) for path, size, modified in after.files}
    return sum(
        before_by_path.get(path) != after_by_path.get(path)
        for path in before_by_path.keys() | after_by_path.keys()
    )


class KenshiScreenMechanics:
    """Screen, binding, scroll, and visible-control mechanics."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def activate_visible_control(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_visible_operation(
                current, started, dispatch, token
            ),
        )

    async def dismiss_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_dismiss_operation(
                current, started, dispatch, token
            ),
        )

    async def open_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_open_screen_operation(
                current, started, dispatch, token
            ),
        )

    async def use_game_binding(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_binding_operation(
                current, started, dispatch, token
            ),
        )

    async def scroll_screen(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_scroll_operation(
                current, started, dispatch, token
            ),
        )

    async def _execute_visible_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_visible_control(
            cast(ActivateVisibleControlAction, action), started, token
        )

    async def _execute_dismiss_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_dismiss_screen(cast(DismissScreenAction, action), started, token)

    async def _execute_open_screen_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_open_screen(cast(OpenScreenAction, action), started, token)

    async def _execute_binding_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_game_binding(cast(UseGameBindingAction, action), started, token)

    async def _execute_scroll_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_scroll_screen(cast(ScrollScreenAction, action), started, token)

    async def _execute_visible_control(
        self,
        action: ActivateVisibleControlAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Click exactly one currently advertised control, re-resolved in-lease.

        This runs inside the acquired input lease, after the generic input
        boundary already revalidated the plan's typed authority. What is checked
        here is the part only this action knows: that the exact label, role,
        uniqueness, and bounds it bound to are still what the interface reports.
        Any drift emits zero input.
        """

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundVisibleControl,
        )
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self._surface.controller.execute(
            ClickAction(
                x=x,
                y=y,
                hold_seconds=self._surface.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.ACTIVATE_VISIBLE_CONTROL_DEFINITION.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved to exactly one current control inside the input lease "
                f"before the click. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Activated the current {binding.resolved_role} control "
                    f"{binding.resolved_label!r} at its observed bounds. "
                    "A later observation must confirm the resulting transition."
                ),
            }
        )

    async def _execute_open_screen(
        self,
        action: OpenScreenAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Have the named screen open, pressing nothing when it already is.

        The agent used to name a binding and author its own proof that the
        binding worked. One live run then looped: a window it had asked for
        opened, telemetry did not show it, and nothing could tell the agent it
        had already succeeded. Here the controller reads the exact screen state,
        presses only when it needs to, and the contract's terminal proves which
        screen arrived rather than that something changed.
        """

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.EmptyBinding,
        )
        if observation.telemetry is None:
            raise RuntimeError("No input was sent: current telemetry is unavailable.")

        already = screen_is_open(action.screen, observation.telemetry)
        control = SCREEN_BINDINGS[action.screen]
        if already:
            semantic = SemanticActionReceipt(
                action_kind=action.kind,
                contract_version=operations.OPEN_SCREEN_DEFINITION.version,
                resolved_label=action.screen.value,
                source_revision=observation.world_revision,
                revalidation=binding.reason,
            )
            return ActionReceipt(
                action=action,
                control_mode=self._surface.port.control_mode,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=0,
                started_at=started,
                finished_at=datetime.now(UTC),
                message=(
                    f"The {action.screen.value} screen was already open, so no key was pressed."
                ),
                semantic=semantic,
            )

        primitive = game_binding_primitive(control)
        await self._surface.controller.execute(primitive)
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.OPEN_SCREEN_DEFINITION.version,
            resolved_label=action.screen.value,
            source_revision=observation.world_revision,
            revalidation=binding.reason,
        )
        return ActionReceipt(
            action=action,
            control_mode=self._surface.port.control_mode,
            accepted=True,
            executed=True,
            dry_run=False,
            primitive_actions=1,
            started_at=started,
            finished_at=datetime.now(UTC),
            message=(
                f"Pressed {control.value} to open the {action.screen.value} "
                "screen. A later observation must confirm it arrived."
            ),
            semantic=semantic,
        )

    async def _execute_game_binding(
        self,
        action: UseGameBindingAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Send the key Kenshi itself binds to this control.

        Re-checks inside the lease that a game is still loaded, because a key
        pressed at a loading screen is swallowed with no evidence either way -
        the silent failure this action exists to replace.
        """

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundNamedOperation,
        )
        quicksave_before = (
            _quicksave_tree_state(self._surface.port.quicksave_dir)
            if action.binding is GameBinding.QUICKSAVE
            and self._surface.port.quicksave_dir is not None
            else None
        )
        primitive = game_binding_primitive(action.binding)
        primitive_receipt = await self._surface.controller.execute(primitive)
        if isinstance(primitive, KeyAction):
            mapped_input = primitive.key
        elif isinstance(primitive, HotkeyAction):
            mapped_input = "+".join(primitive.keys)
        else:
            mapped_input = primitive.button.value
        quicksave = None
        if action.binding is GameBinding.QUICKSAVE:
            if self._surface.port.quicksave_dir is None or quicksave_before is None:
                raise RuntimeError("Quicksave completion monitoring disappeared before input.")
            quicksave = await self._wait_for_quicksave_completion(
                self._surface.port.quicksave_dir,
                quicksave_before,
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.USE_GAME_BINDING_DEFINITION.version,
            resolved_label=action.binding.value,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed a game was loaded inside the input lease before "
                f"pressing the key. {binding.reason}"
            ),
            quicksave=quicksave,
        )
        completion_message = (
            f" {quicksave.reason}"
            if quicksave is not None
            else " A later observation must confirm the transition."
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Pressed Kenshi's {action.binding.value!r} binding "
                    f"({mapped_input!r}), "
                    f"expecting: {action.expected_effect}."
                    f"{completion_message}"
                ),
            }
        )

    async def _wait_for_quicksave_completion(
        self,
        path: Path,
        before: _QuicksaveTreeState,
    ) -> QuicksaveEvidence:
        """Require an exact changed slot to stop mutating after F5."""

        deadline = time.monotonic() + self._surface.port.quicksave_timeout_seconds
        previous = before
        latest = before
        stable_since: float | None = None
        last_error: OSError | RuntimeError | None = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            try:
                current = _quicksave_tree_state(path)
                last_error = None
            except (OSError, RuntimeError) as exc:
                last_error = exc
                stable_since = None
                await asyncio.sleep(0.05)
                continue
            latest = current
            changed = current.files != before.files
            complete_file = current.quick_save_size_bytes is not None
            if changed and complete_file:
                if current.files != previous.files:
                    stable_since = now
                elif stable_since is None:
                    stable_since = now
                elif now - stable_since >= self._surface.port.quicksave_stable_seconds:
                    changed_files = _changed_quicksave_files(before, current)
                    return QuicksaveEvidence(
                        status=QuicksaveStatus.SAVED,
                        changed_files=changed_files,
                        quick_save_size_bytes=current.quick_save_size_bytes,
                        quiescent_seconds=now - stable_since,
                        reason=(
                            "Observed the exact quicksave tree change after F5 "
                            f"and remain quiescent for {now - stable_since:.3f}s."
                        ),
                    )
            else:
                stable_since = None
            previous = current
            await asyncio.sleep(
                min(0.05, max(0.005, self._surface.port.quicksave_stable_seconds / 2.0))
            )
        changed_files = _changed_quicksave_files(before, latest)
        error = (
            f" Last monitor error: {type(last_error).__name__}: {last_error}."
            if last_error is not None
            else ""
        )
        return QuicksaveEvidence(
            status=QuicksaveStatus.NOT_OBSERVED,
            changed_files=changed_files,
            quick_save_size_bytes=latest.quick_save_size_bytes,
            quiescent_seconds=0.0,
            reason=(
                "F5 was sent, but the exact quicksave tree did not produce a "
                "changed, nonempty, quiescent quick.save before the completion "
                f"timeout.{error}"
            ),
        )

    async def _execute_scroll_screen(
        self,
        action: ScrollScreenAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Scroll at the centre of one window's own observed bounds.

        Re-resolves the window inside the lease, because a window that closed
        during the polite wait would otherwise have its scroll delivered to
        whatever is behind it.
        """

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundVisibleControl,
        )
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self._surface.controller.execute(
            ScrollAction(x=x, y=y, notches=action.notches)
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.SCROLL_SCREEN_DEFINITION.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                f"Re-resolved window {action.window!r} inside the input lease. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Scrolled {action.notches:+d} notches inside {action.window!r}. "
                    "A later observation must report the newly visible controls."
                ),
            }
        )

    async def _execute_dismiss_screen(
        self,
        action: DismissScreenAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Back out of the currently open screen with one configured key.

        Re-checks inside the lease that the screen the planner named is still
        the one that is open, so a screen that changed during the polite wait
        cannot be closed by a stale intention.
        """

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundScreenDismissal,
        )
        if binding.resolved_bounds is not None:
            # A window closes by its own close box. Escape does not close
            # Kenshi's inventory or trade windows at all - with nothing else
            # open it opens the ESC menu instead.
            close_x, close_y = window_close_point(binding.resolved_bounds)
            primitive_receipt = await self._surface.controller.execute(
                ClickAction(
                    x=close_x,
                    y=close_y,
                    hold_seconds=self._surface.controls_config.control_activation_hold_seconds,
                )
            )
        elif isinstance(action.expected_screen, GameScreen):
            primitive_receipt = await self._surface.controller.execute(
                game_binding_primitive(SCREEN_BINDINGS[action.expected_screen])
            )
        else:
            primitive_receipt = await self._surface.controller.execute(
                KeyAction(key=self._surface.controls_config.dismiss_screen_key)
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.DISMISS_SCREEN_DEFINITION.version,
            resolved_label=binding.resolved_label,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed the expected screen was still open inside the input "
                f"lease before dismissing it. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    (
                        f"Closed the {action.window!r} window on the "
                        f"{action.expected_screen!r} screen via its own close box."
                        if binding.resolved_bounds is not None
                        else (
                            f"Closed the current {action.expected_screen.value!r} "
                            "screen through its named toggle binding."
                            if isinstance(action.expected_screen, GameScreen)
                            else (
                                f"Dismissed the current {action.expected_screen!r} "
                                "screen with the configured "
                                f"{self._surface.controls_config.dismiss_screen_key!r} key."
                            )
                        )
                    )
                    + " A later observation must confirm the transition."
                ),
            }
        )
