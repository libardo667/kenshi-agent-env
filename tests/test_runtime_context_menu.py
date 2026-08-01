from __future__ import annotations

import pytest
from pydantic import ValidationError

from kenshi_agent.models import (
    CharacterState,
    ContextMenuProbe,
    RuntimeContextMenu,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.runtime_context_menu import (
    context_menu_capability_is_consistent,
    context_menu_state_is_consistent,
    require_consistent_context_menu_state,
    require_truthful_context_menu_capability,
)


def test_runtime_menu_orders_remain_observation_not_execution_authority() -> None:
    target_id = "entity-runtime-menu-target"
    snapshot = TelemetrySnapshot(
        identity_session_id="session-runtime-menu",
        capabilities=["identity.stable_handles", "ui.context_menu.orders"],
        ui=UIState(
            context_menu_open=True,
            context_menu_probe=ContextMenuProbe.CAPTURED,
            context_menu=RuntimeContextMenu(
                target_id=target_id,
                target_name="Iron Resource",
                # Preserve an unknown future value as game-owned evidence. It
                # must not be rejected or promoted into ContextActionKind.
                task_type_values=[87, 9999],
                task_type_values_complete=True,
            ),
            selected_character_ids=["entity-selected"],
        ),
        squad=[CharacterState(id="entity-selected", name="Tassilo", selected=True)],
        world_targets=[
            WorldTarget(
                id=target_id,
                name="Iron Resource",
                kind="unknown",
                position=Vec3(x=1.0, y=2.0, z=3.0),
                distance=4.0,
                context_actions=[],
                default_task="unknown",
            )
        ],
    )

    assert snapshot.ui.context_menu is not None
    assert snapshot.ui.context_menu.task_type_values == [87, 9999]
    assert snapshot.world_targets[0].context_actions == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "context_menu_open": False,
            "context_menu_probe": "captured",
            "context_menu": {
                "target_id": "entity-target",
                "task_type_values": [],
                "task_type_values_complete": True,
            },
        },
        {"context_menu_open": True, "context_menu_probe": "captured"},
        {
            "context_menu_open": True,
            "context_menu_probe": "invalid_target",
            "context_menu": {
                "target_id": "entity-target",
                "task_type_values": [87],
                "task_type_values_complete": True,
            },
        },
    ],
)
def test_runtime_menu_capture_fails_closed_on_inconsistent_ui_state(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="context menu"):
        UIState.model_validate(payload)


def test_runtime_menu_capability_requires_a_probe_and_stable_identity() -> None:
    with pytest.raises(ValidationError, match="ui.context_menu.orders"):
        TelemetrySnapshot(capabilities=["ui.context_menu.orders"])

    with pytest.raises(ValidationError, match="identity.stable_handles"):
        TelemetrySnapshot(
            capabilities=["ui.context_menu.orders"],
            ui=UIState(
                context_menu_open=True,
                context_menu_probe=ContextMenuProbe.CAPTURED,
                context_menu=RuntimeContextMenu(
                    target_id="entity-target",
                    task_type_values=[87],
                    task_type_values_complete=True,
                ),
            ),
        )


def test_runtime_menu_validation_errors_identify_the_failed_authority() -> None:
    with pytest.raises(ValueError) as state_error:
        require_consistent_context_menu_state(
            context_menu_open=True,
            context_menu_probe="captured",
            context_menu=None,
        )
    assert state_error.value.args == (
        "context menu open, probe, and payload are inconsistent",
    )

    with pytest.raises(ValueError) as envelope_error:
        require_truthful_context_menu_capability(
            capabilities=["ui.context_menu.orders"],
            context_menu_open=None,
            context_menu_probe="closed",
            context_menu=None,
        )
    assert envelope_error.value.args == (
        "ui.context_menu.orders requires context menu open and probe state",
    )

    with pytest.raises(ValueError) as identity_error:
        require_truthful_context_menu_capability(
            capabilities=["ui.context_menu.orders"],
            context_menu_open=True,
            context_menu_probe="captured",
            context_menu=object(),
        )
    assert identity_error.value.args == (
        "runtime context menu targets require identity.stable_handles",
    )


@pytest.mark.parametrize("open_state", [None, False, True])
@pytest.mark.parametrize("probe", [None, "closed", "captured", "invalid_target"])
@pytest.mark.parametrize("has_capture", [False, True])
def test_runtime_menu_state_truth_table(
    open_state: bool | None,
    probe: str | None,
    has_capture: bool,
) -> None:
    expected = (
        (probe is None and not has_capture)
        or (probe == "closed" and open_state is False and not has_capture)
        or (probe == "captured" and open_state is True and has_capture)
        or (probe == "invalid_target" and open_state is True and not has_capture)
    )

    assert (
        context_menu_state_is_consistent(
            context_menu_open=open_state,
            context_menu_probe=probe,
            has_context_menu=has_capture,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("capabilities", "open_state", "probe", "has_capture", "expected"),
    [
        ([], None, None, False, True),
        (["ui.context_menu.orders"], None, None, False, False),
        (["ui.context_menu.orders"], None, "closed", False, False),
        (["ui.context_menu.orders"], False, None, False, False),
        (["ui.context_menu.orders"], False, "closed", False, True),
        (["ui.context_menu.orders"], True, "captured", True, False),
        (
            ["ui.context_menu.orders", "identity.stable_handles"],
            True,
            "captured",
            True,
            True,
        ),
    ],
)
def test_runtime_menu_capability_truth_table(
    capabilities: list[str],
    open_state: bool | None,
    probe: str | None,
    has_capture: bool,
    expected: bool,
) -> None:
    assert (
        context_menu_capability_is_consistent(
            capabilities=capabilities,
            context_menu_open=open_state,
            context_menu_probe=probe,
            has_context_menu=has_capture,
        )
        is expected
    )
