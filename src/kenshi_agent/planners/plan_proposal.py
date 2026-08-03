"""Compile exact hosted affordance selections into runtime-owned execution.

The model chooses an objective and ordered current affordances. It does not
author operation kinds, causal revision fences, graph bookkeeping, retries,
timeouts, idempotency, risk, completion, or cleanup. Those are mechanical facts
already owned by the runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, TypeAdapter

from ..action_contracts import (
    ActionExecution,
    contract_for,
)
from ..affordances import (
    AffordanceSelection,
    bind_affordance,
    bound_affordance,
)
from ..config import PlanningConfig
from ..models import (
    ActionOutcomeEvidence,
    AdvisorBriefEvidence,
    AppendFieldbookEntryOperation,
    Condition,
    ContinuityOperation,
    CreateFieldbookProjectOperation,
    CurrentObservationEvidence,
    EvidenceReference,
    FieldbookEntryKind,
    FieldbookOperation,
    FieldbookProjectKind,
    FieldbookProjectStatus,
    IdempotencyPolicy,
    KeepMemoryOperation,
    MemoryEvidence,
    MemoryKind,
    MemoryResolutionDisposition,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlanOutcomeEvidence,
    PlanPatch,
    PlanStep,
    PurchaseItemAction,
    ReinforceMemoryOperation,
    ResolveMemoryOperation,
    RetractMemoryOperation,
    RiskBudget,
    SelectFieldbookProjectOperation,
    SetFieldbookProjectStatusOperation,
    SingleStepRuntimeAction,
    StrictModel,
    SupersedeMemoryOperation,
    UpdateFieldbookSummaryOperation,
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
    """The hosted model's choices; deterministic code compiles the envelope."""

    objective: str = Field(min_length=1, max_length=1000)
    steps: list[ProposedPlanStep] = Field(min_length=1, max_length=8)
    continuity_operations: list[ContinuityProposal] = Field(
        default_factory=list,
        max_length=6,
    )
    fieldbook_operations: list[FieldbookProposal] = Field(
        default_factory=list,
        max_length=4,
    )


class DecisionProposal(StrictModel):
    """One hosted-model choice using the same current affordance contract."""

    intent: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    selection: AffordanceSelection
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_observation: str | None = Field(default=None, max_length=1000)
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
class CompiledPlanPatchProposal:
    patch: PlanPatch
    rejected_sidecars: tuple[RejectedProposalSidecar, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledHostedPlanProposal:
    output: PlanEnvelope | PlanPatch
    rejected_sidecars: tuple[RejectedProposalSidecar, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledDecisionProposal:
    decision: PlannerDecision
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
    execution: ActionExecution | None,
    plan_wall_seconds: float,
) -> float:
    """Give immediate effects a short terminal without clipping owned options."""

    horizon = (
        _OWNED_OPTION_TIMEOUT_SECONDS
        if execution in {ActionExecution.MONITORED_OPTION, ActionExecution.COMPOSITE_OPTION}
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
    if len(raw_steps) > planning.max_plan_steps:
        raise ValueError(  # mutation: diagnostic-only
            f"PlanProposal has {len(raw_steps)} actions; runtime permits "
            f"{planning.max_plan_steps}"
        )
    if len(raw_steps) > planning.max_actions_per_plan:
        raise ValueError(  # mutation: diagnostic-only
            f"PlanProposal has {len(raw_steps)} actions; runtime action ceiling is "
            f"{planning.max_actions_per_plan}"
        )

    proposals = [_proposal_step(raw) for raw in raw_steps]
    fresh = _freshness_condition()
    steps: list[PlanStep] = []
    pointer_risk = purchase_risk = native_risk = max_spend = 0
    for index, proposal in enumerate(proposals):
        bound = bind_affordance(proposal.selection, observation)
        action = bound.operation
        contract = contract_for(action)
        success_conditions: list[Condition] = []

        if contract is None:
            idempotency = IdempotencyPolicy.AT_MOST_ONCE
        else:
            idempotency = contract.idempotency
            risk = contract.risk_for(action)
            pointer_risk += risk.pointer_actions
            purchase_risk += risk.purchase_actions
            native_risk += risk.native_assisted_actions
        if isinstance(action, PurchaseItemAction):
            max_spend += action.expected_price * action.quantity

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
                    execution=contract.execution if contract is not None else None,
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


def compile_decision_proposal(
    document: object,
    *,
    observation: Observation,
) -> CompiledDecisionProposal:
    """Compile one hosted single-step selection into a runtime decision."""

    if not isinstance(document, Mapping):
        raise ValueError("DecisionProposal must be one JSON object")
    proposal = DecisionProposal.model_validate(document)
    bound = bind_affordance(proposal.selection, observation)
    action: SingleStepRuntimeAction = TypeAdapter(
        SingleStepRuntimeAction
    ).validate_python(bound.operation)
    continuity, continuity_rejected = _compile_continuity_sidecars(
        document.get("continuity_operations")
    )
    fieldbook, fieldbook_rejected = _compile_fieldbook_sidecars(
        document.get("fieldbook_operations")
    )
    return CompiledDecisionProposal(
        decision=PlannerDecision(
            intent=proposal.intent,
            rationale=proposal.rationale,
            action=action,
            affordance=bound_affordance(bound),
            confidence=proposal.confidence,
            expected_observation=proposal.expected_observation,
            continuity_operations=continuity,
            fieldbook_operations=fieldbook,
        ),
        rejected_sidecars=tuple([*continuity_rejected, *fieldbook_rejected]),
    )


def compile_plan_patch_proposal(
    document: object,
    *,
    observation: Observation,
    context_id: str,
    planning: PlanningConfig,
) -> CompiledPlanPatchProposal:
    """Compile future intent without asking the model to edit a live plan graph."""

    active = observation.active_plan
    if active is None:
        raise ValueError("A future plan proposal requires an active plan context")
    compiled = compile_plan_proposal(
        document,
        observation=observation,
        context_id=context_id,
        planning=planning,
    )
    reserved_ids = {active.active_step_id, *active.completed_step_ids}
    step_ids: dict[str, str] = {}
    next_suffix = 1
    for step in compiled.plan.steps:
        candidate = f"future-{context_id}-{next_suffix}"
        while candidate in reserved_ids:
            next_suffix += 1
            candidate = f"future-{context_id}-{next_suffix}"
        step_ids[step.step_id] = candidate
        reserved_ids.add(candidate)
        next_suffix += 1
    future_steps = [
        step.model_copy(
            update={
                "step_id": step_ids[step.step_id],
                "on_success": (
                    step_ids[step.on_success]
                    if step.on_success is not None
                    else None
                ),
                "on_failure": (
                    step_ids[step.on_failure]
                    if step.on_failure is not None
                    else None
                ),
            },
            deep=True,
        )
        for step in compiled.plan.steps
    ]
    patch = PlanPatch(
        schema_version="1.0",
        plan_id=active.plan_id,
        based_on_plan_version=active.plan_version,
        based_on_revision=observation.world_revision,
        interrupt_active_step_id=None,
        replace_future_steps=future_steps,
        rationale=compiled.plan.objective,
        continuity_operations=compiled.plan.continuity_operations,
        fieldbook_operations=compiled.plan.fieldbook_operations,
    )
    return CompiledPlanPatchProposal(
        patch=patch,
        rejected_sidecars=compiled.rejected_sidecars,
    )


def compile_hosted_plan_proposal(
    document: object,
    *,
    observation: Observation,
    context_id: str,
    planning: PlanningConfig,
) -> CompiledHostedPlanProposal:
    """Compile the same small model surface for fresh and concurrent planning."""

    if observation.active_plan is None:
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
    compiled_patch = compile_plan_patch_proposal(
        document,
        observation=observation,
        context_id=context_id,
        planning=planning,
    )
    return CompiledHostedPlanProposal(
        output=compiled_patch.patch,
        rejected_sidecars=compiled_patch.rejected_sidecars,
    )
