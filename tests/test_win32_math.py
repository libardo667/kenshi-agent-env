import pytest

from kenshi_agent.control.base import WindowRect
from kenshi_agent.control.win32 import normalize_virtual_desktop_point, resolve_screen_point
from kenshi_agent.models import CoordinateSpace


def test_normalized_client_point_resolves_to_window_bounds() -> None:
    rect = WindowRect(left=100, top=200, right=1100, bottom=700)
    assert resolve_screen_point(0.0, 0.0, CoordinateSpace.NORMALIZED, rect) == (100, 200)
    assert resolve_screen_point(1.0, 1.0, CoordinateSpace.NORMALIZED, rect) == (1099, 699)
    assert resolve_screen_point(10, 20, CoordinateSpace.CLIENT, rect) == (110, 220)


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
