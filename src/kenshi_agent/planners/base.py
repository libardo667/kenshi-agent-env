from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from ..config import PlannerConfig
from ..models import (
    AuthoredPlannerContext,
    LiveContinuousPolicy,
    Observation,
    PlanEnvelope,
    PlannerContextManifest,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanPatch,
)

PlannerOutputModel = type[PlannerDecision] | type[PlanEnvelope] | type[PlanPatch]

_POLICY_SECTION = re.compile(
    r"<!-- policy:(?P<policy>[a-z0-9_,]+) -->\n(?P<body>.*?)<!-- /policy -->\n",
    re.DOTALL,
)


def instructions_for_policy(instructions: str, policy: LiveContinuousPolicy) -> str:
    """Keep only the prompt sections that apply to the active live policy.

    Every policy's rules used to be sent on every call, so a generic run also
    received the Barman recipe - wasted tokens, and a standing invitation to
    anchor on a scenario the run is not in. Sections are marked in the prompt
    file rather than split across files so the whole document stays readable.
    """

    def keep(match: re.Match[str]) -> str:
        wanted = {name.strip() for name in match.group("policy").split(",")}
        return match.group("body") if policy.value in wanted else ""

    return _POLICY_SECTION.sub(keep, instructions).replace("\n\n\n", "\n\n")


def structured_output_model(observation: Observation) -> PlannerOutputModel:
    if observation.planning_mode != PlanningMode.CONTINUOUS:
        return PlannerDecision
    if observation.active_plan is not None:
        return PlanPatch
    return PlanEnvelope


def output_token_budget(
    config: PlannerConfig,
    observation: Observation,
    *,
    max_plan_steps: int,
) -> int:
    expected_steps = 0
    if observation.planning_mode == PlanningMode.CONTINUOUS:
        if observation.active_plan is not None:
            expected_steps = max(1, observation.active_plan.remaining_actions)
        else:
            expected_steps = max_plan_steps
        expected_steps = min(expected_steps, max_plan_steps)
    return min(
        config.max_output_tokens_ceiling,
        config.max_output_tokens_base
        + config.max_output_tokens_per_plan_step * expected_steps,
    )


def _string_ids(items: Any, key: str) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {
        value
        for item in items
        if isinstance(item, dict)
        for value in [item.get(key)]
        if isinstance(value, str)
    }


def _payload_target_ids(payload: dict[str, Any], observation: Observation) -> set[str]:
    """Current entity IDs actually present in world-facing payload fields."""

    candidates: set[str] = set()
    telemetry = payload.get("telemetry")
    if isinstance(telemetry, dict):
        for field in ("squad", "nearby_entities", "world_targets"):
            candidates.update(_string_ids(telemetry.get(field), "id"))
        ui = telemetry.get("ui")
        if isinstance(ui, dict):
            for field in (
                "dialogue_target_id",
                "selected_character_id",
                "context_inventory_target_id",
            ):
                value = ui.get(field)
                if isinstance(value, str):
                    candidates.add(value)
            selected = ui.get("selected_character_ids")
            if isinstance(selected, list):
                candidates.update(value for value in selected if isinstance(value, str))
    for field in ("dialogue_targets", "travel_destinations", "context_targets"):
        candidates.update(_string_ids(payload.get(field), "id"))
    return candidates & observation.current_memory_target_ids()


def planner_context_manifest(
    observation: Observation,
    *,
    context_id: str,
    input_kind: Literal["full_observation", "budgeted_json", "scripted"],
    payload: dict[str, Any] | None = None,
    payload_characters: int | None = None,
) -> PlannerContextManifest:
    """Describe only identities the final planner representation contains."""

    candidate_memory_ids = {
        record.memory_id for record in observation.memories
    }
    if observation.memory_search is not None:
        candidate_memory_ids.update(
            record.memory_id for record in observation.memory_search.records
        )
    candidate_memory_ids.update(
        receipt.memory_id
        for receipt in observation.recent_continuity_receipts
        if receipt.memory_id is not None
    )

    if input_kind == "scripted":
        action_outcome_ids: set[str] = set()
        plan_outcome_ids: set[str] = set()
        memory_ids: set[str] = set()
        continuity_receipt_ids: set[str] = set()
        memory_read_receipt_ids: set[str] = set()
        fieldbook_project_ids: set[str] = set()
        fieldbook_entry_ids: set[str] = set()
        fieldbook_receipt_ids: set[str] = set()
        fieldbook_read_receipt_ids: set[str] = set()
        advisor_brief_ids: set[str] = set()
        current_target_ids: set[str] = set()
        current_observation_delivered = False
    elif payload is None:
        action_outcome_ids = {
            outcome.outcome_id for outcome in observation.recent_action_outcomes
        }
        if observation.memory_search is not None:
            action_outcome_ids.update(
                outcome.outcome_id
                for outcome in observation.memory_search.action_outcomes
            )
        plan_outcome_ids = {
            outcome.plan_outcome_id for outcome in observation.recent_plan_outcomes
        }
        if observation.memory_search is not None:
            plan_outcome_ids.update(
                outcome.plan_outcome_id
                for outcome in observation.memory_search.plan_outcomes
            )
        memory_ids = {record.memory_id for record in observation.memories}
        if observation.memory_search is not None:
            memory_ids.update(
                record.memory_id for record in observation.memory_search.records
            )
        memory_ids.update(
            receipt.memory_id
            for receipt in observation.recent_continuity_receipts
            if receipt.memory_id is not None
        )
        continuity_receipt_ids = {
            receipt.receipt_id
            for receipt in observation.recent_continuity_receipts
        }
        memory_read_receipt_ids = (
            {observation.memory_search.receipt_id}
            if observation.memory_search is not None
            else set()
        )
        fieldbook_project_ids = {
            project.project_id for project in observation.fieldbook_projects
        }
        if observation.active_fieldbook_project is not None:
            fieldbook_project_ids.add(
                observation.active_fieldbook_project.project_id
            )
        fieldbook_entry_ids = set()
        if observation.fieldbook_read is not None:
            fieldbook_project_ids.update(
                observation.fieldbook_read.project_ids
            )
            fieldbook_entry_ids.update(observation.fieldbook_read.entry_ids)
        for receipt in observation.recent_fieldbook_receipts:
            if receipt.project_id is not None:
                fieldbook_project_ids.add(receipt.project_id)
            if receipt.entry_id is not None:
                fieldbook_entry_ids.add(receipt.entry_id)
        fieldbook_receipt_ids = {
            receipt.receipt_id
            for receipt in observation.recent_fieldbook_receipts
        }
        fieldbook_read_receipt_ids = (
            {observation.fieldbook_read.receipt_id}
            if observation.fieldbook_read is not None
            else set()
        )
        latest_brief = observation.advisor.latest_brief
        advisor_brief_ids = (
            {latest_brief.brief_id} if latest_brief is not None else set()
        )
        current_target_ids = observation.current_memory_target_ids()
        current_observation_delivered = True
    else:
        action_outcome_ids = _string_ids(
            payload.get("recent_action_outcomes"), "outcome_id"
        )
        plan_outcome_ids = _string_ids(
            payload.get("recent_plan_outcomes"), "plan_outcome_id"
        )
        memory_ids = _string_ids(payload.get("memories"), "memory_id")
        search = payload.get("memory_search")
        if isinstance(search, dict):
            memory_ids.update(_string_ids(search.get("records"), "memory_id"))
            action_outcome_ids.update(
                _string_ids(search.get("action_outcomes"), "outcome_id")
            )
            plan_outcome_ids.update(
                _string_ids(search.get("plan_outcomes"), "plan_outcome_id")
            )
        memory_read_receipt_ids = (
            {search["receipt_id"]}
            if isinstance(search, dict)
            and isinstance(search.get("receipt_id"), str)
            else set()
        )
        fieldbook_project_ids = _string_ids(
            payload.get("fieldbook_projects"),
            "project_id",
        )
        active_fieldbook = payload.get("active_fieldbook_project")
        if isinstance(active_fieldbook, dict):
            active_project_id = active_fieldbook.get("project_id")
            if isinstance(active_project_id, str):
                fieldbook_project_ids.add(active_project_id)
        fieldbook_entry_ids = set()
        fieldbook_read = payload.get("fieldbook_read")
        if isinstance(fieldbook_read, dict):
            fieldbook_project_ids.update(
                _string_ids(fieldbook_read.get("entries"), "project_id")
            )
            fieldbook_entry_ids.update(
                _string_ids(fieldbook_read.get("entries"), "entry_id")
            )
            read_project = fieldbook_read.get("project")
            if isinstance(read_project, dict):
                read_project_id = read_project.get("project_id")
                if isinstance(read_project_id, str):
                    fieldbook_project_ids.add(read_project_id)
        for receipt in payload.get("recent_fieldbook_receipts", []):
            if not isinstance(receipt, dict):
                continue
            receipt_project_id = receipt.get("project_id")
            receipt_entry_id = receipt.get("entry_id")
            if isinstance(receipt_project_id, str):
                fieldbook_project_ids.add(receipt_project_id)
            if isinstance(receipt_entry_id, str):
                fieldbook_entry_ids.add(receipt_entry_id)
        fieldbook_receipt_ids = _string_ids(
            payload.get("recent_fieldbook_receipts"),
            "receipt_id",
        )
        fieldbook_read_receipt_ids = (
            {fieldbook_read["receipt_id"]}
            if isinstance(fieldbook_read, dict)
            and isinstance(fieldbook_read.get("receipt_id"), str)
            else set()
        )
        for receipt in payload.get("recent_continuity_receipts", []):
            if isinstance(receipt, dict) and isinstance(receipt.get("memory_id"), str):
                memory_ids.add(receipt["memory_id"])
        continuity_receipt_ids = _string_ids(
            payload.get("recent_continuity_receipts"),
            "receipt_id",
        )
        advisor = payload.get("advisor")
        latest_brief = advisor.get("latest_brief") if isinstance(advisor, dict) else None
        advisor_brief_ids = (
            {latest_brief["brief_id"]}
            if isinstance(latest_brief, dict)
            and isinstance(latest_brief.get("brief_id"), str)
            else set()
        )
        current_target_ids = _payload_target_ids(payload, observation)
        current_observation_delivered = "world_revision" in payload

    return PlannerContextManifest(
        context_id=context_id,
        run_id=observation.run_id,
        authored_revision=observation.world_revision,
        current_observation_delivered=current_observation_delivered,
        telemetry_was_fresh=(
            observation.telemetry is not None and not observation.telemetry_stale
        ),
        input_kind=input_kind,
        current_target_ids=sorted(current_target_ids),
        action_outcome_ids=sorted(action_outcome_ids),
        plan_outcome_ids=sorted(plan_outcome_ids),
        memory_ids=sorted(memory_ids),
        continuity_receipt_ids=sorted(continuity_receipt_ids),
        memory_read_receipt_ids=sorted(memory_read_receipt_ids),
        fieldbook_project_ids=sorted(fieldbook_project_ids),
        fieldbook_entry_ids=sorted(fieldbook_entry_ids),
        fieldbook_receipt_ids=sorted(fieldbook_receipt_ids),
        fieldbook_read_receipt_ids=sorted(fieldbook_read_receipt_ids),
        advisor_brief_ids=sorted(advisor_brief_ids),
        candidate_memory_count=len(candidate_memory_ids),
        payload_characters=payload_characters,
        created_at=datetime.now(UTC),
    )


@dataclass(frozen=True, slots=True)
class PreparedPlannerInput:
    """Final planner representation paired with its immutable authored basis."""

    context: AuthoredPlannerContext
    payload: str | None = None


def prepared_budgeted_input(
    observation: Observation,
    *,
    context_id: str,
    payload: str,
) -> PreparedPlannerInput:
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Planner observation payload must be one JSON object.")
    return PreparedPlannerInput(
        context=AuthoredPlannerContext(
            manifest=planner_context_manifest(
                observation,
                context_id=context_id,
                input_kind="budgeted_json",
                payload=document,
                payload_characters=len(payload),
            ),
            observation=observation,
        ),
        payload=payload,
    )


class Planner(ABC):
    def prepare_input(
        self,
        observation: Observation,
        *,
        context_id: str,
    ) -> PreparedPlannerInput:
        """Prepare the representation this planner implementation consumes.

        In-process planners receive the full typed observation by default.
        Hosted planners override this with their final budgeted JSON.
        """

        return PreparedPlannerInput(
            context=AuthoredPlannerContext(
                manifest=planner_context_manifest(
                    observation,
                    context_id=context_id,
                    input_kind="full_observation",
                ),
                observation=observation,
            )
        )

    async def decide_prepared(self, prepared: PreparedPlannerInput) -> PlannerOutput:
        return await self.decide(prepared.context.observation)

    @abstractmethod
    async def decide(self, observation: Observation) -> PlannerOutput:
        raise NotImplementedError
