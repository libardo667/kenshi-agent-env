from datetime import UTC, datetime

from kenshi_agent.models import (
    GameState,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.ui_messages import (
    causally_new_game_message,
    game_message_panel_texts,
)

BOUNDS = NormalizedPointerBounds(
    min_x=0.1,
    max_x=0.9,
    min_y=0.7,
    max_y=0.8,
)


def _snapshot(*controls: VisibleUIControl) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=2,
        captured_at=datetime.now(UTC),
        game=GameState(loaded=True),
        ui=UIState(visible_controls=list(controls)),
    )


def _text(
    label: str,
    *,
    widget_name: str = "ABC_MessageTextBox",
) -> VisibleUIControl:
    return VisibleUIControl(
        label=label,
        role="text",
        widget_name=widget_name,
        widget_type="EditBox",
        bounds=BOUNDS,
    )


def test_only_kenshis_message_panel_is_action_result_evidence() -> None:
    snapshot = _snapshot(
        _text("No room for that item."),
        _text("Money: c.307", widget_name="ABC_MoneyAmountText"),
        VisibleUIControl(
            label="ARRANGE",
            role="button",
            widget_name="ABC_ArrangeButton",
            widget_type="Button",
            bounds=BOUNDS,
        ),
    )

    assert game_message_panel_texts(snapshot) == {
        "ABC_MessageTextBox": "No room for that item."
    }


def test_a_message_must_change_after_the_action_to_claim_causality() -> None:
    baseline = {"ABC_MessageTextBox": "No room for that item."}

    assert (
        causally_new_game_message(
            _snapshot(_text("No room for that item.")),
            baseline,
        )
        is None
    )
    assert (
        causally_new_game_message(
            _snapshot(_text("You can't afford that.")),
            baseline,
        )
        == "You can't afford that."
    )
