"""Compact evidence for game-owned runtime context-menu orders.

The telemetry snapshot is intentionally rich and transient.  This tracker
turns each distinct authoritative open-menu state into one small event that a
run ledger can retain.  It copies the separately reviewed target authority for
comparison, but never derives or mutates that authority from observed orders.
"""

from __future__ import annotations

from .core.observation import Observation

ContextMenuEvidence = dict[str, object]
_EvidenceSignature = tuple[object, ...]


class ContextMenuEvidenceTracker:
    """Emit on a new open-menu state and re-arm after the menu closes."""

    def __init__(self) -> None:
        # None is only the inactive sentinel; replacing it with any value that
        # cannot equal a signature is behaviorally identical.
        self._active_signature: _EvidenceSignature | None = None  # pragma: no mutate

    def observe(self, observation: Observation) -> ContextMenuEvidence | None:
        telemetry = observation.telemetry
        if telemetry is None:
            self._active_signature = None  # pragma: no mutate
            return None
        menu = telemetry.ui.context_menu
        if menu is None:
            self._active_signature = None  # pragma: no mutate
            return None

        target = next(
            (item for item in telemetry.world_targets if item.id == menu.target_id),
            None,
        )
        selected_character_ids = tuple(telemetry.selected_character_ids)
        reviewed_actions = (
            tuple(action.value for action in target.context_actions)
            if target is not None
            else ()
        )
        target_kind = (
            target.kind
            if target is not None
            else "squad_character"
            if menu.target_id in selected_character_ids
            else None
        )
        reviewed_default_task = target.default_task if target is not None else None
        task_type_values = tuple(menu.task_type_values)
        signature: _EvidenceSignature = (
            telemetry.identity_session_id,
            menu.target_id,
            menu.target_name,
            target_kind,
            task_type_values,
            menu.task_type_values_complete,
            selected_character_ids,
            reviewed_actions,
            reviewed_default_task,
        )
        if signature == self._active_signature:
            return None
        self._active_signature = signature
        return {
            "identity_session_id": telemetry.identity_session_id,
            "target_id": menu.target_id,
            "target_name": menu.target_name,
            "target_kind": target_kind,
            "task_type_values": list(task_type_values),
            "task_type_values_complete": menu.task_type_values_complete,
            "selected_character_ids": list(selected_character_ids),
            "reviewed_context_actions": list(reviewed_actions),
            "reviewed_default_task": reviewed_default_task,
        }
