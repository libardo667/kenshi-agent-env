"""Registry-derived proof that private operation authority is singular."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..affordances import AFFORDANCE_ADAPTERS
from ..operation_definitions import OPERATION_DEFINITION_LIST, OPERATION_DEFINITIONS


@dataclass(frozen=True, slots=True)
class DefinitionAudit:
    kind: str
    operation_type: str
    handler_key: str
    adapters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdapterAudit:
    name: str
    operation_kinds: tuple[str, ...]
    completeness_boundary: str


@dataclass(frozen=True, slots=True)
class OperationRegistryAudit:
    definitions: tuple[DefinitionAudit, ...]
    adapters: tuple[AdapterAudit, ...]
    missing_definitions: tuple[str, ...]
    duplicate_definitions: tuple[str, ...]
    missing_handler_keys: tuple[str, ...]
    duplicate_handler_keys: tuple[str, ...]
    missing_binders: tuple[str, ...]
    missing_boundaries: tuple[str, ...]
    # Definitions no adapter claims, and which the agent therefore cannot be
    # offered no matter what the world looks like. The audit checked the
    # forward direction only - adapters naming definitions that do not exist -
    # so an operation could be built end to end and remain unreachable. `pause`,
    # `set_speed` and `wait` all were, which left an agent handed a paused save
    # with sixty-four affordances and no way to start the clock.
    unreachable_definitions: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not any(
            (
                self.missing_definitions,
                self.duplicate_definitions,
                self.missing_handler_keys,
                self.duplicate_handler_keys,
                self.missing_binders,
                self.missing_boundaries,
                self.unreachable_definitions,
            )
        )


# Operations an adapter never offers on its own because another offered
# operation runs them internally. The retired harvest composite used to make
# `produce_resource_output` such a private step; the unified transaction now
# offers production directly, so this exception set is intentionally empty.
REACHED_BY_COMPOSITION: frozenset[str] = frozenset()


def audit_operation_registry() -> OperationRegistryAudit:
    """Prove adapter coverage directly from the one definition registry."""

    kind_counts = Counter(definition.kind for definition in OPERATION_DEFINITION_LIST)
    handler_counts = Counter(
        definition.handler_key
        for definition in OPERATION_DEFINITION_LIST
        if definition.handler_key
    )
    adapter_kinds = {
        kind
        for adapter in AFFORDANCE_ADAPTERS
        for kind in adapter.operation_kinds
    }
    definitions = tuple(
        DefinitionAudit(
            kind=definition.kind,
            operation_type=definition.operation_type.__name__,
            handler_key=definition.handler_key,
            adapters=tuple(
                sorted(
                    adapter.name
                    for adapter in AFFORDANCE_ADAPTERS
                    if definition.kind in adapter.operation_kinds
                )
            ),
        )
        for definition in sorted(
            OPERATION_DEFINITION_LIST,
            key=lambda candidate: candidate.kind,
        )
    )
    adapters = tuple(
        AdapterAudit(
            name=adapter.name,
            operation_kinds=tuple(sorted(adapter.operation_kinds)),
            completeness_boundary=adapter.completeness_boundary,
        )
        for adapter in AFFORDANCE_ADAPTERS
    )
    return OperationRegistryAudit(
        definitions=definitions,
        adapters=adapters,
        missing_definitions=tuple(sorted(adapter_kinds - OPERATION_DEFINITIONS.keys())),
        duplicate_definitions=tuple(
            sorted(kind for kind, count in kind_counts.items() if count != 1)
        ),
        missing_handler_keys=tuple(
            sorted(
                definition.kind
                for definition in OPERATION_DEFINITION_LIST
                if not definition.handler_key
            )
        ),
        duplicate_handler_keys=tuple(
            sorted(key for key, count in handler_counts.items() if count != 1)
        ),
        missing_binders=tuple(
            sorted(
                definition.kind
                for definition in OPERATION_DEFINITION_LIST
                if not callable(definition.bind)
            )
        ),
        missing_boundaries=tuple(
            sorted(
                adapter.name
                for adapter in AFFORDANCE_ADAPTERS
                if not adapter.completeness_boundary.strip()
            )
        ),
        unreachable_definitions=tuple(
            sorted(OPERATION_DEFINITIONS.keys() - adapter_kinds - REACHED_BY_COMPOSITION)
        ),
    )


def render_operation_registry_report(audit: OperationRegistryAudit) -> list[str]:
    """Render the ownership proof without inventing an implementation queue."""

    adapter_operation_kinds = {
        kind for adapter in audit.adapters for kind in adapter.operation_kinds
    }
    lines = [
        f"definitions            {len(audit.definitions):3d}",
        f"adapter operation kinds {len(adapter_operation_kinds):3d}",
        f"adapter routes          {sum(len(row.operation_kinds) for row in audit.adapters):3d}",
        f"adapters                {len(audit.adapters):3d}",
        f"ownership proof         {'PASS' if audit.passed else 'FAIL'}",
        "",
        "DEFINITIONS",
    ]
    for definition_row in audit.definitions:
        adapters = (
            ", ".join(definition_row.adapters)
            if definition_row.adapters
            else (
                "by-composition"
                if definition_row.kind in REACHED_BY_COMPOSITION
                else "UNREACHABLE"
            )
        )
        lines.append(
            f"  {definition_row.kind:<35} {definition_row.handler_key:<55} "
            f"{definition_row.operation_type} [{adapters}]"
        )
    lines.extend(("", "SOURCE-SPECIFIC COMPLETENESS BOUNDARIES"))
    for adapter_row in audit.adapters:
        lines.append(f"  {adapter_row.name}: {adapter_row.completeness_boundary}")
    failures = (
        ("missing definitions", audit.missing_definitions),
        ("duplicate definitions", audit.duplicate_definitions),
        ("missing handler keys", audit.missing_handler_keys),
        ("duplicate handler keys", audit.duplicate_handler_keys),
        ("missing binders", audit.missing_binders),
        ("missing completeness boundaries", audit.missing_boundaries),
        ("unreachable definitions", audit.unreachable_definitions),
    )
    for label, values in failures:
        if values:
            lines.extend(("", f"{label.upper()}: {', '.join(values)}"))
    return lines
