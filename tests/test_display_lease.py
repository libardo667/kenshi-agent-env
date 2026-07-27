from __future__ import annotations

from collections.abc import Iterator

import pytest

from kenshi_agent.display_lease import (
    DisplayLeaseError,
    DisplayScreen,
    DisplayTopology,
    DisplayTopologyController,
    external_display_lease,
)


def _screen(device: str, width: int, height: int) -> DisplayScreen:
    return DisplayScreen(
        device_name=device,
        width=width,
        height=height,
        primary=device == "external",
    )


def _controller(
    states: Iterator[DisplayTopology],
    switches: list[str],
) -> DisplayTopologyController:
    return DisplayTopologyController(
        query_state=lambda: next(states),
        switch_topology=switches.append,
        sleep=lambda _: None,
        timeout_seconds=0.1,
    )


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_external_display_lease_restores_panel_after_body_failure(
    failure_type: type[BaseException],
) -> None:
    switches: list[str] = []
    states = iter(
        (
            DisplayTopology(
                screens=(_screen("internal", 2496, 1664), _screen("external", 1920, 1080)),
                internal_active=True,
                external_active=True,
            ),
            DisplayTopology(
                screens=(_screen("external", 1920, 1080),),
                internal_active=False,
                external_active=True,
            ),
            DisplayTopology(
                screens=(_screen("internal", 2496, 1664), _screen("external", 1920, 1080)),
                internal_active=True,
                external_active=True,
            ),
        )
    )
    controller = _controller(states, switches)

    with pytest.raises(failure_type, match="journey failed"):
        with external_display_lease(controller):
            raise failure_type("journey failed")

    assert switches == ["external", "extend"]


def test_external_display_lease_fails_before_switch_without_1080p_external() -> None:
    switches: list[str] = []
    controller = _controller(
        iter(
            (
                DisplayTopology(
                    screens=(_screen("internal", 2496, 1664),),
                    internal_active=True,
                    external_active=False,
                ),
            )
        ),
        switches,
    )

    with pytest.raises(DisplayLeaseError, match="external 1920x1080"):
        with external_display_lease(controller):
            raise AssertionError("lease body must not run")

    assert switches == []


def test_external_display_lease_reports_restore_failure() -> None:
    switches: list[str] = []
    external_only = DisplayTopology(
        screens=(_screen("external", 1920, 1080),),
        internal_active=False,
        external_active=True,
    )
    controller = _controller(
        iter(
            (
                DisplayTopology(
                    screens=(
                        _screen("internal", 2496, 1664),
                        _screen("external", 1920, 1080),
                    ),
                    internal_active=True,
                    external_active=True,
                ),
                external_only,
                external_only,
            )
        ),
        switches,
    )

    with pytest.raises(DisplayLeaseError, match=r"Win\+P.*Extend"):
        with external_display_lease(controller):
            pass

    assert switches == ["external", "extend"]
