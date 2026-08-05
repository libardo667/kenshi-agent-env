"""Registry-derived inventory of how each operation interacts with Kenshi.

Slice 0 of the interaction-scope reconstruction. Two artefacts, deliberately
kept apart:

*The catalog* is generated from the sole operation registry and the affordance
adapters. It states only what the code says today, including the
`SelectionRequirement` this stage exists to delete. It invents nothing.

*The proof-status manifest* is hand-authored and lives beside the catalog
rather than inside it. Today it also carries the proposed interaction contract,
because `OperationDefinition` has no contract field yet - the proposal has one
home, not two. Slice 1 moves those `proposed_*` fields into the registry, and
from then on the manifest may carry only proof status and evidence. The
`manifest_restates_registry` check below is what makes that migration
enforceable rather than aspirational.

Neither artefact is an authority over gameplay semantics. The catalog derives
from the registry; the manifest records what has actually been proven, and by
what evidence.
"""

from __future__ import annotations

import json
import typing
from dataclasses import dataclass
from pathlib import Path

from ..affordances import AFFORDANCE_ADAPTERS
from ..core.transport import NativeCommandRequest
from ..operation_definitions import OPERATION_DEFINITION_LIST

MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "reconstruction"
    / "interaction_proof_status.json"
)

PROOF_STATUSES = (
    "source_proven",
    "unit_proven",
    "live_proven",
    "unproven",
    "withheld",
)

# Fields the registry already owns. A manifest entry may never carry one; that
# is how a second semantic authority is prevented from growing here.
REGISTRY_OWNED_FIELDS = frozenset(
    {
        "selection_requirement",
        "control_modes",
        "pointer_class",
        "native_assisted",
        "native_command",
        "execution",
        "emits_world_command",
        "controller_verified",
        "handler_key",
        "required_capabilities",
        "native_terminal_success_reasons",
        "native_task_started_reasons",
        "adapters",
        "planner_visible",
    }
)


# Wire command names match their operation kind except where history left a
# vestigial name behind. `approach_confirmed_vendor` is the wire name for
# `approach_dialogue_target`; it predates the Stage 7 deletion of the vendor
# macro and no longer describes what the command does. Renaming it is a
# protocol change and belongs to the Slice 2 bump, not here - this map records
# the mismatch rather than papering over it.
NATIVE_COMMAND_OWNERS: dict[str, str] = {
    "approach_confirmed_vendor": "approach_dialogue_target",
}

VESTIGIAL_NATIVE_COMMAND_NAMES = frozenset(NATIVE_COMMAND_OWNERS)


def native_command_names() -> tuple[str, ...]:
    """Every native command the transport schema admits."""

    annotation = NativeCommandRequest.model_fields["command"].annotation
    return tuple(str(value) for value in typing.get_args(annotation))


def native_command_owner(command: str) -> str:
    """The operation kind that issues one wire command."""

    return NATIVE_COMMAND_OWNERS.get(command, command)


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """One operation, described only by what the registry already declares."""

    kind: str
    planner_visible: bool
    adapters: tuple[str, ...]
    selection_requirement: str
    control_modes: tuple[str, ...]
    pointer_class: str
    native_assisted: bool
    native_command: str
    execution: str
    emits_world_command: bool
    controller_verified: bool
    handler_key: str
    native_terminal_success_reasons: tuple[str, ...]
    native_task_started_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProofEntry:
    """One hand-authored classification proposal and its proof status."""

    key: str
    operation_kind: str
    subcase: str
    proposed_interaction_kind: str
    proposed_recipient_scope: str
    proposed_selection_dependency: str
    proposed_completion_milestone: str
    proposed_conflict_policy: str
    proposed_playback_requirement: str
    proof_status: str
    evidence: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class InteractionCatalogAudit:
    rows: tuple[CatalogRow, ...]
    entries: tuple[ProofEntry, ...]
    native_commands: tuple[str, ...]
    uncatalogued_operations: tuple[str, ...]
    unknown_manifest_operations: tuple[str, ...]
    duplicate_manifest_keys: tuple[str, ...]
    invalid_proof_statuses: tuple[str, ...]
    manifest_restates_registry: tuple[str, ...]
    native_commands_without_definition: tuple[str, ...]
    entries_without_evidence: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not any(
            (
                self.uncatalogued_operations,
                self.unknown_manifest_operations,
                self.duplicate_manifest_keys,
                self.invalid_proof_statuses,
                self.manifest_restates_registry,
                self.native_commands_without_definition,
                self.entries_without_evidence,
            )
        )

    def status_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(PROOF_STATUSES, 0)
        for entry in self.entries:
            if entry.proof_status in counts:
                counts[entry.proof_status] += 1
        return counts


def _catalog_rows() -> tuple[CatalogRow, ...]:
    owners = {native_command_owner(name): name for name in native_command_names()}
    rows = []
    for definition in sorted(OPERATION_DEFINITION_LIST, key=lambda item: item.kind):
        adapters = tuple(
            sorted(
                adapter.name
                for adapter in AFFORDANCE_ADAPTERS
                if definition.kind in adapter.operation_kinds
            )
        )
        rows.append(
            CatalogRow(
                kind=definition.kind,
                planner_visible=bool(adapters),
                adapters=adapters,
                selection_requirement=definition.selection_requirement.value,
                control_modes=tuple(
                    sorted(mode.value for mode in definition.allowed_control_modes)
                ),
                pointer_class=definition.pointer_class.value,
                native_assisted=definition.native_assisted,
                native_command=owners.get(definition.kind, ""),
                execution=definition.execution.value,
                emits_world_command=definition.emits_world_command,
                controller_verified=definition.controller_verified,
                handler_key=definition.handler_key,
                native_terminal_success_reasons=tuple(
                    sorted(definition.native_terminal_success_reasons)
                ),
                native_task_started_reasons=tuple(
                    sorted(definition.native_task_started_reasons)
                ),
            )
        )
    return tuple(rows)


def load_proof_manifest(path: Path = MANIFEST_PATH) -> tuple[ProofEntry, ...]:
    """Read the hand-authored proposal/proof manifest."""

    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for raw in payload.get("entries", []):
        operation_kind = str(raw.get("operation_kind", ""))
        subcase = str(raw.get("subcase", ""))
        entries.append(
            ProofEntry(
                key=f"{operation_kind}:{subcase}" if subcase else operation_kind,
                operation_kind=operation_kind,
                subcase=subcase,
                proposed_interaction_kind=str(raw.get("proposed_interaction_kind", "")),
                proposed_recipient_scope=str(raw.get("proposed_recipient_scope", "")),
                proposed_selection_dependency=str(
                    raw.get("proposed_selection_dependency", "")
                ),
                proposed_completion_milestone=str(
                    raw.get("proposed_completion_milestone", "")
                ),
                proposed_conflict_policy=str(raw.get("proposed_conflict_policy", "")),
                proposed_playback_requirement=str(
                    raw.get("proposed_playback_requirement", "")
                ),
                proof_status=str(raw.get("proof_status", "")),
                evidence=tuple(str(item) for item in raw.get("evidence", [])),
                note=str(raw.get("note", "")),
            )
        )
    return tuple(entries)


def _manifest_field_names(path: Path = MANIFEST_PATH) -> tuple[str, ...]:
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for raw in payload.get("entries", []):
        names.update(str(key) for key in raw)
    return tuple(sorted(names))


def audit_interaction_catalog(
    manifest_path: Path = MANIFEST_PATH,
) -> InteractionCatalogAudit:
    """Prove the catalog covers every operation and the manifest stays separate."""

    rows = _catalog_rows()
    entries = load_proof_manifest(manifest_path)
    known_kinds = {row.kind for row in rows}
    covered = {entry.operation_kind for entry in entries}

    seen: set[str] = set()
    duplicates: set[str] = set()
    for entry in entries:
        if entry.key in seen:
            duplicates.add(entry.key)
        seen.add(entry.key)

    claimed_commands = {row.native_command for row in rows if row.native_command}

    return InteractionCatalogAudit(
        rows=rows,
        entries=entries,
        native_commands=native_command_names(),
        uncatalogued_operations=tuple(sorted(known_kinds - covered)),
        unknown_manifest_operations=tuple(sorted(covered - known_kinds)),
        duplicate_manifest_keys=tuple(sorted(duplicates)),
        invalid_proof_statuses=tuple(
            sorted(
                f"{entry.key}={entry.proof_status or 'missing'}"
                for entry in entries
                if entry.proof_status not in PROOF_STATUSES
            )
        ),
        manifest_restates_registry=tuple(
            sorted(REGISTRY_OWNED_FIELDS.intersection(_manifest_field_names(manifest_path)))
        ),
        native_commands_without_definition=tuple(
            sorted(set(native_command_names()) - claimed_commands)
        ),
        entries_without_evidence=tuple(
            sorted(
                entry.key
                for entry in entries
                if not entry.evidence and entry.proof_status != "unproven"
            )
        ),
    )


def render_interaction_catalog(audit: InteractionCatalogAudit) -> list[str]:
    """Render the registry-derived catalog beside its separate proof status."""

    by_key = {entry.key: entry for entry in audit.entries}
    counts = audit.status_counts()
    lines = [
        f"operations              {len(audit.rows):3d}",
        f"planner-visible         {sum(1 for row in audit.rows if row.planner_visible):3d}",
        f"internal-only           {sum(1 for row in audit.rows if not row.planner_visible):3d}",
        f"native commands         {len(audit.native_commands):3d}",
        f"manifest entries        {len(audit.entries):3d}",
        f"coverage proof          {'PASS' if audit.passed else 'FAIL'}",
        "",
        "PROOF STATUS",
    ]
    for status in PROOF_STATUSES:
        lines.append(f"  {status:<16} {counts[status]:3d}")
    lines.extend(
        (
            "",
            "REGISTRY-DERIVED (what the code says today)",
            f"  {'operation':<35} {'selection':<12} {'exec':<12} "
            f"{'native cmd':<26} visibility",
        )
    )
    for row in audit.rows:
        visibility = ", ".join(row.adapters) if row.adapters else "internal-only"
        lines.append(
            f"  {row.kind:<35} {row.selection_requirement:<12} {row.execution:<12} "
            f"{row.native_command or '-':<26} {visibility}"
        )
    lines.extend(
        (
            "",
            "PROPOSED CONTRACT AND PROOF (hand-authored; moves to the registry in Slice 1)",
            f"  {'operation':<35} {'kind':<18} {'recipients':<19} "
            f"{'selection':<16} {'milestone':<23} status",
        )
    )
    for row in audit.rows:
        entry = by_key.get(row.kind)
        if entry is None:
            lines.append(f"  {row.kind:<35} (no manifest entry)")
            continue
        lines.append(
            f"  {row.kind:<35} {entry.proposed_interaction_kind:<18} "
            f"{entry.proposed_recipient_scope:<19} "
            f"{entry.proposed_selection_dependency:<16} "
            f"{entry.proposed_completion_milestone:<23} {entry.proof_status}"
        )
    lines.extend(("", "NATIVE COMMAND ROUTES"))
    for command in sorted(audit.native_commands):
        owner = native_command_owner(command)
        vestigial = " (vestigial name)" if command in VESTIGIAL_NATIVE_COMMAND_NAMES else ""
        lines.append(f"  {command:<28} -> {owner}{vestigial}")
    subcases = [entry for entry in audit.entries if entry.subcase]
    if subcases:
        lines.extend(("", "SEMANTIC SUBCASES"))
        for entry in subcases:
            lines.append(
                f"  {entry.key:<45} {entry.proposed_recipient_scope:<19} "
                f"{entry.proof_status}"
            )
    failures = (
        ("operations missing from the manifest", audit.uncatalogued_operations),
        ("manifest entries for unknown operations", audit.unknown_manifest_operations),
        ("duplicate manifest keys", audit.duplicate_manifest_keys),
        ("invalid proof statuses", audit.invalid_proof_statuses),
        ("manifest restates registry-owned fields", audit.manifest_restates_registry),
        ("native commands without a definition", audit.native_commands_without_definition),
        ("entries claiming proof without evidence", audit.entries_without_evidence),
    )
    for label, values in failures:
        if values:
            lines.extend(("", f"{label.upper()}: {', '.join(values)}"))
    return lines
