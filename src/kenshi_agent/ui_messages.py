"""Causal extraction of Kenshi's transient message-panel text."""

from __future__ import annotations

from collections.abc import Mapping

from .models import TelemetrySnapshot

MESSAGE_PANEL_WIDGET = "MessageTextBox"


def game_message_panel_texts(snapshot: TelemetrySnapshot) -> dict[str, str]:
    """Return visible game-message text keyed by its exact widget identity.

    `Kenshi_MessagePanel.layout` owns one `MessageTextBox`. Other visible text
    is ordinary interface content, so treating every new caption as an action
    refusal would let unrelated HUD updates claim causality.
    """

    messages: dict[str, str] = {}
    for control in snapshot.ui.visible_controls or ():
        text = control.label.strip()
        if (
            control.role == "text"
            and control.layout_widget_name == MESSAGE_PANEL_WIDGET
            and text
        ):
            messages[control.widget_name] = text
    return messages


def causally_new_game_message(
    snapshot: TelemetrySnapshot,
    baseline: Mapping[str, str],
) -> str | None:
    """Return the first message-panel value absent or different at baseline."""

    for widget_name, text in game_message_panel_texts(snapshot).items():
        if baseline.get(widget_name) != text:
            return text
    return None
