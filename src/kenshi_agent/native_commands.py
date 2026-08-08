from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from .core.transport import NativeCommandRequest

NATIVE_APPROACH_WIRE_COMMAND: Literal["approach_confirmed_vendor"] = (
    "approach_confirmed_vendor"
)
NATIVE_SQUAD_SELECTION_WIRE_COMMAND: Literal["select_squad_member"] = (
    "select_squad_member"
)
NATIVE_SQUAD_REGROUP_WIRE_COMMAND: Literal["regroup_with_squad_member"] = (
    "regroup_with_squad_member"
)
NATIVE_DIRECTION_WIRE_COMMAND: Literal["move_in_direction"] = "move_in_direction"
NATIVE_MOVE_WIRE_COMMAND: Literal["move_to_character"] = "move_to_character"
NATIVE_SHIFT_BODY_WIRE_COMMAND: Literal["shift_into_body"] = "shift_into_body"
NATIVE_MAP_TRAVEL_WIRE_COMMAND: Literal["travel_to_map_destination"] = (
    "travel_to_map_destination"
)
NATIVE_EXIT_BUILDING_WIRE_COMMAND: Literal["exit_current_building"] = (
    "exit_current_building"
)
NATIVE_CONTEXT_ACTION_WIRE_COMMAND: Literal["perform_context_action"] = (
    "perform_context_action"
)
NATIVE_CHARACTER_ORDER_WIRE_COMMAND: Literal["perform_character_order"] = (
    "perform_character_order"
)
NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND: Literal["produce_resource_output"] = (
    "produce_resource_output"
)
NATIVE_TRANSFER_WIRE_COMMAND: Literal["transfer_item"] = "transfer_item"
NATIVE_TRADE_WINDOW_WIRE_COMMAND: Literal["open_trade_window"] = "open_trade_window"
NATIVE_RESOURCE_SURVEY_WIRE_COMMAND: Literal["survey_local_resources"] = (
    "survey_local_resources"
)


def write_native_command_request_atomic(
    path: Path,
    request: NativeCommandRequest,
) -> None:
    """Atomically publish one complete, strictly validated native request."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = request.model_dump_json(indent=2).encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
