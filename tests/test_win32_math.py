import asyncio
import ctypes

import pytest

from kenshi_agent.control.base import WindowRect
from kenshi_agent.control.win32 import (
    AmbiguousWindowError,
    Win32InputController,
    enable_per_monitor_dpi_awareness,
    normalize_virtual_desktop_point,
    relative_drag_steps,
    relative_pointer_delta,
    resolve_screen_point,
    select_unique_window,
    wheel_delta_data,
)
from kenshi_agent.models import CoordinateSpace


class FakeModernUser32:
    def __init__(self) -> None:
        self.contexts: list[int | None] = []

    def SetProcessDpiAwarenessContext(self, context: ctypes.c_void_p) -> int:
        self.contexts.append(context.value)
        return 1


class FakeLegacyUser32:
    def __init__(self) -> None:
        self.calls = 0

    def SetProcessDPIAware(self) -> int:
        self.calls += 1
        return 1


class LeaseProbeUser32:
    @staticmethod
    def GetForegroundWindow() -> int:
        return 0


class LeaseProbeController(Win32InputController):
    def __init__(self) -> None:
        self.polite_input_enabled = True
        self._lease_active = False
        self.pointer_mode = "absolute"
        self._last_lease_wait_seconds = -1.0
        self._lease_alt_tab_on_restore = False
        self._lease_kenshi_foreground = None
        self.wait_calls = 0
        self.user32 = LeaseProbeUser32()

    async def _wait_for_input_turn(self) -> None:
        self.wait_calls += 1
        raise RuntimeError("polite wait invoked")

    def _cursor_position(self) -> tuple[int, int]:
        return (0, 0)

    def _last_input_tick(self) -> int:
        return 1

    def user_input_detected(self) -> bool:
        return False

    def _restore_desktop_state(self) -> None:
        return None


def test_safety_input_lease_bypasses_only_the_polite_wait() -> None:
    async def scenario() -> None:
        controller = LeaseProbeController()

        with pytest.raises(RuntimeError, match="polite wait invoked"):
            async with controller.input_lease():
                pass
        assert controller.wait_calls == 1

        async with controller.safety_input_lease():
            assert controller._lease_active is True

        assert controller.wait_calls == 1
        assert controller._lease_active is False

    asyncio.run(scenario())


def test_polite_wait_names_a_stuck_alt_without_sending_input() -> None:
    class HeldAltController(Win32InputController):
        def __init__(self) -> None:
            self.idle_seconds_before_input = 0.0
            self.max_wait_for_input_turn_seconds = 0.0

        def _idle_seconds(self) -> float:
            return 10.0

        def _pressed_input_vks(self) -> tuple[int, ...]:
            return (0x12, 0xA4)

    async def scenario() -> None:
        with pytest.raises(
            RuntimeError,
            match=r"0x12 \(alt\).*0xA4 \(left alt\)",
        ):
            await HeldAltController()._wait_for_input_turn()

    asyncio.run(scenario())


def test_normalized_client_point_resolves_to_window_bounds() -> None:
    rect = WindowRect(left=100, top=200, right=1100, bottom=700)
    assert resolve_screen_point(0.0, 0.0, CoordinateSpace.NORMALIZED, rect) == (100, 200)
    assert resolve_screen_point(1.0, 1.0, CoordinateSpace.NORMALIZED, rect) == (1099, 699)
    assert resolve_screen_point(10, 20, CoordinateSpace.CLIENT, rect) == (110, 220)


def test_per_monitor_dpi_awareness_uses_modern_context() -> None:
    user32 = FakeModernUser32()

    assert enable_per_monitor_dpi_awareness(user32)
    assert user32.contexts == [ctypes.c_void_p(-4).value]


def test_per_monitor_dpi_awareness_falls_back_for_older_windows() -> None:
    user32 = FakeLegacyUser32()

    assert enable_per_monitor_dpi_awareness(user32)
    assert user32.calls == 1


def test_virtual_desktop_normalization_supports_negative_origin() -> None:
    assert normalize_virtual_desktop_point(
        -1920, 0, left=-1920, top=0, width=3840, height=1080
    ) == (0, 0)
    x, y = normalize_virtual_desktop_point(1919, 1079, left=-1920, top=0, width=3840, height=1080)
    assert x == 65535
    assert y == 65535


def test_invalid_virtual_desktop_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_virtual_desktop_point(0, 0, left=0, top=0, width=1, height=1080)


def test_wheel_delta_data_encodes_both_directions() -> None:
    assert wheel_delta_data(1) == 120
    assert wheel_delta_data(-1) == 0xFFFFFF88


def test_camera_tilt_key_names_resolve_to_windows_oem_keys() -> None:
    assert Win32InputController._vk("comma") == 0xBC
    assert Win32InputController._vk("period") == 0xBE


def test_relative_drag_steps_preserve_exact_bounded_motion() -> None:
    moves = relative_drag_steps(96, -32, 8)

    assert moves == ((12, -4),) * 8
    assert sum(delta_x for delta_x, _ in moves) == 96
    assert sum(delta_y for _, delta_y in moves) == -32


def test_relative_pointer_delta_is_bounded_and_converges() -> None:
    assert relative_pointer_delta((0, 0), (100, -50), max_step_pixels=12, tolerance_pixels=1) == (
        12,
        -12,
    )
    assert relative_pointer_delta(
        (99, -49), (100, -50), max_step_pixels=12, tolerance_pixels=1
    ) == (0, 0)
    assert relative_pointer_delta(
        (102, -48), (100, -50), max_step_pixels=12, tolerance_pixels=1
    ) == (-1, -1)
    assert relative_pointer_delta(
        (100, 10), (100, 0), max_step_pixels=12, tolerance_pixels=1
    ) == (0, -5)


def test_window_target_must_be_unique() -> None:
    assert select_unique_window([(42, "Kenshi 1.0.68")], "kenshi") == 42
    with pytest.raises(AmbiguousWindowError, match="narrower window title"):
        select_unique_window([(42, "Kenshi 1.0.68"), (84, "Kenshi crash reporter")], "kenshi")


def test_relative_correction_needs_a_synchronised_starting_point() -> None:
    """Kenshi's drawn cursor is only knowable while it moves with the OS cursor.

    An absolute warp is invisible to Kenshi, so the two desynchronise and the
    correction loop reads "already at target" while Kenshi's cursor sits
    elsewhere entirely — the launcher clicked CONTINUE with the OS cursor
    exactly on it, and Kenshi never saw the click.
    """

    from kenshi_agent.config import ControlsConfig

    # The warp must not be on by default in relative mode.
    assert ControlsConfig().relative_pointer_warp_enabled is False

    # A zero delta is what the loop sends once it believes it has arrived, which
    # is precisely why a desynchronised start is unrecoverable without a resync.
    assert relative_pointer_delta((651, 185), (651, 185), max_step_pixels=12,
                                  tolerance_pixels=1) == (0, 0)
