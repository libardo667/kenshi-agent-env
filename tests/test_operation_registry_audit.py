"""The generated operation report derives only from surviving authorities."""

from __future__ import annotations

from kenshi_agent.affordances import AFFORDANCE_ADAPTERS
from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS
from kenshi_agent.tooling.operation_registry_audit import (
    audit_operation_registry,
    render_operation_registry_report,
)


def test_every_adapter_operation_has_exactly_one_definition_and_handler() -> None:
    audit = audit_operation_registry()

    assert audit.passed
    assert not audit.missing_definitions
    assert not audit.duplicate_definitions
    assert not audit.missing_handler_keys
    assert not audit.duplicate_handler_keys
    assert not audit.missing_binders


def test_adapter_completeness_boundaries_remain_source_specific() -> None:
    audit = audit_operation_registry()

    assert not audit.missing_boundaries
    assert {row.name for row in audit.adapters} == {
        adapter.name for adapter in AFFORDANCE_ADAPTERS
    }
    assert all(row.completeness_boundary for row in audit.adapters)


def test_registry_report_names_every_definition_without_an_implementation_queue() -> None:
    lines = render_operation_registry_report(audit_operation_registry())
    report = "\n".join(lines)

    assert "ownership proof         PASS" in report
    assert "SOURCE-SPECIFIC COMPLETENESS BOUNDARIES" in report
    assert set(OPERATION_DEFINITIONS) <= {
        line.split()[0] for line in lines if line.startswith("  ")
    }
