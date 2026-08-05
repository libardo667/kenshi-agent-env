"""Registry-derived inventory of how each operation interacts with Kenshi.

Two artefacts, deliberately kept apart:

*The catalog* is generated from the sole operation registry and the affordance
adapters. It states the interaction contract each operation actually declares.
It invents nothing.

*The proof-status manifest* is hand-authored and lives beside the catalog
rather than inside it. It carries only proof status and evidence. Slice 1 moved
the interaction contract into `OperationDefinition`, so the contract now has
exactly one home; `REGISTRY_OWNED_FIELDS` and the `manifest_restates_registry`
check keep the manifest from growing a second copy of it.

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
from ..operation_definitions import OPERATION_DEFINITION_LIST, OperationDefinition

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
        "interaction_kind",
        "recipient_scope",
        "selection_dependency",
        "completion_milestone",
        "conflict_policy",
        "playback_requirement",
        "proposed_interaction_kind",
        "proposed_recipient_scope",
        "proposed_selection_dependency",
        "proposed_completion_milestone",
        "proposed_conflict_policy",
        "proposed_playback_requirement",
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
    interaction_kind: str
    recipient_scope: str
    selection_dependency: str
    completion_milestone: str
    conflict_policy: str
    playback_requirement: str
    contract_fingerprint: str
    contract_is_dynamic: bool
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
    """One operation's proof status and the evidence behind it."""

    key: str
    operation_kind: str
    subcase: str
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


DYNAMIC = "varies"


def _contract_fields(definition: OperationDefinition) -> dict[str, str]:
    """The six contract values for display, honest about dynamic resolution.

    A definition with a resolver has no single contract. Its recipient scope is
    invariant across subcases and is declared, so that is reported exactly;
    everything the resolver varies is reported as varying rather than as one
    arbitrarily chosen subcase.
    """

    contract = definition.interaction
    if contract is not None:
        return {
            "interaction_kind": contract.interaction_kind.value,
            "recipient_scope": contract.recipient_scope.value,
            "selection_dependency": contract.selection_dependency.value,
            "completion_milestone": contract.completion_milestone.value,
            "conflict_policy": contract.conflict_policy.value,
            "playback_requirement": contract.playback_requirement.value,
            "contract_fingerprint": contract.fingerprint(),
        }
    return {
        "interaction_kind": DYNAMIC,
        "recipient_scope": definition.recipient_scope_for().value,
        "selection_dependency": DYNAMIC,
        "completion_milestone": DYNAMIC,
        "conflict_policy": DYNAMIC,
        "playback_requirement": DYNAMIC,
        "contract_fingerprint": DYNAMIC,
    }


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
        contract = _contract_fields(definition)
        rows.append(
            CatalogRow(
                kind=definition.kind,
                planner_visible=bool(adapters),
                adapters=adapters,
                interaction_kind=contract["interaction_kind"],
                recipient_scope=contract["recipient_scope"],
                selection_dependency=contract["selection_dependency"],
                completion_milestone=contract["completion_milestone"],
                conflict_policy=contract["conflict_policy"],
                playback_requirement=contract["playback_requirement"],
                contract_fingerprint=contract["contract_fingerprint"],
                contract_is_dynamic=definition.interaction is None,
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
            "INTERACTION CONTRACT (resolved from the sole operation registry)",
            f"  {'operation':<35} {'kind':<18} {'recipients':<19} "
            f"{'selection':<16} {'milestone':<23} proof",
        )
    )
    for row in audit.rows:
        entry = by_key.get(row.kind)
        status = entry.proof_status if entry is not None else "NO MANIFEST ENTRY"
        marker = " *" if row.contract_is_dynamic else ""
        lines.append(
            f"  {row.kind + marker:<35} {row.interaction_kind:<18} "
            f"{row.recipient_scope:<19} {row.selection_dependency:<16} "
            f"{row.completion_milestone:<23} {status}"
        )
    if any(row.contract_is_dynamic for row in audit.rows):
        lines.extend(
            (
                "",
                "  * resolves its contract per exact action; recipient scope is",
                "    invariant across its subcases and is what appears above.",
            )
        )
    lines.extend(
        (
            "",
            "EXECUTION AND ROUTING",
            f"  {'operation':<35} {'exec':<18} {'playback':<24} "
            f"{'native cmd':<26} visibility",
        )
    )
    for row in audit.rows:
        visibility = ", ".join(row.adapters) if row.adapters else "internal-only"
        lines.append(
            f"  {row.kind:<35} {row.execution:<18} {row.playback_requirement:<24} "
            f"{row.native_command or '-':<26} {visibility}"
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
            lines.append(f"  {entry.key:<45} {entry.proof_status}")
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
