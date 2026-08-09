"""The one place that decides what the playing model sees.

An observation arrives describing the world. What reaches a planner is that plus
everything the run knows about itself: what it recently did and how that turned
out, what durable memory makes relevant now, which continuity writes are
degraded, whether an advisor brief may be requested, and the answer to any read
the last planner call asked for.

Assembling that in one place is what keeps an observation model from importing
recall, affordance enumeration, or advisor state to describe itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .affordances import offered_affordances
from .config import PlanningConfig
from .continuity import ContinuityLedger
from .continuity_service import ContinuityService
from .core.advisor import AdvisorAvailability
from .core.observation import Observation
from .nutrition import (
    model_facing_telemetry_payload,
    roster_nutrition_digest,
)
from .observation_budget import budget_observation_payload


def planner_affordance_digest(observation: Observation) -> list[dict[str, Any]]:
    """Project the one runtime-authored action surface for the playing model."""

    return [offer.planner_digest() for offer in offered_affordances(observation)]


def planner_nutrition_digest(observation: Observation) -> dict[str, Any]:
    """Interpret the native nutrition reserve for the player roster."""

    if observation.telemetry is None:
        return {}
    return roster_nutrition_digest(
        observation.telemetry.roster,
        observation.telemetry.selected_character_ids,
    )


def _planner_json(value: Any) -> str:
    """Render the canonical compact planner document."""

    # pragma: no mutate start
    return json.dumps(
        value,
        separators=(",", ":"),
        # `json.dumps` treats None exactly like False for this flag.
        ensure_ascii=False,
    )
    # pragma: no mutate end


def _project_planner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove deterministic runtime mechanics from the playing-model view."""

    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict):
        return payload
    telemetry["capabilities"] = []
    ui = telemetry.get("ui")
    if isinstance(ui, dict) and ui.get("visible_controls") is not None:
        ui["visible_controls"] = []
    native = telemetry.get("native_control")
    if isinstance(native, dict):
        native.update(
            {
                "active_command_id": None,
                "acknowledgements": [],
                "last_command_sequence": 0,
                "last_command": None,
                "last_result": None,
                "last_target": None,
                "last_target_id": None,
            }
        )
    return payload


def render_planner_payload(
    observation: Observation,
    *,
    max_chars: int | None = None,
    max_context_chars: int | None = None,
    measure: Callable[[str], int] = len,
    measurement: str = "characters",
) -> str:
    """Render one authored observation within its soft and hard envelopes."""

    payload = observation.model_dump(mode="json", exclude={"screenshot_path"})
    payload["telemetry"] = model_facing_telemetry_payload(payload.get("telemetry"))
    payload["affordances"] = planner_affordance_digest(observation)
    payload["roster_nutrition"] = planner_nutrition_digest(observation)
    payload = _project_planner_payload(payload)
    if max_chars is None and max_context_chars is None:
        return _planner_json(payload)
    if max_chars is None:
        assert max_context_chars is not None
        max_chars = max_context_chars
    if max_context_chars is None:
        max_context_chars = max_chars

    text = _planner_json(payload)
    return budget_observation_payload(
        payload,
        full_text=text,
        max_chars=min(max_chars, max_context_chars),
        hard_max_chars=max_context_chars,
        measure=measure,
        measurement=measurement,
    )


class PlannerContextAssembler:
    """Decorate a world observation into the payload a planner may read."""

    def __init__(
        self,
        *,
        continuity: ContinuityService,
        ledger: ContinuityLedger,
        planning_config: PlanningConfig,
        advisor_availability: Callable[[Observation], AdvisorAvailability],
    ) -> None:
        self._continuity = continuity
        self._ledger = ledger
        self._planning_config = planning_config
        self._advisor_availability = advisor_availability

    def decorate(self, observation: Observation) -> Observation:
        """Return the same world, described with everything the run knows."""

        authority = self._continuity.authority
        updates: dict[str, object] = {
            "recent_action_outcomes": self._ledger.recent_action_outcomes,
            "recent_plan_outcomes": self._ledger.recent_plan_outcomes,
            "recent_continuity_receipts": self._continuity.recent_receipts,
            "recent_fieldbook_receipts": self._continuity.recent_fieldbook_receipts,
            "continuity_writes_degraded_reason": authority.writes_degraded_reason,
            "continuity_reads_degraded_reason": authority.reads_degraded_reason,
            "advisor": self._advisor_availability(observation),
            "memory_search": self._continuity.pending_memory_search,
            "fieldbook_read": self._continuity.pending_fieldbook_read,
            "fieldbook_projects": [],
            "active_fieldbook_project": None,
        }
        recalled = self._continuity.recall(observation)
        if recalled is not None:
            updates["memories"] = recalled.records
            updates["memory_recall"] = recalled.summary
            updates["continuity_reads_degraded_reason"] = recalled.reads_degraded_reason
            updates["continuity_writes_degraded_reason"] = recalled.writes_degraded_reason
            projects, active = self._continuity.fieldbook_index(observation)
            updates["fieldbook_projects"] = projects
            updates["active_fieldbook_project"] = active
            # The index read can quarantine reads, so re-read the reasons after it.
            if authority.reads_degraded_reason is not None:
                updates["continuity_reads_degraded_reason"] = authority.reads_degraded_reason
                updates["continuity_writes_degraded_reason"] = authority.writes_degraded_reason
        return observation.model_copy(update=updates)
