import ctypes

import pytest

from kenshi_agent.control.base import WindowRect
from kenshi_agent.control.win32 import (
    AmbiguousWindowError,
    enable_per_monitor_dpi_awareness,
    normalize_virtual_desktop_point,
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
    x, y = normalize_virtual_desktop_point(
        1919, 1079, left=-1920, top=0, width=3840, height=1080
    )
    assert x == 65535
    assert y == 65535


def test_invalid_virtual_desktop_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_virtual_desktop_point(0, 0, left=0, top=0, width=1, height=1080)


def test_wheel_delta_data_encodes_both_directions() -> None:
    assert wheel_delta_data(1) == 120
    assert wheel_delta_data(-1) == 0xFFFFFF88


def test_window_target_must_be_unique() -> None:
    assert select_unique_window([(42, "Kenshi 1.0.68")], "kenshi") == 42
    with pytest.raises(AmbiguousWindowError, match="narrower window title"):
        select_unique_window(
            [(42, "Kenshi 1.0.68"), (84, "Kenshi crash reporter")], "kenshi"
        )
