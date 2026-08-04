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

from collections.abc import Callable

from .config import PlanningConfig
from .continuity import ContinuityLedger
from .continuity_service import ContinuityService
from .models import AdvisorAvailability, Observation


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
            "planning_mode": self._planning_config.mode,
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
