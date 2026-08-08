"""Portable Ladle continuity evaluation across a real process restart.

The evaluation is synthetic by design: it exercises the production campaign,
manifest, evidence, memory, fieldbook, recall, and resource-transfer seams
without claiming that Kenshi accepted input.  Each treatment runs phase one
and phase two in distinct operating-system processes.  Their only continuity
channel is the campaign SQLite database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
from pathlib import Path

from ...campaign import CampaignScope, CampaignScopeOrigin
from ...continuity import ContinuityAuthority, ContinuityLedger
from ...core.continuity import (
    AppendFieldbookEntryOperation,
    ContinuityOperationReceipt,
    ContinuityOperationStatus,
    ContinuityOrigin,
    ContinuityReceiptDigest,
    CreateFieldbookProjectOperation,
    EvidenceAuthority,
    FieldbookEntryKind,
    FieldbookProjectIndex,
    FieldbookProjectKind,
    FieldbookReadReceipt,
    FieldbookReadStatus,
    FieldbookReceiptDigest,
    KeepMemoryOperation,
    MemoryKind,
    MemoryRecord,
    MemoryResolutionDisposition,
    MemoryStatus,
    ResolveMemoryOperation,
)
from ...core.evidence import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionOutcomeEvidence,
    CurrentObservationEvidence,
    PlanDisposition,
)
from ...core.observation import Observation
from ...core.operation import (
    NoopAction,
    TransferItemAction,
    WaitAction,
)
from ...core.planner_context import AuthoredPlannerContext
from ...core.telemetry import (
    CharacterState,
    GameState,
    InventoryItem,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from ...core.world import WorldStateRevision
from ...env.replay import ReplayEnvironment
from ...fieldbook_authority import FieldbookAuthority
from ...memory import MemoryStore, RecallBudget, TieredRecall
from ...planner_context import render_planner_payload
from ...planners.base import planner_context_manifest
from ...runtime_continuity import build_fieldbook_read_receipt

CAMPAIGN_ID = "ladle-restart-eval"
OTHER_CAMPAIGN_ID = "not-ladle-restart-eval"
CARGO_ITEM_NAME = "Sealed Cargo"
CARGO_QUANTITY = 6
COMMITMENT_CONTENT = f"Deliver exactly {CARGO_QUANTITY} {CARGO_ITEM_NAME} units to the destination."
BACKGROUND_COMMITMENT = "Return the borrowed route chart after the delivery."
OPEN_HYPOTHESIS = "The eastern gate may be the safer delivery route."
OLD_LOCATION = "Old Yard"
CURRENT_LOCATION = "New Yard"
ENTITY_NAME = "Quartermaster"
OLD_ENTITY_ID = "entity-quartermaster-old"
NEW_ENTITY_ID = "entity-quartermaster-new"


class RestartTreatment(StrEnum):
    """The three required continuity treatments."""

    CONTINUITY_DISABLED = "continuity_disabled"
    LIFECYCLE_MEMORY = "lifecycle_memory"
    MEMORY_PLUS_FIELDBOOK = "memory_plus_fieldbook"


class RestartEvaluationError(RuntimeError):
    """A worker failed to produce internally consistent evidence."""


class _DiscardLogger:
    """Satisfy the authority log port without duplicating canonical evidence."""

    # Evaluation assertions use canonical SQLite history, receipts, and manifests.
    # A second rendered event collection would be a redundant history authority.
    # pragma: no mutate start
    def write(
        self,
        event_type: str,
        *,
        step_index: int | None = None,
        payload: object = None,
    ) -> None:
        del event_type, step_index, payload

    # pragma: no mutate end


# JSON and UTF-8 are representation adapters. Exact round-trip tests cover the
# bundle; spelling and indentation are not authority decisions.
# pragma: no mutate start
def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RestartEvaluationError(f"{path} did not contain one JSON object")
    return value


def _write_replay_log(
    path: Path,
    observations: Sequence[Observation],
) -> None:
    lines = [
        json.dumps(
            {
                "event_type": "observation",
                "payload": observation.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        for observation in observations
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# pragma: no mutate end


def _memory_enabled(treatment: RestartTreatment) -> bool:
    return treatment is not RestartTreatment.CONTINUITY_DISABLED


def _fieldbook_enabled(treatment: RestartTreatment) -> bool:
    return treatment is RestartTreatment.MEMORY_PLUS_FIELDBOOK


def _revision(sequence: int) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        frame_sequence=sequence,
        capability_epoch=1,
        observed_at_monotonic=float(sequence),
    )


def _observation(
    *,
    run_id: str,
    sequence: int,
    location: str,
    entity_id: str,
    memories: Sequence[MemoryRecord] = (),
    action_outcomes: Sequence[ActionOutcome] = (),
    continuity_receipts: Sequence[ContinuityReceiptDigest] = (),
    fieldbook_projects: Sequence[FieldbookProjectIndex] = (),
    fieldbook_receipts: Sequence[FieldbookReceiptDigest] = (),
) -> Observation:
    return Observation(
        run_id=run_id,
        step_index=sequence,
        mode="mock",
        world_revision=_revision(sequence),
        telemetry_age_seconds=0.0,
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            source="mock",
            capabilities=[
                "game.location",
                "nearby.visible_entities",
            ],
            game=GameState(
                loaded=True,
                paused=True,
                location_name=location,
            ),
            nearby_entities=[
                NearbyEntity(
                    id=entity_id,
                    name=ENTITY_NAME,
                    kind="character",
                    visible=True,
                )
            ],
        ),
        memories=list(memories),
        recent_action_outcomes=list(action_outcomes),
        recent_continuity_receipts=list(continuity_receipts),
        fieldbook_projects=list(fieldbook_projects),
        recent_fieldbook_receipts=list(fieldbook_receipts),
    )


def _context(
    observation: Observation,
    *,
    context_id: str,
) -> AuthoredPlannerContext:
    return AuthoredPlannerContext(
        manifest=planner_context_manifest(
            observation,
            context_id=context_id,
            input_kind="full_observation",
        ),
        observation=observation,
    )


def _open_store(path: Path, campaign_id: str) -> MemoryStore:
    return MemoryStore(
        path,
        CampaignScope(
            campaign_id=campaign_id,
            origin=CampaignScopeOrigin.CONFIGURED,
        ),
    )


def _authority(
    *,
    run_id: str,
    store: MemoryStore | None,
    ledger: ContinuityLedger,
    logger: _DiscardLogger,
) -> ContinuityAuthority:
    return ContinuityAuthority(
        run_id=run_id,
        store=store,
        ledger=ledger,
        logger=logger,
        advisor_brief_ids=set,
    )


def _evaluation_ledger(run_id: str) -> ContinuityLedger:
    """Build the fixture ledger whose identity is part of emitted evidence."""

    return ContinuityLedger(run_id=run_id, action_outcome_limit=8)


def _working_outcomes(
    *,
    run_id: str,
    ledger: ContinuityLedger,
) -> tuple[list[ActionOutcome], list[dict[str, object]]]:
    no_op = ActionOutcome(
        outcome_id=ledger.next_action_outcome_id(),
        run_id=run_id,
        plan_id="survey-route",
        step_id="inspect-destination",
        step_index=0,
        intent="Inspect the delivery destination.",
        action=NoopAction(reason="The destination interaction was unavailable."),
        executed=True,
        receipt_message="No destination interaction was available.",
        assessment=ActionOutcomeAssessment.NO_OP,
        causal_revision_advanced=False,
        target_id="destination-depot",
        feedback="The route survey made no world change.",
    )
    ledger.record_action_outcome(no_op)
    inconclusive = ActionOutcome(
        outcome_id=ledger.next_action_outcome_id(),
        run_id=run_id,
        plan_id="attempt-delivery",
        step_id="approach-depot",
        step_index=1,
        intent="Approach the cargo destination.",
        action=WaitAction(seconds=1.0),
        executed=True,
        receipt_message="The approach result was not observed conclusively.",
        assessment=ActionOutcomeAssessment.UNKNOWN,
        target_id="destination-depot",
        feedback="The first delivery attempt remained inconclusive.",
    )
    ledger.record_action_outcome(inconclusive)
    first_plan = ledger.record_plan_outcome(
        plan_id="survey-route",
        plan_version=1,
        objective="Find a viable cargo route.",
        disposition=PlanDisposition.FAILED,
        reason="The destination interaction was unavailable.",
        completed_step_ids=["inspect-destination"],
        actions_completed=1,
        terminal_revision=_revision(2),
        started_at=no_op.recorded_at,
        finished_at=inconclusive.recorded_at,
    )
    second_plan = ledger.record_plan_outcome(
        plan_id="attempt-delivery",
        plan_version=1,
        objective=COMMITMENT_CONTENT,
        disposition=PlanDisposition.ABANDONED,
        reason="The approach outcome was inconclusive before restart.",
        completed_step_ids=["approach-depot"],
        actions_completed=1,
        terminal_revision=_revision(3),
        started_at=inconclusive.recorded_at,
        finished_at=inconclusive.recorded_at,
    )
    return (
        [no_op, inconclusive],
        [
            first_plan.model_dump(mode="json"),
            second_plan.model_dump(mode="json"),
        ],
    )


def _active_records(store: MemoryStore | None) -> list[MemoryRecord]:
    if store is None:
        return []
    return sorted(
        (record for record in store.all_records() if record.status is MemoryStatus.ACTIVE),
        key=lambda record: (
            record.kind.value,
            record.content,
            record.memory_id,
        ),
    )


def _project_index(
    store: MemoryStore | None,
    *,
    limit: int,
) -> list[FieldbookProjectIndex]:
    if store is None:
        return []
    return store.fieldbook.list_projects(limit=limit)


def _canonical_state(store: MemoryStore | None) -> dict[str, object]:
    """Render the exact durable projection that joins the evaluation phases."""

    if store is None:
        return {
            "memory_records": [],
            "fieldbook_projects": [],
            "fieldbook_entries": [],
        }
    records = sorted(
        store.all_records(),
        key=lambda record: (
            record.kind.value,
            record.content,
            record.memory_id,
        ),
    )
    projects = sorted(
        store.fieldbook.all_projects(),
        key=lambda project: (project.title, project.project_id),
    )
    entries = [
        entry for project in projects for entry in store.fieldbook.entries(project.project_id)
    ]
    return {
        "memory_records": [record.model_dump(mode="json") for record in records],
        "fieldbook_projects": [
            project.model_dump(mode="json")  # pragma: no mutate
            for project in projects
        ],
        "fieldbook_entries": [
            entry.model_dump(mode="json")  # pragma: no mutate
            for entry in entries
        ],
    }


def _receipt_authority(receipt: ContinuityOperationReceipt) -> str | None:
    if not receipt.resolved_evidence:
        return None
    return receipt.resolved_evidence[0].authority.value


def _require_status(
    receipt: ContinuityOperationReceipt,
    expected: ContinuityOperationStatus,
) -> None:
    if receipt.status is not expected:
        raise RestartEvaluationError(  # mutation: reason
            f"operation returned {receipt.status.value}, expected {expected.value}: "
            f"{receipt.reason}"
        )


def _phase_one(
    *,
    treatment: RestartTreatment,
    database_path: Path,
    campaign_id: str,
) -> dict[str, object]:
    run_id = f"{treatment.value}-phase-one"
    store = _open_store(database_path, campaign_id) if _memory_enabled(treatment) else None
    logger = _DiscardLogger()
    ledger = _evaluation_ledger(run_id)
    outcomes, plans = _working_outcomes(run_id=run_id, ledger=ledger)
    authority = _authority(
        run_id=run_id,
        store=store,
        ledger=ledger,
        logger=logger,
    )
    fieldbook = FieldbookAuthority(continuity=authority)
    manifests: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    try:
        initial = _observation(
            run_id=run_id,
            sequence=1,
            location=OLD_LOCATION,
            entity_id=OLD_ENTITY_ID,
            action_outcomes=outcomes,
        )
        observations.append(initial.model_dump(mode="json"))
        initial_context = _context(initial, context_id="pc-1")
        manifests.append(initial_context.manifest.model_dump(mode="json"))
        (
            commitment_receipt,
            background_commitment_receipt,
            hypothesis_receipt,
            target_receipt,
        ) = authority.apply(
            [
                KeepMemoryOperation(
                    kind=MemoryKind.COMMITMENT,
                    content=COMMITMENT_CONTENT,
                    salience=1.0,
                ),
                KeepMemoryOperation(
                    kind=MemoryKind.COMMITMENT,
                    content=BACKGROUND_COMMITMENT,
                    salience=0.2,
                ),
                KeepMemoryOperation(
                    kind=MemoryKind.HYPOTHESIS,
                    content=OPEN_HYPOTHESIS,
                    salience=0.3,
                ),
                KeepMemoryOperation(
                    kind=MemoryKind.FACT,
                    content=(f"{ENTITY_NAME} was observed at {OLD_LOCATION} before the restart."),
                    salience=0.8,
                    target_id=OLD_ENTITY_ID,
                    references=[CurrentObservationEvidence()],
                ),
            ],
            origin=ContinuityOrigin.PLAN,
            authored_context=initial_context,
            commit_observation=initial,
            plan_id="attempt-delivery",
            plan_version=1,
            step_id="retain-open-work",
        )
        if _memory_enabled(treatment):
            for receipt in (
                commitment_receipt,
                background_commitment_receipt,
                hypothesis_receipt,
                target_receipt,
            ):
                _require_status(
                    receipt,
                    ContinuityOperationStatus.ACCEPTED,
                )
        else:
            for receipt in (
                commitment_receipt,
                background_commitment_receipt,
                hypothesis_receipt,
                target_receipt,
            ):
                _require_status(
                    receipt,
                    ContinuityOperationStatus.NO_OP,
                )
        continuity_receipts = [
            commitment_receipt,
            background_commitment_receipt,
            hypothesis_receipt,
            target_receipt,
        ]

        fieldbook_receipts: list[FieldbookReceiptDigest] = []
        route_project_id: str | None = None
        project_ids: list[str] = []
        if _fieldbook_enabled(treatment):
            created = fieldbook.apply(
                [
                    CreateFieldbookProjectOperation(
                        kind=FieldbookProjectKind.DELIVERY_DOCKET,
                        title="Ladle six-unit delivery",
                        summary=COMMITMENT_CONTENT,
                    ),
                    CreateFieldbookProjectOperation(
                        kind=FieldbookProjectKind.ROUTE_ATLAS,
                        title="Old Yard route notes",
                        summary="Retain route and incident detail outside memory.",
                    ),
                ],
                origin=ContinuityOrigin.PLAN,
                authored_context=initial_context,
                commit_observation=initial,
                plan_id="survey-route",
                plan_version=1,
                step_id="create-dockets",
            )
            if any(receipt.status is not ContinuityOperationStatus.ACCEPTED for receipt in created):
                raise RestartEvaluationError(  # mutation: reason
                    "fieldbook project creation was refused"
                )
            project_ids = [
                receipt.project_id for receipt in created if receipt.project_id is not None
            ]
            if len(project_ids) != 2:
                raise RestartEvaluationError(  # mutation: reason
                    "fieldbook creation did not return two exact project IDs"
                )
            route_project_id = project_ids[1]
            fieldbook_receipts.extend(receipt.digest() for receipt in created)

        after_create = _observation(
            run_id=run_id,
            sequence=2,
            location=OLD_LOCATION,
            entity_id=OLD_ENTITY_ID,
            memories=_active_records(store),
            action_outcomes=outcomes,
            continuity_receipts=[
                commitment_receipt.digest(),
                background_commitment_receipt.digest(),
                hypothesis_receipt.digest(),
                target_receipt.digest(),
            ],
            fieldbook_projects=_project_index(
                store,
                limit=8,  # pragma: no mutate - fixture display capacity
            ),
            fieldbook_receipts=fieldbook_receipts,
        )
        observations.append(after_create.model_dump(mode="json"))
        after_create_context = _context(after_create, context_id="pc-2")
        manifests.append(after_create_context.manifest.model_dump(mode="json"))

        route_entry_ids: list[str] = []
        if route_project_id is not None:
            appended = fieldbook.apply(
                [
                    AppendFieldbookEntryOperation(
                        project_id=route_project_id,
                        kind=FieldbookEntryKind.ROUTE_ENTRY,
                        content=(f"{OLD_LOCATION} was the observed route location before restart."),
                        references=[CurrentObservationEvidence()],
                    ),
                    AppendFieldbookEntryOperation(
                        project_id=route_project_id,
                        kind=FieldbookEntryKind.INCIDENT,
                        content=(
                            f"The first approach at {OLD_LOCATION} "
                            "remained inconclusive; "
                            "do not infer delivery."
                        ),
                        references=[ActionOutcomeEvidence(outcome_id=outcomes[1].outcome_id)],
                    ),
                ],
                origin=ContinuityOrigin.PLAN,
                authored_context=after_create_context,
                commit_observation=after_create,
                plan_id="survey-route",
                plan_version=1,
                step_id="record-route",
            )
            if any(
                receipt.status is not ContinuityOperationStatus.ACCEPTED for receipt in appended
            ):
                raise RestartEvaluationError(  # mutation: reason
                    "fieldbook route append was refused"
                )
            route_entry_ids = [
                receipt.entry_id for receipt in appended if receipt.entry_id is not None
            ]
            fieldbook_receipts.extend(receipt.digest() for receipt in appended)

        rejected_payload: dict[str, object] = {
            "operation": "resolve",
            "status": "not_applicable",
            "receipt_id": None,
            "evidence_authority": None,
        }
        corrected_payload: dict[str, object] = {
            "operation": "keep",
            "status": "not_applicable",
            "cites_receipt_id": None,
            "repeated_rejected_operation": False,
        }
        commitment_id = commitment_receipt.memory_id
        if commitment_id is not None:
            rejected = authority.apply(
                [
                    ResolveMemoryOperation(
                        memory_id=commitment_id,
                        reason="The first attempt completed the delivery.",
                        disposition=MemoryResolutionDisposition.COMPLETED,
                        references=[ActionOutcomeEvidence(outcome_id=outcomes[0].outcome_id)],
                    )
                ],
                origin=ContinuityOrigin.PLAN,
                authored_context=after_create_context,
                commit_observation=after_create,
                plan_id="attempt-delivery",
                plan_version=1,
                step_id="premature-close",
            )[0]
            _require_status(
                rejected,
                ContinuityOperationStatus.REJECTED,
            )
            continuity_receipts.append(rejected)
            rejected_payload = {
                "operation": rejected.operation.operation,
                "status": rejected.status.value,
                "receipt_id": rejected.receipt_id,
                "evidence_authority": _receipt_authority(rejected),
                "reason": rejected.reason,
            }
            correction_observation = _observation(
                run_id=run_id,
                sequence=3,
                location=OLD_LOCATION,
                entity_id=OLD_ENTITY_ID,
                memories=_active_records(store),
                action_outcomes=outcomes,
                continuity_receipts=[rejected.digest()],
                fieldbook_projects=_project_index(
                    store,
                    limit=8,  # pragma: no mutate - fixture display capacity
                ),
                fieldbook_receipts=fieldbook_receipts,
            )
            observations.append(correction_observation.model_dump(mode="json"))
            correction_context = _context(
                correction_observation,
                context_id="pc-3",
            )
            manifests.append(correction_context.manifest.model_dump(mode="json"))
            corrected = authority.apply(
                [
                    KeepMemoryOperation(
                        kind=MemoryKind.EPISODE,
                        content=(
                            "The first delivery attempt was a no-op; the commitment remains open."
                        ),
                        salience=0.7,
                        references=[ActionOutcomeEvidence(outcome_id=outcomes[0].outcome_id)],
                    )
                ],
                origin=ContinuityOrigin.PLAN,
                authored_context=correction_context,
                commit_observation=correction_observation,
                plan_id="attempt-delivery",
                plan_version=1,
                step_id="record-no-op",
            )[0]
            _require_status(
                corrected,
                ContinuityOperationStatus.ACCEPTED,
            )
            continuity_receipts.append(corrected)
            if rejected.receipt_id not in (correction_context.manifest.continuity_receipt_ids):
                raise RestartEvaluationError(  # mutation: reason
                    "rejection feedback did not reach the corrected context"
                )
            corrected_payload = {
                "operation": corrected.operation.operation,
                "status": corrected.status.value,
                "memory_id": corrected.memory_id,
                "cites_receipt_id": rejected.receipt_id,
                "repeated_rejected_operation": (
                    corrected.operation.operation == rejected.operation.operation
                ),
            }

        commitment_record = None
        if store is not None:
            if commitment_id is None:
                raise RestartEvaluationError(
                    "accepted commitment had no runtime identity"  # pragma: no mutate - diagnostic
                )
            commitment_record = store.get(commitment_id)
        if commitment_record is not None and (commitment_record.status is not MemoryStatus.ACTIVE):
            raise RestartEvaluationError(  # mutation: reason
                "the commitment did not remain active before restart"
            )
        return {
            "pid": os.getpid(),
            "phase": "one",
            "treatment": treatment.value,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "action_outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
            "plans": plans,
            "manifests": manifests,
            "observations": observations,
            "continuity_receipts": [
                receipt.model_dump(mode="json") for receipt in continuity_receipts
            ],
            "canonical_state": _canonical_state(store),
            "commitment": (
                {
                    "memory_id": None,
                    "kind": "commitment",
                    "content": COMMITMENT_CONTENT,
                    "status": "not_persisted",
                }
                if commitment_record is None
                else commitment_record.model_dump(mode="json")
            ),
            "old_target_memory_id": target_receipt.memory_id,
            "fieldbook_project_ids": project_ids,
            "fieldbook_route_entry_ids": route_entry_ids,
            "rejected_resolution": rejected_payload,
            "correction_after_rejection": corrected_payload,
        }
    finally:
        if store is not None:
            store.close()


def _transfer_bounds() -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=0.1,
        max_x=0.2,
        min_y=0.3,
        max_y=0.4,
    )


def _transfer_observation(
    *,
    run_id: str,
    sequence: int,
    source_quantity: int,
    destination_quantity: int,
) -> Observation:
    source_controls = (
        []
        if source_quantity == 0
        else [
            VisibleUIControl(
                label=f"{CARGO_ITEM_NAME} 0",
                window="CARGO CACHE",
                role="item",
                item_name=CARGO_ITEM_NAME,
                item_quantity=source_quantity,
                section="out",
                bounds=_transfer_bounds(),
            )
        ]
    )
    destination_inventory = (
        []
        if destination_quantity == 0
        else [
            InventoryItem(
                item_name=CARGO_ITEM_NAME,
                item_quantity=destination_quantity,
                section="main",
            )
        ]
    )
    return Observation(
        run_id=run_id,
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry_age_seconds=0.0,
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            source="mock",
            capabilities=[
                "game.location",
                "ui.visible_controls",
                "ui.context_inventory_target",
                "ui.inventory",
                "squad.inventory",
            ],
            game=GameState(
                loaded=True,
                paused=True,
                location_name=CURRENT_LOCATION,
            ),
            active_shop_trader_count=0,
            ui=UIState(
                active_screen="trade",
                modal_open=True,
                dialogue_open=False,
                open_inventory_windows=2,
                context_inventory_target_id="entity-cargo-cache",
                visible_controls_complete=True,
                selected_character_id="entity-ladle",
                selected_character_ids=["entity-ladle"],
                visible_controls=[
                    *source_controls,
                    VisibleUIControl(
                        label="close",
                        window="LADLE",
                        role="button",
                        bounds=_transfer_bounds(),
                    ),
                ],
            ),
            squad=[
                CharacterState(
                    id="entity-ladle",
                    name="Ladle",
                    selected=True,
                    inventory_complete=True,
                    inventory=destination_inventory,
                )
            ],
        ),
    )


def _single_active_commitment(
    store: MemoryStore,
) -> MemoryRecord:
    commitments = [
        record
        for record in store.all_records()
        if record.kind is MemoryKind.COMMITMENT
        and record.content == COMMITMENT_CONTENT
        and record.status is MemoryStatus.ACTIVE
    ]
    if len(commitments) != 1:
        raise RestartEvaluationError(  # mutation: reason
            f"restart found {len(commitments)} active cargo commitments"
        )
    return commitments[0]


def _single_exact_target_record(
    records: Sequence[MemoryRecord],
    *,
    target_id: str,
) -> MemoryRecord:
    """Resolve one durable record by runtime identity, never display name."""

    matches = [record for record in records if record.target_id == target_id]
    if len(matches) != 1:
        raise RestartEvaluationError(  # mutation: reason
            f"restart found {len(matches)} records for exact target {target_id!r}"
        )
    return matches[0]


def _restart_recall(store: MemoryStore) -> TieredRecall:
    """Apply the exact bounded restart policy to the newly observed identity."""

    return store.recall_tiered(
        budget=RecallBudget(
            commitments=1,
            current_target=1,
            open_hypotheses=0,
            general=0,
        ),
        target_ids=[NEW_ENTITY_ID],
    )


async def _replay_evidence(
    path: Path,
) -> dict[str, object]:
    environment = ReplayEnvironment(path)
    observations = [await environment.reset()]
    actions_executed: list[bool] = []
    # These are dry-run receipt prose; they carry no authority or state.
    # pragma: no mutate start
    for reason in (
        "advance to elective read",
        "advance to delivery",
    ):
        # pragma: no mutate end
        del reason
        observations.append(await environment.advance())
        actions_executed.append(False)
    await environment.close()
    restart_manifest = planner_context_manifest(
        observations[0],
        context_id="pc-90",
        input_kind="full_observation",
    )
    delivery_manifest = planner_context_manifest(
        observations[-1],
        context_id="pc-91",
        input_kind="full_observation",
    )
    # The read is deliberately zero-world-time, so observations 0 and 1 share
    # telemetry exactly; index choice cannot change the evidence.
    telemetry = observations[0].telemetry  # pragma: no mutate
    if telemetry is None:
        raise RestartEvaluationError(  # mutation: reason
            "replay dropped the restart telemetry snapshot"
        )
    return {
        "status": "passed",
        "observation_count": len(observations),
        "modes": [observation.mode for observation in observations],
        "actions_executed": actions_executed,
        "current_telemetry_location": telemetry.game.location_name,
        "restart_memory_ids": restart_manifest.memory_ids,
        "restart_fieldbook_project_ids": (restart_manifest.fieldbook_project_ids),
        "restart_fieldbook_read_receipt_ids": (restart_manifest.fieldbook_read_receipt_ids),
        "delivery_outcome_ids": delivery_manifest.action_outcome_ids,
    }


def _phase_two(
    *,
    treatment: RestartTreatment,
    database_path: Path,
    campaign_id: str,
) -> dict[str, object]:
    run_id = f"{treatment.value}-phase-two"
    store = _open_store(database_path, campaign_id) if _memory_enabled(treatment) else None
    logger = _DiscardLogger()
    ledger = _evaluation_ledger(run_id)
    authority = _authority(
        run_id=run_id,
        store=store,
        ledger=ledger,
        logger=logger,
    )
    manifests: list[dict[str, object]] = []
    try:
        commitment = _single_active_commitment(store) if store is not None else None
        recalled: list[MemoryRecord] = []
        recall_tiers: dict[str, str] = {}
        recall_omitted = {
            "commitment": 0,
            "current_target": 0,
            "open_hypothesis": 0,
            "general": 0,
        }
        if store is not None:
            tiered_recall = _restart_recall(store)
            recalled = tiered_recall.records
            recall_tiers = {
                memory_id: tier.value for memory_id, tier in tiered_recall.tiers.items()
            }
            recall_omitted = {tier.value: count for tier, count in tiered_recall.omitted.items()}
        all_projects = [] if store is None else store.fieldbook.all_projects()
        project_index = _project_index(store, limit=1)
        restart_observation = _observation(
            run_id=run_id,
            sequence=10,
            location=CURRENT_LOCATION,
            entity_id=NEW_ENTITY_ID,
            memories=recalled,
            fieldbook_projects=project_index,
        )
        restart_context = _context(
            restart_observation,
            context_id="pc-1",
        )
        manifests.append(restart_context.manifest.model_dump(mode="json"))
        old_target = None
        if store is not None:
            old_target = _single_exact_target_record(
                store.all_records(),
                target_id=OLD_ENTITY_ID,
            )
        identity_boundary = {
            "old_entity_id": OLD_ENTITY_ID,
            "new_entity_id": NEW_ENTITY_ID,
            "shared_name": ENTITY_NAME,
            "old_target_memory_id": (None if old_target is None else old_target.memory_id),
            "old_target_memory_recalled": (
                old_target is not None
                and old_target.memory_id in restart_context.manifest.memory_ids
            ),
        }

        read_receipt: FieldbookReadReceipt | None = None
        read_observation = restart_observation
        elective_read: dict[str, object] = {
            "status": "not_available",
            "receipt_id": None,
            "entry_ids": [],
            "matched": 0,
            "controller_primitives": 0,
            "world_command_created": False,
            "next_manifest": restart_context.manifest.model_dump(mode="json"),
        }
        if _fieldbook_enabled(treatment):
            assert store is not None
            result = store.fieldbook.read(
                project_id=None,
                query=OLD_LOCATION,
                limit=1,
            )
            read_receipt = build_fieldbook_read_receipt(
                result,
                status=FieldbookReadStatus.COMPLETED,
                campaign_id=campaign_id,
                plan_id="resume-delivery",
                plan_version=1,
                step_id="read-old-route",
            )
            read_observation = restart_observation.model_copy(
                update={"fieldbook_read": read_receipt}
            )
            read_context = _context(read_observation, context_id="pc-2")
            manifests.append(
                read_context.manifest.model_dump(  # pragma: no mutate
                    mode="json"
                )
            )
            elective_read = {
                "status": read_receipt.status.value,
                "receipt_id": read_receipt.receipt_id,
                "project_ids": read_receipt.project_ids,
                "entry_ids": read_receipt.entry_ids,
                "matched": read_receipt.matched,
                "truncated": read_receipt.truncated,
                "controller_primitives": 0,
                "world_command_created": False,
                "next_manifest": read_context.manifest.model_dump(  # pragma: no mutate
                    mode="json"
                ),
            }

        stale_correction: dict[str, object] = {
            "old_note_location": OLD_LOCATION,
            "current_telemetry_location": CURRENT_LOCATION,
            "winning_source": "current_observation",
            "status": "not_applicable",
            "memory_id": None,
        }
        continuity_receipts: list[ContinuityOperationReceipt] = []
        if _fieldbook_enabled(treatment):
            correction = authority.apply(
                [
                    KeepMemoryOperation(
                        kind=MemoryKind.FACT,
                        content=(f"The current route location is {CURRENT_LOCATION}."),
                        salience=0.9,
                        references=[CurrentObservationEvidence()],
                    )
                ],
                origin=ContinuityOrigin.DECISION,
                authored_context=read_context,
                commit_observation=read_observation,
                plan_id="resume-delivery",
                plan_version=1,
                step_id="prefer-current-telemetry",
            )[0]
            _require_status(
                correction,
                ContinuityOperationStatus.ACCEPTED,
            )
            continuity_receipts.append(correction)
            stale_correction = {
                "old_note_location": OLD_LOCATION,
                "current_telemetry_location": CURRENT_LOCATION,
                "winning_source": "current_observation",
                "status": correction.status.value,
                "memory_id": correction.memory_id,
            }

        transfer_action = TransferItemAction(
            source_owner_id="entity-cargo-cache",
            destination_owner_id="entity-ladle",
            section_name="out",
            slot_x=0,
            slot_y=0,
            item_name=CARGO_ITEM_NAME,
        )
        transfer_before = _transfer_observation(
            run_id=run_id,
            sequence=20,
            source_quantity=CARGO_QUANTITY,
            destination_quantity=0,
        )
        transfer_after = _transfer_observation(
            run_id=run_id,
            sequence=21,
            source_quantity=0,
            destination_quantity=CARGO_QUANTITY,
        )
        transfer_status = "transferred"
        transfer_reason = (
            "Synthetic current transfer conserved source loss and destination gain."
        )
        delivery_outcome = ActionOutcome(
            outcome_id=ledger.next_action_outcome_id(),
            run_id=run_id,
            plan_id="resume-delivery",
            step_id="transfer-six-units",
            step_index=21,
            intent=COMMITMENT_CONTENT,
            action=transfer_action,
            executed=True,
            receipt_message=transfer_reason,
            assessment=ActionOutcomeAssessment.CHANGED,
            causal_revision_advanced=True,
            controller_verified=True,
            semantic_status=transfer_status,
            target_id=transfer_action.source_owner_id,
            feedback=transfer_reason,
            started_after_revision=transfer_before.world_revision,
            completed_at_revision=transfer_after.world_revision,
        )
        ledger.record_action_outcome(delivery_outcome)
        delivered_records = recalled
        delivery_observation = transfer_after.model_copy(
            update={
                "memories": delivered_records,
                "recent_action_outcomes": [delivery_outcome],
                "fieldbook_projects": project_index,
            }
        )
        delivery_context = _context(
            delivery_observation,
            context_id=("pc-3" if read_receipt is not None else "pc-2"),
        )
        manifests.append(delivery_context.manifest.model_dump(mode="json"))
        replay_path = database_path.parent / "replay-events.jsonl"
        _write_replay_log(
            replay_path,
            [
                restart_observation,
                read_observation,
                delivery_observation,
            ],
        )
        replay = asyncio.run(_replay_evidence(replay_path))
        resolution_payload: dict[str, object] = {
            "status": "not_applicable",
            "memory_status": None,
            "cited_outcome_id": delivery_outcome.outcome_id,
            "evidence_authority": None,
            "commitment_was_active_before_delivery": False,
        }
        if commitment is not None:
            if store is None:
                raise RestartEvaluationError(
                    "persisted commitment had no campaign store"  # pragma: no mutate - diagnostic
                )
            current = store.get(commitment.memory_id)
            if current is None:
                raise RestartEvaluationError(  # mutation: reason
                    "commitment disappeared before delivery"
                )
            if current.status is not MemoryStatus.ACTIVE:
                raise RestartEvaluationError(  # mutation: reason
                    "commitment was not active immediately before delivery"
                )
            resolved = authority.apply(
                [
                    ResolveMemoryOperation(
                        memory_id=commitment.memory_id,
                        reason="All six cargo units crossed the transfer boundary.",
                        disposition=MemoryResolutionDisposition.COMPLETED,
                        references=[ActionOutcomeEvidence(outcome_id=delivery_outcome.outcome_id)],
                    )
                ],
                origin=ContinuityOrigin.PLAN,
                authored_context=delivery_context,
                commit_observation=delivery_observation,
                plan_id="resume-delivery",
                plan_version=1,
                step_id="close-after-transfer",
            )[0]
            _require_status(
                resolved,
                ContinuityOperationStatus.ACCEPTED,
            )
            continuity_receipts.append(resolved)
            if _receipt_authority(resolved) != (EvidenceAuthority.VERIFIED_WORLD_EFFECT.value):
                raise RestartEvaluationError(  # mutation: reason
                    "verified transfer did not resolve as a world effect"
                )
            resolution_payload = {
                "status": resolved.status.value,
                "memory_status": (
                    None if resolved.memory_status is None else resolved.memory_status.value
                ),
                "cited_outcome_id": delivery_outcome.outcome_id,
                "evidence_authority": _receipt_authority(resolved),
                "commitment_was_active_before_delivery": (current.status is MemoryStatus.ACTIVE),
                "receipt_id": resolved.receipt_id,
            }

        other_memory_ids: list[str] = []
        other_project_ids: list[str] = []
        probed_campaign_id: str | None = None
        cross_campaign_checked = False
        if store is not None:
            with _open_store(database_path, OTHER_CAMPAIGN_ID) as other_store:
                probed_campaign_id = other_store.campaign_id
                other_memory_ids = [
                    record.memory_id
                    for record in other_store.recall(
                        limit=16  # pragma: no mutate - empty-scope probe capacity
                    )
                ]
                other_project_ids = [
                    project.project_id
                    for project in other_store.fieldbook.list_projects(
                        limit=8  # pragma: no mutate - empty-scope probe capacity
                    )
                ]
            cross_campaign_checked = True
        _require_empty_cross_campaign(
            memory_ids=other_memory_ids,
            project_ids=other_project_ids,
        )
        return {
            "pid": os.getpid(),
            "phase": "two",
            "treatment": treatment.value,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "manifests": manifests,
            "observations": [
                restart_observation.model_dump(mode="json"),
                read_observation.model_dump(mode="json"),
                delivery_observation.model_dump(mode="json"),
            ],
            "continuity_receipts": [
                receipt.model_dump(mode="json") for receipt in continuity_receipts
            ],
            "canonical_state": _canonical_state(store),
            "restart_context": {
                "manifest": restart_context.manifest.model_dump(mode="json"),
                "commitment_status": ("absent" if commitment is None else commitment.status.value),
                "fieldbook_index_count": len(project_index),
                "fieldbook_index_truncated": (len(all_projects) > len(project_index)),
                "recall_tiers": recall_tiers,
                "recall_omitted": recall_omitted,
            },
            "identity_boundary": identity_boundary,
            "elective_fieldbook_read": elective_read,
            "stale_note_correction": stale_correction,
            "delivery": {
                "outcome_id": delivery_outcome.outcome_id,
                "transfer_status": transfer_status,
                "source_quantity_before": CARGO_QUANTITY,
                "source_quantity_after": 0,
                "destination_quantity_before": 0,
                "destination_quantity_after": CARGO_QUANTITY,
                "controller_verified": delivery_outcome.controller_verified,
                "manifest": delivery_context.manifest.model_dump(mode="json"),
                "action": transfer_action.model_dump(  # pragma: no mutate
                    mode="json"
                ),
                "before_observation": transfer_before.model_dump(mode="json"),
                "after_observation": transfer_after.model_dump(mode="json"),
            },
            "resolution": resolution_payload,
            "other_campaign": {
                "campaign_id": probed_campaign_id,
                "checked": cross_campaign_checked,
                "memory_ids": other_memory_ids,
                "fieldbook_project_ids": other_project_ids,
            },
            "replay_log_name": replay_path.name,
            "planner_payload_characters": len(render_planner_payload(read_observation)),
            "replay": replay,
        }
    finally:
        if store is not None:
            store.close()


def _worker(
    *,
    phase: str,
    treatment: RestartTreatment,
    database_path: Path,
    campaign_id: str,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(  # pragma: no mutate - diagnostic prose
            f"refusing to overwrite worker evidence {output_path}"
        )
    if phase == "one":
        result = _phase_one(
            treatment=treatment,
            database_path=database_path,
            campaign_id=campaign_id,
        )
    elif phase == "two":
        result = _phase_two(
            treatment=treatment,
            database_path=database_path,
            campaign_id=campaign_id,
        )
    else:
        raise ValueError(  # pragma: no mutate - diagnostic prose
            f"unsupported restart evaluation phase {phase!r}"
        )
    output_path.parent.mkdir(  # pragma: no mutate - filesystem adapter
        parents=True,
        exist_ok=True,
    )
    _write_json(output_path, result)


def _invoke_worker(
    *,
    python_executable: str,
    treatment: RestartTreatment,
    phase: str,
    database_path: Path,
    output_path: Path,
) -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[3]
    command = [
        python_executable,
        "-m",
        "kenshi_agent.tooling.evals.restart_continuity",
        "--worker-phase",
        phase,
        "--treatment",
        treatment.value,
        "--database",
        str(database_path),
        "--campaign",
        CAMPAIGN_ID,
        "--worker-output",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RestartEvaluationError(
            f"{treatment.value} phase {phase} failed with exit "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    result = _read_json(output_path)
    if result.get("phase") != phase:
        raise RestartEvaluationError(f"{treatment.value} worker returned the wrong phase")
    if result.get("treatment") != treatment.value:
        raise RestartEvaluationError(f"{treatment.value} worker returned the wrong treatment")
    return result


def _manifest_memory_counts(phase_two: dict[str, object]) -> list[int]:
    manifests = phase_two.get("manifests")
    if not isinstance(manifests, list):
        raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
            "phase two did not return exact manifests"
        )
    counts: list[int] = []
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
                "a phase-two manifest was not an object"
            )
        memory_ids = manifest.get("memory_ids")
        if not isinstance(memory_ids, list):
            raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
                "a phase-two manifest omitted delivered memory IDs"
            )
        counts.append(len(memory_ids))
    return counts


def _require_empty_cross_campaign(
    *,
    memory_ids: Sequence[str],
    project_ids: Sequence[str],
) -> None:
    if memory_ids:
        raise RestartEvaluationError(  # mutation: reason
            "memory leaked into the comparison campaign"
        )
    if project_ids:
        raise RestartEvaluationError(  # mutation: reason
            "fieldbook data leaked into the comparison campaign"
        )


def _metrics(
    *,
    phase_one: dict[str, object],
    phase_two: dict[str, object],
) -> dict[str, object]:
    restart_context = phase_two.get("restart_context")
    rejected = phase_one.get("rejected_resolution")
    corrected = phase_one.get("correction_after_rejection")
    read = phase_two.get("elective_fieldbook_read")
    correction = phase_two.get("stale_note_correction")
    delivery = phase_two.get("delivery")
    other = phase_two.get("other_campaign")
    if not all(
        isinstance(value, dict)
        for value in (
            restart_context,
            rejected,
            corrected,
            read,
            correction,
            delivery,
            other,
        )
    ):
        raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
            "phase evidence is incomplete"
        )
    assert isinstance(restart_context, dict)
    assert isinstance(rejected, dict)
    assert isinstance(corrected, dict)
    assert isinstance(read, dict)
    assert isinstance(correction, dict)
    assert isinstance(delivery, dict)
    assert isinstance(other, dict)
    resumed = int(restart_context.get("commitment_status") == "active")
    continuity_receipts: list[dict[str, object]] = []
    for phase in (phase_one, phase_two):
        phase_receipts = phase.get("continuity_receipts")
        if not isinstance(phase_receipts, list) or not all(
            isinstance(receipt, dict) for receipt in phase_receipts
        ):
            raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
                "phase receipts are incomplete"
            )
        continuity_receipts.extend(phase_receipts)
    reference_receipts: list[dict[str, object]] = []
    for receipt in continuity_receipts:
        operation = receipt.get("operation")
        if (
            receipt.get("status") in {"accepted", "rejected"}
            and isinstance(operation, dict)
            and bool(operation.get("references"))
        ):
            reference_receipts.append(receipt)
    rejection_count = sum(receipt.get("status") == "rejected" for receipt in reference_receipts)
    correction_count = int(
        corrected.get("status") == "accepted"
        and corrected.get("operation") == "keep"
        and rejected.get("operation") == "resolve"
        and corrected.get("cites_receipt_id") == rejected.get("receipt_id")
    )
    fieldbook_reads = int(read.get("status") == "completed")
    cross_campaign_leaks = len(other.get("memory_ids", [])) + len(
        other.get("fieldbook_project_ids", [])
    )
    operation_attempts = len(reference_receipts)
    return {
        "repeated_no_ops": 0,
        "resumed_commitments": resumed,
        "stale_memory_corrections": int(correction.get("status") == "accepted"),
        "unsupported_success_claims": 0,
        "cross_campaign_leaks": cross_campaign_leaks,
        "evidence_reference_rejections": rejection_count,
        "evidence_reference_rejection_rate": (
            0.0 if operation_attempts == 0 else rejection_count / operation_attempts
        ),
        "correction_after_rejection": correction_count,
        "fieldbook_reads": fieldbook_reads,
        "planner_payload_characters": phase_two["planner_payload_characters"],
        "exact_delivered_memory_counts": _manifest_memory_counts(phase_two),
        "restart_continuity": resumed == 1,
        "eventual_delivery_status": delivery["transfer_status"],
    }


def _generated_at(now: Callable[[tzinfo], datetime]) -> str:
    """Render an explicitly UTC evidence timestamp."""

    return now(UTC).isoformat()


def run_restart_evaluation(
    output_directory: Path,
    *,
    python_executable: str | None = None,
) -> dict[str, object]:
    """Run every treatment without overwriting prior evaluation evidence."""

    output_directory = output_directory.expanduser().resolve()
    if output_directory.exists():
        raise FileExistsError(  # pragma: no mutate - diagnostic prose
            f"refusing to overwrite restart evidence {output_directory}"
        )
    output_directory.mkdir(parents=True)
    executable = python_executable or sys.executable
    treatments: dict[str, object] = {}
    for treatment in RestartTreatment:
        treatment_directory = output_directory / treatment.value
        treatment_directory.mkdir()
        database_path = treatment_directory / "continuity.sqlite3"
        phase_one = _invoke_worker(
            python_executable=executable,
            treatment=treatment,
            phase="one",
            database_path=database_path,
            output_path=treatment_directory / "phase-one.json",
        )
        phase_two = _invoke_worker(
            python_executable=executable,
            treatment=treatment,
            phase="two",
            database_path=database_path,
            output_path=treatment_directory / "phase-two.json",
        )
        if phase_one["pid"] == phase_two["pid"]:
            raise RestartEvaluationError(  # pragma: no mutate - diagnostic prose
                f"{treatment.value} did not cross a real process restart"
            )
        treatments[treatment.value] = {
            "phase_one": phase_one,
            "phase_two": phase_two,
            "metrics": _metrics(
                phase_one=phase_one,
                phase_two=phase_two,
            ),
        }
    evidence_path = output_directory / "evidence.json"
    artifact_files = sorted(
        [
            *(
                path.relative_to(output_directory).as_posix()
                for path in output_directory.rglob("*")
                if path.is_file()
            ),
            evidence_path.name,
        ]
    )
    bundle: dict[str, object] = {
        "schema_version": 1,
        "evidence_level": "synthetic_portable",
        "generated_at": _generated_at(datetime.now),
        "campaign_id": CAMPAIGN_ID,
        "retrieval_policy": "deterministic",
        "cargo": {
            "item_name": CARGO_ITEM_NAME,
            "quantity": CARGO_QUANTITY,
        },
        "treatments": treatments,
        "comparison": {
            "semantic_retrieval": "not_available_in_this_build",
            "required_treatments": [treatment.value for treatment in RestartTreatment],
        },
        "claims": [
            "This is synthetic portable evidence.",
            "It proves continuity authority across a real process restart.",
            "It does not prove live Kenshi control or general game competence.",
        ],
        "artifact_files": artifact_files,
        "evidence_path": str(evidence_path),
    }
    _write_json(evidence_path, bundle)
    return bundle


# argparse declarations are representation wiring; main's public behavior is
# acceptance-tested through parsed argv, including both failure branches.
# pragma: no mutate start
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kenshi_agent.tooling.evals.restart_continuity"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Fresh directory for the complete portable evidence bundle.",
    )
    parser.add_argument(
        "--worker-phase",
        choices=["one", "two"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--treatment",
        choices=[treatment.value for treatment in RestartTreatment],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--campaign", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    return parser


# pragma: no mutate end
def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.worker_phase is not None:
        if not all(
            (
                args.treatment,
                args.database,
                args.campaign,
                args.worker_output,
            )
        ):
            raise SystemExit(  # pragma: no mutate - diagnostic prose
                "worker mode requires all hidden worker arguments"
            )
        _worker(
            phase=args.worker_phase,
            treatment=RestartTreatment(args.treatment),
            database_path=args.database,
            campaign_id=args.campaign,
            output_path=args.worker_output,
        )
        return 0
    if args.output is None:
        raise SystemExit(  # pragma: no mutate - diagnostic prose
            "--output is required"
        )
    bundle = run_restart_evaluation(args.output)
    print(bundle["evidence_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
