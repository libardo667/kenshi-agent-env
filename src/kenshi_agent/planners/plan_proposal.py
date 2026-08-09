"""Compile the policy-bounded hosted choice into runtime-owned execution.

The model may state a broader objective, but it chooses only the current
affordance for this deliberation. It does not author future selections,
operation kinds, causal revision fences, graph bookkeeping, retries, timeouts,
idempotency, risk, completion, or cleanup. Those are mechanical facts already
owned by the runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from ..affordances import (
    AffordanceSelection,
    bind_affordance,
    bound_affordance,
)
from ..config import PLANNER_OUTPUT_POLICY, PlanningConfig
from ..core.base import StrictModel
from ..core.continuity import (
    AppendFieldbookEntryOperation,
    ContinuityOperation,
    CreateFieldbookProjectOperation,
    FieldbookEntryKind,
    FieldbookOperation,
    FieldbookProjectKind,
    FieldbookProjectStatus,
    KeepMemoryOperation,
    MemoryKind,
    MemoryResolutionDisposition,
    ReinforceMemoryOperation,
    ResolveMemoryOperation,
    RetractMemoryOperation,
    SelectFieldbookProjectOperation,
    SetFieldbookProjectStatusOperation,
    SupersedeMemoryOperation,
    UpdateFieldbookSummaryOperation,
)
from ..core.evidence import (
    ActionOutcomeEvidence,
    AdvisorBriefEvidence,
    CurrentObservationEvidence,
    EvidenceReference,
    MemoryEvidence,
    PlanOutcomeEvidence,
)
from ..core.observation import Observation
from ..core.planning import (
    Condition,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)
from ..operation_definitions import (
    OperationExecution,
)


class ProposedPlanStep(StrictModel):
    """One exact current affordance, without executor bookkeeping."""

    selection: AffordanceSelection


class ContinuityProposal(StrictModel):
    """A memory transition proposal using evidence IDs, not tagged ID wrappers."""

    operation: Literal["keep", "reinforce", "resolve", "supersede", "retract"]
    memory_id: str | None = None
    kind: MemoryKind | None = None
    content: str | None = None
    reason: str | None = None
    disposition: MemoryResolutionDisposition | None = None
    salience: float | None = Field(default=None, ge=0.0, le=1.0)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class FieldbookProposal(StrictModel):
    """A private-project transition proposal with runtime-resolved evidence."""

    operation: Literal[
        "create_project",
        "append_entry",
        "update_summary",
        "select_project",
        "set_project_status",
    ]
    project_id: str | None = None
    kind: FieldbookProjectKind | FieldbookEntryKind | None = None
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    status: FieldbookProjectStatus | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class PlanProposal(StrictModel):
    """A hosted choice; deterministic code observes again before another."""

    objective: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "The broader gameplay goal this current choice advances. It may "
            "span later deliberations without naming their affordances."
        ),
        examples=["Establish a reliable food supply."],
    )
    steps: list[ProposedPlanStep] = Field(
        min_length=PLANNER_OUTPUT_POLICY.current_affordances_per_deliberation,
        max_length=PLANNER_OUTPUT_POLICY.current_affordances_per_deliberation,
        description=PLANNER_OUTPUT_POLICY.schema_description,
        examples=[
            [
                {
                    "selection": {
                        "semantic": "observe",
                        "target_id": None,
                        "parameters": [],
                    }
                }
                for _ in range(
                    PLANNER_OUTPUT_POLICY.current_affordances_per_deliberation
                )
            ]
        ],
    )
    continuity_operations: list[ContinuityProposal] = Field(
        default_factory=list,
        max_length=6,
    )
    fieldbook_operations: list[FieldbookProposal] = Field(
        default_factory=list,
        max_length=4,
    )


@dataclass(frozen=True, slots=True)
class RejectedProposalSidecar:
    surface: Literal["continuity_operations", "fieldbook_operations"]
    index: int
    detail: str


@dataclass(frozen=True, slots=True)
class CompiledPlanProposal:
    plan: PlanEnvelope
    rejected_sidecars: tuple[RejectedProposalSidecar, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledHostedPlanProposal:
    output: PlanEnvelope
    rejected_sidecars: tuple[RejectedProposalSidecar, ...] = ()


_ATOMIC_EFFECT_TIMEOUT_SECONDS = 10.0
_OWNED_OPTION_TIMEOUT_SECONDS = 300.0


def _freshness_condition() -> Condition:
    return Condition(
        max_age_seconds=3.0,
    )


def _proposal_step(raw: object) -> ProposedPlanStep:
    if not isinstance(raw, Mapping):
        raise ValueError(  # mutation: diagnostic-only
            "PlanProposal steps must be JSON objects"
        )
    return ProposedPlanStep.model_validate({"selection": raw.get("selection")})


def _evidence_reference(evidence_id: str) -> EvidenceReference:
    if evidence_id == "current_observation":
        return CurrentObservationEvidence()
    if evidence_id.startswith("ao-"):
        return ActionOutcomeEvidence(outcome_id=evidence_id)
    if evidence_id.startswith("po-"):
        return PlanOutcomeEvidence(plan_outcome_id=evidence_id)
    if evidence_id.startswith("mem-"):
        return MemoryEvidence(memory_id=evidence_id)
    if evidence_id.startswith("advisor-"):
        return AdvisorBriefEvidence(brief_id=evidence_id)
    raise ValueError(  # mutation: diagnostic-only
        f"unknown evidence ID {evidence_id!r}"
    )


def _proposal_evidence_ids(raw: Mapping[str, object]) -> object:
    if "evidence_ids" in raw:
        return raw["evidence_ids"]
    references = raw.get("references")
    if references is None:
        return []
    # Compatibility for the exact live failure that selected this slice. The
    # model named an unambiguous plan-outcome source but used the generic
    # `outcome_id` field. Other legacy tagged wrappers are deliberately not a
    # second supported proposal language.
    if (
        isinstance(references, list)
        and len(references) == 1
        and isinstance(reference := references[0], Mapping)
        and reference.get("source") == "plan_outcome"
        and isinstance(outcome_id := reference.get("outcome_id"), str)
    ):
        return [outcome_id]
    raise ValueError(  # mutation: diagnostic-only
        "PlanProposal evidence must use evidence_ids"
    )


def _continuity_proposal(raw: object) -> ContinuityProposal:
    if not isinstance(raw, Mapping):
        raise ValueError(  # mutation: diagnostic-only
            "continuity proposal must be a JSON object"
        )
    projected = {
        name: raw[name]
        for name in ContinuityProposal.model_fields
        if name in raw
    }
    projected["evidence_ids"] = _proposal_evidence_ids(raw)
    return ContinuityProposal.model_validate(projected)


def _fieldbook_proposal(raw: object) -> FieldbookProposal:
    if not isinstance(raw, Mapping):
        raise ValueError(  # mutation: diagnostic-only
            "fieldbook proposal must be a JSON object"
        )
    projected = {
        name: raw[name]
        for name in FieldbookProposal.model_fields
        if name in raw
    }
    return FieldbookProposal.model_validate(projected)


def _compile_continuity(proposal: ContinuityProposal) -> ContinuityOperation:
    references = [_evidence_reference(value) for value in proposal.evidence_ids]
    if proposal.operation == "keep":
        return KeepMemoryOperation.model_validate(
            {
                "kind": proposal.kind,
                "content": proposal.content,
                "salience": proposal.salience if proposal.salience is not None else 0.5,
                "target_id": proposal.target_id,
                "references": references,
            }
        )
    if proposal.operation == "reinforce":
        return ReinforceMemoryOperation.model_validate(
            {
                "memory_id": proposal.memory_id,
                "salience": proposal.salience,
                "references": references,
            }
        )
    if proposal.operation == "resolve":
        return ResolveMemoryOperation.model_validate(
            {
                "memory_id": proposal.memory_id,
                "reason": proposal.reason,
                "disposition": proposal.disposition,
                "references": references,
            }
        )
    if proposal.operation == "supersede":
        return SupersedeMemoryOperation.model_validate(
            {
                "memory_id": proposal.memory_id,
                "kind": proposal.kind,
                "content": proposal.content,
                "salience": proposal.salience if proposal.salience is not None else 0.5,
                "target_id": proposal.target_id,
                "references": references,
            }
        )
    return RetractMemoryOperation.model_validate(
        {
            "memory_id": proposal.memory_id,
            "reason": proposal.reason,
        }
    )


def _compile_fieldbook(proposal: FieldbookProposal) -> FieldbookOperation:
    references = [_evidence_reference(value) for value in proposal.evidence_ids]
    if proposal.operation == "create_project":
        return CreateFieldbookProjectOperation.model_validate(
            {
                "kind": proposal.kind,
                "title": proposal.title,
                "summary": proposal.summary,
            }
        )
    if proposal.operation == "append_entry":
        return AppendFieldbookEntryOperation.model_validate(
            {
                "project_id": proposal.project_id,
                "kind": proposal.kind,
                "content": proposal.content,
                "references": references,
            }
        )
    if proposal.operation == "update_summary":
        return UpdateFieldbookSummaryOperation.model_validate(
            {
                "project_id": proposal.project_id,
                "summary": proposal.summary,
            }
        )
    if proposal.operation == "select_project":
        return SelectFieldbookProjectOperation(project_id=proposal.project_id)
    return SetFieldbookProjectStatusOperation.model_validate(
        {
            "project_id": proposal.project_id,
            "status": proposal.status,
        }
    )


def _step_timeout_seconds(
    *,
    execution: OperationExecution | None,
    plan_wall_seconds: float,
) -> float:
    """Give immediate effects a short terminal without clipping owned options."""

    horizon = (
        _OWNED_OPTION_TIMEOUT_SECONDS
        if execution in {OperationExecution.MONITORED_OPTION, OperationExecution.COMPOSITE_OPTION}
        else _ATOMIC_EFFECT_TIMEOUT_SECONDS
    )
    return min(horizon, plan_wall_seconds)


def _sidecar_items(
    raw_items: object,
    *,
    surface: Literal["continuity_operations", "fieldbook_operations"],
) -> tuple[list[object], list[RejectedProposalSidecar]]:
    if raw_items is None:
        return [], []
    if not isinstance(raw_items, list):
        return [], [
            RejectedProposalSidecar(
                surface=surface,
                index=0,
                detail=f"{surface} must be a list",  # mutation: diagnostic-only
            )
        ]
    return list(raw_items), []


def _compile_continuity_sidecars(
    raw_items: object,
) -> tuple[list[ContinuityOperation], list[RejectedProposalSidecar]]:
    items, rejected = _sidecar_items(
        raw_items,
        surface="continuity_operations",
    )
    accepted: list[ContinuityOperation] = []
    for index, raw in enumerate(items):
        try:
            accepted.append(_compile_continuity(_continuity_proposal(raw)))
        except (TypeError, ValueError) as exc:
            rejected.append(
                RejectedProposalSidecar(
                    surface="continuity_operations",
                    index=index,
                    detail=str(exc),  # mutation: diagnostic-only
                )
            )
    return accepted, rejected


def _compile_fieldbook_sidecars(
    raw_items: object,
) -> tuple[list[FieldbookOperation], list[RejectedProposalSidecar]]:
    items, rejected = _sidecar_items(
        raw_items,
        surface="fieldbook_operations",
    )
    accepted: list[FieldbookOperation] = []
    for index, raw in enumerate(items):
        try:
            accepted.append(_compile_fieldbook(_fieldbook_proposal(raw)))
        except (TypeError, ValueError) as exc:
            rejected.append(
                RejectedProposalSidecar(
                    surface="fieldbook_operations",
                    index=index,
                    detail=str(exc),  # mutation: diagnostic-only
                )
            )
    return accepted, rejected


def compile_plan_proposal(
    document: object,
    *,
    observation: Observation,
    context_id: str,
    planning: PlanningConfig,
) -> CompiledPlanProposal:
    """Compile model-owned choices against one immutable authored observation."""

    if not isinstance(document, Mapping):
        raise ValueError(  # mutation: diagnostic-only
            "PlanProposal must be one JSON object"
        )
    objective = document.get("objective")
    raw_steps = document.get("steps")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError(  # mutation: diagnostic-only
            "PlanProposal objective must be non-empty text"
        )
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(  # mutation: diagnostic-only
            "PlanProposal steps must be a non-empty list"
        )
    expected_choices = planning.planner_output_policy.current_affordances_per_deliberation
    if len(raw_steps) != expected_choices:
        raise ValueError(  # mutation: diagnostic-only
            planning.planner_output_policy.cardinality_error(len(raw_steps))
        )

    proposals = [_proposal_step(raw) for raw in raw_steps]
    fresh = _freshness_condition()
    steps: list[PlanStep] = []
    pointer_risk = purchase_risk = native_risk = max_spend = 0
    for index, proposal in enumerate(proposals):
        bound = bind_affordance(proposal.selection, observation)
        action = bound.operation
        definition = bound.definition
        success_conditions: list[Condition] = []

        idempotency = definition.idempotency
        risk = definition.risk_for(action)
        pointer_risk += risk.pointer_actions
        purchase_risk += risk.purchase_actions
        native_risk += risk.native_assisted_actions
        step_id = f"step-{index + 1}"
        next_step_id = f"step-{index + 2}" if index + 1 < len(proposals) else None
        steps.append(
            PlanStep(
                step_id=step_id,
                action=action,
                affordance=bound_affordance(bound),
                preconditions=[fresh],
                success_conditions=success_conditions,
                timeout_seconds=_step_timeout_seconds(
                    execution=definition.execution,
                    plan_wall_seconds=planning.max_plan_wall_seconds,
                ),
                idempotency=idempotency,
                on_success=next_step_id,
            )
        )

    continuity, continuity_rejected = _compile_continuity_sidecars(
        document.get("continuity_operations")
    )
    fieldbook, fieldbook_rejected = _compile_fieldbook_sidecars(
        document.get("fieldbook_operations")
    )
    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id=f"plan-{context_id}",
        objective=objective.strip(),
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh],
        steps=steps,
        entry_step_id=steps[0].step_id,
        max_actions=len(steps),
        max_wall_seconds=planning.max_plan_wall_seconds,
        max_game_seconds=planning.max_plan_game_seconds,
        risk_budget=RiskBudget(
            max_pointer_actions=pointer_risk,
            max_purchase_actions=purchase_risk,
            max_native_assisted_actions=native_risk,
            max_spend=max_spend,
        ),
        continuity_operations=continuity,
        fieldbook_operations=fieldbook,
    )
    return CompiledPlanProposal(
        plan=plan,
        rejected_sidecars=tuple([*continuity_rejected, *fieldbook_rejected]),
    )


def compile_hosted_plan_proposal(
    document: object,
    *,
    observation: Observation,
    context_id: str,
    planning: PlanningConfig,
) -> CompiledHostedPlanProposal:
    """Compile a fresh choice; hosted output never reserves a future affordance."""

    if observation.active_plan is not None:
        raise ValueError(
            "Hosted planning cannot pre-bind a future affordance while a plan is "
            "active; observe again after the current affordance completes."
        )
    compiled = compile_plan_proposal(
        document,
        observation=observation,
        context_id=context_id,
        planning=planning,
    )
    return CompiledHostedPlanProposal(
        output=compiled.plan,
        rejected_sidecars=compiled.rejected_sidecars,
    )
